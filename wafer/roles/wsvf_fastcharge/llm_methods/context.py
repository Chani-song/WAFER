"""Canonical redesign context and semantic-information artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from optimizers.core.domain import (
    CHEMISTRIES,
    INTEGER_BOUNDS,
    REQUIRED_CANDIDATE_KEYS,
    THICKNESS_BOUNDS_BY_CHEMISTRY,
    make_condition,
    resolved_design_variable_bounds,
    validate_candidate,
)

SEMANTIC_LEVELS = (
    "minimal",
    "compressed_manual",
    "full_manual",
    "level1_definitions",
    "level2_engineering",
    "level3_manual_lookup",
)
SEMANTIC_ASSET_DIR = Path(__file__).resolve().parent / "semantic_assets"
BUNDLED_SEMANTIC_PATHS = {
    "compressed_manual": SEMANTIC_ASSET_DIR / "batpac_v5_manual_compressed_10pct.txt",
    "full_manual": SEMANTIC_ASSET_DIR / "batpac_v5_manual_full_extracted.txt",
    "level1_definitions": SEMANTIC_ASSET_DIR / "semantic_level1_definitions.txt",
    "level2_engineering": SEMANTIC_ASSET_DIR / "semantic_level2_engineering_v2.txt",
    "level3_manual_lookup": SEMANTIC_ASSET_DIR / "semantic_level2_engineering.txt",
}
CONTEXT_SCHEMA_VERSION = "llm_methods.redesign_context.v1"
PROMPT_PROJECTION_SCHEMA_VERSION = "llm_methods.prompt_projection.v3_3"
SEMANTIC_SOURCE_KEYS = {
    "compressed_manual": "compressed_manual",
    "full_manual": "full_manual",
    "level1_definitions": "level1_definitions",
    "level2_engineering": "level2_engineering",
    "level3_manual_lookup": "level3_manual_lookup",
}
CANONICAL_CONTEXT_KEYS = (
    "previous_condition",
    "previous_best_design",
    "previous_best_result",
    "previous_optimization_history",
    "changed_condition",
    "structured_condition_changes",
    "previous_best_reevaluated_under_changed_condition",
    "objective",
    "constraints",
    "variable_bounds",
)

SMOKE_PREVIOUS_BEST_DESIGN: Dict[str, Any] = {
    "x_chem": "NMC811",
    "x_T": 100.0,
    "x_S": 20,
    "x_MPR": 4,
    "x_R": 4,
}
SMOKE_PREVIOUS_BEST_RESULT: Dict[str, Any] = {
    "success": True,
    "cost": 95.55437618154303,
    "total_pack_cost": 9555.437618154303,
    "P": 239.0,
    "E": 84.0,
    "V": 403.0,
    "c": 63.0,
    "L_bat": 1309.4,
    "W_bat": 1402.3,
    "raw": {"fixture": "llm_methods_smoke_previous_best"},
}


def build_redesign_context(
    previous_condition_name: str,
    changed_condition_name: str,
    previous_best_design: Mapping[str, Any],
    previous_best_result: Mapping[str, Any],
    previous_optimization_history: Optional[List[Mapping[str, Any]]] = None,
    previous_best_reevaluated_under_changed_condition: Optional[Mapping[str, Any]] = None,
    design_variable_bounds_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical LLM redesign context."""
    previous_condition = make_condition(previous_condition_name)
    changed_condition = make_condition(changed_condition_name)
    previous_best = dict(previous_best_design)
    resolved_bounds = variable_bounds(design_variable_bounds_override)
    validate_candidate(previous_best, resolved_bounds)
    return normalize_context(
        {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "previous_condition": previous_condition,
            "previous_best_design": previous_best,
            "previous_best_result": dict(previous_best_result),
            "previous_optimization_history": [
                dict(item) for item in (previous_optimization_history or [])
            ],
            "changed_condition": changed_condition,
            "structured_condition_changes": condition_changes(previous_condition, changed_condition),
            "previous_best_reevaluated_under_changed_condition": (
                dict(previous_best_reevaluated_under_changed_condition)
                if previous_best_reevaluated_under_changed_condition is not None
                else None
            ),
            "objective": objective_description(),
            "constraints": constraint_descriptions(changed_condition),
            "variable_bounds": resolved_bounds,
            "design_variable_bounds_override": copy.deepcopy(
                design_variable_bounds_override
            ),
        }
    )


