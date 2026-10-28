"""Production grid normalization and leakage-controlled scenario artifacts."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from llm_methods.context import (
    condition_changes,
    constraint_descriptions,
    objective_description,
    variable_bounds,
)
from optimizers.core.domain import CHEMISTRIES, REQUIRED_CANDIDATE_KEYS, make_condition, validate_candidate
from optimizers.core.evaluation import is_feasible

FEASIBILITY_PREDICATE_VERSION = "optimizers.core.evaluation.is_feasible.v1"
CSV_RESULT_MAP = {
    "cost": "cost_per_kwh",
    "cost_per_kwh": "cost_per_kwh",
    "total_pack_cost": "pack_cost",
    "E": "energy_kwh",
    "P": "power_kw",
    "V": "nominal_voltage_v",
    "c": "cell_capacity_ah",
    "L_bat": "pack_length_mm",
    "W_bat": "pack_width_mm",
}


def normalize_grid_row(
    row: Mapping[str, Any],
    grid_path: Optional[Path | str] = None,
    row_number: Optional[int] = None,
) -> Dict[str, Any]:
    """Normalize a real grid CSV row or retain an existing canonical row."""
    if "candidate" in row and "result" in row:
        normalized = copy.deepcopy(dict(row))
        candidate = _syntactic_candidate(normalized["candidate"])
        normalized["candidate"] = candidate
        normalized.update(_domain_metadata(candidate))
        return normalized
    candidate = _syntactic_candidate({
        "x_chem": row.get("chemistry"),
        "x_T": _float(row.get("x_T"), "x_T"),
        "x_S": _int(row.get("x_S"), "x_S"),
        "x_MPR": _int(row.get("x_MPR"), "x_MPR"),
        "x_R": _int(row.get("x_R"), "x_R"),
    })
    result = {target: _float(row.get(source), source) for target, source in CSV_RESULT_MAP.items()}
    result["success"] = str(row.get("status", "")).strip().lower() == "success"
    result["raw"] = {
        "condition_name": row.get("condition_name"),
        "design_id": row.get("design_id"),
        "grid_path": str(grid_path) if grid_path is not None else None,
        "row_number": row_number,
        "runtime_sec": _optional_float(row.get("runtime_sec")),
    }
    return {
        "row_id": row.get("design_id", row_number),
        "condition_name": row.get("condition_name"),
        "candidate": candidate,
        "result": result,
        "source_row_number": row_number,
        "design_id": row.get("design_id"),
        **_domain_metadata(candidate),
    }


def candidate_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("candidate", row.get("best_candidate", row))
    if not isinstance(value, Mapping):
        raise ValueError("grid row does not contain a candidate object")
    return _syntactic_candidate(value)


def result_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("result", row.get("best_result", row))
    if not isinstance(value, Mapping):
        raise ValueError("grid row does not contain a result object")
    return dict(value)


def rows_for_condition(rows: Sequence[Mapping[str, Any]], label: str) -> List[Mapping[str, Any]]:
    selected = [row for row in rows if row.get("condition_name") == label]
    if not selected:
        raise ValueError(f"grid contains no rows for condition_name={label!r}")
    return selected


def feasible_rows(
    rows: Sequence[Mapping[str, Any]], condition: Mapping[str, Any],
    domain_eligible_only: bool = False,
) -> List[Mapping[str, Any]]:
    return [
        row for row in rows
        if (not domain_eligible_only or bool(row.get("domain_eligible", True)))
        and is_feasible(result_from_row(row), condition)
    ]


def select_grid_optimum(
    rows: Sequence[Mapping[str, Any]],
    condition: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    eligible = feasible_rows(rows, condition, domain_eligible_only=True) if condition is not None else [
        row for row in rows if bool(row.get("feasible", result_from_row(row).get("success", False)))
        and bool(row.get("domain_eligible", True))
    ]
    numeric = [
        (float(result_from_row(row)["cost"]), index, row)
        for index, row in enumerate(eligible)
        if _finite(result_from_row(row).get("cost"))
    ]
    if not numeric:
        raise ValueError("grid artifact has no feasible row with numeric cost")
    return min(numeric, key=lambda item: (item[0], item[1]))[2]


def incumbent_update_trajectory(
    rows: Sequence[Mapping[str, Any]], condition: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Return only strict feasible incumbent improvements in original row order."""
    trajectory: List[Dict[str, Any]] = []
    incumbent = math.inf
    for row in rows:
        result = result_from_row(row)
        if not bool(row.get("domain_eligible", True)):
            continue
        if not is_feasible(result, condition) or not _finite(result.get("cost")):
            continue
        cost = float(result["cost"])
        if cost >= incumbent:
            continue
        incumbent = cost
        trajectory.append({
            "iteration": len(trajectory),
            "event_type": "feasible_incumbent_update",
            "optimization_method": "deterministic_exhaustive_grid_search",
            "source_row_number": row.get("source_row_number"),
            "design_id": row.get("design_id"),
            "candidate": candidate_from_row(row),
            "result": result,
        })
    if not trajectory:
        raise ValueError("source grid produced no feasible incumbent trajectory")
    return trajectory


