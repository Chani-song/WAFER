"""Production-owned runtime helpers for LLM method experiments."""

from __future__ import annotations

import csv
import itertools
import json
import random
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from optimizers.core.base import Evaluator
from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.mixed_ga import MixedVariableGAOptimizer
from optimizers.pattern_search import PatternSearchOptimizer
from optimizers.random_search import RandomSearchOptimizer
from optimizers.core.domain import use_design_variable_bounds

OPTIMIZER_CHOICES = ("random", "mixed_ga", "pattern", "axbo", "mads")
SOLVER_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "pattern": {
        "description": "Deterministic local pattern search over the mixed-variable design domain.",
        "initialization": "Initial candidates are evaluated first; the best evaluated point seeds local coordinate-pattern refinement.",
    },
    "axbo": {
        "description": "Ax-based Bayesian optimization using completed evaluations to fit and update a surrogate.",
        "initialization": "Initial candidates are registered as completed observations before adaptive model-based trials.",
    },
    "mixed_ga": {
        "description": "Population-based mixed-variable genetic search.",
        "initialization": "Initial candidates populate the first generation, where useful diversity competes with later evolutionary evaluations.",
    },
    "random": {
        "description": "Independent uniform random sampling after any supplied initial candidates.",
        "initialization": "Initial candidates are evaluated first; later samples do not adapt to their semantic rationale.",
    },
    "mads": {
        "description": "Mesh-adaptive direct search for mixed-variable local refinement.",
        "initialization": "Initial candidates are evaluated as starting points before mesh-based local search.",
    },
}


def solver_description(name: str) -> Dict[str, str]:
    if name not in SOLVER_DESCRIPTIONS:
        raise ValueError(f"No stable solver description for {name!r}")
    return dict(SOLVER_DESCRIPTIONS[name])
SUMMARY_COLUMNS = [
    "optimizer",
    "semantic_strategy",
    "condition",
    "budget",
    "seed",
    "k",
    "generated_warm_start_candidates",
    "fixed_variables",
    "evaluations",
    "feasible_count",
    "failed_count",
    "missing_cost_count",
    "best_feasible_cost",
    "best_feasible_candidate",
    "total_runtime_sec",
    "mean_eval_time_sec",
]


def make_optimizer_with_options(
    name: str,
    evaluator: Evaluator,
    axbo_surrogate_slices_path: Optional[Path] = None,
) -> Any:
    """Create an optimizer from a production runner name."""
    if name == "random":
        return RandomSearchOptimizer(evaluator=evaluator)
    if name == "mixed_ga":
        return MixedVariableGAOptimizer(evaluator=evaluator, population_size=5)
    if name == "pattern":
        return PatternSearchOptimizer(evaluator=evaluator)
    if name == "axbo":
        from optimizers.ax_bayes import AxBayesOptOptimizer

        return AxBayesOptOptimizer(
            evaluator=evaluator,
            surrogate_slices_path=str(axbo_surrogate_slices_path) if axbo_surrogate_slices_path else None,
        )
    if name == "mads":
        from optimizers.nomad_mads import NomadMadsOptimizer

        return NomadMadsOptimizer(evaluator=evaluator)
    raise ValueError(f"Unknown optimizer: {name}")


def optimize_with_design_variable_bounds(
    optimizer: Any,
    variable_bounds: Optional[Mapping[str, Any]],
    **kwargs: Any,
) -> Any:
    """Run one optimizer under the scenario's resolved, non-global domain."""
    with use_design_variable_bounds(variable_bounds):
        return optimizer.optimize(**kwargs)


