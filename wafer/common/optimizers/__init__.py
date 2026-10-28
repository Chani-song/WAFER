"""Optimizer scaffold for BatPaC-driven battery design studies."""

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import (
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
from optimizers.core.evaluation import (
    compute_constraint_violations,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)
from .random_search import RandomSearchOptimizer
from .mixed_ga import MixedVariableGAOptimizer
from .pattern_search import PatternSearchOptimizer
from .ax_bayes import AxBayesOptOptimizer

__all__ = [
    "AxBayesOptOptimizer",
    "BaseOptimizer",
    "EvaluationRecord",
    "OptimizationResult",
    "MixedVariableGAOptimizer",
    "RandomSearchOptimizer",
    "PatternSearchOptimizer",
    "apply_fixed_variables",
    "candidate_to_batpac_inputs",
    "compute_constraint_violations",
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