def build_smoke_context(changed_condition_name: str) -> Dict[str, Any]:
    """Build a deterministic non-production context for tests and mock smoke runs."""
    context = build_redesign_context(
        previous_condition_name="s0",
        changed_condition_name=changed_condition_name,
        previous_best_design=SMOKE_PREVIOUS_BEST_DESIGN,
        previous_best_result=SMOKE_PREVIOUS_BEST_RESULT,
        previous_optimization_history=[
            {
                "iteration": 0,
                "candidate": dict(SMOKE_PREVIOUS_BEST_DESIGN),
                "result": dict(SMOKE_PREVIOUS_BEST_RESULT),
                "source": "llm_methods_smoke_fixture",
            }
        ],
        previous_best_reevaluated_under_changed_condition=None,
    )
    context["context_kind"] = "smoke_fixture"
    return context


def load_context_artifact(path: Path | str) -> Dict[str, Any]:
    """Load and validate a versioned redesign-context JSON artifact."""
    artifact_path = Path(path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Context artifact must contain a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != CONTEXT_SCHEMA_VERSION:
        raise ValueError(
            "Context artifact schema_version must be "
            f"{CONTEXT_SCHEMA_VERSION!r}, got {schema_version!r}"
        )
    context = payload.get("context", payload)
    if not isinstance(context, Mapping):
        raise ValueError("Context artifact context must be a JSON object")
    normalized = normalize_context(context)
    normalized["context_artifact"] = {
        "path": str(artifact_path),
        "sha256": _sha256_text(artifact_path.read_text(encoding="utf-8")),
    }
    return normalized


def save_context_artifact(context: Mapping[str, Any], path: Path | str) -> Path:
    """Validate and save a canonical redesign context for production runners."""
    normalized = normalize_context(context)
    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "context": {
            key: copy.deepcopy(normalized[key])
            for key in CANONICAL_CONTEXT_KEYS
        },
    }
    for optional_key in ("semantic_sources", "context_kind", "design_variable_bounds_override"):
        if optional_key in normalized:
            payload["context"][optional_key] = copy.deepcopy(normalized[optional_key])
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return artifact_path


