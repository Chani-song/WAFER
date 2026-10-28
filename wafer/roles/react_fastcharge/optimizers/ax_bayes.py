"""Optional Ax/BoTorch Bayesian optimizer."""

from __future__ import annotations

import copy
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import (
    active_integer_bounds,
    apply_fixed_variables,
    get_thickness_bounds,
    make_condition,
    repair_candidate,
    thickness_from_unit_interval,
    unit_interval_from_thickness,
    validate_candidate,
)
from optimizers.core.evaluation import (
    decompose_evaluator_status,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)

_PHYSICAL_VARIABLE_NAMES = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")


# 목적: Ax/BoTorch 기반 mixed-variable Bayesian optimization을 수행한다.
# 입력: dependency injection으로 받은 evaluator와 Ax 초기화 설정.
# 출력: optimize 호출 시 OptimizationResult.
@dataclass
class AxBayesOptOptimizer(BaseOptimizer):
    """Ax/BoTorch Bayesian optimizer."""

    method_name: str = "ax_bayes_opt"
    initialization_trials: Optional[int] = None
    surrogate_slices_path: Optional[str] = None
    surrogate_slice_grid_points: int = 25
    torch_device: str = "cpu"

    # 목적: 초기 후보를 먼저 평가하고 남은 budget을 Ax trial suggestion으로 채운다.
    # 입력: condition 이름, budget, seed, 초기 후보, fixed variables.
    # 출력: 모든 simulator record와 best solution 정보를 담은 OptimizationResult.
    def optimize(
        self,
        condition_name: str,
        budget: int,
        seed: int,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        fixed_variables: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        """Run Ax/BoTorch optimization."""
        if budget < 0:
            raise ValueError("budget must be non-negative")

        ax_api = _import_ax()
        condition = make_condition(condition_name)
        fixed = fixed_variables or {}
        free_names = _free_variable_names(fixed)
        initialization_trial_count = (
            _initialization_trials(self.initialization_trials, budget, len(free_names))
            if free_names
            else 0
        )
        records: List[EvaluationRecord] = []
        tracker = _SearchTracker()
        self._prepare_surrogate_slice_output()

        ax_client = self._create_ax_client(ax_api, free_names, seed, initialization_trial_count)
        self._evaluate_initial_candidates(
            ax_client=ax_client,
            free_names=free_names,
            condition=condition,
            budget=budget,
            initial_candidates=initial_candidates,
            fixed_variables=fixed,
            initialization_trial_count=initialization_trial_count,
            records=records,
            tracker=tracker,
        )

        if not free_names:
            self._evaluate_fixed_candidate_if_needed(
                condition=condition,
                budget=budget,
                fixed_variables=fixed,
                initialization_trial_count=initialization_trial_count,
                records=records,
                tracker=tracker,
            )
        else:
            self._run_ax_trials(
                ax_client=ax_client,
                condition=condition,
                budget=budget,
                initialization_trial_count=initialization_trial_count,
                fixed_variables=fixed,
                records=records,
                tracker=tracker,
            )

        result = tracker.to_result(
            method_name=self.method_name,
            condition_name=condition_name,
            records=records,
            seed=seed,
            budget=budget,
        )
        if self.surrogate_slices_path:
            result.metadata["axbo_surrogate_slices_path"] = self.surrogate_slices_path
        return result

    # 목적: AxClient와 search space를 생성한다.
    # 입력: lazy Ax API, free variable 이름, seed, budget.
    # 출력: AxClient 또는 free variable이 없으면 None.
    def _create_ax_client(
        self,
        ax_api: "_AxAPI",
        free_names: Sequence[str],
        seed: int,
        initialization_trial_count: int,
    ) -> Optional[Any]:
        if not free_names:
            return None
        ax_client = ax_api.AxClient(
            random_seed=seed,
            verbose_logging=False,
            # Ax 1.3.x forwards this constructor option to its generation
            # strategy. Passing it again through create_experiment causes
            # choose_generation_strategy_legacy(..., torch_device=...) to
            # receive the same keyword twice.
            torch_device=_ax_torch_device(self.torch_device),
        )
        ax_client.create_experiment(
            parameters=_ax_parameters(free_names),
            objectives={"loss": ax_api.ObjectiveProperties(minimize=True)},
            choose_generation_strategy_kwargs={
                "num_initialization_trials": initialization_trial_count,
            },
            is_test=True,
        )
        return ax_client
    # 목적: initial candidates를 evaluator로 먼저 평가하고 Ax에 관측값으로 등록한다.
    # 입력: AxClient, free variable 이름, condition, budget, 초기 후보, fixed variables.
    # 출력: 없음, records/tracker와 Ax trial 상태를 갱신한다.
    def _evaluate_initial_candidates(
        self,
        ax_client: Optional[Any],
        free_names: Sequence[str],
        condition: Dict[str, Any],
        budget: int,
        initial_candidates: Optional[List[Dict[str, Any]]],
        fixed_variables: Mapping[str, Any],
        initialization_trial_count: int,
        records: List[EvaluationRecord],
        tracker: "_SearchTracker",
    ) -> None:
        for candidate in initial_candidates or []:
            if len(records) >= budget:
                return
            prepared = _prepare_candidate(candidate, fixed_variables)
            trial_index = _attach_trial(ax_client, prepared, free_names)
            record = self._evaluate_candidate(
                iteration=len(records),
                candidate=prepared,
                condition=condition,
                source="initial",
                ax_trial_index=trial_index,
                generation_phase=_ax_generation_phase(trial_index, initialization_trial_count),
                fixed_variables=fixed_variables,
                free_names=free_names,
                candidate_chosen_by_ax=False,
            )
            records.append(record)
            tracker.update(record)
            _complete_trial(ax_client, trial_index, record.loss)

    # 목적: 모든 변수가 fixed일 때 한 번 평가해 trace를 남긴다.
    # 입력: condition, budget, fixed variables, records/tracker.
    # 출력: 없음, 필요하면 records/tracker를 갱신한다.
    def _evaluate_fixed_candidate_if_needed(
        self,
        condition: Dict[str, Any],
        budget: int,
        fixed_variables: Mapping[str, Any],
        initialization_trial_count: int,
        records: List[EvaluationRecord],
        tracker: "_SearchTracker",
    ) -> None:
        if records or len(records) >= budget:
            return
        prepared = _prepare_candidate(dict(fixed_variables), fixed_variables)
        record = self._evaluate_candidate(len(records), prepared, condition, "fixed", None)
        records.append(record)
        tracker.update(record)

    # 목적: Ax가 제안한 trial들을 budget 한도까지 평가한다.
    # 입력: AxClient, condition, budget, fixed variables, records/tracker.
    # 출력: 없음, records/tracker와 Ax trial 상태를 갱신한다.
    def _run_ax_trials(
        self,
        ax_client: Any,
        condition: Dict[str, Any],
        budget: int,
        initialization_trial_count: int,
        fixed_variables: Mapping[str, Any],
        records: List[EvaluationRecord],
        tracker: "_SearchTracker",
    ) -> None:
        while len(records) < budget:
            try:
                parameters, trial_index = ax_client.get_next_trial()
            except Exception as exc:
                if _is_search_space_exhausted(exc):
                    tracker.termination_reason = "search_space_exhausted"
                    tracker.termination_details = {
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                        "requested_budget": int(budget),
                        "completed_evaluations": len(records),
                        "unused_evaluation_budget": max(0, int(budget) - len(records)),
                    }
                    return
                raise
            candidate = _candidate_from_ax_parameters(parameters, fixed_variables)
            prepared = _prepare_candidate(candidate, fixed_variables)
            generation_phase = _ax_generation_phase(trial_index, initialization_trial_count)
            if generation_phase == "BoTorch":
                self._export_surrogate_slice(
                    ax_client=ax_client,
                    condition=condition,
                    iteration=len(records),
                    ax_trial_index=trial_index,
                    selected_candidate=prepared,
                    fixed_variables=fixed_variables,
                    free_names=_free_variable_names(fixed_variables),
                    tracker=tracker,
                )
            record = self._evaluate_candidate(
                iteration=len(records),
                candidate=prepared,
                condition=condition,
                source="ax",
                ax_trial_index=trial_index,
                generation_phase=generation_phase,
                fixed_variables=fixed_variables,
                free_names=_free_variable_names(fixed_variables),
                candidate_chosen_by_ax=True,
            )
            records.append(record)
            tracker.update(record)
            _complete_trial(ax_client, trial_index, record.loss)

    # 목적: candidate 하나를 evaluator로 평가하고 record로 포장한다.
    # 입력: iteration 번호, candidate, condition, candidate source, Ax trial index.
    # 출력: EvaluationRecord.
    def _evaluate_candidate(
        self,
        iteration: int,
        candidate: Dict[str, Any],
        condition: Dict[str, Any],
        source: str,
        ax_trial_index: Optional[int],
        generation_phase: Optional[str] = None,
        fixed_variables: Optional[Mapping[str, Any]] = None,
        free_names: Sequence[str] = (),
        candidate_chosen_by_ax: bool = False,
    ) -> EvaluationRecord:
        started = time.perf_counter()
        try:
            result = self.evaluator(copy.deepcopy(candidate), dict(condition))
        except Exception as exc:  # pragma: no cover - defensive evaluator boundary
            result = _failure_result(exc)
        wall_time_sec = time.perf_counter() - started

        feasible = is_feasible(result, condition)
        violation = normalized_constraint_violation(result, condition)
        loss = penalized_loss(result, condition)
        metadata = {
            "source": source,
            "generation_phase": generation_phase,
            "candidate_chosen_by_ax": candidate_chosen_by_ax,
            "ax_candidate": copy.deepcopy(candidate) if candidate_chosen_by_ax else None,
            "observed_loss": loss,
            "failed_or_missing_cost": (not result.get("optimization_valid", result.get("success", False)))
            or result.get("failure_reason") == "missing_cost",
            "fixed_variables": dict(fixed_variables or {}),
            "free_variables": list(free_names),
            "parameter_space": _ax_parameters(free_names),
        }
        if ax_trial_index is not None:
            metadata["ax_trial_index"] = ax_trial_index
        return EvaluationRecord(
            iteration=iteration,
            candidate=copy.deepcopy(candidate),
            condition_name=str(condition["name"]),
            result=copy.deepcopy(result),
            feasible=feasible,
            loss=loss,
            violation=violation,
            wall_time_sec=wall_time_sec,
            metadata=metadata,
        )

    # 목적: AxBO surrogate slice JSONL을 새 optimize 실행마다 초기화한다.
    # 입력: self.surrogate_slices_path.
    # 출력: 없음, 저장 경로가 있으면 빈 파일 상태로 준비한다.
    def _prepare_surrogate_slice_output(self) -> None:
        if not self.surrogate_slices_path:
            return
        path = Path(self.surrogate_slices_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

    # 목적: BoTorch가 선택한 trial 주변의 x_T posterior/acquisition slice를 저장한다.
    # 입력: AxClient, 선택 trial, incumbent tracker, free/fixed variable 정보.
    # 출력: 없음, JSONL에 한 줄을 append한다.
    def _export_surrogate_slice(
        self,
        ax_client: Any,
        condition: Mapping[str, Any],
        iteration: int,
        ax_trial_index: int,
        selected_candidate: Mapping[str, Any],
        fixed_variables: Mapping[str, Any],
        free_names: Sequence[str],
        tracker: "_SearchTracker",
    ) -> None:
        if not self.surrogate_slices_path:
            return
        try:
            payload = build_surrogate_slice_payload(
                ax_client=ax_client,
                condition=condition,
                iteration=iteration,
                ax_trial_index=ax_trial_index,
                selected_candidate=selected_candidate,
                fixed_variables=fixed_variables,
                free_names=free_names,
                incumbent_candidate=tracker.best_feasible_candidate or tracker.best_candidate or dict(selected_candidate),
                grid_points=self.surrogate_slice_grid_points,
            )
        except Exception as exc:  # pragma: no cover - Ax version boundary
            payload = {
                "status": "failed",
                "error": repr(exc),
                "iteration": iteration,
                "ax_trial_index": int(ax_trial_index),
                "selected_trial_point": copy.deepcopy(dict(selected_candidate)),
                "condition_name": condition.get("name"),
            }
        _append_jsonl(Path(self.surrogate_slices_path), payload)


# 목적: Ax/BoTorch import를 지연시키고 없을 때 명확한 설치 안내를 제공한다.
# 입력: 없음.
# 출력: Ax optimizer 실행에 필요한 API 묶음.
def _import_ax() -> "_AxAPI":
    try:
        from ax.service.ax_client import AxClient
        from ax.service.utils.instantiation import ObjectiveProperties
    except ImportError as exc:
        raise ImportError(
            "Ax/BoTorch is required for AxBayesOptOptimizer. "
            "Install with `pip install ax-platform` in a compatible Python environment."
        ) from exc
    return _AxAPI(AxClient=AxClient, ObjectiveProperties=ObjectiveProperties)


def _ax_torch_device(configured: str) -> Any:
    """Resolve the explicit Ax device; CPU is the reproducible default."""
    value = os.environ.get("JMD_AX_TORCH_DEVICE", configured or "cpu").strip().lower()
    if value not in {"cpu", "cuda"} and not value.startswith("cuda:"):
        raise ValueError("JMD_AX_TORCH_DEVICE must be cpu, cuda, or cuda:<index>")
    try:
        import torch
    except ImportError:
        return value
    if value.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError(
            f"Ax requested {value}, but torch.cuda.is_available() is false. "
            "Use JMD_AX_TORCH_DEVICE=cpu for the BatPaC experiment."
        )
    return torch.device(value)


def _is_search_space_exhausted(exc: Exception) -> bool:
    """Return whether Ax reports that no unique feasible candidates remain."""
    return type(exc).__name__ == "SearchSpaceExhausted" or (
        "No more feasible choices in a fully discrete search space" in str(exc)
    )


# 목적: lazy import된 Ax 객체를 명확히 전달한다.
# 입력: AxClient class와 ObjectiveProperties class.
# 출력: _AxAPI dataclass.
@dataclass(frozen=True)
class _AxAPI:
    AxClient: Any
    ObjectiveProperties: Any


# 목적: Ax search space에 남길 free variable 이름을 결정한다.
# 입력: fixed variables mapping.
# 출력: fixed가 아닌 variable 이름 tuple.
def _free_variable_names(fixed_variables: Mapping[str, Any]) -> Tuple[str, ...]:
    names: List[str] = []
    if "x_chem" not in fixed_variables:
        names.append("x_chem")
    if "x_T" not in fixed_variables:
        names.append("u_T")
    for name in ("x_S", "x_MPR", "x_R"):
        if name not in fixed_variables:
            names.append(name)
    return tuple(names)


# 목적: AxClient.create_experiment에 전달할 parameter specs를 만든다.
# 입력: free variable 이름.
# 출력: Ax parameter spec list.
def _ax_parameters(free_names: Sequence[str]) -> List[Dict[str, Any]]:
    integer_bounds = active_integer_bounds()
    specs = {
        "x_chem": {
            "name": "x_chem",
            "type": "choice",
            "values": ["LFP", "NMC811"],
            "value_type": "str",
            "is_ordered": False,
            "sort_values": False,
        },
        "u_T": {
            "name": "u_T",
            "type": "range",
            "bounds": [0.0, 1.0],
            "value_type": "float",
        },
        "x_S": {
            "name": "x_S",
            "type": "range",
            "bounds": list(integer_bounds["x_S"]),
            "value_type": "int",
        },
        "x_MPR": {
            "name": "x_MPR",
            "type": "range",
            "bounds": list(integer_bounds["x_MPR"]),
            "value_type": "int",
        },
        "x_R": {
            "name": "x_R",
            "type": "range",
            "bounds": list(integer_bounds["x_R"]),
            "value_type": "int",
        },
    }
    return [dict(specs[name]) for name in free_names]


# 목적: Ax initialization trial 수를 budget에 맞게 보수적으로 정한다.
# 입력: 사용자 override, budget, free variable 수.
# 출력: Ax generation strategy에 전달할 initialization trial 수.
def _initialization_trials(
    requested: Optional[int],
    budget: int,
    free_variable_count: int,
) -> int:
    if requested is not None:
        return max(1, min(int(requested), max(1, budget)))
    if budget <= 1:
        return 1
    return max(1, min(5, budget // 2, 2 * free_variable_count))


# 목적: Ax trial index를 initialization/model phase로 구분한다.
# 입력: trial index와 initialization trial 수.
# 출력: phase 이름 또는 None.
def _ax_generation_phase(trial_index: Optional[int], initialization_trial_count: int) -> Optional[str]:
    if trial_index is None:
        return None
    return "Sobol" if int(trial_index) < initialization_trial_count else "BoTorch"


# 목적: Ax parameter dict와 fixed variables를 내부 candidate dict로 합친다.
# 입력: Ax parameter dict와 fixed variables.
# 출력: repair 전 candidate dict.
def _candidate_from_ax_parameters(
    parameters: Mapping[str, Any],
    fixed_variables: Mapping[str, Any],
) -> Dict[str, Any]:
    candidate = {key: value for key, value in parameters.items() if key != "u_T"}
    candidate.update(fixed_variables)
    if "u_T" in parameters and "x_T" not in candidate:
        chemistry = candidate.get("x_chem")
        if chemistry is None:
            raise ValueError("x_chem is required to map u_T to physical x_T")
        candidate["x_T"] = thickness_from_unit_interval(chemistry, parameters["u_T"])
    return candidate


# 목적: initial candidate를 Ax trial로 등록한다.
# 입력: AxClient, prepared candidate, free variable 이름.
# 출력: Ax trial index 또는 Ax가 없으면 None.
def _attach_trial(
    ax_client: Optional[Any],
    candidate: Mapping[str, Any],
    free_names: Sequence[str],
) -> Optional[int]:
    if ax_client is None:
        return None
    parameters = _parameters_for_free_variables(candidate, free_names)
    _, trial_index = ax_client.attach_trial(parameters)
    return int(trial_index)


# 목적: simulator loss를 Ax trial 결과로 완료 처리한다.
# 입력: AxClient, trial index, loss.
# 출력: 없음.
def _complete_trial(ax_client: Optional[Any], trial_index: Optional[int], loss: float) -> None:
    if ax_client is None or trial_index is None:
        return
    ax_client.complete_trial(trial_index=trial_index, raw_data={"loss": float(loss)})


# 목적: Ax/BoTorch model 상태에서 x_T slice posterior/acquisition payload를 만든다.
# 입력: AxClient, condition, 선택 trial, incumbent, free variable 정보.
# 출력: JSONL에 저장할 surrogate slice dict.
def build_surrogate_slice_payload(
    ax_client: Any,
    condition: Mapping[str, Any],
    iteration: int,
    ax_trial_index: int,
    selected_candidate: Mapping[str, Any],
    fixed_variables: Mapping[str, Any],
    free_names: Sequence[str],
    incumbent_candidate: Mapping[str, Any],
    grid_points: int = 25,
) -> Dict[str, Any]:
    """Build one AxBO surrogate/acquisition slice payload."""
    incumbent = repair_candidate(incumbent_candidate)
    selected = repair_candidate(selected_candidate)
    thickness_search_name = _thickness_search_name(free_names)
    if thickness_search_name is None:
        return {
            "status": "skipped",
            "reason": "x_T is fixed or absent from the Ax search space",
            "iteration": iteration,
            "ax_trial_index": int(ax_trial_index),
            "condition_name": condition.get("name"),
            "incumbent_candidate": copy.deepcopy(incumbent),
            "selected_trial_point": copy.deepcopy(selected),
            "fixed_variables": dict(fixed_variables),
            "free_variables": list(free_names),
            "sweep_variable": "u_T",
            "slice": [],
        }

    slice_candidates = _x_t_slice_candidates(incumbent, grid_points)
    parameter_points = [
        _parameters_for_free_variables(candidate, free_names)
        for candidate in slice_candidates
    ]
    model = _ax_model_adapter(ax_client)
    features = _observation_features(parameter_points)
    means, stds = _predict_loss_mean_std(model, features)
    acquisition_values = _evaluate_acquisition_values(model, features)
    slice_rows = []
    for index, candidate in enumerate(slice_candidates):
        slice_rows.append(
            {
                "x_T": candidate["x_T"],
                "candidate": copy.deepcopy(candidate),
                "parameters": dict(parameter_points[index]),
                "posterior_mean": _value_at(means, index),
                "posterior_std": _value_at(stds, index),
                "acquisition_value": _value_at(acquisition_values, index),
            }
        )
    acquisition_available = any(row["acquisition_value"] is not None for row in slice_rows)
    posterior_available = any(row["posterior_mean"] is not None for row in slice_rows)
    return {
        "status": "success" if posterior_available else "partial",
        "iteration": iteration,
        "ax_trial_index": int(ax_trial_index),
        "condition_name": condition.get("name"),
        "incumbent_candidate": copy.deepcopy(incumbent),
        "selected_trial_point": copy.deepcopy(selected),
        "selected_trial_x_T": selected.get("x_T"),
        "fixed_variables": dict(fixed_variables),
        "free_variables": list(free_names),
        "sweep_variable": thickness_search_name,
        "grid_points": len(slice_rows),
        "posterior_available": posterior_available,
        "acquisition_available": acquisition_available,
        "acquisition_note": None if acquisition_available else "Ax model adapter did not expose acquisition evaluation.",
        "slice": slice_rows,
    }


# 목적: incumbent의 x_chem/x_S/x_MPR/x_R을 고정하고 x_T grid 후보를 만든다.
# 입력: incumbent candidate와 grid point 수.
# 출력: repaired candidate list.
def _x_t_slice_candidates(incumbent: Mapping[str, Any], grid_points: int) -> List[Dict[str, Any]]:
    chemistry = incumbent["x_chem"]
    lower, upper = get_thickness_bounds(chemistry)
    count = max(2, int(grid_points))
    if count == 1:
        values = [float(incumbent["x_T"])]
    else:
        step = (upper - lower) / float(count - 1)
        values = [lower + step * index for index in range(count)]
    candidates = []
    for value in values:
        candidate = dict(incumbent)
        candidate["x_T"] = float(value)
        candidates.append(repair_candidate(candidate))
    return candidates


# 목적: full candidate에서 Ax search space에 포함된 parameter만 추출한다.
# 입력: candidate와 free variable 이름.
# 출력: Ax parameter dict.
def _parameters_for_free_variables(
    candidate: Mapping[str, Any],
    free_names: Sequence[str],
) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {}
    for name in free_names:
        if name == "u_T":
            parameters[name] = unit_interval_from_thickness(candidate["x_chem"], candidate["x_T"])
        else:
            parameters[name] = candidate[name]
    return parameters


def _thickness_search_name(free_names: Sequence[str]) -> Optional[str]:
    if "u_T" in free_names:
        return "u_T"
    if "x_T" in free_names:
        return "x_T"
    return None


# 목적: AxClient에서 현재 model adapter를 찾는다.
# 입력: AxClient.
# 출력: model adapter 객체.
def _ax_model_adapter(ax_client: Any) -> Any:
    generation_strategy = getattr(ax_client, "generation_strategy", None)
    if generation_strategy is None:
        raise RuntimeError("AxClient does not expose generation_strategy")
    model = getattr(generation_strategy, "model", None) or getattr(generation_strategy, "model_bridge", None)
    if model is None:
        raise RuntimeError("Ax generation strategy does not expose a fitted model adapter")
    return model


# 목적: Ax ObservationFeatures를 만들되 Ax가 없는 test 환경에서는 dict로 대체한다.
# 입력: parameter point list.
# 출력: model.predict에 전달할 feature list.
def _observation_features(parameter_points: Sequence[Mapping[str, Any]]) -> List[Any]:
    try:
        from ax.core.observation import ObservationFeatures
    except ImportError:
        return [dict(point) for point in parameter_points]
    return [ObservationFeatures(parameters=dict(point)) for point in parameter_points]


# 목적: model adapter에서 loss posterior mean/std를 추출한다.
# 입력: model adapter와 observation features.
# 출력: mean list, std list.
def _predict_loss_mean_std(model: Any, features: Sequence[Any]) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if not hasattr(model, "predict"):
        raise RuntimeError("Ax model adapter does not expose predict(...)")
    means, covariances = model.predict(list(features))
    mean_values = _metric_values(means, "loss", len(features))
    variances = _metric_variances(covariances, "loss", len(features))
    std_values = []
    for variance in variances:
        if variance is None:
            std_values.append(None)
        else:
            std_values.append(math.sqrt(max(0.0, variance)))
    return mean_values, std_values


# 목적: Ax model adapter가 제공하면 acquisition value를 계산한다.
# 입력: model adapter와 observation features.
# 출력: acquisition value list 또는 None list.
def _evaluate_acquisition_values(model: Any, features: Sequence[Any]) -> List[Optional[float]]:
    evaluator = getattr(model, "evaluate_acquisition_function", None)
    if evaluator is None:
        evaluator = getattr(getattr(model, "model", None), "evaluate_acquisition_function", None)
    if evaluator is None:
        return [None for _ in features]
    try:
        raw_values = evaluator(list(features))
    except Exception:
        return [None for _ in features]
    return _sequence_to_optional_floats(raw_values, len(features))


# 목적: Ax predict mean dict에서 metric 값을 list로 정규화한다.
# 입력: mean mapping, metric 이름, expected length.
# 출력: optional float list.
def _metric_values(metric_mapping: Any, metric_name: str, expected_length: int) -> List[Optional[float]]:
    if isinstance(metric_mapping, Mapping):
        values = metric_mapping.get(metric_name)
    else:
        values = None
    return _sequence_to_optional_floats(values, expected_length)


# 목적: Ax predict covariance dict에서 metric variance를 list로 정규화한다.
# 입력: covariance mapping, metric 이름, expected length.
# 출력: optional float list.
def _metric_variances(metric_mapping: Any, metric_name: str, expected_length: int) -> List[Optional[float]]:
    values = None
    if isinstance(metric_mapping, Mapping):
        nested = metric_mapping.get(metric_name)
        if isinstance(nested, Mapping):
            values = nested.get(metric_name)
    return _sequence_to_optional_floats(values, expected_length)


# 목적: tensor/list/scalar 값을 optional float list로 정규화한다.
# 입력: raw values와 expected length.
# 출력: optional float list.
def _sequence_to_optional_floats(raw_values: Any, expected_length: int) -> List[Optional[float]]:
    if raw_values is None:
        return [None for _ in range(expected_length)]
    if hasattr(raw_values, "detach"):
        raw_values = raw_values.detach().cpu().tolist()
    elif hasattr(raw_values, "tolist"):
        raw_values = raw_values.tolist()
    if not isinstance(raw_values, (list, tuple)):
        raw_values = [raw_values for _ in range(expected_length)]
    values = list(raw_values)
    if values and isinstance(values[0], (list, tuple)):
        values = [item[0] if item else None for item in values]
    normalized = [_finite_or_none(value) for value in values[:expected_length]]
    while len(normalized) < expected_length:
        normalized.append(None)
    return normalized


# 목적: list index 접근을 안전하게 수행한다.
# 입력: sequence와 index.
# 출력: value 또는 None.
def _value_at(values: Sequence[Optional[float]], index: int) -> Optional[float]:
    return values[index] if index < len(values) else None


# 목적: finite float 또는 None으로 정규화한다.
# 입력: raw value.
# 출력: finite float 또는 None.
def _finite_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


# 목적: surrogate slice payload를 JSONL에 append한다.
# 입력: path와 payload.
# 출력: 없음.
def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


# 목적: JSONL 저장 가능한 값으로 변환한다.
# 입력: 임의 값.
# 출력: JSON-compatible value.
def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    return value


# 목적: fixed variables 적용 후 candidate를 유효 범위로 보정한다.
# 입력: candidate mapping과 fixed variables.
# 출력: evaluator에 전달할 candidate dict.
def _prepare_candidate(
    candidate: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    fixed_candidate = apply_fixed_variables(candidate, fixed_variables)
    validate_candidate(fixed_candidate)
    return repair_candidate(fixed_candidate)


# 목적: evaluator 예외를 optimizer가 처리 가능한 실패 result로 변환한다.
# 입력: 발생한 Exception.
# 출력: expected evaluator schema를 따르는 실패 result dict.
def _failure_result(exc: Exception) -> Dict[str, Any]:
    result = {
        "success": False,
        "cost": None,
        "total_pack_cost": None,
        "P": None,
        "E": None,
        "V": None,
        "c": None,
        "L_bat": None,
        "W_bat": None,
        "raw": {"error": repr(exc)},
    }
    result.update(decompose_evaluator_status(result, simulator_call_success=False))
    return result


# 목적: best feasible solution과 best penalized-loss solution을 따로 추적한다.
# 입력: EvaluationRecord들.
# 출력: OptimizationResult 생성에 필요한 best 상태.
@dataclass
class _SearchTracker:
    best_candidate: Optional[Dict[str, Any]] = None
    best_result: Optional[Dict[str, Any]] = None
    best_loss: float = math.inf
    best_feasible_candidate: Optional[Dict[str, Any]] = None
    best_feasible_result: Optional[Dict[str, Any]] = None
    best_feasible_cost: Optional[float] = None
    termination_reason: str = "budget_exhausted"
    termination_details: Optional[Dict[str, Any]] = None

    # 목적: 새 evaluation record로 best 상태를 갱신한다.
    # 입력: EvaluationRecord.
    # 출력: 없음, tracker 내부 상태를 갱신한다.
    def update(self, record: EvaluationRecord) -> None:
        """Update tracked best records."""
        if record.loss < self.best_loss:
            self.best_loss = record.loss
            self.best_candidate = copy.deepcopy(record.candidate)
            self.best_result = copy.deepcopy(record.result)

        # feasible 해가 없을 수도 있으므로 best loss와 best feasible solution은 따로 추적한다.
        cost = _finite_cost(record.result)
        if record.feasible and cost is not None:
            if self.best_feasible_cost is None or cost < self.best_feasible_cost:
                self.best_feasible_cost = cost
                self.best_feasible_candidate = copy.deepcopy(record.candidate)
                self.best_feasible_result = copy.deepcopy(record.result)

    # 목적: tracker 상태와 evaluation records를 최종 결과 객체로 변환한다.
    # 입력: method 이름, condition 이름, records, seed, budget.
    # 출력: OptimizationResult.
    def to_result(
        self,
        method_name: str,
        condition_name: str,
        records: List[EvaluationRecord],
        seed: int,
        budget: int,
    ) -> OptimizationResult:
        feasible_count = sum(1 for record in records if record.feasible)
        metadata = {
            "total_evaluations": len(records),
            "feasible_evaluations": feasible_count,
            "feasibility_rate": feasible_count / len(records) if records else 0.0,
            "best_feasible_candidate": copy.deepcopy(self.best_feasible_candidate),
            "best_feasible_result": copy.deepcopy(self.best_feasible_result),
            "best_penalized_candidate": copy.deepcopy(self.best_candidate),
            "best_penalized_result": copy.deepcopy(self.best_result),
            "requested_evaluation_budget": int(budget),
            "completed_evaluations": len(records),
            "unused_evaluation_budget": max(0, int(budget) - len(records)),
            "termination_reason": self.termination_reason,
            "termination_details": copy.deepcopy(self.termination_details),
        }
        return OptimizationResult(
            method_name=method_name,
            condition_name=condition_name,
            best_candidate=copy.deepcopy(self.best_candidate),
            best_result=copy.deepcopy(self.best_result),
            best_feasible_cost=self.best_feasible_cost,
            best_loss=self.best_loss,
            records=records,
            seed=seed,
            budget=budget,
            metadata=metadata,
        )


# 목적: result의 cost를 best feasible 비교에 사용할 수 있는지 확인한다.
# 입력: evaluator result mapping.
# 출력: finite cost 또는 None.
def _finite_cost(result: Mapping[str, Any]) -> Optional[float]:
    try:
        cost = float(result.get("cost"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost):
        return None
    return cost
