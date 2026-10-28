"""Warm-start strategy driven by structured LLM output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from llm_methods.backends.base import LLMBackend, LLMJSONExtractionError
from llm_methods.prompts.builder import PromptBuilder
from llm_methods.protocols.schemas import WarmStartOutput, parse_warm_start_output
from llm_methods.traces.recorder import TraceRecorder
from llm_methods.runtime import solver_description
from optimizers.core.domain import INTEGER_BOUNDS, THICKNESS_BOUNDS_BY_CHEMISTRY


@dataclass
class WarmStartStrategy:
    """Generate optimizer initial candidates."""

    backend: LLMBackend
    prompt_builder: PromptBuilder = PromptBuilder()
    temperature: float = 0.0
    max_tokens: int = 1024
    parse_retries: int = 1

    # 목적: context 기반 warm-start 후보를 LLM backend에서 생성한다.
    # 입력: redesign context, semantic level, model key, 후보 개수, 선택적 trace recorder.
    # 출력: 검증된 WarmStartOutput.
    def run(
        self,
        context: Mapping[str, Any],
        semantic_level: str = "minimal",
        model_key: str = "qwen35_4b",
        k: int = 3,
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        minimum_count: int = 1,
        maximum_count: Optional[int] = None,
        model_selected_count: bool = False,
        trace_recorder: Optional[TraceRecorder] = None,
        run_id: str = "llm_strategy_smoke",
    ) -> WarmStartOutput:
        """Run warm-start strategy."""
        total = int(total_budget if total_budget is not None else max(k + 1, 2))
        maximum = int(maximum_count if maximum_count is not None else min(5, total - 1))
        solver = (
            solver_description(optimizer_name)
            if optimizer_name != "unspecified"
            else {"description": "Unspecified numerical search.", "initialization": "Uses supplied initial points."}
        )
        messages = self.prompt_builder.warm_start_messages(
            context,
            semantic_level,
            model_key,
            k,
            optimizer_name=optimizer_name,
            total_budget=total,
            minimum_count=minimum_count,
            maximum_count=maximum,
            solver_description=solver["description"],
            solver_initialization_behavior=solver["initialization"],
            model_selected_count=model_selected_count,
        )
        output, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning = _generate_parse_with_retries(
            backend=self.backend,
            messages=messages,
            schema_name="WarmStartOutput",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            parse_retries=self.parse_retries,
            parser=lambda payload: parse_warm_start_output(
                payload,
                minimum_count=minimum_count if model_selected_count else k,
                maximum_count=maximum if model_selected_count else k,
                total_budget=total,
                fixed_count=None if model_selected_count else k,
                variable_bounds=context.get("variable_bounds"),
            ),
            trace_recorder=trace_recorder,
            run_id=run_id,
            scenario=_scenario_name(context),
            strategy="warm_start",
            semantic_level=semantic_level,
            model_key=model_key,
            retry_context={
                "minimum_warm_start_count": minimum_count if model_selected_count else k,
                "maximum_warm_start_count": maximum if model_selected_count else k,
                "total_budget": total,
                "chemistry_dependent_x_T_bounds": (
                    context.get("variable_bounds", {}).get("x_T", {}).get(
                        "bounds_by_chemistry", THICKNESS_BOUNDS_BY_CHEMISTRY
                    )
                ),
                "integer_bounds": {
                    name: [context.get("variable_bounds", {}).get(name, {}).get("lower", bounds[0]),
                           context.get("variable_bounds", {}).get(name, {}).get("upper", bounds[1])]
                    for name, bounds in INTEGER_BOUNDS.items()
                },
            },
        )
        if trace_recorder is not None:
            trace_recorder.record_step(
                run_id=run_id,
                scenario=_scenario_name(context),
                strategy="warm_start",
                optimizer=None,
                semantic_level=semantic_level,
                model_key=model_key,
                iteration=0,
                messages=messages,
                raw_response=raw_response,
                parsed_output={
                    "warm_start_count": output.warm_start_count,
                    "allocation_rationale": output.allocation_rationale,
                    "candidates": output.candidates,
                },
                llm_time_sec=llm_time_sec,
                retry_attempt=retry_attempt,
                raw_reasoning=raw_reasoning,
                generation_metadata=_generation_metadata(self.backend),
            )
        return output


# 목적: backend 호출 시간을 측정해 trace에 남길 값을 만든다.
# 입력: backend, messages, schema name, generation 설정.
# 출력: response dict와 elapsed seconds.
def _generate_with_timing(
    backend: LLMBackend,
    messages: Any,
    schema_name: str,
    temperature: float,
    max_tokens: int,
) -> Any:
    import time

    started = time.perf_counter()
    response = backend.generate_json(messages, schema_name, temperature=temperature, max_tokens=max_tokens)
    return response, time.perf_counter() - started


def _generate_parse_with_retries(
    backend: LLMBackend,
    messages: Any,
    schema_name: str,
    temperature: float,
    max_tokens: int,
    parse_retries: int,
    parser: Any,
    trace_recorder: Optional[TraceRecorder],
    run_id: str,
    scenario: str,
    strategy: str,
    semantic_level: str,
    model_key: str,
    retry_context: Optional[Mapping[str, Any]] = None,
    repair_message_builder: Optional[Any] = None,
) -> Any:
    # A caller cannot accidentally turn a malformed response into an unbounded run.
    attempts = max(1, min(6, int(parse_retries) + 1))
    last_error: Optional[Exception] = None
    attempt_messages = list(messages)
    for retry_attempt in range(attempts):
        import time

        generation_started = time.perf_counter()
        try:
            response, llm_time_sec = _generate_with_timing(
                backend,
                attempt_messages,
                schema_name,
                temperature,
                max_tokens,
            )
        except LLMJSONExtractionError as exc:
            llm_time_sec = time.perf_counter() - generation_started
            last_error = exc
            raw_response = exc.raw_assistant_content
            raw_reasoning = getattr(backend, "last_message_reasoning", None)
            parse_error = (
                f"JSON extraction failed for {exc.schema_name}: "
                f"{exc.extraction_error}"
            )
            if exc.finish_reason:
                parse_error += f"; finish_reason={exc.finish_reason}"
            logical_iteration = int(
                (retry_context or {}).get("current_iteration", retry_attempt)
            )
            if trace_recorder is not None:
                trace_recorder.record_step(
                    run_id=run_id,
                    scenario=scenario,
                    strategy=strategy,
                    optimizer=None,
                    semantic_level=semantic_level,
                    model_key=model_key,
                    iteration=logical_iteration,
                    messages=attempt_messages,
                    raw_response=raw_response,
                    parsed_output=None,
                    llm_time_sec=llm_time_sec,
                    parse_error=parse_error,
                    retry_attempt=retry_attempt,
                    raw_reasoning=raw_reasoning,
                    generation_metadata=_generation_metadata(backend),
                    generation_failure_kind=(
                        "completion_truncated"
                        if exc.finish_reason == "length"
                        else "json_extraction_failed"
                    ),
                    **_react_forbidden_trace_fields(raw_response, parse_error, retry_context),
                )
            if retry_attempt + 1 >= attempts:
                raise
            attempt_messages = (
                repair_message_builder(raw_response, parse_error, retry_context)
                if repair_message_builder is not None
                else _retry_messages(
                    attempt_messages, raw_response=raw_response,
                    parse_error=parse_error, schema_name=schema_name,
                    retry_context=retry_context,
                )
            )
            continue
        except RuntimeError as exc:
            if not _is_generation_format_error(exc):
                raise
            llm_time_sec = time.perf_counter() - generation_started
            last_error = exc
            raw_response = getattr(backend, "last_raw_response_text", None) or str(exc)
            raw_reasoning = getattr(backend, "last_message_reasoning", None)
            if trace_recorder is not None:
                trace_recorder.record_step(
                    run_id=run_id,
                    scenario=scenario,
                    strategy=strategy,
                    optimizer=None,
                    semantic_level=semantic_level,
                    model_key=model_key,
                    iteration=int(
                        (retry_context or {}).get("current_iteration", retry_attempt)
                    ),
                    messages=attempt_messages,
                    raw_response=raw_response,
                    parsed_output=None,
                    llm_time_sec=llm_time_sec,
                    parse_error=str(exc),
                    retry_attempt=retry_attempt,
                    raw_reasoning=raw_reasoning,
                    generation_metadata=_generation_metadata(backend),
                    **_react_forbidden_trace_fields(raw_response, str(exc), retry_context),
                )
            if retry_attempt + 1 >= attempts:
                raise
            attempt_messages = (
                repair_message_builder(raw_response, str(exc), retry_context)
                if repair_message_builder is not None
                else _retry_messages(
                    attempt_messages, raw_response=raw_response,
                    parse_error=str(exc), schema_name=schema_name,
                    retry_context=retry_context,
                )
            )
            continue
        raw_response = (
            getattr(backend, "last_message_content", None)
            or getattr(backend, "last_raw_response_text", None)
            or response
        )
        raw_reasoning = getattr(backend, "last_message_reasoning", None)
        try:
            output = parser(response)
            return output, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning
        except ValueError as exc:
            last_error = exc
            setattr(
                exc,
                "jmd_raw_response",
                raw_response,
            )
            setattr(
                exc,
                "jmd_retry_attempt",
                int(retry_attempt),
            )
            setattr(
                exc,
                "jmd_generation_attempt_count",
                int(retry_attempt) + 1,
            )
            if trace_recorder is not None:
                trace_recorder.record_step(
                    run_id=run_id,
                    scenario=scenario,
                    strategy=strategy,
                    optimizer=None,
                    semantic_level=semantic_level,
                    model_key=model_key,
                    iteration=int(
                        (retry_context or {}).get("current_iteration", retry_attempt)
                    ),
                    messages=attempt_messages,
                    raw_response=raw_response,
                    parsed_output=None,
                    llm_time_sec=llm_time_sec,
                    parse_error=str(exc),
                    retry_attempt=retry_attempt,
                    raw_reasoning=raw_reasoning,
                    generation_metadata=_generation_metadata(backend),
                    **_react_forbidden_trace_fields(raw_response, str(exc), retry_context),
                )
            if retry_attempt + 1 >= attempts:
                raise
            attempt_messages = (
                repair_message_builder(raw_response, str(exc), retry_context)
                if repair_message_builder is not None
                else _retry_messages(
                    attempt_messages, raw_response=raw_response,
                    parse_error=str(exc), schema_name=schema_name,
                    retry_context=retry_context,
                )
            )
    raise RuntimeError(f"{schema_name} parsing failed") from last_error


def _react_forbidden_trace_fields(
    raw_response: Any,
    parse_error: str,
    retry_context: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Attach exact duplicate-exclusion evidence without changing parsing behavior."""
    if "duplicates an already evaluated candidate" not in str(parse_error):
        return {}
    forbidden = list((retry_context or {}).get("forbidden_candidates") or [])
    payload = raw_response
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            payload = {}
    duplicate = payload.get("candidate") if isinstance(payload, Mapping) else None
    matching_index = next(
        (index for index, candidate in enumerate(forbidden) if duplicate == candidate),
        None,
    )
    return {
        "forbidden_candidates": forbidden,
        "matching_forbidden_candidate_index": matching_index,
    }