def normalize_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a canonical context with compatibility aliases for older helpers."""
    missing = [key for key in CANONICAL_CONTEXT_KEYS if key not in context]
    if missing:
        raise ValueError(f"Redesign context is missing required keys: {missing}")
    normalized = {key: copy.deepcopy(context[key]) for key in CANONICAL_CONTEXT_KEYS}
    normalized["schema_version"] = str(context.get("schema_version", CONTEXT_SCHEMA_VERSION))
    validate_candidate(normalized["previous_best_design"], normalized["variable_bounds"])

    # Compatibility aliases are retained for non-prompt helper code only.
    normalized["source_condition"] = copy.deepcopy(normalized["previous_condition"])
    normalized["source_candidate"] = copy.deepcopy(normalized["previous_best_design"])
    normalized["source_result"] = copy.deepcopy(normalized["previous_best_result"])
    normalized["target_condition"] = copy.deepcopy(normalized["changed_condition"])
    normalized["condition_changes"] = copy.deepcopy(normalized["structured_condition_changes"])
    normalized["condition_change_description"] = describe_condition_change(
        normalized["structured_condition_changes"]
    )
    if "context_artifact" in context:
        normalized["context_artifact"] = copy.deepcopy(context["context_artifact"])
    if "semantic_information" in context:
        normalized["semantic_information"] = str(context["semantic_information"])
    if "semantic_artifact" in context:
        normalized["semantic_artifact"] = copy.deepcopy(context["semantic_artifact"])
    if "semantic_sources" in context:
        normalized["semantic_sources"] = copy.deepcopy(context["semantic_sources"])
    if "context_kind" in context:
        normalized["context_kind"] = str(context["context_kind"])
    if "design_variable_bounds_override" in context:
        normalized["design_variable_bounds_override"] = copy.deepcopy(
            context["design_variable_bounds_override"]
        )
    return normalized


def project_model_visible_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the v3.3 source-reference-only model-visible projection."""
    normalized = normalize_context(context)
    projected = {
        "source_reference_design": copy.deepcopy(normalized["previous_best_design"]),
        "evaluated_source_result": _project_result(normalized["previous_best_result"]),
        "target_condition_change": copy.deepcopy(normalized["structured_condition_changes"]),
        "target_condition": copy.deepcopy(normalized["changed_condition"]),
        "target_objective": copy.deepcopy(normalized["objective"]),
        "target_constraints": copy.deepcopy(normalized["constraints"]),
        "design_variable_definitions_and_bounds": copy.deepcopy(normalized["variable_bounds"]),
    }
    for optional_key in ("semantic_information", "semantic_artifact", "manual_lookup_ledger", "manual_lookup_metrics"):
        if optional_key in context:
            projected[optional_key] = copy.deepcopy(context[optional_key])
    artifact = context.get("context_artifact")
    projection_json = json.dumps(
        {
            key: projected.get(key)
            for key in sorted(projected)
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    projected["prompt_projection_metadata"] = {
        "schema_version": PROMPT_PROJECTION_SCHEMA_VERSION,
        "full_context_artifact_sha256": (
            artifact.get("sha256") if isinstance(artifact, Mapping) else None
        ),
        "projected_context_character_count": len(projection_json),
        "projected_context_estimated_token_count": estimate_token_count(projection_json),
        "source_context_mode": "source_reference_only",
        "history_policy": "source_reference_only",
    }
    return projected


def _project_history_event(event: Mapping[str, Any], index: int) -> Dict[str, Any]:
    result = event.get("result") if isinstance(event.get("result"), Mapping) else event
    return {
        "iteration": event.get("iteration", index),
        "candidate": copy.deepcopy(event.get("candidate")),
        "result": _project_result(result),
    }


def _project_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    fields = ("cost", "E", "P", "V", "c", "L_bat", "W_bat", "success",
              "optimization_valid", "feasible", "failure_reason")
    return {
        field: copy.deepcopy(result[field])
        for field in fields
        if field in result
    }


def with_semantic_information(
    context: Mapping[str, Any],
    semantic_level: str,
    artifact_dir: Optional[Path | str] = None,
    compressed_manual_path: Optional[Path | str] = None,
    full_manual_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Attach semantic content and metadata for a semantic level."""
    if semantic_level not in SEMANTIC_LEVELS:
        raise ValueError(f"Unknown semantic_level: {semantic_level!r}")
    normalized = normalize_context(context)
    explicit_sources = {
        "compressed_manual": compressed_manual_path,
        "full_manual": full_manual_path,
        "level1_definitions": None,
        "level2_engineering": None,
        "level3_manual_lookup": None,
    }
    content, source_metadata = semantic_content_for_level(
        normalized,
        semantic_level,
        explicit_source=explicit_sources.get(semantic_level),
        return_metadata=True,
    )
    metadata: Dict[str, Any] = {
        "semantic_level": semantic_level,
        "sha256": _sha256_text(content),
        "character_count": len(content),
        "token_count": estimate_token_count(content),
        **source_metadata,
    }
    if artifact_dir is not None:
        directory = Path(artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"semantic_{semantic_level}.txt"
        path.write_text(content, encoding="utf-8")
        metadata["path"] = str(path)
    normalized["semantic_information"] = content
    normalized["semantic_artifact"] = metadata
    normalized["semantic_level"] = semantic_level
    return normalized


def semantic_content_for_level(
    context: Mapping[str, Any],
    semantic_level: str,
    explicit_source: Optional[Path | str] = None,
    return_metadata: bool = False,
) -> Any:
    """Return semantic information loaded from a traceable source artifact."""
    if semantic_level == "minimal":
        content = "\n".join(
            [
                "BatPaC redesign task definitions.",
                f"Objective: {json.dumps(context['objective'], sort_keys=True)}",
                f"Constraints: {json.dumps(context['constraints'], sort_keys=True)}",
                f"Variable bounds: {json.dumps(context['variable_bounds'], sort_keys=True)}",
            ]
        )
        metadata = {"source_kind": "structured_problem_definition"}
    elif semantic_level in SEMANTIC_SOURCE_KEYS:
        source = (
            explicit_source
            or _semantic_source_from_context(context, semantic_level)
            or BUNDLED_SEMANTIC_PATHS[semantic_level]
        )
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(
                f"{semantic_level} semantic artifact does not exist: {source_path}"
            )
        content = source_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"{semantic_level} semantic artifact is empty: {source_path}")
        metadata = {
            "source_kind": "external_artifact",
            "source_path": str(source_path.resolve()),
            "source_sha256": _sha256_text(content),
        }
        if semantic_level == "level3_manual_lookup":
            manual_path = BUNDLED_SEMANTIC_PATHS["full_manual"]
            metadata.update(
                {
                    "lookup_enabled": True,
                    "lookup_corpus_path": str(manual_path.resolve()),
                    "lookup_corpus_sha256": _sha256_text(
                        manual_path.read_text(encoding="utf-8")
                    ),
                    "lookup_manual_title": "A Manual for BatPaC v5.0",
                    "lookup_manual_workbook": "BatPaC 5.0 2022-02-09.xlsm",
                }
            )
    else:
        raise ValueError(f"Unknown semantic_level: {semantic_level!r}")
    if return_metadata:
        return content, metadata
    return content

def _semantic_source_from_context(
    context: Mapping[str, Any], semantic_level: str
) -> Optional[Path | str]:
    sources = context.get("semantic_sources")
    if not isinstance(sources, Mapping):
        return None
    raw = sources.get(SEMANTIC_SOURCE_KEYS[semantic_level])
    if isinstance(raw, Mapping):
        raw = raw.get("path")
    return raw if isinstance(raw, (str, Path)) and str(raw).strip() else None


def _smoke_semantic_reference(semantic_level: str) -> str:
    if semantic_level == "compressed_manual":
        return (
            "NON-PRODUCTION SMOKE REFERENCE. BatPaC maps a complete candidate to cost, "
            "performance, capacity, and pack-dimension outputs. Simulator observations, "
            "not unverified heuristics, determine feasibility."
        )
    if semantic_level == "full_manual":
        return (
            "NON-PRODUCTION SMOKE REFERENCE. This deterministic fixture exercises the full-manual "
            "code path without claiming to contain the BatPaC manual. A complete candidate contains "
            "chemistry, cathode thickness, series cells, modules per row, and pack rows. BatPaC returns "
            "cost, energy, power, voltage, cell capacity, length, and width. Every proposal must satisfy "
            "the explicit mixed-variable bounds and must be evaluated before its feasibility is known."
        )
    raise ValueError(f"No smoke semantic reference for {semantic_level!r}")


def variable_bounds(
    override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return design-variable bounds in prompt-friendly form."""
    return resolved_design_variable_bounds(override)



def condition_changes(
    previous_condition: Mapping[str, Any],
    changed_condition: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Return structured previous-to-changed condition differences."""
    changes: List[Dict[str, Any]] = []

    tracked_fields = {
        "P_req": "Peak power requirement",
        "E_req": "Energy requirement",
        "V_min": "Minimum voltage constraint",
        "V_max": "Maximum voltage constraint",
        "c_min": "Minimum cell capacity constraint",
        "c_max": "Maximum cell capacity constraint",
        "L_max": "Maximum pack length constraint",
        "W_max": "Maximum pack width constraint",
        "cam_cost_nmc811": "NMC811 CAM cost",
        "cam_cost_lfp": "LFP CAM cost",
    }

    for field, label in tracked_fields.items():
        previous_value = previous_condition.get(field)
        changed_value = changed_condition.get(field)

        if previous_value == changed_value:
            continue

        direction = _change_direction(previous_value, changed_value)

        changes.append(
            {
                "field": field,
                "previous": previous_value,
                "changed": changed_value,
                "direction": direction,
                "description": (
                    f"{label} {direction} from "
                    f"{previous_value} to {changed_value}."
                ),
            }
        )

    # Fast-charge redesign is a change in requested charging time rather
    # than one of the legacy pack-level requirement fields.
    if changed_condition.get("name") == "s_fastcharge":
        previous_value = changed_condition.get(
            "source_charge_time_min",
            previous_condition.get("charge_time_min"),
        )
        changed_value = changed_condition.get("charge_time_min")

        if previous_value != changed_value:
            direction = _change_direction(
                previous_value,
                changed_value,
            )

            changes.append(
                {
                    "field": "charge_time_min",
                    "previous": previous_value,
                    "changed": changed_value,
                    "direction": direction,
                    "description": (
                        "Required charging time "
                        f"{direction} from {previous_value} "
                        f"to {changed_value} minutes."
                    ),
                }
            )

    return changes

def describe_condition_change(changes: List[Mapping[str, Any]]) -> str:
    """Render structured condition changes in previous/changed terminology."""
    if not changes:
        return "No condition change."
    return " ".join(str(change.get("description", "")) for change in changes)


def objective_description() -> Dict[str, str]:
    """Return objective metadata."""
    return {
        "name": "cost_per_kWh",
        "direction": "minimize",
        "description": "Minimize BatPaC cost_per_kWh while satisfying all constraints.",
    }



def constraint_descriptions(condition: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return constraint metadata for the changed condition."""

    constraints = [
        {
            "name": "P_min",
            "metric": "P",
            "operator": ">=",
            "value": condition["P_req"],
        },
        {
            "name": "E_min",
            "metric": "E",
            "operator": ">=",
            "value": condition["E_req"],
        },
        {
            "name": "V_min",
            "metric": "V",
            "operator": ">=",
            "value": condition["V_min"],
        },
        {
            "name": "V_max",
            "metric": "V",
            "operator": "<=",
            "value": condition["V_max"],
        },
        {
            "name": "c_min",
            "metric": "c",
            "operator": ">=",
            "value": condition["c_min"],
        },
        {
            "name": "c_max",
            "metric": "c",
            "operator": "<=",
            "value": condition["c_max"],
        },
        {
            "name": "L_bat_max",
            "metric": "L_bat",
            "operator": "<=",
            "value": condition["L_max"],
        },
        {
            "name": "W_bat_max",
            "metric": "W_bat",
            "operator": "<=",
            "value": condition["W_max"],
        },
    ]

    if condition.get("name") == "s_fastcharge":
        constraints.append(
            {
                "name": "fastcharge_thickness_max",
                "metric": "x_T",
                "operator": "<=",
                "value": (
                    "candidate-specific BatPaC fast-charge "
                    "thickness limit"
                ),
                "description": (
                    "Cathode thickness must not exceed the "
                    "candidate-specific BatPaC fast-charge "
                    "thickness limit, subject to the "
                    "chemistry-dependent minimum-thickness floor. "
                    "The numerical limit is established only by "
                    "BatPaC evaluation."
                ),
            }
        )

    return constraints

def estimate_token_count(text: str) -> int:
    """Return a deterministic approximate token count for artifact metadata."""
    return len(text.split())


def _change_direction(previous_value: Any, changed_value: Any) -> str:
    try:
        previous_numeric = float(previous_value)
        changed_numeric = float(changed_value)
    except (TypeError, ValueError):
        return "changes"
    if changed_numeric > previous_numeric:
        return "increases"
    if changed_numeric < previous_numeric:
        return "decreases"
    return "changes"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
