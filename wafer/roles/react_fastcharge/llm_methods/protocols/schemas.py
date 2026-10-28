"""Strict JSON schemas for method-level LLM strategy outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from optimizers.core.domain import (
    CHEMISTRIES,
    INTEGER_BOUNDS,
    REQUIRED_CANDIDATE_KEYS,
    THICKNESS_BOUNDS_BY_CHEMISTRY,
    resolved_design_variable_bounds,
)

from .actions import ACTION_EVALUATE_CANDIDATE, ACTION_FINISH

DESIGN_VARIABLES = tuple(REQUIRED_CANDIDATE_KEYS)
MAX_REACT_OBSERVATION_REFERENCES = 3
HYBRID_STRATEGIES = ("warm_start", "variable_fixing", "react")
VARIABLE_FIXING_ALL_FREE_FALLBACK: Dict[str, Any] = {}

WARM_START_CANDIDATE_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "x_chem", "x_T", "x_S", "x_MPR", "x_R", "rationale",
        "evidence_references", "search_role", "expected_effects",
        "tradeoffs", "uncertainty",
    ],
    "properties": {
        "x_chem": {"type": "string"},
        "x_T": {"type": "number"},
        "x_S": {"type": "integer"},
        "x_MPR": {"type": "integer"},
        "x_R": {"type": "integer"},
        "rationale": {"type": "string", "minLength": 1},
        "evidence_references": {
            "type": "array", "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "search_role": {"type": "string", "minLength": 1},
        "expected_effects": {
            "type": "array", "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "tradeoffs": {
            "type": "array", "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "uncertainty": {
            "type": "array", "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
}
WARM_START_JSON_SCHEMA: Dict[str, Any] = {
    "name": "WarmStartOutput",
    "type": "object",
    "additionalProperties": False,
    "required": ["warm_start_count", "allocation_rationale", "candidates"],
    "properties": {
        "warm_start_count": {"type": "integer", "minimum": 1},
        "allocation_rationale": {"type": "string", "minLength": 1},
        "candidates": {
            "type": "array",
            "minItems": 1,
            "items": WARM_START_CANDIDATE_JSON_SCHEMA,
        },
    },
    "invariant": "candidates array length must equal warm_start_count",
}
VARIABLE_FIXING_JSON_SCHEMA: Dict[str, Any] = {
    "name": "VariableFixingOutput",
    "required": ["free_variables", "fixed_variables", "variable_assessments", "rationale", "warnings"],
    "field_order": ["free_variables", "fixed_variables", "variable_assessments", "rationale", "warnings"],
    "semantics": {
        "free_variables": "variable names retained in the downstream optimizer search space",
        "fixed_variables": "source-incumbent values removed from the downstream optimizer search space",
        "variable_assessments": "one concise fix-or-free decision for each design variable",
    },
    "free_variables": "list of remaining allowed keys; disjoint from fixed_variables and exhaustive with it",
    "fixed_variables": "object with allowed keys x_chem, x_T, x_S, x_MPR, x_R; every value must equal the source incumbent",
    "variable_assessments": {
        "type": "array",
        "exact_variables": list(DESIGN_VARIABLES),
        "required_fields": [
            "variable", "source_value", "decision", "justification", "confidence",
        ],
        "decision_enum": ["fix", "free"],
    },
    "invariant": (
        "fixed keys union free names equals x_chem, x_T, x_S, x_MPR, x_R; "
        "intersection is empty; len(free_variables) >= 1; fixed values equal source values"
    ),
    "partition_rule": "all-fixed is invalid; at least one variable must be free",
    "warnings": "list of concrete uncertainty strings; use [] when there is no concrete uncertainty",
}
OBSERVATION_REFERENCE_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["iteration", "fields"],
    "properties": {
        "iteration": {"type": "integer", "minimum": 0},
        "fields": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        },
    },
}
REACT_ACTION_JSON_SCHEMA: Dict[str, Any] = {
    "name": "ReActAction",
    "oneOf": [
        {
            "title": "evaluate_candidate",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "action", "candidate", "observations_used", "decision_summary", "evidence_used",
                "variables_changed", "expected_effect", "uncertainty",
            ],
            "properties": {
                "action": {"const": ACTION_EVALUATE_CANDIDATE},
                "candidate": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(DESIGN_VARIABLES),
                    "properties": {
                        "x_chem": {"type": "string"},
                        "x_T": {"type": "number"},
                        "x_S": {"type": "integer"},
                        "x_MPR": {"type": "integer"},
                        "x_R": {"type": "integer"},
                    },
                },
                "observations_used": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": MAX_REACT_OBSERVATION_REFERENCES,
                    "items": OBSERVATION_REFERENCE_JSON_SCHEMA,
                },
                "decision_summary": {"type": "string", "minLength": 1, "maxWords": 80},
                "evidence_used": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "minLength": 1}},
                "variables_changed": {"type": "array", "items": {"enum": list(DESIGN_VARIABLES)}},
                "expected_effect": {"type": "string", "minLength": 1},
                "uncertainty": {"type": "string", "minLength": 1},
            },
        },
        {
            "title": "finish",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "action", "final_candidate", "observations_used", "decision_summary", "evidence_used",
                "variables_changed", "uncertainty",
            ],
            "properties": {
                "action": {"const": ACTION_FINISH},
                "final_candidate": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(DESIGN_VARIABLES),
                    "properties": {
                        "x_chem": {"type": "string"},
                        "x_T": {"type": "number"},
                        "x_S": {"type": "integer"},
                        "x_MPR": {"type": "integer"},
                        "x_R": {"type": "integer"},
                    },
                },
                "observations_used": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": MAX_REACT_OBSERVATION_REFERENCES,
                    "items": OBSERVATION_REFERENCE_JSON_SCHEMA,
                },
                "decision_summary": {"type": "string", "minLength": 1, "maxWords": 80},
                "evidence_used": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "minLength": 1}},
                "variables_changed": {"type": "array", "items": {"enum": list(DESIGN_VARIABLES)}},
                "expected_effect": {"type": "string", "minLength": 1},
                "uncertainty": {"type": "string", "minLength": 1},
            },
        },
    ],
}
REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA: Dict[str, Any] = {
    "name": "ReActAction",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "thought",
        "action",
        "candidate",
    ],
    "properties": {
        "thought": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2048,
        },
        "action": {
            "const": ACTION_EVALUATE_CANDIDATE,
        },
        "candidate": {
            "type": "object",
            "additionalProperties": False,
            "required": list(DESIGN_VARIABLES),
            "properties": {
                "x_chem": {"type": "string"},
                "x_T": {"type": "number"},
                "x_S": {"type": "integer"},
                "x_MPR": {"type": "integer"},
                "x_R": {"type": "integer"},
            },
        },
    },
}

HYBRID_POLICY_JSON_SCHEMA: Dict[str, Any] = {
    "name": "HybridPolicyOutput",
    "required": ["selected_strategy", "rationale", "strategy_args"],
    "selected_strategy": list(HYBRID_STRATEGIES),
}
NEUTRAL_REOPTIMIZATION_JSON_SCHEMA: Dict[str, Any] = {
    "name": "NeutralReoptimizationDecision",
    "version": "v1_structured_neutral",
    "required": [
        "changed_requirements",
        "reusable_prior_information",
        "obsolete_prior_information",
        "variables_to_fix",
        "variables_to_reopen",
        "selected_reoptimization_strategy",
        "justification",
        "proposed_initial_design_or_search_region",
        "uncertainty",
        "need_for_additional_evaluation",
    ],
    "notes": [
        "variables_to_fix may be empty",
        "the schema does not prescribe a scenario-specific decision",
    ],
}


@dataclass
class WarmStartOutput:
    """Validated warm-start strategy output."""

    candidates: List[Dict[str, Any]]
    warm_start_count: int = 0
    allocation_rationale: str = ""


@dataclass
class VariableFixingOutput:
    """Validated variable-fixing strategy output."""

    fixed_variables: Dict[str, Any]
    free_variables: List[str]
    rationale: str
    warnings: List[str]
    variable_assessments: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def reason(self) -> str:
        """Backward-compatible rationale alias."""
        return self.rationale


@dataclass
class ReActAction:
    """Validated ReAct action."""

    action_type: str
    reason: str
    candidate: Optional[Dict[str, Any]] = None
    final_candidate: Optional[Dict[str, Any]] = None
    observations_used: List[Any] = field(default_factory=list)
    resolved_observations_used: List[Dict[str, Any]] = field(default_factory=list)
    variables_changed: List[str] = field(default_factory=list)
    reported_variables_changed: List[str] = field(default_factory=list)
    expected_effect: str = ""
    uncertainty: str = ""
    evidence_used: List[str] = field(default_factory=list)


@dataclass
class HybridPolicyOutput:
    """Validated hybrid policy output."""

    selected_strategy: str
    rationale: str
    strategy_args: Dict[str, Any]
    optimizer_recommendation: Optional[str] = None

    @property
    def reason(self) -> str:
        """Backward-compatible rationale alias."""
        return self.rationale


@dataclass
class NeutralReoptimizationDecision:
    changed_requirements: List[str]
    reusable_prior_information: List[str]
    obsolete_prior_information: List[str]
    variables_to_fix: List[str]
    variables_to_reopen: List[str]
    selected_reoptimization_strategy: str
    justification: str
    proposed_initial_design_or_search_region: Any
    uncertainty: List[str]
    need_for_additional_evaluation: bool


def parse_neutral_reoptimization_decision(
    payload: Mapping[str, Any],
) -> NeutralReoptimizationDecision:
    """Parse the optional neutral v1 decision without changing v0 strategies."""
    _require_mapping(payload, "NeutralReoptimizationDecision")
    string_lists = {}
    for field in (
        "changed_requirements",
        "reusable_prior_information",
        "obsolete_prior_information",
        "variables_to_fix",
        "variables_to_reopen",
        "uncertainty",
    ):
        value = payload.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"NeutralReoptimizationDecision.{field} must be a string list")
        string_lists[field] = list(value)
    for field in ("variables_to_fix", "variables_to_reopen"):
        invalid = [item for item in string_lists[field] if item not in DESIGN_VARIABLES]
        if invalid:
            raise ValueError(f"NeutralReoptimizationDecision.{field} has invalid names: {invalid}")
    overlap = set(string_lists["variables_to_fix"]) & set(string_lists["variables_to_reopen"])
    if overlap:
        raise ValueError(f"variables_to_fix and variables_to_reopen overlap: {sorted(overlap)}")
    selected = payload.get("selected_reoptimization_strategy")
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("selected_reoptimization_strategy must be a non-empty string")
    justification = payload.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError("justification must be a non-empty string")
    need = payload.get("need_for_additional_evaluation")
    if not isinstance(need, bool):
        raise ValueError("need_for_additional_evaluation must be boolean")
    return NeutralReoptimizationDecision(
        **string_lists,
        selected_reoptimization_strategy=selected,
        justification=justification,
        proposed_initial_design_or_search_region=payload.get(
            "proposed_initial_design_or_search_region"
        ),
        need_for_additional_evaluation=need,
    )


# 목적: warm-start JSON을 candidate list dataclass로 엄격히 검증한다.
# 입력: backend가 반환한 JSON object.
# 출력: WarmStartOutput.
def parse_warm_start_output(
    payload: Mapping[str, Any],
    minimum_count: Optional[int] = None,
    maximum_count: Optional[int] = None,
    total_budget: Optional[int] = None,
    fixed_count: Optional[int] = None,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> WarmStartOutput:
    """Parse warm-start output."""
    _require_mapping(payload, "WarmStartOutput")
    unexpected = [
        key for key in payload
        if key not in {"warm_start_count", "allocation_rationale", "candidates"}
    ]
    if unexpected:
        raise ValueError(
            f"WarmStartOutput.candidates contract: root contains unexpected keys: {unexpected}"
        )
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise ValueError("WarmStartOutput.candidates must be a list")
    candidates = []
    for item in candidates_raw:
        candidates.append(_parse_candidate_object(
            item, require_proposal_reasoning=True, variable_bounds=variable_bounds
        ))
    has_explicit_count = "warm_start_count" in payload
    # Legacy fixed-count callers did not expose allocation fields. Preserve
    # those stored/static fixtures by deriving only their count from the list;
    # model-selected mode always supplies and validates the explicit field.
    count_raw = payload.get("warm_start_count", len(candidates))
    if isinstance(count_raw, bool) or not isinstance(count_raw, int):
        raise ValueError("WarmStartOutput.warm_start_count must be an integer")
    if has_explicit_count and minimum_count is not None and count_raw < int(minimum_count):
        raise ValueError("WarmStartOutput.warm_start_count is below the allowed minimum")
    if has_explicit_count and maximum_count is not None and count_raw > int(maximum_count):
        raise ValueError("WarmStartOutput.warm_start_count exceeds the allowed maximum")
    if has_explicit_count and total_budget is not None and count_raw >= int(total_budget):
        raise ValueError("WarmStartOutput.warm_start_count must be less than total budget")
    if len(candidates) != count_raw:
        raise ValueError("WarmStartOutput.warm_start_count must equal len(candidates)")
    keys = [
        tuple(candidate[name] for name in DESIGN_VARIABLES)
        for candidate in candidates
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("WarmStartOutput.candidates must be distinct")
    rationale = payload.get("allocation_rationale")
    if rationale is None and not has_explicit_count:
        rationale = f"Fixed-count warm-start ablation with {count_raw} candidates."
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("WarmStartOutput.allocation_rationale must be a non-empty string")
    if len(rationale.split()) > 60:
        raise ValueError("WarmStartOutput.allocation_rationale must be 60 words or fewer")
    return WarmStartOutput(
        candidates=candidates,
        warm_start_count=count_raw,
        allocation_rationale=rationale,
    )


# 목적: variable-fixing JSON을 fixed/free variable dataclass로 엄격히 검증한다.
# 입력: backend가 반환한 JSON object.
# 출력: VariableFixingOutput.
def parse_variable_fixing_output(
    payload: Mapping[str, Any],
    source_candidate: Optional[Mapping[str, Any]] = None,
    require_assessments: bool = False,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> VariableFixingOutput:
    """Parse variable-fixing output."""
    _require_mapping(payload, "VariableFixingOutput")
    fixed_variables, warnings = _parse_fixed_variables(
        payload.get("fixed_variables"), variable_bounds=variable_bounds
    )
    if len(fixed_variables) >= len(DESIGN_VARIABLES):
        raise ValueError(
            "VariableFixingOutput must leave at least one design variable free; "
            "fixing the complete design eliminates reoptimization"
        )
    free_variables = _parse_free_variables(payload.get("free_variables"), fixed_variables)
    _validate_variable_partition(fixed_variables, free_variables)
    rationale = payload.get("rationale", payload.get("reason"))
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("VariableFixingOutput.rationale must be a non-empty string")
    if len(rationale.split()) > 80:
        raise ValueError("VariableFixingOutput.rationale must be 80 words or fewer")
    warnings = _parse_warnings(payload.get("warnings"))
    assessments = _parse_variable_assessments(
        payload.get("variable_assessments"),
        fixed_variables,
        free_variables,
        source_candidate,
        required=require_assessments,
    )
    return VariableFixingOutput(
        fixed_variables=fixed_variables,
        free_variables=free_variables,
        rationale=rationale,
        warnings=warnings,
        variable_assessments=assessments,
    )


def _parse_variable_assessments(
    raw: Any,
    fixed_variables: Mapping[str, Any],
    free_variables: List[str],
    source_candidate: Optional[Mapping[str, Any]],
    required: bool,
) -> List[Dict[str, Any]]:
    if raw is None and not required:
        return []
    if not isinstance(raw, list):
        raise ValueError("VariableFixingOutput.variable_assessments must be a list")
    if len(raw) != len(DESIGN_VARIABLES):
        raise ValueError("variable_assessments must contain exactly five entries")
    parsed: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("variable_assessments entries must be objects")
        variable = item.get("variable")
        if variable not in DESIGN_VARIABLES or variable in seen:
            raise ValueError("variable_assessments variables must appear exactly once")
        seen.add(variable)
        decision = item.get("decision")
        expected_decision = "fix" if variable in fixed_variables else "free"
        if decision != expected_decision:
            raise ValueError("variable assessment decision disagrees with partition")
        justification = item.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            raise ValueError("variable assessment justification must be a non-empty string")
        confidence = item.get("confidence")
        if confidence not in {"low", "medium", "high"}:
            raise ValueError("variable assessment confidence must be low, medium, or high")
        source_value = item.get("source_value")
        if source_candidate is not None and source_value != source_candidate.get(variable):
            raise ValueError("variable assessment source_value must equal source incumbent")
        if variable in fixed_variables and source_candidate is not None:
            if fixed_variables[variable] != source_candidate.get(variable):
                raise ValueError("fixed value must equal source incumbent value")
        parsed.append(dict(item))
    if seen != set(DESIGN_VARIABLES):
        raise ValueError("variable_assessments is missing a design variable")
    return parsed


# 목적: ReAct action JSON을 evaluate/stop action dataclass로 검증한다.
# 입력: backend가 반환한 JSON object.
# 출력: ReActAction.
def parse_react_action(
    payload: Mapping[str, Any],
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> ReActAction:
    """Parse a ReAct action."""
    _require_mapping(payload, "ReActAction")
    action_type = payload.get("action")
    if action_type not in {ACTION_EVALUATE_CANDIDATE, ACTION_FINISH}:
        raise ValueError("ReActAction.action must be 'evaluate_candidate' or 'finish'")
    reason = payload.get("decision_summary")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ReActAction.decision_summary must be a non-empty string")
    if len(reason.split()) > 80:
        raise ValueError("ReActAction.decision_summary must be 80 words or fewer")
    # Validate the action-specific candidate before common fields so the retry
    # path can diagnose a missing top-level candidate precisely.
    candidate = None
    final_candidate = None
    if action_type == ACTION_EVALUATE_CANDIDATE:
        candidate = _parse_candidate_object(
            payload.get("candidate"), variable_bounds=variable_bounds
        )
    else:
        final_raw = payload.get("final_candidate")
        if final_raw is None:
            raise ValueError("ReActAction.finish requires final_candidate")
        final_candidate = _parse_candidate_object(
            final_raw, variable_bounds=variable_bounds
        )
    action_candidate_key = "candidate" if action_type == ACTION_EVALUATE_CANDIDATE else "final_candidate"
    allowed = {
        "action", action_candidate_key, "observations_used", "decision_summary",
        "evidence_used", "variables_changed", "expected_effect", "uncertainty",
    }
    unexpected = [key for key in payload if key not in allowed]
    if unexpected:
        raise ValueError(f"ReActAction contains unexpected keys: {unexpected}")
    observations_used = payload.get("observations_used")
    if not isinstance(observations_used, list):
        raise ValueError("ReActAction.observations_used must be a list")
    if len(observations_used) > MAX_REACT_OBSERVATION_REFERENCES:
        raise ValueError(
            "ReActAction.observations_used must contain at most "
            f"{MAX_REACT_OBSERVATION_REFERENCES} references"
        )
    observations_used = _parse_observation_references(observations_used)
    variables_changed = payload.get("variables_changed")
    if not isinstance(variables_changed, list) or not all(
        isinstance(item, str) and item in DESIGN_VARIABLES for item in variables_changed
    ):
        raise ValueError("ReActAction.variables_changed must be a list of design-variable names")
    expected_effect = payload.get("expected_effect")
    if not isinstance(expected_effect, str) or not expected_effect.strip():
        raise ValueError("ReActAction.expected_effect must be a non-empty string")
    if len(expected_effect.split()) > 60:
        raise ValueError("ReActAction.expected_effect must be 60 words or fewer")
    evidence_used = payload.get("evidence_used")
    if not isinstance(evidence_used, list) or not evidence_used or len(evidence_used) > 5 or not all(isinstance(item, str) and item.strip() for item in evidence_used):
        raise ValueError("ReActAction.evidence_used must contain one to five short strings")
    uncertainty = payload.get("uncertainty")
    if not isinstance(uncertainty, str) or not uncertainty.strip():
        raise ValueError("ReActAction.uncertainty must be a non-empty string")
    return ReActAction(
        action_type=str(action_type),
        reason=reason,
        candidate=candidate,
        final_candidate=final_candidate,
        observations_used=list(observations_used),
        variables_changed=list(variables_changed),
        reported_variables_changed=list(variables_changed),
        expected_effect=expected_effect,
        uncertainty=uncertainty,
        evidence_used=list(evidence_used),
    )


def parse_environment_observation_react_action(
    payload: Mapping[str, Any],
) -> ReActAction:
    """
    Parse the evaluate-only environment-observation ReAct contract.

    The LLM controls only the engineering decision:
      thought + evaluate_candidate + candidate.

    Trajectory bookkeeping and termination are controller responsibilities.
    """
    _require_mapping(payload, "ReActAction")

    action_type = payload.get("action")

    if not isinstance(action_type, str):
        raise ValueError(
            "ReActAction.action must be the string 'evaluate_candidate'"
        )

    if action_type != ACTION_EVALUATE_CANDIDATE:
        raise ValueError(
            "ReActAction.action must be exactly 'evaluate_candidate'"
        )

    thought = payload.get("thought")

    if not isinstance(thought, str) or not thought.strip():
        raise ValueError(
            "ReActAction.thought must be a non-empty string"
        )

    if len(thought) > 2048:
        raise ValueError(
            "ReActAction.thought must be 2048 characters or fewer"
        )

    allowed = {
        "thought",
        "action",
        "candidate",
    }

    unexpected = [
        key
        for key in payload
        if key not in allowed
    ]

    if unexpected:
        raise ValueError(
            f"ReActAction contains unexpected keys: {unexpected}"
        )

    raw_candidate = payload.get("candidate")

    if not isinstance(raw_candidate, Mapping):
        raise ValueError(
            "ReActAction.candidate must be an object"
        )

    if set(raw_candidate) != set(DESIGN_VARIABLES):
        raise ValueError(
            "ReActAction.candidate must contain exactly "
            f"{list(DESIGN_VARIABLES)}"
        )

    if not isinstance(
        raw_candidate.get("x_chem"),
        str,
    ):
        raise ValueError(
            "ReActAction.candidate.x_chem must be a string"
        )

    if (
        isinstance(raw_candidate.get("x_T"), bool)
        or not isinstance(
            raw_candidate.get("x_T"),
            (int, float),
        )
    ):
        raise ValueError(
            "ReActAction.candidate.x_T must be a number"
        )

    for name in (
        "x_S",
        "x_MPR",
        "x_R",
    ):
        value = raw_candidate.get(name)

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
        ):
            raise ValueError(
                f"ReActAction.candidate.{name} must be an integer"
            )

    return ReActAction(
        action_type=ACTION_EVALUATE_CANDIDATE,
        reason=thought.strip(),
        candidate=dict(raw_candidate),
        final_candidate=None,
        observations_used=[],
    )

def _parse_observation_references(raw: List[Any]) -> List[Dict[str, Any]]:
    references: List[Dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"ReActAction.observations_used[{index}] must be an object")
        if set(item) != {"iteration", "fields"}:
            raise ValueError(
                f"ReActAction.observations_used[{index}] must contain exactly iteration and fields"
            )
        iteration = item.get("iteration")
        if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
            raise ValueError(
                f"ReActAction.observations_used[{index}].iteration must be a nonnegative integer"
            )
        fields = item.get("fields")
        if (
            not isinstance(fields, list)
            or not fields
            or not all(isinstance(field_name, str) and field_name for field_name in fields)
        ):
            raise ValueError(
                f"ReActAction.observations_used[{index}].fields must be a non-empty string list"
            )
        if len(set(fields)) != len(fields):
            raise ValueError(
                f"ReActAction.observations_used[{index}].fields must not contain duplicates"
            )
        references.append({"iteration": iteration, "fields": list(fields)})
    return references


# 목적: hybrid policy JSON을 strategy selection dataclass로 검증한다.
# 입력: backend가 반환한 JSON object.
# 출력: HybridPolicyOutput.
def parse_hybrid_policy_output(payload: Mapping[str, Any]) -> HybridPolicyOutput:
    """Parse a hybrid policy output."""
    _require_mapping(payload, "HybridPolicyOutput")
    selected_strategy = payload.get("selected_strategy")
    if selected_strategy not in HYBRID_STRATEGIES:
        raise ValueError(f"HybridPolicyOutput.selected_strategy must be one of {HYBRID_STRATEGIES}")
    rationale = payload.get("rationale", payload.get("reason"))
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("HybridPolicyOutput.rationale must be a non-empty string")
    strategy_args = payload.get("strategy_args", {})
    if strategy_args is None:
        strategy_args = {}
    if not isinstance(strategy_args, Mapping):
        raise ValueError("HybridPolicyOutput.strategy_args must be an object")
    recommendation = payload.get("optimizer_recommendation")
    if recommendation is not None and not isinstance(recommendation, str):
        raise ValueError("HybridPolicyOutput.optimizer_recommendation must be a string or null")
    return HybridPolicyOutput(
        selected_strategy=str(selected_strategy),
        rationale=rationale,
        strategy_args=dict(strategy_args),
        optimizer_recommendation=recommendation,
    )


# 목적: required candidate key를 가진 object를 strict하게 검증한다.
# 입력: raw candidate object.
# 출력: normalized candidate dict와 optional reason.
def _parse_candidate_object(
    raw_candidate: Any,
    require_proposal_reasoning: bool = False,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(raw_candidate, Mapping):
        raise ValueError("candidate must be an object")
    proposal_fields = {
        "rationale", "evidence_references", "search_role",
        "expected_effects", "tradeoffs", "uncertainty",
    }
    allowed = set(DESIGN_VARIABLES) | (proposal_fields if require_proposal_reasoning else set())
    unexpected = [key for key in raw_candidate if key not in allowed]
    if unexpected:
        raise ValueError(f"candidate contains unexpected keys: {unexpected}")
    missing = [key for key in DESIGN_VARIABLES if key not in raw_candidate]
    if missing:
        raise ValueError(f"candidate is missing required keys: {missing}")
    chemistry = _normalize_chemistry(raw_candidate["x_chem"])
    resolved = resolved_design_variable_bounds(variable_bounds)
    thickness = _parse_thickness(raw_candidate["x_T"], chemistry, resolved)
    parsed: Dict[str, Any] = {
        "x_chem": chemistry,
        "x_T": thickness,
    }
    for key in INTEGER_BOUNDS:
        lower, upper = int(resolved[key]["lower"]), int(resolved[key]["upper"])
        parsed[key] = _parse_integer(raw_candidate[key], lower, upper, key)
    if not require_proposal_reasoning:
        return parsed
    rationale = raw_candidate.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("candidate.rationale must be a non-empty string")
    parsed["rationale"] = rationale
    parsed["evidence_references"] = _required_string_list(
        raw_candidate.get("evidence_references"), "candidate.evidence_references", allow_empty=False
    )
    search_role = raw_candidate.get("search_role")
    if not isinstance(search_role, str) or not search_role.strip():
        raise ValueError("candidate.search_role must be a non-empty string")
    parsed["search_role"] = search_role
    parsed["expected_effects"] = _required_string_list(
        raw_candidate.get("expected_effects"), "candidate.expected_effects", allow_empty=False
    )
    parsed["tradeoffs"] = _required_string_list(
        raw_candidate.get("tradeoffs"), "candidate.tradeoffs", allow_empty=False
    )
    parsed["uncertainty"] = _required_string_list(
        raw_candidate.get("uncertainty"), "candidate.uncertainty", allow_empty=False
    )
    return parsed


def _required_string_list(value: Any, field: str, allow_empty: bool) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must be non-empty")
    return list(value)


# 목적: fixed_variables가 허용 변수만 포함하고 값이 bounds에 맞는지 검증한다.
# 입력: raw fixed_variables object.
# 출력: repaired fixed_variables dict.
def _parse_fixed_variables(
    raw_fixed: Any,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    if not isinstance(raw_fixed, Mapping):
        raise ValueError("fixed_variables must be an object")
    unknown = [key for key in raw_fixed if key not in DESIGN_VARIABLES]
    if unknown:
        raise ValueError(f"fixed_variables contains unknown keys: {unknown}")
    fixed = dict(raw_fixed)
    if "x_chem" in fixed:
        fixed["x_chem"] = _normalize_chemistry(fixed["x_chem"])
    resolved = resolved_design_variable_bounds(variable_bounds)
    if "x_T" in fixed:
        if "x_chem" in fixed:
            fixed["x_T"] = _parse_thickness(
                fixed["x_T"], fixed.get("x_chem"), resolved
            )
        else:
            numeric = _as_float(fixed["x_T"], "x_T")
            chemistry_bounds = resolved["x_T"]["bounds_by_chemistry"]
            shared_lower = max(
                float(bounds["lower"]) for bounds in chemistry_bounds.values()
            )
            shared_upper = min(
                float(bounds["upper"]) for bounds in chemistry_bounds.values()
            )
            if shared_lower > shared_upper:
                raise ValueError(
                    "fixed_variables.x_T cannot be used while x_chem remains free "
                    "because the allowed chemistries have no shared x_T range"
                )
            if numeric < shared_lower or numeric > shared_upper:
                raise ValueError(
                    f"fixed_variables.x_T={numeric!r} is not valid for every "
                    "allowed free x_chem; shared bounds are "
                    f"[{shared_lower}, {shared_upper}]"
                )
            fixed["x_T"] = float(numeric)
    for key in INTEGER_BOUNDS:
        lower, upper = int(resolved[key]["lower"]), int(resolved[key]["upper"])
        if key in fixed:
            fixed[key] = _parse_integer(fixed[key], lower, upper, key)
    return fixed, warnings


# 목적: free_variables 리스트가 design variable name만 포함하는지 검증한다.
# 입력: raw free_variables와 fixed_variables.
# 출력: 중복 없는 free variable 리스트.
def _parse_free_variables(raw_free: Any, fixed_variables: Mapping[str, Any]) -> List[str]:
    if raw_free is None:
        raise ValueError("free_variables must be provided explicitly")
    if not isinstance(raw_free, list):
        raise ValueError("free_variables must be a list")
    free_variables = []
    for item in raw_free:
        if not isinstance(item, str) or item not in DESIGN_VARIABLES:
            raise ValueError(f"free_variables contains invalid variable name: {item!r}")
        if item in fixed_variables:
            raise ValueError(f"free_variables cannot include fixed variable: {item}")
        if item in free_variables:
            raise ValueError(f"free_variables contains duplicate variable name: {item}")
        free_variables.append(item)
    return free_variables


def _validate_variable_partition(
    fixed_variables: Mapping[str, Any],
    free_variables: List[str],
) -> None:
    """Require fixed/free variables to form one complete disjoint partition."""
    if not free_variables:
        raise ValueError("VariableFixingOutput must leave at least one free variable")
    fixed_names = set(fixed_variables)
    free_names = set(free_variables)
    overlap = sorted(fixed_names & free_names)
    if overlap:
        raise ValueError(f"fixed_variables and free_variables overlap: {overlap}")
    missing = [key for key in DESIGN_VARIABLES if key not in fixed_names and key not in free_names]
    if missing:
        raise ValueError(f"fixed_variables and free_variables must be exhaustive; missing: {missing}")
    extra = sorted((fixed_names | free_names) - set(DESIGN_VARIABLES))
    if extra:
        raise ValueError(f"fixed_variables and free_variables contain unknown keys: {extra}")


def _parse_warnings(raw_warnings: Any) -> List[str]:
    """Parse explicit warning strings without inventing any."""
    if raw_warnings is None:
        raise ValueError("warnings must be provided explicitly")
    if not isinstance(raw_warnings, list):
        raise ValueError("warnings must be a list")
    warnings: List[str] = []
    for item in raw_warnings:
        if not isinstance(item, str):
            raise ValueError("warnings entries must be strings")
        warnings.append(item)
    return warnings


# 목적: schema root가 JSON object인지 검증한다.
# 입력: payload와 schema 이름.
# 출력: 없음.
def _require_mapping(payload: Mapping[str, Any], schema_name: str) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{schema_name} must be a JSON object")


# 목적: chemistry 값을 표준 문자열로 정규화한다.
# 입력: raw chemistry.
# 출력: 표준 chemistry 문자열.
def _normalize_chemistry(value: Any) -> str:
    chemistry = str(value).strip().upper()
    if chemistry not in CHEMISTRIES:
        raise ValueError(f"Unknown chemistry: {value!r}. Expected one of {CHEMISTRIES}")
    return chemistry


# 목적: fixed thickness를 chemistry별 bounds 또는 전체 bounds로 검증한다.
# 입력: raw thickness와 optional chemistry.
# 출력: bounds 안의 float.
def _parse_thickness(
    value: Any, chemistry: Optional[Any],
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> float:
    numeric = _as_float(value, "x_T")
    resolved = resolved_design_variable_bounds(variable_bounds)
    thickness_bounds = resolved["x_T"]["bounds_by_chemistry"]
    if chemistry is not None:
        item = thickness_bounds[_normalize_chemistry(chemistry)]
        lower, upper = item["lower"], item["upper"]
    else:
        lower = min(bounds["lower"] for bounds in thickness_bounds.values())
        upper = max(bounds["upper"] for bounds in thickness_bounds.values())
    if numeric < lower or numeric > upper:
        raise ValueError(f"x_T={numeric!r} is outside the bounds [{lower}, {upper}]")
    return float(numeric)


# 목적: integer variable 값을 정수와 bounds로 검증한다.
# 입력: raw value, bounds, 변수명.
# 출력: int 값.
def _parse_integer(value: Any, lower: int, upper: int, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    numeric = _as_float(value, name)
    if not numeric.is_integer():
        raise ValueError(f"{name} must be an integer, got {value!r}")
    parsed = int(numeric)
    if parsed < lower or parsed > upper:
        raise ValueError(f"{name}={parsed!r} is outside the bounds [{lower}, {upper}]")
    return parsed


# 목적: raw 값을 float로 변환하고 실패를 명확히 만든다.
# 입력: raw value와 변수명.
# 출력: float.
def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
