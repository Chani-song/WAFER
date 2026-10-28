"""Dependency-free mixed-variable pattern search optimizer."""

from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import (
    apply_fixed_variables,
    make_condition,
    repair_candidate,
    sample_random_candidate,
)
from optimizers.core.evaluation import (
    decompose_evaluator_status,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)


# 목적: pattern search step size를 명확하게 보관한다.
# 입력: 각 변수의 local move 크기.
# 출력: neighborhood 생성과 step 축소에 사용되는 객체.
@dataclass
class _PatternSteps:
    step_T: float = 20.0
    step_S: int = 4
    step_MPR: int = 4
    step_R: int = 1

    # 목적: 개선이 없을 때 local search step size를 줄인다.
    # 입력: 없음.
    # 출력: step이 실제로 줄었는지 여부.
    def shrink(self) -> bool:
        """Shrink local search steps."""
        old = (self.step_T, self.step_S, self.step_MPR, self.step_R)
        self.step_T *= 0.5
        self.step_S = max(1, self.step_S // 2)
        self.step_MPR = max(1, self.step_MPR // 2)
        self.step_R = 1
        return old != (self.step_T, self.step_S, self.step_MPR, self.step_R)

    # 목적: 더 줄일 discrete step이 없는지 확인한다.
    # 입력: 없음.
    # 출력: 최소 step 여부.
    def at_minimum(self) -> bool:
        """Return whether discrete steps are minimal."""
        return (
            self.step_S == 1
            and self.step_MPR == 1
            and self.step_R == 1
            and self.step_T < 1e-6
        )


# 목적: mixed-variable pattern search를 수행한다.
# 입력: dependency injection으로 받은 evaluator.
# 출력: optimize 호출 시 OptimizationResult.
@dataclass
class PatternSearchOptimizer(BaseOptimizer):
    """Mixed-variable pattern search optimizer."""

    method_name: str = "pattern_search"

    # 목적: 초기 후보를 평가한 뒤 best penalized-loss candidate 주변을 탐색한다.
    # 입력: condition 이름, budget, seed, 초기 후보, fixed variables.
    # 출력: 모든 evaluation record와 best solution 정보를 담은 OptimizationResult.
    def optimize(
        self,
        condition_name: str,
        budget: int,
        seed: int,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        fixed_variables: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        """Run pattern search."""
        if budget < 0:
            raise ValueError("budget must be non-negative")

        condition = make_condition(condition_name)
        rng = random.Random(seed)
        records: List[EvaluationRecord] = []
        evaluated_keys: Set[Tuple[Any, ...]] = set()
        tracker = _SearchTracker()

        self._evaluate_initial_candidates(
            condition=condition,
            budget=budget,
            initial_candidates=initial_candidates,
            fixed_variables=fixed_variables,
            rng=rng,
            records=records,
            evaluated_keys=evaluated_keys,
            tracker=tracker,
        )
        self._run_local_search(
            condition=condition,
            budget=budget,
            fixed_variables=fixed_variables,
            records=records,
            evaluated_keys=evaluated_keys,
            tracker=tracker,
        )
        metadata = tracker.metadata(records)
        if len(records) < budget:
            metadata["stop_reason"] = "no_unique_neighbors"

        return tracker.to_result(
            method_name=self.method_name,
            condition_name=condition_name,
            records=records,
            seed=seed,
            budget=budget,
            metadata=metadata,
        )

    # 목적: initial candidates 또는 random start candidate를 먼저 평가한다.
    # 입력: condition, budget, initial candidates, fixed variables, rng, records/cache/tracker.
    # 출력: 없음, records/cache/tracker를 갱신한다.
    def _evaluate_initial_candidates(
        self,
        condition: Dict[str, Any],
        budget: int,
        initial_candidates: Optional[List[Dict[str, Any]]],
        fixed_variables: Optional[Dict[str, Any]],
        rng: random.Random,
        records: List[EvaluationRecord],
        evaluated_keys: Set[Tuple[Any, ...]],
        tracker: "_SearchTracker",
    ) -> None:
        candidates = list(initial_candidates or [])
        if not candidates and budget > 0:
            candidates.append(sample_random_candidate(rng, fixed_variables=fixed_variables))

        for candidate in candidates:
            if len(records) >= budget:
                return
            prepared = _prepare_candidate(candidate, fixed_variables)
            self._evaluate_if_new(
                candidate=prepared,
                condition=condition,
                source="initial",
                records=records,
                evaluated_keys=evaluated_keys,
                tracker=tracker,
                metadata={
                    "fixed_variables": dict(fixed_variables or {}),
                    "sampled_variables": [],
                },
            )

    # 목적: incumbent 주변의 pattern moves를 평가하고 개선 시 이동한다.
    # 입력: condition, budget, fixed variables, records/cache/tracker.
    # 출력: 없음, records/cache/tracker를 갱신한다.
    def _run_local_search(
        self,
        condition: Dict[str, Any],
        budget: int,
        fixed_variables: Optional[Dict[str, Any]],
        records: List[EvaluationRecord],
        evaluated_keys: Set[Tuple[Any, ...]],
        tracker: "_SearchTracker",
    ) -> None:
        if not records:
            return

        steps = _PatternSteps()
        incumbent = copy.deepcopy(tracker.best_candidate)
        incumbent_loss = tracker.best_loss
        if incumbent is None:
            return

        while len(records) < budget:
            improved = False
            evaluated_this_sweep = False

            for candidate, move_metadata in _neighborhood(incumbent, steps, fixed_variables):
                if len(records) >= budget:
                    break
                incumbent_before = copy.deepcopy(incumbent)
                record = self._evaluate_if_new(
                    candidate=candidate,
                    condition=condition,
                    source="pattern_neighbor",
                    records=records,
                    evaluated_keys=evaluated_keys,
                    tracker=tracker,
                    metadata={
                        **move_metadata,
                        "fixed_variables": dict(fixed_variables or {}),
                        "incumbent_before": incumbent_before,
                        "incumbent_loss_before": incumbent_loss,
                    },
                )
                if record is None:
                    continue
                evaluated_this_sweep = True
                if record.loss < incumbent_loss:
                    incumbent = copy.deepcopy(record.candidate)
                    incumbent_loss = record.loss
                    record.metadata["accepted_move"] = True
                    record.metadata["incumbent_after"] = copy.deepcopy(incumbent)
                    record.metadata["incumbent_loss_after"] = incumbent_loss
                    improved = True
                    break
                record.metadata["accepted_move"] = False
                record.metadata["incumbent_after"] = incumbent_before
                record.metadata["incumbent_loss_after"] = incumbent_loss

            if improved:
                continue
            if not evaluated_this_sweep and steps.at_minimum():
                break
            if not steps.shrink() and not evaluated_this_sweep:
                break

    # 목적: duplicate를 피하면서 candidate 하나를 평가한다.
    # 입력: candidate, condition, source, records/cache/tracker.
    # 출력: 새 EvaluationRecord 또는 duplicate이면 None.
    def _evaluate_if_new(
        self,
        candidate: Dict[str, Any],
        condition: Dict[str, Any],
        source: str,
        records: List[EvaluationRecord],
        evaluated_keys: Set[Tuple[Any, ...]],
        tracker: "_SearchTracker",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[EvaluationRecord]:
        key = _candidate_key(candidate)
        if key in evaluated_keys:
            return None
        evaluated_keys.add(key)
        record = self._evaluate_candidate(len(records), candidate, condition, source, metadata)
        records.append(record)
        tracker.update(record)
        return record

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
            metadata={"source": source, **dict(metadata or {})},
        )


# 목적: incumbent 주변의 mixed-variable local moves를 생성한다.
# 입력: incumbent candidate, step sizes, fixed variables.
# 출력: repaired candidate iterable.
def _neighborhood(
    incumbent: Dict[str, Any],
    steps: _PatternSteps,
    fixed_variables: Optional[Mapping[str, Any]],
) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    fixed = fixed_variables or {}
    raw_moves: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    if "x_chem" not in fixed:
        flipped = dict(incumbent)
        flipped["x_chem"] = "LFP" if incumbent["x_chem"] == "NMC811" else "NMC811"
        raw_moves.append(
            (
                flipped,
                {
                    "base_candidate": dict(incumbent),
                    "changed_variable": "x_chem",
                    "step_size": None,
                    "direction": "flip",
                },
            )
        )

    _append_numeric_moves(raw_moves, incumbent, "x_T", steps.step_T, fixed)
    _append_numeric_moves(raw_moves, incumbent, "x_S", steps.step_S, fixed)
    _append_numeric_moves(raw_moves, incumbent, "x_MPR", steps.step_MPR, fixed)
    _append_numeric_moves(raw_moves, incumbent, "x_R", steps.step_R, fixed)

    for move, metadata in raw_moves:
        yield _prepare_candidate(move, fixed), metadata


# 목적: 특정 numeric variable의 ± step move를 추가한다.
# 입력: move list, incumbent, variable name, step, fixed variables.
# 출력: 없음, move list를 갱신한다.
def _append_numeric_moves(
    raw_moves: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    incumbent: Mapping[str, Any],
    key: str,
    step: float,
    fixed_variables: Mapping[str, Any],
) -> None:
    if key in fixed_variables:
        return
    for sign in (1, -1):
        moved = dict(incumbent)
        moved[key] = moved[key] + sign * step
        raw_moves.append(
            (
                moved,
                {
                    "base_candidate": dict(incumbent),
                    "changed_variable": key,
                    "step_size": step,
                    "direction": "+" if sign > 0 else "-",
                },
            )
        )


# 목적: fixed variables 적용 후 candidate를 유효 범위로 보정한다.
# 입력: candidate mapping과 fixed variables.
# 출력: evaluator에 전달할 candidate dict.
def _prepare_candidate(
    candidate: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    fixed_candidate = apply_fixed_variables(candidate, fixed_variables)
    return repair_candidate(fixed_candidate)


# 목적: candidate duplicate 판별용 canonical key를 만든다.
# 입력: repaired candidate dict.
# 출력: hashable key tuple.
def _candidate_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    repaired = repair_candidate(candidate)
    return (
        repaired["x_chem"],
        round(float(repaired["x_T"]), 10),
        int(repaired["x_S"]),
        int(repaired["x_MPR"]),
        int(repaired["x_R"]),
    )


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

    # 목적: result metadata에 넣을 tracking summary를 만든다.
    # 입력: evaluation records.
    # 출력: metadata dict.
    def metadata(self, records: List[EvaluationRecord]) -> Dict[str, Any]:
        feasible_count = sum(1 for record in records if record.feasible)
        return {
            "total_evaluations": len(records),
            "feasible_evaluations": feasible_count,
            "feasibility_rate": feasible_count / len(records) if records else 0.0,
            "best_feasible_candidate": copy.deepcopy(self.best_feasible_candidate),
            "best_feasible_result": copy.deepcopy(self.best_feasible_result),
            "best_penalized_candidate": copy.deepcopy(self.best_candidate),
            "best_penalized_result": copy.deepcopy(self.best_result),
        }

    # 목적: tracker 상태와 evaluation records를 최종 결과 객체로 변환한다.
    # 입력: method 이름, condition 이름, records, seed, budget, metadata.
    # 출력: OptimizationResult.
    def to_result(
        self,
        method_name: str,
        condition_name: str,
        records: List[EvaluationRecord],
        seed: int,
        budget: int,
        metadata: Dict[str, Any],
    ) -> OptimizationResult:
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
