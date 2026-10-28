"""Mixed-variable genetic algorithm optimizer."""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import (
    active_integer_bounds,
    apply_fixed_variables,
    make_condition,
    repair_candidate,
    thickness_from_unit_interval,
    unit_interval_from_thickness,
    validate_candidate,
)
from optimizers.core.evaluation import decompose_evaluator_status, is_feasible, normalized_constraint_violation, penalized_loss


# 목적: pymoo 기반 mixed-variable GA를 수행한다.
# 입력: dependency injection으로 받은 evaluator와 population size.
# 출력: optimize 호출 시 OptimizationResult.
@dataclass
class MixedVariableGAOptimizer(BaseOptimizer):
    """Mixed-variable genetic algorithm optimizer."""

    method_name: str = "mixed_variable_ga"
    population_size: int = 20

    # 목적: 초기 후보를 pymoo initial population으로 넣고 전체 budget 안에서 한 번만 평가한다.
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
        """Run mixed-variable GA."""
        if budget < 0:
            raise ValueError("budget must be non-negative")
        if self.population_size <= 0:
            raise ValueError("population_size must be positive")

        pymoo_api = _import_pymoo()
        condition = make_condition(condition_name)
        records: List[EvaluationRecord] = []
        tracker = _SearchTracker()

        # MixedVariableGA owns evaluation of the supplied sampling population.
        # Pre-evaluating here and seeding the same points again would spend the
        # simulator budget twice on identical LLM candidates.
        remaining_budget = budget
        if remaining_budget > 0:
            self._run_pymoo_ga(
                pymoo_api=pymoo_api,
                condition=condition,
                seed=seed,
                remaining_budget=remaining_budget,
                fixed_variables=fixed_variables,
                initial_candidates=initial_candidates or [],
                records=records,
                tracker=tracker,
            )

        return tracker.to_result(
            method_name=self.method_name,
            condition_name=condition_name,
            records=records,
            seed=seed,
            budget=budget,
        )

    # 목적: pymoo MixedVariableGA를 실행하고 simulator evaluations를 records에 추가한다.
    # 입력: pymoo API, condition, seed, 남은 budget, fixed variables, records, tracker.
    # 출력: 없음, records와 tracker를 갱신한다.
    def _run_pymoo_ga(
        self,
        pymoo_api: "_PymooAPI",
        condition: Dict[str, Any],
        seed: int,
        remaining_budget: int,
        fixed_variables: Optional[Dict[str, Any]],
        initial_candidates: List[Dict[str, Any]],
        records: List[EvaluationRecord],
        tracker: "_SearchTracker",
    ) -> None:
        pop_size = min(self.population_size, remaining_budget)
        problem = _make_batpac_mixed_variable_problem(
            optimizer=self,
            condition=condition,
            fixed_variables=fixed_variables,
            records=records,
            tracker=tracker,
            max_records=len(records) + remaining_budget,
            population_size=max(1, pop_size),
            start_record_count=len(records),
            pymoo_api=pymoo_api,
            initial_candidates=initial_candidates,
        )
        algorithm_kwargs = {"pop_size": max(1, pop_size)}
        sampling = _initial_population_sampling(
            pymoo_api,
            initial_candidates=initial_candidates,
            fixed_variables=fixed_variables,
            pop_size=max(1, pop_size),
        )
        if sampling is not None:
            algorithm_kwargs["sampling"] = sampling
        algorithm = pymoo_api.MixedVariableGA(**algorithm_kwargs)

        # pymoo는 generation 단위로 평가할 수 있으므로 Problem 내부에서도 simulator budget을 막는다.
        pymoo_api.minimize(
            problem,
            algorithm,
            termination=("n_evals", remaining_budget),
            seed=seed,
            verbose=False,
        )

    # 목적: candidate 하나를 evaluator로 평가하고 record로 포장한다.
    # 입력: iteration 번호, candidate, condition, candidate source.
    # 출력: EvaluationRecord.
    def _evaluate_candidate(
        self,
        iteration: int,
        candidate: Dict[str, Any],
        condition: Dict[str, Any],
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
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
        return EvaluationRecord(
            iteration=iteration,
            candidate=copy.deepcopy(candidate),
            condition_name=str(condition["name"]),
            result=copy.deepcopy(result),
            feasible=feasible,
            loss=loss,
            violation=violation,
            wall_time_sec=wall_time_sec,
            metadata={"source": source, "fitness": loss, **dict(metadata or {})},
        )


# 목적: pymoo ElementwiseProblem으로 BatPaC candidate 평가를 연결한다.
# 입력: optimizer, condition, fixed variables, 공유 records/tracker, budget.
# 출력: pymoo가 호출할 mixed-variable problem 객체.
def _make_batpac_mixed_variable_problem(
    optimizer: MixedVariableGAOptimizer,
    condition: Dict[str, Any],
    fixed_variables: Optional[Dict[str, Any]],
    records: List[EvaluationRecord],
    tracker: "_SearchTracker",
    max_records: int,
    population_size: int,
    start_record_count: int,
    pymoo_api: "_PymooAPI",
    initial_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Create a pymoo problem wrapper."""
    base_cls = pymoo_api.ElementwiseProblem
    initial_keys = {
        _candidate_key(_prepare_candidate(candidate, fixed_variables))
        for candidate in (initial_candidates or [])
    }

    class Problem(base_cls):
        # 목적: mixed-variable bounds와 evaluation state를 초기화한다.
        # 입력: optimizer state와 pymoo API.
        # 출력: pymoo Problem instance.
        def __init__(self) -> None:
            vars_spec = _pymoo_vars_spec(pymoo_api, fixed_variables)
            super().__init__(vars=vars_spec, n_obj=1)

        # 목적: pymoo candidate를 repair 후 evaluator loss로 변환한다.
        # 입력: pymoo mixed-variable candidate X와 out dict.
        # 출력: out["F"]에 penalized loss를 저장한다.
        def _evaluate(self, X: Mapping[str, Any], out: Dict[str, Any], *args: Any, **kwargs: Any) -> None:
            if len(records) >= max_records:
                out["F"] = math.inf
                return

            candidate = _prepare_candidate(_candidate_from_search_variables(dict(X), fixed_variables), fixed_variables)
            candidate_key = _candidate_key(candidate)
            is_seeded_initial = candidate_key in initial_keys
            if is_seeded_initial:
                initial_keys.remove(candidate_key)
            local_index = max(0, len(records) - start_record_count)
            generation_index = local_index // max(1, population_size)
            individual_index = local_index % max(1, population_size)
            record = optimizer._evaluate_candidate(
                len(records),
                candidate,
                condition,
                "initial" if is_seeded_initial else "pymoo_ga",
                metadata={
                    "generation_index": generation_index,
                    "individual_index": individual_index,
                    "population_size": population_size,
                    "role": "initial_candidate" if is_seeded_initial else (
                        "initial_population" if generation_index == 0 else "offspring"
                    ),
                    "fixed_variables": dict(fixed_variables or {}),
                    "mutation_metadata": None,
                    "crossover_metadata": None,
                },
            )
            records.append(record)
            tracker.update(record)
            record.metadata["fitness"] = record.loss
            out["F"] = record.loss

    return Problem()


# 목적: pymoo import를 지연시키고 없을 때 명확한 설치 안내를 제공한다.
# 입력: 없음.
# 출력: pymoo mixed-variable 최적화에 필요한 API 묶음.
def _import_pymoo() -> "_PymooAPI":
    try:
        from pymoo.core.mixed import MixedVariableGA
        from pymoo.core.problem import ElementwiseProblem
        from pymoo.core.variable import Choice, Integer, Real
        from pymoo.optimize import minimize
        try:
            from pymoo.core.population import Population
        except ImportError:
            Population = None
    except ImportError as exc:
        raise ImportError(
            "MixedVariableGAOptimizer requires pymoo. Install it with "
            "`python3 -m pip install pymoo` or your environment manager, "
            "then rerun the GA optimizer."
        ) from exc
    return _PymooAPI(
        ElementwiseProblem=ElementwiseProblem,
        Choice=Choice,
        Integer=Integer,
        Real=Real,
        Population=Population,
        MixedVariableGA=MixedVariableGA,
        minimize=minimize,
    )


# 목적: lazy import된 pymoo 객체들을 명확히 전달한다.
# 입력: pymoo classes/functions.
# 출력: _PymooAPI dataclass.
@dataclass(frozen=True)
class _PymooAPI:
    ElementwiseProblem: Any
    Choice: Any
    Integer: Any
    Real: Any
    Population: Any
    MixedVariableGA: Any
    minimize: Any


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


def _pymoo_vars_spec(pymoo_api: "_PymooAPI", fixed_variables: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    fixed = fixed_variables or {}
    integer_bounds = active_integer_bounds()
    vars_spec: Dict[str, Any] = {}
    if "x_chem" not in fixed:
        vars_spec["x_chem"] = pymoo_api.Choice(options=["LFP", "NMC811"])
    if "x_T" not in fixed:
        vars_spec["u_T"] = pymoo_api.Real(bounds=(0.0, 1.0))
    if "x_S" not in fixed:
        vars_spec["x_S"] = pymoo_api.Integer(bounds=integer_bounds["x_S"])
    if "x_MPR" not in fixed:
        vars_spec["x_MPR"] = pymoo_api.Integer(bounds=integer_bounds["x_MPR"])
    if "x_R" not in fixed:
        vars_spec["x_R"] = pymoo_api.Integer(bounds=integer_bounds["x_R"])
    return vars_spec


def _candidate_from_search_variables(
    search_variables: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    fixed = dict(fixed_variables or {})
    candidate = {key: value for key, value in search_variables.items() if key != "u_T"}
    candidate.update(fixed)
    if "u_T" in search_variables and "x_T" not in candidate:
        chemistry = candidate.get("x_chem")
        if chemistry is None:
            raise ValueError("x_chem is required to map u_T to physical x_T")
        candidate["x_T"] = thickness_from_unit_interval(chemistry, search_variables["u_T"])
    return candidate


def _search_variables_from_candidate(
    candidate: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    fixed = fixed_variables or {}
    prepared = _prepare_candidate(candidate, fixed)
    search: Dict[str, Any] = {}
    if "x_chem" not in fixed:
        search["x_chem"] = prepared["x_chem"]
    if "x_T" not in fixed:
        search["u_T"] = unit_interval_from_thickness(prepared["x_chem"], prepared["x_T"])
    for key in ("x_S", "x_MPR", "x_R"):
        if key not in fixed:
            search[key] = prepared[key]
    return search


def _initial_population_sampling(
    pymoo_api: "_PymooAPI",
    initial_candidates: List[Dict[str, Any]],
    fixed_variables: Optional[Mapping[str, Any]],
    pop_size: int,
) -> Any:
    """Initial population with warm starts: the warm starts first, filled up to pop_size by the cold-start sampler.

    Without warm starts the pymoo default sampling is used (None).
    """
    if not initial_candidates:
        return None
    rows = [
        _search_variables_from_candidate(candidate, fixed_variables)
        for candidate in initial_candidates[: max(0, int(pop_size))]
    ]
    return _warm_start_fill_sampling(rows)


def _warm_start_fill_sampling(rows: List[Dict[str, Any]]) -> Any:
    """pymoo Sampling that returns `rows` and then distinct samples of MixedVariableSampling.

    MixedVariableSampling is the sampler of the cold start. It draws from the algorithm's random state, which pymoo
    seeds with the optimizer seed.
    """
    from pymoo.core.mixed import MixedVariableSampling
    from pymoo.core.sampling import Sampling

    def key(row: Mapping[str, Any]) -> Tuple[Any, ...]:
        return tuple(sorted((k, str(v) if isinstance(v, str) else float(v)) for k, v in row.items()))

    class WarmStartFillSampling(Sampling):
        def __init__(self, warm_rows: List[Dict[str, Any]]) -> None:
            super().__init__()
            self.rows = [dict(row) for row in warm_rows]

        def _do(self, problem: Any, n_samples: int, random_state: Any = None, **kwargs: Any) -> List[Dict[str, Any]]:
            out, seen = list(self.rows), {key(row) for row in self.rows}
            for _ in range(100):
                if len(out) >= n_samples:
                    break
                for row in MixedVariableSampling()._do(problem, n_samples - len(out), random_state=random_state):
                    if key(row) not in seen and len(out) < n_samples:
                        seen.add(key(row))
                        out.append(row)
            return out

    return WarmStartFillSampling(rows)


def _candidate_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Return a stable identity for simulator-relevant design values."""
    return tuple(candidate[key] for key in ("x_chem", "x_T", "x_S", "x_MPR", "x_R"))


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