def finite_discrete_optimizer_plan(
    optimizer_name: str,
    variable_bounds: Optional[Mapping[str, Any]],
    fixed_variables: Mapping[str, Any],
    free_variables: Sequence[str],
    requested_budget: int,
    seed: int,
    maximum_enumerated_candidates: int = 100_000,
) -> Dict[str, Any]:
    """Plan duplicate-free execution when the reduced search space is finite.

    A plan is returned for every optimizer. ``finite_search_space_cardinality``
    is ``None`` when any free variable is continuous or the active bounds are
    unavailable. Random search receives a seeded, without-replacement list of
    initial candidates whenever the reduced space is fully discrete. AxBO is
    converted to exhaustive initial-candidate evaluation only when the entire
    finite space fits within the requested budget.
    """
    if requested_budget < 0:
        raise ValueError("requested_budget must be non-negative")

    candidates = _enumerate_finite_discrete_candidates(
        variable_bounds=variable_bounds,
        fixed_variables=fixed_variables,
        free_variables=free_variables,
        maximum_candidates=maximum_enumerated_candidates,
    )
    base = {
        "requested_evaluation_budget": int(requested_budget),
        "effective_evaluation_budget": int(requested_budget),
        "finite_search_space_cardinality": None,
        "initial_candidates": None,
        "planned_termination_reason": "budget_exhausted",
        "sampling_without_replacement": False,
    }
    if candidates is None:
        return base

    cardinality = len(candidates)
    base["finite_search_space_cardinality"] = cardinality

    if optimizer_name == "random":
        ordered = [dict(candidate) for candidate in candidates]
        random.Random(int(seed)).shuffle(ordered)
        effective_budget = min(int(requested_budget), cardinality)
        base.update(
            {
                "effective_evaluation_budget": effective_budget,
                "initial_candidates": ordered[:effective_budget],
                "planned_termination_reason": (
                    "search_space_exhausted"
                    if int(requested_budget) >= cardinality
                    else "budget_exhausted"
                ),
                "sampling_without_replacement": True,
            }
        )
        return base

    if optimizer_name == "axbo" and cardinality <= int(requested_budget):
        base.update(
            {
                "effective_evaluation_budget": cardinality,
                "initial_candidates": [dict(candidate) for candidate in candidates],
                "planned_termination_reason": "search_space_exhausted",
                "sampling_without_replacement": True,
            }
        )
    return base


def _enumerate_finite_discrete_candidates(
    variable_bounds: Optional[Mapping[str, Any]],
    fixed_variables: Mapping[str, Any],
    free_variables: Sequence[str],
    maximum_candidates: int,
) -> Optional[list[Dict[str, Any]]]:
    """Enumerate the complete reduced domain, or return ``None`` if continuous."""
    if not isinstance(variable_bounds, Mapping):
        return None

    physical_order = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
    free_set = set(free_variables)
    if free_set - set(physical_order):
        raise ValueError(f"Unknown free variables: {sorted(free_set - set(physical_order))}")
    if "x_T" in free_set:
        return None

    missing = [
        name for name in physical_order
        if name not in free_set and name not in fixed_variables
    ]
    if missing:
        raise ValueError(f"Reduced-space partition is missing variables: {missing}")

    option_names = [name for name in physical_order if name in free_set]
    option_values = []
    cardinality = 1
    for name in option_names:
        spec = variable_bounds.get(name)
        if not isinstance(spec, Mapping):
            return None
        if name == "x_chem":
            values = list(spec.get("choices") or [])
        elif spec.get("type") == "integer":
            lower = int(spec["lower"])
            upper = int(spec["upper"])
            values = list(range(lower, upper + 1))
        else:
            return None
        if not values:
            raise ValueError(f"No active values are available for free variable {name}")
        cardinality *= len(values)
        if cardinality > int(maximum_candidates):
            return None
        option_values.append(values)

    if not option_names:
        return [dict(fixed_variables)]

    candidates = []
    for combination in itertools.product(*option_values):
        candidate = dict(fixed_variables)
        candidate.update(dict(zip(option_names, combination)))
        candidates.append(candidate)
    return candidates


def make_real_batpac_evaluator(
    real_batpac: bool,
    direct_eval: bool,
    timeout_sec: float,
    persistent_workbook_path: str = DEFAULT_PERSISTENT_WORKBOOK_PATH,
    visible: bool = False,
    runtime_environment: Optional[Mapping[str, str]] = None,
    output_dir: Optional[str] = None,
) -> Tuple[Evaluator, str]:
    """Create a real BatPaC evaluator or an explicitly allowed mock evaluator."""
    if not real_batpac:
        return fake_evaluator, "fake"

    from optimizers.scripts.run_real_batpac_smoke import batpac_evaluator_mode, make_timeout_batpac_evaluator

    evaluator = make_timeout_batpac_evaluator(
        timeout_sec=timeout_sec,
        persistent_workbook_path=persistent_workbook_path,
        visible=visible,
        use_subprocess_timeout=not direct_eval,
        runtime_environment=dict(runtime_environment or {}),
        output_dir=output_dir,
    )
    return evaluator, batpac_evaluator_mode(use_subprocess_timeout=not direct_eval)