def filter_exact_target_from_history(
    history: Sequence[Mapping[str, Any]], target_candidate: Mapping[str, Any]
) -> tuple[List[Dict[str, Any]], List[Any]]:
    target_key = candidate_key(target_candidate)
    kept, removed = [], []
    for index, row in enumerate(history):
        if candidate_key(candidate_from_row(row)) == target_key:
            removed.append(row.get("design_id", row.get("row_id", row.get("source_row_number", index))))
        else:
            kept.append(copy.deepcopy(dict(row)))
    return kept, removed


def lookup_exact_candidate(
    rows: Sequence[Mapping[str, Any]], candidate: Mapping[str, Any]
) -> Mapping[str, Any]:
    matches = [row for row in rows if candidate_key(candidate_from_row(row)) == candidate_key(candidate)]
    matches = [row for row in matches if bool(row.get("domain_eligible", True))]
    if not matches:
        raise ValueError(f"no exact grid row for candidate {dict(candidate)}")
    return matches[0]


def build_production_scenario(
    scenario: str,
    all_rows: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    expert_prior: Mapping[str, Any],
    grid_path: Path | str | Sequence[Path | str],
) -> Dict[str, Any]:
    """Build one model-visible context and its separate hidden audit."""
    override = spec.get("design_variable_bounds_override")
    resolved_bounds = variable_bounds(override)
    source_condition, target_condition = make_condition("s0"), make_condition(scenario)
    source_rows = _rows_with_domain(
        rows_for_condition(all_rows, str(spec["source_condition_label"])), resolved_bounds
    )
    if scenario == "s_space":
        target_rows = source_rows
    else:
        target_rows = _rows_with_domain(
            rows_for_condition(all_rows, str(spec["target_condition_label"])), resolved_bounds
        )
    raw_source_feasible = feasible_rows(source_rows, source_condition)
    raw_target_feasible = feasible_rows(target_rows, target_condition)
    source_feasible = feasible_rows(source_rows, source_condition, domain_eligible_only=True)
    target_feasible = feasible_rows(target_rows, target_condition, domain_eligible_only=True)
    source_row = select_grid_optimum(source_rows, source_condition)
    target_row = select_grid_optimum(target_rows, target_condition)
    source_candidate, target_candidate = candidate_from_row(source_row), candidate_from_row(target_row)
    validate_candidate(source_candidate, resolved_bounds)
    validate_candidate(target_candidate, resolved_bounds)
    source_result, target_result = result_from_row(source_row), result_from_row(target_row)
    trajectory = incumbent_update_trajectory(source_rows, source_condition)
    if candidate_key(candidate_from_row(trajectory[-1])) != candidate_key(source_candidate):
        raise ValueError("final source trajectory incumbent does not equal source optimum")
    filtered, removed = filter_exact_target_from_history(trajectory, target_candidate)
    if scenario == "s_space":
        reuse_result = source_result
    else:
        reuse_result = result_from_row(lookup_exact_candidate(target_rows, source_candidate))
    reuse_feasible = is_feasible(reuse_result, target_condition)
    _assert_expected(spec, source_candidate, target_candidate, source_result, target_result,
                     len(raw_source_feasible), len(raw_target_feasible), reuse_feasible,
                     len(source_feasible), len(target_feasible))
    if scenario == "s_cost" and source_candidate["x_chem"] == target_candidate["x_chem"]:
        raise ValueError("s_cost target optimum chemistry must differ from source optimum")
    context = {
        "previous_condition": source_condition,
        "previous_best_design": source_candidate,
        "previous_best_result": source_result,
        "previous_optimization_history": filtered,
        "changed_condition": target_condition,
        "structured_condition_changes": condition_changes(source_condition, target_condition),
        "previous_best_reevaluated_under_changed_condition": {
            **copy.deepcopy(reuse_result),
            "feasible": reuse_feasible,
        },
        "objective": objective_description(),
        "constraints": constraint_descriptions(target_condition),
        "variable_bounds": resolved_bounds,
        "design_variable_bounds_override": copy.deepcopy(override),
    }
    changed = [name for name in REQUIRED_CANDIDATE_KEYS if source_candidate[name] != target_candidate[name]]
    audit = {
        "schema_version": "jmd_scenario_ground_truth.v2",
        "scenario": scenario,
        **_grid_provenance(grid_path),
        "source_condition_label": spec["source_condition_label"],
        "target_condition_label": spec["target_condition_label"],
        "condition_dictionaries": {"source": source_condition, "target": target_condition},
        "expert_prior": copy.deepcopy(dict(expert_prior)),
        "simulator_ground_truth": {
            "source_optimum": {
                "candidate": source_candidate,
                "result": source_result,
                "domain_eligible": bool(source_row.get("domain_eligible")),
            },
            "target_optimum": {
                "candidate": target_candidate,
                "result": target_result,
                "domain_eligible": bool(target_row.get("domain_eligible")),
            },
            "actually_changed_variables": changed,
            "source_reuse_feasible_in_target": reuse_feasible,
            "source_reuse_target_result": reuse_result,
            "raw_source_feasible_count": len(raw_source_feasible),
            "domain_eligible_source_feasible_count": len(source_feasible),
            "raw_target_feasible_count": len(raw_target_feasible),
            "domain_eligible_target_feasible_count": len(target_feasible),
        },
        "feasibility_predicate_version": FEASIBILITY_PREDICATE_VERSION,
        "leakage_control": {
            "history_policy": "leakage_controlled",
            "original_source_row_count": len(source_rows),
            "pure_incumbent_trajectory_count": len(trajectory),
            "filtered_trajectory_count": len(filtered),
            "trajectory_sha256_before_filtering": stable_sha256(trajectory),
            "trajectory_sha256_after_filtering": stable_sha256(filtered),
            "removed_source_row_identifiers": removed,
        },
        "domain_eligibility": _domain_audit(source_rows, target_rows),
        "design_variable_bounds_override": copy.deepcopy(override),
        "resolved_variable_bounds": copy.deepcopy(resolved_bounds),
        "assertions_passed": True,
    }
    return {"context": context, "audit": audit}


