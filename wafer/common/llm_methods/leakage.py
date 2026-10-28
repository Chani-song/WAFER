"""Source-history leakage diagnostics for redesign experiments."""

from __future__ import annotations

import copy
import math
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from optimizers.core.domain import resolved_design_variable_bounds, validate_candidate
from optimizers.core.evaluation import is_feasible


def diagnose_source_history_leakage(
    context: Mapping[str, Any],
    evaluator: Callable[[Dict[str, Any], Dict[str, Any]], Mapping[str, Any]],
    target_grid_optimum: Optional[Mapping[str, Any]] = None,
    near_distance_threshold: float = 0.05,
    near_objective_gap_fraction: float = 0.01,
) -> Dict[str, Any]:
    """Re-evaluate the original source trajectory under the target condition.

    The function is diagnostic only: it never removes or reorders history.
    """
    target = context.get("changed_condition", context.get("target_condition"))
    if not isinstance(target, Mapping):
        raise ValueError("changed_condition/target_condition is required")
    source_optimum = context.get("previous_best_design")
    history = context.get("previous_optimization_history", [])
    if not isinstance(history, list):
        raise ValueError("previous_optimization_history must be a list")

    points: List[Dict[str, Any]] = []
    if isinstance(source_optimum, Mapping):
        points.append({"origin": "source_optimum", "source_index": None, "candidate": dict(source_optimum)})
    for index, row in enumerate(history):
        candidate = _candidate_from_history_row(row)
        if candidate is not None:
            points.append({"origin": "source_trajectory", "source_index": index, "candidate": candidate})

    evaluated: List[Dict[str, Any]] = []
    variable_bounds = context.get("variable_bounds")
    cache: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for point in points:
        candidate = dict(point["candidate"])
        key = _candidate_key(candidate)
        started = time.perf_counter()
        if key not in cache:
            try:
                validate_candidate(candidate, variable_bounds)
            except Exception as exc:
                result = {"success": False, "optimization_valid": False, "cost": None}
                error = f"candidate_validation_error: {type(exc).__name__}: {exc}"
            else:
                try:
                    result = dict(evaluator(copy.deepcopy(candidate), dict(target)))
                    error = None
                except Exception as exc:
                    result = {"success": False, "optimization_valid": False, "cost": None}
                    error = f"{type(exc).__name__}: {exc}"
            cache[key] = {"result": result, "error": error}
        cached = cache[key]
        result = cached["result"]
        evaluated.append(
            {
                **point,
                "candidate": candidate,
                "target_result": copy.deepcopy(result),
                "target_feasible": bool(is_feasible(result, target)),
                "target_cost": _finite_cost(result),
                "evaluation_error": cached["error"],
                "wall_time_sec": time.perf_counter() - started,
            }
        )

    trajectory_rows = [row for row in evaluated if row["origin"] == "source_trajectory"]
    feasible_rows = [row for row in trajectory_rows if row["target_feasible"]]
    target_best = min(
        (row for row in feasible_rows if row["target_cost"] is not None),
        key=lambda row: row["target_cost"],
        default=None,
    )
    grid_candidate = _grid_candidate(target_grid_optimum)
    grid_cost = _grid_cost(target_grid_optimum)
    near_matches = []
    if grid_candidate is not None:
        for row in trajectory_rows:
            distance = normalized_design_distance(row["candidate"], grid_candidate, variable_bounds)
            gap = _objective_gap(row["target_cost"], grid_cost)
            if distance <= near_distance_threshold or (
                gap is not None and abs(gap) <= near_objective_gap_fraction
            ):
                near_matches.append(
                    {
                        "source_index": row["source_index"],
                        "candidate": row["candidate"],
                        "design_distance": distance,
                        "objective_gap_fraction": gap,
                        "exact_design_match": distance == 0.0,
                    }
                )
    warnings = []
    if feasible_rows:
        warnings.append(
            f"source history contains {len(feasible_rows)}/{len(trajectory_rows)} target-feasible points"
        )
    if near_matches:
        warnings.append(
            f"source history contains {len(near_matches)} point(s) equal or near the target grid optimum"
        )
    return {
        "schema_version": "jmd_source_history_leakage_v1",
        "history_policy": "preserve_original",
        "target_condition": dict(target),
        "source_history_points": len(trajectory_rows),
        "target_feasible_count": len(feasible_rows),
        "target_feasible_fraction": (
            len(feasible_rows) / len(trajectory_rows) if trajectory_rows else 0.0
        ),
        "source_optimum_target_reevaluation": next(
            (row for row in evaluated if row["origin"] == "source_optimum"), None
        ),
        "source_trajectory_target_best": target_best,
        "target_grid_optimum": copy.deepcopy(target_grid_optimum),
        "near_target_grid_matches": near_matches,
        "leakage_warning": bool(warnings),
        "warnings": warnings,
        "evaluations": evaluated,
    }


def normalized_design_distance(
    a: Mapping[str, Any], b: Mapping[str, Any], variable_bounds: Optional[Mapping[str, Any]] = None,
) -> float:
    """Mixed-variable RMS distance normalized to the declared domain."""
    terms = [0.0 if str(a.get("x_chem")) == str(b.get("x_chem")) else 1.0]
    chemistry = str(b.get("x_chem", a.get("x_chem", "LFP")))
    resolved = resolved_design_variable_bounds(variable_bounds)
    thickness = resolved["x_T"]["bounds_by_chemistry"].get(chemistry, {"lower": 50.0, "upper": 200.0})
    t_low, t_high = float(thickness["lower"]), float(thickness["upper"])
    terms.append((float(a["x_T"]) - float(b["x_T"])) / max(t_high - t_low, 1.0))
    for name in ("x_S", "x_MPR", "x_R"):
        low, high = int(resolved[name]["lower"]), int(resolved[name]["upper"])
        terms.append((float(a[name]) - float(b[name])) / float(high - low))
    return math.sqrt(sum(value * value for value in terms) / len(terms))


def filter_history_by_leakage(
    history: Sequence[Mapping[str, Any]],
    diagnostic: Mapping[str, Any],
    enabled: bool = False,
) -> List[Mapping[str, Any]]:
    """Optional explicit filter; disabled means byte-order-equivalent preservation."""
    if not enabled:
        return list(history)
    excluded = {
        row["source_index"]
        for row in diagnostic.get("near_target_grid_matches", [])
        if row.get("source_index") is not None
    }
    return [row for index, row in enumerate(history) if index not in excluded]


def _candidate_from_history_row(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, Mapping):
        return None
    candidate = row.get("candidate", row.get("design", row))
    if not isinstance(candidate, Mapping):
        return None
    keys = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
    if not all(key in candidate for key in keys):
        return None
    return {key: candidate[key] for key in keys}


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(candidate[key] for key in ("x_chem", "x_T", "x_S", "x_MPR", "x_R"))


def _finite_cost(result: Mapping[str, Any]) -> Optional[float]:
    try:
        value = float(result.get("cost"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _grid_candidate(payload: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return None
    candidate = payload.get("candidate", payload.get("best_candidate", payload))
    return candidate if isinstance(candidate, Mapping) else None


def _grid_cost(payload: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("result", payload)
    return _finite_cost(result) if isinstance(result, Mapping) else None


def _objective_gap(value: Optional[float], reference: Optional[float]) -> Optional[float]:
    if value is None or reference is None:
        return None
    return (value - reference) / max(abs(reference), 1e-12)