def fake_evaluator(candidate: Dict[str, Any], condition: Dict[str, Any]) -> Dict[str, Any]:
    """Return deterministic synthetic battery metrics for allowed mock runs."""
    module_count = float(candidate["x_S"] * candidate["x_MPR"] * candidate["x_R"])
    chemistry_cost = (
        float(condition["cam_cost_lfp"])
        if candidate["x_chem"] == "LFP"
        else float(condition["cam_cost_nmc811"])
    )
    energy_factor = 0.95 if candidate["x_chem"] == "LFP" else 1.05
    cost = 75.0 + chemistry_cost + 0.12 * candidate["x_T"] + 0.015 * module_count

    return {
        "success": True,
        "cost": cost,
        "total_pack_cost": cost * float(condition["E_req"]),
        "P": 120.0 + 3.5 * module_count,
        "E": 60.0 + 0.30 * candidate["x_T"] * energy_factor + 0.70 * module_count,
        "V": 240.0 + 10.0 * candidate["x_S"],
        "c": 28.0 + 0.35 * candidate["x_T"],
        "L_bat": 900.0 + 45.0 * candidate["x_MPR"],
        "W_bat": 550.0 + 150.0 * candidate["x_R"],
        "raw": None,
    }


def summarize_optimizer_result(
    optimizer_name: str,
    semantic_strategy: str,
    condition_name: str,
    budget: int,
    seed: int,
    k: int,
    generated_warm_start_candidates: Sequence[Mapping[str, Any]],
    fixed_variables: Mapping[str, Any],
    result: Any,
    total_runtime_sec: float,
) -> Dict[str, Any]:
    """Summarize an LLM-guided optimization result."""
    records = result.records
    eval_times = [float(record.wall_time_sec) for record in records]
    feasible_count = sum(1 for record in records if record.feasible)
    failed_count = sum(
        1
        for record in records
        if not record.result.get("optimization_valid", record.result.get("success", False))
    )
    missing_cost_count = sum(1 for record in records if record.result.get("failure_reason") == "missing_cost")
    return {
        "optimizer": optimizer_name,
        "semantic_strategy": semantic_strategy,
        "condition": condition_name,
        "budget": budget,
        "seed": seed,
        "k": k,
        "generated_warm_start_candidates": [dict(candidate) for candidate in generated_warm_start_candidates],
        "fixed_variables": dict(fixed_variables),
        "evaluations": len(records),
        "feasible_count": feasible_count,
        "failed_count": failed_count,
        "missing_cost_count": missing_cost_count,
        "best_feasible_cost": result.best_feasible_cost,
        "best_feasible_candidate": result.best_feasible_candidate,
        "total_runtime_sec": total_runtime_sec,
        "optimizer_runtime_sec": total_runtime_sec,
        "simulator_runtime_sec": sum(eval_times),
        "optimizer_overhead_sec": max(0.0, total_runtime_sec - sum(eval_times)),
        "mean_eval_time_sec": statistics.mean(eval_times) if eval_times else 0.0,
    }


def save_records_jsonl(path: Path, records: Iterable[Any]) -> None:
    """Save evaluation records as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = {
                "iteration": record.iteration,
                "candidate": record.candidate,
                "condition_name": record.condition_name,
                "result": record.result,
                "feasible": record.feasible,
                "loss": record.loss,
                "violation": record.violation,
                "wall_time_sec": record.wall_time_sec,
                "metadata": record.metadata,
            }
            handle.write(json.dumps(jsonable(payload), sort_keys=True) + "\n")


def save_summary_json(path: Path, summary: Any) -> None:
    """Save a JSON summary artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(summary), indent=2, sort_keys=True), encoding="utf-8")


def save_summary_csv(path: Path, summaries: Sequence[Mapping[str, Any]]) -> None:
    """Save optimizer summaries as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(_csv_summary_row(summary))


def jsonable(value: Any) -> Any:
    """Return a JSON-compatible value."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _csv_summary_row(summary: Mapping[str, Any]) -> Dict[str, Any]:
    row = {column: summary.get(column, "") for column in SUMMARY_COLUMNS}
    row["generated_warm_start_candidates"] = json.dumps(
        jsonable(row["generated_warm_start_candidates"]),
        sort_keys=True,
    )
    row["fixed_variables"] = json.dumps(jsonable(row["fixed_variables"]), sort_keys=True)
    row["best_feasible_candidate"] = json.dumps(jsonable(row["best_feasible_candidate"]), sort_keys=True)
    return row
