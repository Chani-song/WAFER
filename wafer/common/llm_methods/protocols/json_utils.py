"""JSON extraction helpers for production LLM method responses."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, Dict, List, Mapping, Optional, Tuple


def extract_json_object(raw: str) -> Dict[str, Any]:
    """Extract exactly one JSON object from an LLM response string."""
    text = raw.strip()
    if not text:
        raise ValueError(_no_json_message(raw))

    exact_result = _load_exact_object(text)
    if exact_result is not None:
        return exact_result

    spans = _find_balanced_object_spans(text)
    parsed_objects: List[Dict[str, Any]] = []
    parse_errors: List[str] = []
    for start, end in spans:
        snippet = text[start:end]
        try:
            value = json.loads(snippet)
        except JSONDecodeError as exc:
            parse_errors.append(f"{exc.msg} at position {exc.pos}")
            continue
        if isinstance(value, Mapping):
            parsed_objects.append(dict(value))
        else:
            parse_errors.append("extracted JSON value is not an object")

    if len(parsed_objects) > 1:
        raise ValueError("Multiple JSON objects found in LLM response; expected exactly one.")
    if len(parsed_objects) == 1 and parse_errors:
        raise ValueError(
            "Malformed JSON object candidate found in LLM response: "
            f"{parse_errors[0]}. Response preview: {_response_preview(raw)}"
        )
    if len(parsed_objects) == 1:
        return parsed_objects[0]
    if parse_errors:
        raise ValueError(
            "Could not parse JSON object from LLM response: "
            f"{parse_errors[0]}. Response preview: {_response_preview(raw)}"
        )
    raise ValueError(_no_json_message(raw))


def _load_exact_object(text: str) -> Optional[Dict[str, Any]]:
    try:
        loaded = json.loads(text)
    except JSONDecodeError:
        return None
    if not isinstance(loaded, Mapping):
        raise ValueError("LLM output must be a JSON object")
    return dict(loaded)


def _find_balanced_object_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escaped = False

    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, index + 1))
                start = None

    return spans


def _no_json_message(raw: str) -> str:
    return f"No JSON object could be extracted from LLM response. Response preview: {_response_preview(raw)}"


def _response_preview(raw: str) -> str:
    return raw[:300].replace("\n", "\\n")