def _generation_metadata(backend: LLMBackend) -> Mapping[str, Any]:
    """Capture backend evidence without changing the backend return contract."""
    return {
        "usage": dict(getattr(backend, "last_usage", {}) or {}),
        "finish_reason": getattr(backend, "last_finish_reason", None),
        "latency_sec": getattr(backend, "last_latency_sec", None),
        "attempts": list(getattr(backend, "last_attempts", []) or []),
        "generation_kwargs": dict(getattr(backend, "last_generation_kwargs", {}) or {}),
        "model_path": getattr(backend, "last_model_path", None),
        "request_model": getattr(backend, "last_request_model", None),
        "seed": getattr(backend, "seed", None),
        "pre_request_token_audit": dict(getattr(backend, "last_request_audit", {}) or {}),
    }


def _is_generation_format_error(exc: RuntimeError) -> bool:
    """Return true only for recoverable backend output-format failures."""
    message = str(exc).lower()
    return (
        "not parseable" in message
        or "no json object could be extracted" in message
        or "configured fixed completion budget" in message
    )


def _retry_messages(
    messages: Any,
    raw_response: Any,
    parse_error: str,
    schema_name: str,
    retry_context: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Append explicit validation feedback for one bounded repair attempt."""
    import json

    repaired = [dict(message) for message in messages]
    if isinstance(raw_response, str):
        assistant_content = raw_response
    else:
        assistant_content = json.dumps(raw_response, sort_keys=True, default=str)
    repaired.append({"role": "assistant", "content": assistant_content})
    repair_context = dict(retry_context or {})
    response_object = raw_response
    if isinstance(raw_response, str):
        try:
            response_object = json.loads(raw_response)
        except (TypeError, ValueError):
            response_object = None
    if schema_name == "WarmStartOutput" and isinstance(response_object, Mapping):
        repair_context["rejected_warm_start_output"] = response_object
        rationale = response_object.get("allocation_rationale")
        if isinstance(rationale, str):
            repair_context["allocation_rationale_word_count"] = len(rationale.split())
    repaired.append(
        {
            "role": "user",
            "content": _retry_instruction(
                schema_name=schema_name,
                parse_error=parse_error,
                retry_context=repair_context,
            ),
        }
    )
    return repaired


def _retry_instruction(
    schema_name: str,
    parse_error: str,
    retry_context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build a schema-specific repair instruction for one bounded retry."""
    import json

    context_text = ""
    if retry_context:
        context_text = f" Repair context: {json.dumps(retry_context, sort_keys=True, default=str)}."
    base = (
        f"The previous response was invalid and failed {schema_name} validation with this exact error: "
        f"{parse_error}.{context_text} Return one complete corrected JSON object: exactly one "
        "complete replacement JSON object. "
        "Do not return prose, markdown, a partial patch, or an explanation."
    )
    if schema_name == "VariableFixingOutput":
        source = dict((retry_context or {}).get("source_candidate") or {})
        require_assessments = bool((retry_context or {}).get("require_assessments", False))
        assessment_guidance = (
            "variable_assessments, rationale, warnings. variable_assessments MUST be a JSON list, "
            "not an object, with exactly five complete objects ordered x_chem, x_T, x_S, x_MPR, x_R. "
            "Every assessment requires variable, source_value, decision, justification, and confidence. decision must be fix or free "
            "and agree with the partition. confidence must be low, medium, or high. source_value "
            f"must exactly equal the supplied source incumbent: {json.dumps(source, sort_keys=True)}. "
            if require_assessments else
            "rationale, warnings. "
        )
        return (
            f"{base} Required field order: free_variables, fixed_variables, "
            f"{assessment_guidance}"
            "free_variables and fixed_variables must be a disjoint exhaustive partition of "
            "x_chem, x_T, x_S, x_MPR, x_R, and at least one variable must remain free. "
            "Every fixed value must exactly equal the corresponding source-incumbent value. "
            "For each variable, give a concise variable-specific justification that balances search efficiency and redesign flexibility. "
            "All-free is allowed when all five assessments contain explicit variable-specific justifications; do not use a generic repeated sentence. "
            "Never copy the complete previous design into fixed_variables. "
            "rationale must be one or two concise sentences of 80 words or fewer. "
            "warnings must be [] unless a concrete uncertainty must be reported."
        )
    if schema_name == "ReActAction":
        candidate_error_guidance = ""
        observation_error_guidance = ""
        if "candidate must be an object" in parse_error:
            candidate_skeleton = {
                "action": "evaluate_candidate",
                "candidate": {
                    "x_chem": "<valid chemistry>",
                    "x_T": "<valid number>",
                    "x_S": "<valid integer>",
                    "x_MPR": "<valid integer>",
                    "x_R": "<valid integer>",
                },
                "observations_used": [],
                "rationale": "Test a bounded unevaluated candidate.",
                "variables_changed": ["x_chem", "x_T", "x_S", "x_MPR", "x_R"],
                "expected_effect": "Hypothesis to test with BatPaC.",
                "uncertainty": "The simulator result is unknown.",
            }
            candidate_error_guidance = (
                ' Include the top-level key "candidate". candidate must be a JSON object '
                "containing all five design variables x_chem, x_T, x_S, x_MPR, and x_R. "
                "Do not merely describe a candidate in rationale. Complete format skeleton "
                "(numerical values are examples, not recommendations): "
                f"{json.dumps(candidate_skeleton, sort_keys=False)}."
            )
        if "observations_used must contain at least one prior observation" in parse_error:
            available = list(
                (retry_context or {}).get("available_previous_observations", [])
            )
            observation_error_guidance = (
                " observations_used cannot be [] after iteration 0. Include at least one "
                'reference shaped exactly as {"iteration": 0, "fields": ["feasible", "cost"]}. '
                "Choose only an earlier iteration and non-empty fields present in the available "
                "previous observations. Available previous observations: "
                f"{json.dumps(available, sort_keys=True, default=str)}."
            )
        return (
            f"{base} Include observations_used, concise rationale, variables_changed, "
            "expected_effect as a testable hypothesis, and explicit uncertainty. "
            "If action is evaluate_candidate, candidate must not appear in evaluated_candidates. "
            "If action is finish, final_candidate must exactly match a valid previously evaluated candidate. "
            "Do not return the duplicate candidate identified by the validation error again."
            f"{candidate_error_guidance}"
            f"{observation_error_guidance}"
        )
    if schema_name == "WarmStartOutput":
        minimum = int((retry_context or {}).get("minimum_warm_start_count", 1))
        maximum = int((retry_context or {}).get("maximum_warm_start_count", 5))
        rationale_words = ""
        if "allocation_rationale" in parse_error and "60 words" in parse_error:
            count = (retry_context or {}).get("allocation_rationale_word_count")
            rationale_words = (
                f" The rejected allocation_rationale contained {count} words and exceeded the hard "
                "60-word limit." if count is not None else
                " The rejected allocation_rationale exceeded the hard 60-word limit."
            )
        template = {
            "warm_start_count": minimum,
            "allocation_rationale": "Brief allocation rationale.",
            "candidates": [{
                "x_chem": "<LFP or NMC811>", "x_T": TEMPLATE_NUMBER["x_T"], "x_S": TEMPLATE_NUMBER["x_S"],
                "x_MPR": TEMPLATE_NUMBER["x_MPR"], "x_R": TEMPLATE_NUMBER["x_R"],
                "rationale": "Brief candidate rationale.",
                "evidence_references": ["supplied_evidence_reference"],
                "search_role": "Distinct initialization role.",
                "expected_effects": ["Effect remains a hypothesis until evaluation."],
                "tradeoffs": ["Consumes one evaluation."],
                "uncertainty": ["Actual results require BatPaC evaluation."],
            }],
        }
        error_specific = ""
        rejected = (retry_context or {}).get("rejected_warm_start_output", {})
        strict_context = (
            " Preserve the complete candidate array and make every five-variable tuple distinct. "
            "The rejected candidates are: "
            f"{json.dumps(rejected.get('candidates', []), sort_keys=True, default=str)}. "
            "Use new in-bounds values rather than local clipping. Chemistry-dependent x_T bounds "
            f"and integer bounds are: {json.dumps({'x_T': (retry_context or {}).get('chemistry_dependent_x_T_bounds', {}), 'integers': (retry_context or {}).get('integer_bounds', {})}, sort_keys=True)}."
        )
        if "candidate.uncertainty must be a list of non-empty strings" in parse_error:
            error_specific += (
                " Replace every candidate's uncertainty value with a JSON array of one or more "
                "non-empty strings. Do not alter the engineering decision except where required "
                "to satisfy validation."
            )
        if "candidates must be distinct" in parse_error:
            error_specific += (
                " The rejected complete candidate list, including the duplicate candidate, is: "
                f"{json.dumps(rejected.get('candidates', []), sort_keys=True, default=str)}. "
                "Return all candidates with every five-variable tuple distinct. Do not silently "
                "delete a candidate when that would make candidates length differ from warm_start_count."
            )
        if "outside the bounds" in parse_error:
            error_specific += (
                " Use a new in-bounds value; do not clip locally. Chemistry-dependent x_T bounds "
                f"and integer bounds are: {json.dumps({'x_T': (retry_context or {}).get('chemistry_dependent_x_T_bounds', {}), 'integers': (retry_context or {}).get('integer_bounds', {})}, sort_keys=True)}."
            )
        return (
            f"{base}{rationale_words} warm_start_count must be an integer from {minimum} through "
            f"{maximum}, must be less than the total budget, and must equal len(candidates). "
            "Candidates must be distinct, complete, and valid. allocation_rationale must be at "
            "most 50 words to remain safely below the hard 60-word parser limit. "
            "Return the complete WarmStartOutput object; do not return a partial patch. "
            "Every ordered candidate must include a non-empty rationale, "
            "evidence_references, search_role, expected_effects, tradeoffs, and uncertainty. "
            "evidence_references, expected_effects, tradeoffs, and uncertainty must each be "
            "JSON arrays of one or more non-empty strings. uncertainty is never a scalar string. "
            "Candidates must remain distinct and bounded."
            f"{strict_context}{error_specific} Exact typed template (repeat the candidate object until its array "
            f"length equals warm_start_count): {_template_text(template)}"
        )
    return base


# Placeholders of the typed template. The numeric ones are written without quotes, so the template shows the type
# of each design variable and no design value.
TEMPLATE_NUMBER = {"x_T": "<thickness in um>", "x_S": "<integer>", "x_MPR": "<integer>", "x_R": "<integer>"}


def _template_text(template: Mapping[str, Any]) -> str:
    """Render the typed template as JSON with the numeric placeholders unquoted."""
    text = json.dumps(template, sort_keys=False)
    for placeholder in set(TEMPLATE_NUMBER.values()):
        text = text.replace(json.dumps(placeholder), placeholder)
    return text


# 목적: context에서 scenario 이름을 안전하게 추출한다.
# 입력: redesign context.
# 출력: scenario 이름.
def _scenario_name(context: Mapping[str, Any]) -> str:
    target = context.get("changed_condition", context.get("target_condition"))
    if isinstance(target, Mapping):
        return str(target.get("name", "unknown"))
    return "unknown"