def build_scenario_artifacts(
    scenario: str,
    source_rows: Sequence[Mapping[str, Any]],
    target_rows: Sequence[Mapping[str, Any]],
    expert_prior: Mapping[str, Any],
    design_variable_bounds_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Backward-compatible fixture builder using canonical rows."""
    resolved_bounds = variable_bounds(design_variable_bounds_override)
    source_rows = _rows_with_domain(source_rows, resolved_bounds)
    target_rows = _rows_with_domain(target_rows, resolved_bounds)
    source_row = select_grid_optimum(source_rows)
    target_row = select_grid_optimum(target_rows)
    source_candidate, target_candidate = candidate_from_row(source_row), candidate_from_row(target_row)
    history, removed = filter_exact_target_from_history(source_rows, target_candidate)
    changed = [name for name in REQUIRED_CANDIDATE_KEYS if source_candidate[name] != target_candidate[name]]
    context = {
        "previous_condition": make_condition("s0"),
        "previous_best_design": source_candidate,
        "previous_best_result": result_from_row(source_row),
        "previous_optimization_history": history,
        "changed_condition": make_condition(scenario),
        "structured_condition_changes": condition_changes(make_condition("s0"), make_condition(scenario)),
        "previous_best_reevaluated_under_changed_condition": None,
        "objective": objective_description(),
        "constraints": constraint_descriptions(make_condition(scenario)),
        "variable_bounds": resolved_bounds,
        "design_variable_bounds_override": copy.deepcopy(design_variable_bounds_override),
    }
    return {
        "context": context,
        "audit": {
            "scenario": scenario,
            "expert_prior": dict(expert_prior),
            "simulator_ground_truth": {
                "source_optimum": {"candidate": source_candidate, "result": result_from_row(source_row)},
                "target_optimum": {"candidate": target_candidate, "result": result_from_row(target_row)},
                "actually_changed_variables": changed,
            },
            "leakage_control": {"removed_source_row_identifiers": removed},
            "design_variable_bounds_override": copy.deepcopy(design_variable_bounds_override),
            "resolved_variable_bounds": copy.deepcopy(resolved_bounds),
        },
    }


def load_rows(path: Path) -> List[Mapping[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        raw = list(csv.DictReader(text.splitlines()))
        return [normalize_grid_row(row, path, index + 2) for index, row in enumerate(raw)]
    if path.suffix.lower() == ".jsonl":
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        value = json.loads(text)
        if isinstance(value, Mapping):
            value = value.get("rows", value.get("records", [value]))
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise ValueError(f"{path} must contain grid rows")
    return [normalize_grid_row(row, path, index + 1) for index, row in enumerate(value)]


def candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(candidate[name] for name in REQUIRED_CANDIDATE_KEYS)


def stable_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_expected(
    spec: Mapping[str, Any], source: Mapping[str, Any], target: Mapping[str, Any],
    source_result: Mapping[str, Any], target_result: Mapping[str, Any],
    raw_source_count: int, raw_target_count: int, reuse_feasible: bool,
    eligible_source_count: int, eligible_target_count: int,
) -> None:
    for label, actual in (("expected_source_candidate", source), ("expected_target_candidate", target)):
        if label in spec and candidate_key(spec[label]) != candidate_key(actual):
            raise ValueError(f"{label} mismatch: expected {spec[label]}, got {actual}")
    for label, result in (("expected_source_cost", source_result), ("expected_target_cost", target_result)):
        if label in spec and not math.isclose(float(spec[label]), float(result["cost"]), rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"{label} mismatch: expected {spec[label]}, got {result['cost']}")
    for label, actual in (("expected_source_feasible_count", raw_source_count), ("expected_target_feasible_count", raw_target_count)):
        if label in spec and int(spec[label]) != actual:
            raise ValueError(f"{label} mismatch: expected {spec[label]}, got {actual}")
    for label, actual in (
        ("expected_domain_eligible_source_feasible_count", eligible_source_count),
        ("expected_domain_eligible_target_feasible_count", eligible_target_count),
    ):
        if label in spec and int(spec[label]) != actual:
            raise ValueError(f"{label} mismatch: expected {spec[label]}, got {actual}")
    if "expected_source_reuse_feasible" in spec and bool(spec["expected_source_reuse_feasible"]) != reuse_feasible:
        raise ValueError("expected_source_reuse_feasible mismatch")


def _float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, name: str) -> int:
    number = _float(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _syntactic_candidate(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("candidate must be an object")
    missing = [name for name in REQUIRED_CANDIDATE_KEYS if name not in value]
    if missing:
        raise ValueError(f"candidate is missing required fields: {missing}")
    chemistry = str(value["x_chem"]).strip()
    if chemistry not in CHEMISTRIES:
        raise ValueError(f"Unknown chemistry: {value['x_chem']!r}")
    return {
        "x_chem": chemistry,
        "x_T": _float(value["x_T"], "x_T"),
        "x_S": _int(value["x_S"], "x_S"),
        "x_MPR": _int(value["x_MPR"], "x_MPR"),
        "x_R": _int(value["x_R"], "x_R"),
    }


def _domain_metadata(
    candidate: Mapping[str, Any], bounds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        validate_candidate(candidate, bounds)
    except ValueError as exc:
        return {"domain_eligible": False, "domain_exclusion_reason": str(exc)}
    return {"domain_eligible": True, "domain_exclusion_reason": None}


def _rows_with_domain(
    rows: Sequence[Mapping[str, Any]], bounds: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    resolved = []
    for row in rows:
        item = copy.deepcopy(dict(row))
        item.update(_domain_metadata(candidate_from_row(item), bounds))
        resolved.append(item)
    return resolved


def _grid_provenance(grid_path: Path | str | Sequence[Path | str]) -> Dict[str, Any]:
    """Retain the legacy scalar fields or record every source in a split grid."""
    if isinstance(grid_path, (str, Path)):
        return {"grid_path": str(grid_path), "grid_sha256": file_sha256(Path(grid_path))}
    paths = [Path(item) for item in grid_path]
    return {
        "grid_paths": [str(item) for item in paths],
        "grid_sha256_by_path": {str(item): file_sha256(item) for item in paths},
    }


def _domain_audit(
    source_rows: Sequence[Mapping[str, Any]],
    target_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    source_excluded = [row for row in source_rows if not bool(row.get("domain_eligible", True))]
    target_excluded = [row for row in target_rows if not bool(row.get("domain_eligible", True))]
    reasons: Dict[str, int] = {}
    for row in [*source_excluded, *target_excluded]:
        reason = str(row.get("domain_exclusion_reason") or "unspecified")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "raw_source_row_count": len(source_rows),
        "domain_eligible_source_row_count": len(source_rows) - len(source_excluded),
        "domain_excluded_source_row_count": len(source_excluded),
        "raw_target_row_count": len(target_rows),
        "domain_eligible_target_row_count": len(target_rows) - len(target_excluded),
        "domain_excluded_target_row_count": len(target_excluded),
        "exclusion_reason_counts": reasons,
        "excluded_rows": [
            {
                "condition_name": row.get("condition_name"),
                "design_id": row.get("design_id"),
                "source_row_number": row.get("source_row_number"),
                "reason": row.get("domain_exclusion_reason"),
            }
            for row in [*source_excluded, *target_excluded]
        ],
    }
