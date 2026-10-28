"""Shared optimizer dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

Evaluator = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


# 목적: 단일 evaluator 호출의 입력, 출력, 판단값을 저장한다.
# 입력: optimizer가 생성한 candidate와 evaluator result.
# 출력: record 객체로 누적되어 OptimizationResult에 포함된다.
@dataclass
class EvaluationRecord:
    """One optimizer evaluation."""

    iteration: int
    candidate: Dict[str, Any]
    condition_name: str
    result: Dict[str, Any]
    feasible: bool
    loss: float
    violation: float
    wall_time_sec: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# 목적: optimizer 실행 전체의 최종 결과와 trace를 저장한다.
# 입력: best candidate, best result, records, seed, budget.
# 출력: 사용자와 테스트가 조회하는 optimization summary 객체.
@dataclass
class OptimizationResult:
    """Final optimizer result."""

    method_name: str
    condition_name: str
    best_candidate: Optional[Dict[str, Any]]
    best_result: Optional[Dict[str, Any]]
    best_feasible_cost: Optional[float]
    best_loss: float
    records: List[EvaluationRecord]
    seed: int
    budget: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 목적: metadata에 저장된 best feasible candidate를 안전하게 꺼낸다.
    # 입력: 없음.
    # 출력: best feasible candidate dict 또는 None.
    @property
    def best_feasible_candidate(self) -> Optional[Dict[str, Any]]:
        """Return the best feasible candidate, if any."""
        candidate = self.metadata.get("best_feasible_candidate")
        return candidate if isinstance(candidate, dict) else None

    # 목적: metadata에 저장된 best feasible result를 안전하게 꺼낸다.
    # 입력: 없음.
    # 출력: best feasible result dict 또는 None.
    @property
    def best_feasible_result(self) -> Optional[Dict[str, Any]]:
        """Return the best feasible result, if any."""
        result = self.metadata.get("best_feasible_result")
        return result if isinstance(result, dict) else None


# 목적: optimizer 구현체가 공통으로 따를 interface를 정의한다.
# 입력: dependency injection으로 받은 evaluator.
# 출력: subclass가 optimize로 OptimizationResult를 반환한다.
@dataclass
class BaseOptimizer:
    """Base class for optimizer implementations."""

    evaluator: Evaluator
    method_name: str = "base"

    # 목적: 구체 optimizer의 실행 진입점을 정의한다.
    # 입력: condition 이름, budget, seed, 초기 후보, fixed variables.
    # 출력: OptimizationResult.
    def optimize(
        self,
        condition_name: str,
        budget: int,
        seed: int,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        fixed_variables: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        """Run optimization."""
        raise NotImplementedError
