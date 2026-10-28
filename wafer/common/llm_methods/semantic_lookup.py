"""Bounded LLM-directed BatPaC-manual lookup phase for semantic level 3."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from llm_methods.backends.base import LLMJSONExtractionError
from llm_methods.manual_lookup import lookup_batpac_manual

LEVEL3_NAME = "level3_manual_lookup"


def _scenario_name(context: Mapping[str, Any]) -> str:
    changed = context.get("changed_condition", context.get("target_condition"))
    if isinstance(changed, Mapping):
        return str(changed.get("name", "unknown"))
    return "unknown"


def _compact_source_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "previous_condition": copy.deepcopy(context.get("previous_condition")),
        "previous_best_design": copy.deepcopy(context.get("previous_best_design")),
        "previous_best_result": copy.deepcopy(context.get("previous_best_result")),
        "changed_condition": copy.deepcopy(context.get("changed_condition")),
        "structured_condition_changes": copy.deepcopy(
            context.get("structured_condition_changes")
        ),
        "previous_best_reevaluated_under_changed_condition": copy.deepcopy(
            context.get("previous_best_reevaluated_under_changed_condition")
        ),
        "objective": copy.deepcopy(context.get("objective")),
        "constraints": copy.deepcopy(context.get("constraints")),
        "variable_bounds": copy.deepcopy(context.get("variable_bounds")),
    }


def _lookup_messages(
    context: Mapping[str, Any],
    strategy: str,
    lookup_ledger: Sequence[Mapping[str, Any]],
    remaining_lookups: int,
) -> List[Dict[str, str]]:
    prior = [
        {
            "lookup_index": item.get("lookup_index"),
            "query": item.get("query"),
            "retrieved_chunks": [
                {
                    "chunk_id": result.get("chunk_id"),
                    "section": result.get("section"),
                    "pages": result.get("pages"),
                    "text": result.get("text"),
                }
                for result in (item.get("lookup_result", {}).get("results") or [])
            ],
        }
        for item in lookup_ledger
    ]
    payload = {
        "task": (
            "Decide whether one additional BatPaC-manual lookup would materially improve "
            f"the upcoming {strategy} redesign decision."
        ),
        "strategy": strategy,
        "remaining_manual_lookups": int(remaining_lookups),
        "source_and_target_context": _compact_source_context(context),
        "level2_engineering_reference": context.get("semantic_information", ""),
        "previous_manual_lookups": prior,
        "instructions": [
            "Return exactly one JSON object and no surrounding prose.",
            "Use action=lookup_manual only for a focused question whose answer is not already supplied.",
            "Use action=proceed when the supplied information is sufficient or another lookup would be redundant.",
            "A lookup query should target BatPaC structure, equations, variable coupling, chemistry data, constraints, or cost mechanisms.",
            "Do not ask the manual for the hidden target optimum or for an unevaluated candidate result.",
            "The manual is authoritative reference information for this experiment, while BatPaC evaluations remain authoritative for candidate outcomes.",
        ],
        "output_schema": {
            "oneOf": [
                {
                    "action": "lookup_manual",
                    "query": "one focused non-empty manual question",
                    "rationale": "brief reason the lookup is useful",
                },
                {
                    "action": "proceed",
                    "rationale": "brief reason no further lookup is needed",
                },
            ]
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "You control a bounded documentation lookup phase before an EV battery-pack "
                "redesign decision. Return strict JSON only."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _parse_action(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("ManualLookupAction must be a JSON object")
    action = str(payload.get("action", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    if action not in {"lookup_manual", "proceed"}:
        raise ValueError("ManualLookupAction.action must be lookup_manual or proceed")
    if not rationale:
        raise ValueError("ManualLookupAction.rationale must be non-empty")
    result = {"action": action, "rationale": rationale}
    if action == "lookup_manual":
        query = " ".join(str(payload.get("query", "")).split()).strip()
        if not query:
            raise ValueError("lookup_manual requires a non-empty query")
        if len(query) > 500:
            raise ValueError("lookup_manual query must be at most 500 characters")
        result["query"] = query
    return result


def _generate_action(
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    temperature: float,
    max_tokens: int,
    attempt_recorder: Any = None,
) -> Tuple[Dict[str, str], Any, float, int]:
    attempts = 3
    current_messages = [dict(message) for message in messages]
    last_error: Optional[Exception] = None
    for retry in range(attempts):
        started = time.perf_counter()
        try:
            response = backend.generate_json(
                current_messages,
                "ManualLookupAction",
                temperature=temperature,
                max_tokens=int(max_tokens),
            )
        except LLMJSONExtractionError as exc:
            elapsed = time.perf_counter() - started
            raw = exc.raw_assistant_content
            if attempt_recorder is not None:
                attempt_recorder(retry, current_messages, raw, exc, None, elapsed)
            if exc.finish_reason == "length" or retry + 1 >= attempts:
                raise
            last_error = exc
            current_messages.extend(
                _manual_lookup_repair_messages(raw, exc, messages)
            )
            continue
        elapsed = time.perf_counter() - started
        try:
            parsed = _parse_action(response)
            if attempt_recorder is not None:
                raw = (
                    getattr(backend, "last_message_content", None)
                    or getattr(backend, "last_raw_response_text", None)
                    or response
                )
                attempt_recorder(retry, current_messages, raw, None, parsed, elapsed)
            return parsed, response, elapsed, retry
        except ValueError as exc:
            last_error = exc
            raw = (
                getattr(backend, "last_message_content", None)
                or getattr(backend, "last_raw_response_text", None)
                or response
            )
            if not isinstance(raw, str):
                raw = json.dumps(raw, default=str)
            if attempt_recorder is not None:
                attempt_recorder(retry, current_messages, raw, exc, None, elapsed)
            if retry + 1 >= attempts:
                raise
            current_messages.extend(_manual_lookup_repair_messages(raw, exc, messages))
    raise RuntimeError("Manual lookup planning failed") from last_error


def _manual_lookup_repair_messages(
    raw: str,
    error: BaseException,
    lookup_state_messages: Sequence[Mapping[str, str]],
) -> List[Dict[str, str]]:
    """Build a strict repair turn without interpreting the invalid response."""
    schema = {
        "oneOf": [
            {
                "action": "lookup_manual",
                "query": "one focused non-empty manual question",
                "rationale": "brief reason the lookup is useful",
            },
            {
                "action": "proceed",
                "rationale": "brief reason no further lookup is needed",
            },
        ]
    }
    repair = {
        "instruction": (
            "Return exactly one JSON object matching ManualLookupAction. "
            "Do not return alternatives, commentary, markdown, or multiple objects."
        ),
        "extraction_error": str(error),
        "preserved_raw_assistant_response": raw,
        "allowed_action_schema": schema,
        "current_lookup_state": [dict(message) for message in lookup_state_messages],
    }
    return [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": json.dumps(repair, ensure_ascii=False)},
    ]


def _usage_snapshot(backend: Any) -> Dict[str, Any]:
    return {
        "usage": dict(getattr(backend, "last_usage", {}) or {}),
        "finish_reason": getattr(backend, "last_finish_reason", None),
        "latency_sec": getattr(backend, "last_latency_sec", None),
        "generation_kwargs": dict(getattr(backend, "last_generation_kwargs", {}) or {}),
        "request_model": getattr(backend, "last_request_model", None),
        "request_token_audit": dict(
            getattr(backend, "last_request_audit", {}) or {}
        ),
    }


def _write_trace(trace_recorder: Any, entry: Mapping[str, Any]) -> None:
    output_dir = getattr(trace_recorder, "output_dir", None)
    if output_dir is None:
        return
    path = Path(output_dir) / "manual_lookup_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _render_lookup_evidence(ledger: Sequence[Mapping[str, Any]]) -> str:
    if not ledger:
        return ""
    lines = ["", "BatPaC manual lookup evidence"]
    for entry in ledger:
        result = entry.get("lookup_result", {})
        lines.append(f"Lookup {entry.get('lookup_index')}: {entry.get('query')}")
        for chunk in result.get("results") or []:
            lines.append(
                f"[{chunk.get('chunk_id')} | {chunk.get('section')} | pages {chunk.get('pages')}]"
            )
            lines.append(str(chunk.get("text", "")).strip())
    lines.extend(
        [
            "",
            "The retrieved manual passages are reference information. BatPaC evaluations remain authoritative for objective values, constraints, and feasibility.",
        ]
    )
    return "\n".join(lines).strip()


def augment_context_with_manual_lookup(
    context: Mapping[str, Any],
    semantic_level: str,
    backend: Any,
    strategy: str,
    model_key: str,
    trace_recorder: Any = None,
    run_id: str = "manual_lookup",
    temperature: float = 0.0,
    max_tokens: int = 2048,
    strategy_completion_tokens: Optional[int] = None,
    max_lookups: int = 3,
    top_k: int = 3,
    max_return_tokens: int = 1800,
) -> Dict[str, Any]:
    """Run a bounded pre-strategy manual lookup phase for semantic level 3."""
    augmented = copy.deepcopy(dict(context))
    if semantic_level != LEVEL3_NAME:
        return augmented

    ledger: List[Dict[str, Any]] = []
    total_lookup_runtime = 0.0
    total_planner_runtime = 0.0
    for index in range(max(0, int(max_lookups))):
        messages = _lookup_messages(
            augmented,
            strategy=strategy,
            lookup_ledger=ledger,
            remaining_lookups=int(max_lookups) - index,
        )

        def make_attempt_recorder() -> Any:
            original_extraction_error: List[Optional[str]] = [None]

            def record_attempt(
                repair_attempt: int,
                attempt_messages: Sequence[Mapping[str, str]],
                raw_response: Any,
                error: Optional[BaseException],
                parsed_action: Optional[Mapping[str, str]],
                elapsed: float,
            ) -> None:
                if error is not None and original_extraction_error[0] is None:
                    original_extraction_error[0] = f"{type(error).__name__}: {error}"
                request_audit = dict(
                    getattr(backend, "last_request_audit", {}) or {}
                )
                strategy_limit = int(
                    strategy_completion_tokens
                    if strategy_completion_tokens is not None
                    else max_tokens
                )
                context_limit = int(request_audit.get("model_context_limit", 16384))
                enriched_audit = {
                    **request_audit,
                    "requested_strategy_completion_tokens": strategy_limit,
                    "requested_manual_lookup_completion_tokens": int(max_tokens),
                    "model_context_limit": context_limit,
                }
                metadata = _usage_snapshot(backend)
                metadata.update({
                    "requested_strategy_completion_tokens": strategy_limit,
                    "requested_manual_lookup_completion_tokens": int(max_tokens),
                    "model_context_limit": context_limit,
                    "request_token_audit": enriched_audit,
                })
                _write_trace(trace_recorder, {
                    "run_id": run_id,
                    "scenario": _scenario_name(augmented),
                    "strategy": strategy,
                    "semantic_level": semantic_level,
                    "model_key": model_key,
                    "lookup_index": index + 1,
                    "event_type": "manual_lookup_planner_attempt",
                    "repair_attempt": int(repair_attempt),
                    "original_extraction_error": original_extraction_error[0],
                    "extraction_error": (
                        f"{type(error).__name__}: {error}" if error is not None else None
                    ),
                    "messages": [dict(message) for message in attempt_messages],
                    "raw_response": raw_response,
                    "parsed_action": dict(parsed_action) if parsed_action is not None else None,
                    "planner_time_sec": elapsed,
                    "requested_strategy_completion_tokens": strategy_limit,
                    "requested_manual_lookup_completion_tokens": int(max_tokens),
                    "model_context_limit": context_limit,
                    "finish_reason": metadata.get("finish_reason"),
                    "usage": metadata.get("usage"),
                    "request_token_audit": enriched_audit,
                    "generation_metadata": metadata,
                })

            return record_attempt

        try:
            action, response, planner_time, retry = _generate_action(
                backend=backend,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                attempt_recorder=make_attempt_recorder(),
            )
        except Exception as exc:
            request_audit = dict(getattr(backend, "last_request_audit", {}) or {})
            generation_metadata = _usage_snapshot(backend)
            generation_metadata.update({
                "requested_strategy_completion_tokens": int(
                    strategy_completion_tokens if strategy_completion_tokens is not None else max_tokens
                ),
                "requested_manual_lookup_completion_tokens": int(max_tokens),
                "model_context_limit": int(request_audit.get("model_context_limit", 16384)),
            })
            generation_metadata["request_token_audit"] = {
                **request_audit,
                "requested_strategy_completion_tokens": generation_metadata[
                    "requested_strategy_completion_tokens"
                ],
                "requested_manual_lookup_completion_tokens": int(max_tokens),
                "model_context_limit": generation_metadata["model_context_limit"],
            }
            _write_trace(trace_recorder, {
                "run_id": run_id,
                "scenario": _scenario_name(augmented),
                "strategy": strategy,
                "semantic_level": semantic_level,
                "model_key": model_key,
                "lookup_index": index + 1,
                "messages": messages,
                "raw_response": (
                    getattr(backend, "last_message_content", None)
                    or getattr(backend, "last_raw_response_text", None)
                    or ""
                ),
                "event_type": "manual_lookup_failure",
                "failure": f"{type(exc).__name__}: {exc}",
                "generation_metadata": generation_metadata,
                "requested_strategy_completion_tokens": generation_metadata[
                    "requested_strategy_completion_tokens"
                ],
                "requested_manual_lookup_completion_tokens": int(max_tokens),
                "model_context_limit": generation_metadata["model_context_limit"],
            })
            raise
        if index == 0 and action["action"] == "proceed":
            forced_messages = list(messages) + [{
                "role": "user",
                "content": "A first manual lookup is mandatory. Return a model-generated lookup query and concise query rationale now; proceed is invalid for this request.",
            }]
            action, response, extra_time, retry = _generate_action(
                backend=backend,
                messages=forced_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                attempt_recorder=make_attempt_recorder(),
            )
            planner_time += extra_time
            messages = forced_messages
            if action["action"] == "proceed":
                raise ValueError("Level 3 requires at least one model-generated manual lookup query")
        total_planner_runtime += planner_time
        trace_entry: Dict[str, Any] = {
            "run_id": run_id,
            "scenario": _scenario_name(augmented),
            "strategy": strategy,
            "semantic_level": semantic_level,
            "model_key": model_key,
            "lookup_index": index + 1,
            "messages": messages,
            "raw_response": (
                getattr(backend, "last_message_content", None)
                or getattr(backend, "last_raw_response_text", None)
                or response
            ),
            "parsed_action": action,
            "planner_time_sec": planner_time,
            "retry_attempt": retry,
            "generation_metadata": _usage_snapshot(backend),
            "requested_strategy_completion_tokens": int(
                strategy_completion_tokens if strategy_completion_tokens is not None else max_tokens
            ),
            "requested_manual_lookup_completion_tokens": int(max_tokens),
            "model_context_limit": int(
                (getattr(backend, "last_request_audit", {}) or {}).get(
                    "model_context_limit", 16384
                )
            ),
        }
        trace_entry["generation_metadata"].update({
            "requested_strategy_completion_tokens": trace_entry[
                "requested_strategy_completion_tokens"
            ],
            "requested_manual_lookup_completion_tokens": int(max_tokens),
            "model_context_limit": trace_entry["model_context_limit"],
        })
        trace_entry["generation_metadata"]["request_token_audit"].update({
            "requested_strategy_completion_tokens": trace_entry[
                "requested_strategy_completion_tokens"
            ],
            "requested_manual_lookup_completion_tokens": int(max_tokens),
            "model_context_limit": trace_entry["model_context_limit"],
        })
        if action["action"] == "proceed":
            trace_entry["event_type"] = "manual_lookup_proceed"
            _write_trace(trace_recorder, trace_entry)
            break

        lookup_started = time.perf_counter()
        lookup_result = lookup_batpac_manual(
            action["query"],
            top_k=top_k,
            max_return_tokens=max_return_tokens,
        )
        lookup_time = time.perf_counter() - lookup_started
        total_lookup_runtime += lookup_time
        entry = {
            "lookup_index": index + 1,
            "query": action["query"],
            "rationale": action["rationale"],
            "lookup_result": lookup_result,
            "lookup_time_sec": lookup_time,
            "planner_time_sec": planner_time,
            "generation_metadata": _usage_snapshot(backend),
        }
        ledger.append(entry)
        trace_entry.update(
            {
                "event_type": "manual_lookup",
                "tool_call": {"name": "lookup_batpac_manual", "arguments": {"query": action["query"]}},
                "tool_result": lookup_result,
                "lookup_time_sec": lookup_time,
            }
        )
        _write_trace(trace_recorder, trace_entry)

    evidence = _render_lookup_evidence(ledger)
    base_content = str(augmented.get("semantic_information", "")).strip()
    augmented["semantic_information"] = (
        base_content + ("\n\n" + evidence if evidence else "")
    ).strip()
    augmented["manual_lookup_ledger"] = ledger
    augmented["manual_lookup_metrics"] = {
        "lookup_count": len(ledger),
        "maximum_lookup_count": int(max_lookups),
        "top_k": int(top_k),
        "maximum_return_tokens_per_lookup": int(max_return_tokens),
        "requested_manual_lookup_completion_tokens": int(max_tokens),
        "returned_estimated_tokens": sum(
            int(entry["lookup_result"].get("returned_estimated_tokens", 0))
            for entry in ledger
        ),
        "lookup_runtime_sec": total_lookup_runtime,
        "planner_runtime_sec": total_planner_runtime,
        "batpac_evaluation_budget_consumed": 0,
    }
    artifact = dict(augmented.get("semantic_artifact") or {})
    artifact["manual_lookup"] = copy.deepcopy(augmented["manual_lookup_metrics"])
    augmented["semantic_artifact"] = artifact
    return augmented
