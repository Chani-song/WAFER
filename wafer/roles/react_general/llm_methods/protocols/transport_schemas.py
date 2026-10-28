"""Transport-only JSON schemas: syntax and types, never engineering decisions."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from .schemas import (
    DESIGN_VARIABLES,
    REACT_ACTION_JSON_SCHEMA,
    REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA,
    WARM_START_JSON_SCHEMA,
)

_TRANSPORT_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "oneOf", "anyOf",
}


def _syntax_and_types_only(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key not in _TRANSPORT_KEYWORDS:
                continue
            if key == "properties" and isinstance(item, Mapping):
                result[key] = {
                    field_name: _syntax_and_types_only(field_schema)
                    for field_name, field_schema in item.items()
                }
            else:
                result[key] = _syntax_and_types_only(item)
        return result
    if isinstance(value, list):
        return [_syntax_and_types_only(item) for item in value]
    return copy.deepcopy(value)


WARM_START_TRANSPORT_SCHEMA: Dict[str, Any] = _syntax_and_types_only(WARM_START_JSON_SCHEMA)

VARIABLE_FIXING_TRANSPORT_SCHEMA: Dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["free_variables", "fixed_variables", "variable_assessments", "rationale", "warnings"],
    "properties": {
        "free_variables": {"type": "array", "items": {"enum": list(DESIGN_VARIABLES)}},
        "fixed_variables": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "x_chem": {"type": "string"}, "x_T": {"type": "number"},
                "x_S": {"type": "integer"}, "x_MPR": {"type": "integer"}, "x_R": {"type": "integer"},
            },
        },
        "variable_assessments": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["variable", "source_value", "decision", "justification", "confidence"],
                "properties": {
                    "variable": {"enum": list(DESIGN_VARIABLES)},
                    "source_value": {"anyOf": [{"type": "string"}, {"type": "number"}]},
                    "decision": {"enum": ["fix", "free"]},
                    "justification": {"type": "string"},
                    "confidence": {"enum": ["low", "medium", "high"]},
                },
            },
        },
        "rationale": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

REACT_ENVIRONMENT_TRANSPORT_SCHEMA: Dict[str, Any] = _syntax_and_types_only(
    REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA
)


# Protocol-contract consistency fix, 2026-08-18.
#
# The public ReAct schema/parser already requires a non-empty
# public thought. The transport-only projection previously removed
# minLength, allowing thought="" through strict schema decoding and
# causing a downstream parser failure.
#
# This restores only the pre-existing serialization contract.
# It does not constrain any engineering variable, bound, objective,
# feasibility decision, or candidate value.
def _restore_nonempty_react_thought_transport(value: Any) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")

        if isinstance(properties, dict):
            thought = properties.get("thought")

            if (
                isinstance(thought, dict)
                and thought.get("type") == "string"
            ):
                thought["minLength"] = 1
                thought["maxLength"] = 2048

        for child in value.values():
            _restore_nonempty_react_thought_transport(child)

    elif isinstance(value, list):
        for child in value:
            _restore_nonempty_react_thought_transport(child)


_restore_nonempty_react_thought_transport(
    REACT_ENVIRONMENT_TRANSPORT_SCHEMA
)
REACT_V3_3_4_TRANSPORT_SCHEMA: Dict[str, Any] = _syntax_and_types_only(
    REACT_ACTION_JSON_SCHEMA
)

COMMON_STRATEGY_TRANSPORT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "WarmStartOutput": WARM_START_TRANSPORT_SCHEMA,
    "VariableFixingOutput": VARIABLE_FIXING_TRANSPORT_SCHEMA,
    "ReActAction": REACT_ENVIRONMENT_TRANSPORT_SCHEMA,
}


def strategy_transport_schemas(prompt_version: str) -> Dict[str, Dict[str, Any]]:
    """Return transport-only schemas matching the selected public protocol."""
    schemas = copy.deepcopy(COMMON_STRATEGY_TRANSPORT_SCHEMAS)
    if prompt_version != "v3_3_6_evaluate_only_react":
        schemas["ReActAction"] = copy.deepcopy(REACT_V3_3_4_TRANSPORT_SCHEMA)
    return schemas
