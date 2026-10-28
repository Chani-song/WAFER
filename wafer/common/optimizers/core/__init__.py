"""Shared optimizer utilities and BatPaC adapter support."""

from .base import BaseOptimizer, EvaluationRecord, OptimizationResult
from .domain import (
    apply_fixed_variables,
    candidate_to_batpac_inputs,
    get_thickness_bounds,
    make_condition,
    repair_candidate,
    sample_random_candidate,
    thickness_from_unit_interval,
    unit_interval_from_thickness,
    validate_candidate,
)
from .evaluation import (
    compute_constraint_violations,
    decompose_evaluator_status,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)

__all__ = [
    "BaseOptimizer",
    "EvaluationRecord",
    "OptimizationResult",
    "apply_fixed_variables",
    "candidate_to_batpac_inputs",
    "compute_constraint_violations",
    "decompose_evaluator_status",
    "get_thickness_bounds",
    "is_feasible",
    "make_condition",
    "normalized_constraint_violation",
    "penalized_loss",
    "repair_candidate",
    "sample_random_candidate",
    "thickness_from_unit_interval",
    "unit_interval_from_thickness",
    "validate_candidate",
]
