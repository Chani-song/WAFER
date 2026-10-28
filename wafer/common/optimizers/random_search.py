"""Deterministic random search optimizer."""

from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import apply_fixed_variables, make_condition, repair_candidate, sample_random_candidate
from optimizers.core.evaluation import decompose_evaluator_status, is_feasible, normalized_constraint_violation, penalized_loss


# 목적: mixed design space에서 uniform random search를 수행한다.
# 입력: dependency injection으로 받은 evaluator.
# 출력: optimize 호출 시 OptimizationResult.
@dataclass
class RandomSearchOptimizer(BaseOptimizer):
    """Uniform random search over the mixed design domain."""

    method_name: str = "random_search"

    # 목적: 초기 후보를 먼저 평가하고 남은 budget은 random sampling으로 채운다.
    # 입력: condition 이름, budget, seed, 초기 후보, fixed variables.
    # 출력: 모든 record와 best solution 정보를 담은 OptimizationResult.
    def optimize(
        self,
        condition_name: str,
        budget: int,
        seed: int,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        fixed_variables: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        """Run random search."""
        if budget < 0:
            raise ValueError("budget must be non-negative")

        condition = make_condition(condition_name)
        rng = random.Random(seed)
        records: List[EvaluationRecord] = []
        tracker = _SearchTracker()

        # 예산은 실제 evaluator 호출 횟수 기준으로만 차감한다.
        for candidate in initial_candidates or []:
            if len(records) >= budget:
                break
            prepared = _prepare_candidate(candidate, fixed_variables)
            record = self._evaluate_candidate(
                len(records),
                prepared,
                condition,
                "initial",
                fixed_variables=fixed_variables,
                sampled_variables=[],
            )
            records.append(record)
            tracker.update(record)

        while len(records) < budget:
            sampled = sample_random_candidate(rng, fixed_variables=fixed_variables)
            record = self._evaluate_candidate(
                len(records),
                sampled,
                condition,
                "random",
                fixed_variables=fixed_variables,
                sampled_variables=_free_variable_names(fixed_variables),
            )
            records.append(record)
            tracker.update(record)

        return tracker.to_result(
            method_name=self.method_name,
            condition_name=condition_name,
            records=records,
            seed=seed,
            budget=budget,
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
        fixed_variables: Optional[Mapping[str, Any]] = None,
        sampled_variables: Optional[List[str]] = None,
    ) -> EvaluationRecord:
        started = time.perf_counter()
        try:
            result = self.evaluator(copy.deepcopy(candidate), dict(condition))
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
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
            metadata={
                "source": source,
                "sampled_variables": list(sampled_variables or []),
                "fixed_variables": dict(fixed_variables or {}),
            },
        )


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


# 목적: fixed variables 적용 후 candidate를 유효 범위로 보정한다.
# 입력: candidate mapping과 fixed variables.
# 출력: evaluator에 전달할 candidate dict.
def _prepare_candidate(
    candidate: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    fixed_candidate = apply_fixed_variables(candidate, fixed_variables)
    return repair_candidate(fixed_candidate)


# 목적: random sampling이 실제로 탐색한 변수 이름을 기록한다.
# 입력: fixed variables.
# 출력: fixed가 아닌 design variable 이름 리스트.
def _free_variable_names(fixed_variables: Optional[Mapping[str, Any]]) -> List[str]:
    fixed = fixed_variables or {}
    return [name for name in ("x_chem", "x_T", "x_S", "x_MPR", "x_R") if name not in fixed]


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
