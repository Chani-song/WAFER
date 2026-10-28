"""ReAct-style strategy for iterative candidate evaluation."""

from __future__ import annotations

import math
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from optimizers.core.base import Evaluator
from optimizers.core.evaluation import is_feasible
from optimizers.core.domain import CHEMISTRIES, INTEGER_BOUNDS, THICKNESS_BOUNDS_BY_CHEMISTRY

from llm_methods.backends.base import LLMBackend
from llm_methods.prompts.builder import (
    PromptBuilder,
    _all_unique_evaluated_candidates,
    _project_environment_observation_trajectory,
    _react_controller_state,
    project_react_observations,
)
from llm_methods.protocols.actions import ACTION_EVALUATE_CANDIDATE, ACTION_FINISH
from llm_methods.protocols.schemas import ReActAction, parse_react_action
from llm_methods.protocols.schemas import (
    REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA,
    parse_environment_observation_react_action,
)
from llm_methods.strategies.warm_start import _generate_parse_with_retries, _generation_metadata
from llm_methods.traces.recorder import TraceRecorder

REACT_REPAIR_ATTEMPTS = 2
MAX_EVALUATED_CANDIDATE_CONTEXT = 12


def _environment_schema_copy() -> Dict[str, Any]:
    return json.loads(json.dumps(REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA))


@dataclass
class ReActSearchResult:
    """Result from a bounded ReAct search loop."""

    actions: List[ReActAction]
    evaluations: List[Dict[str, Any]]
    stopped: bool
    best_feasible_candidate: Optional[Dict[str, Any]] = None
    best_feasible_cost: Optional[float] = None
    termination_reason: str = "unknown"
    agent_step_count: int = 0
    accepted_action_count: int = 0
    rejected_action_count: int = 0
    duplicate_rejection_count: int = 0
    bound_rejection_count: int = 0
    invalid_finish_count: int = 0
    format_repair_attempt_count: int = 0
    batpac_evaluation_count: int = 0
    termination_details: Optional[Dict[str, Any]] = None


@dataclass
class ReActSearchStrategy:
    """Run a bounded ReAct loop over an injected evaluator."""

    backend: LLMBackend
    evaluator: Evaluator
    prompt_builder: PromptBuilder = PromptBuilder()
    max_steps: int = 3
    temperature: float = 0.0
    max_tokens: int = 1024
    prompt_style: str = "standard"
    parse_retries: int = REACT_REPAIR_ATTEMPTS
    protocol_version: str = "v3_3_source_reference_only"
    include_protocol_demonstration: bool = False
    evaluation_budget: int = 20

    # 목적: ReAct loop로 candidate 평가 action을 반복하고 trace를 남긴다.
    # 입력: redesign context, semantic level, model key, 선택적 trace recorder.
    # 출력: ReActSearchResult.
    def run(
        self,
        context: Mapping[str, Any],
        semantic_level: str = "minimal",
        model_key: str = "qwen35_4b",
        trace_recorder: Optional[TraceRecorder] = None,
        run_id: str = "llm_strategy_smoke",
    ) -> ReActSearchResult:
        """Run ReAct search."""
        if self.protocol_version == "v3_3_6_evaluate_only_react":
            return self._run_environment_observation(
                context, semantic_level, model_key, trace_recorder, run_id
            )
        observations: List[Dict[str, Any]] = []
        actions: List[ReActAction] = []
        evaluations: List[Dict[str, Any]] = []
        best_candidate: Optional[Dict[str, Any]] = None
        best_cost: Optional[float] = None
        scenario = _scenario_name(context)
        condition = _target_condition(context)

        for iteration in range(self.max_steps):
            messages = build_react_messages(
                context,
                semantic_level,
                model_key,
                observations,
                prompt_builder=self.prompt_builder,
                prompt_style=self.prompt_style,
            )
            repair_evidence_messages: List[List[Dict[str, str]]] = []

            def _build_repair_messages(
                invalid: Any,
                error: str,
                retry_context: Mapping[str, Any],
            ) -> List[Dict[str, str]]:
                repair_messages = build_react_messages(
                    context, semantic_level, model_key, observations,
                    prompt_builder=self.prompt_builder, prompt_style=self.prompt_style,
                    repair={
                        "raw_response": invalid,
                        "parse_error": error,
                        "retry_context": retry_context,
                        "remaining_evaluation_budget": self.max_steps - iteration,
                    },
                )
                repair_evidence_messages.append(repair_messages)
                return repair_messages

            try:
                action, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning = _generate_parse_with_retries(
                    backend=self.backend,
                    messages=messages,
                    schema_name="ReActAction",
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    parse_retries=self.parse_retries,
                    parser=lambda payload: _parse_state_valid_react_action(
                        payload, evaluations, iteration, context.get("variable_bounds")
                    ),
                    trace_recorder=trace_recorder,
                    run_id=run_id,
                    scenario=scenario,
                    strategy="react",
                    semantic_level=semantic_level,
                    model_key=model_key,
                    retry_context=react_retry_context(evaluations, iteration),
                    repair_message_builder=_build_repair_messages,
                )
            except Exception as exc:
                error_message = str(exc)
                duplicate_retry_exhausted = (
                    isinstance(exc, ValueError)
                    and bool(evaluations)
                    and (
                        "duplicates an already evaluated candidate"
                        in error_message
                    )
                )

                if duplicate_retry_exhausted:
                    raw_duplicate_response = getattr(
                        exc,
                        "jmd_raw_response",
                        None,
                    )
                    if raw_duplicate_response is None:
                        raw_duplicate_response = (
                            getattr(
                                self.backend,
                                "last_message_content",
                                None,
                            )
                            or getattr(
                                self.backend,
                                "last_raw_response_text",
                                None,
                            )
                            or ""
                        )

                    duplicate_payload_text = (
                        raw_duplicate_response
                        if isinstance(
                            raw_duplicate_response,
                            str,
                        )
                        else json.dumps(
                            raw_duplicate_response,
                            sort_keys=True,
                            default=str,
                        )
                    )
                    duplicate_candidate = (
                        _candidate_from_raw_response(
                            duplicate_payload_text
                        )
                    )
                    exhausted_retry_attempt = int(
                        getattr(
                            exc,
                            "jmd_retry_attempt",
                            self.parse_retries,
                        )
                    )
                    generation_attempt_count = int(
                        getattr(
                            exc,
                            "jmd_generation_attempt_count",
                            exhausted_retry_attempt + 1,
                        )
                    )
                    details = {
                        "classification": (
                            "model_contract_termination"
                        ),
                        "exception_type": type(exc).__name__,
                        "message": error_message,
                        "duplicate_candidate": (
                            copy_candidate(
                                duplicate_candidate
                            )
                        ),
                        "configured_parse_retries": int(
                            self.parse_retries
                        ),
                        "exhausted_retry_attempt": (
                            exhausted_retry_attempt
                        ),
                        "generation_attempt_count": (
                            generation_attempt_count
                        ),
                        "completed_batpac_evaluations": len(
                            evaluations
                        ),
                        "no_duplicate_reevaluation": True,
                    }

                    _record_terminal_event(
                        trace_recorder,
                        run_id,
                        scenario,
                        semantic_level,
                        model_key,
                        iteration,
                        messages,
                        "duplicate_retry_exhausted",
                        details,
                    )

                    return ReActSearchResult(
                        actions=actions,
                        evaluations=evaluations,
                        stopped=True,
                        best_feasible_candidate=(
                            copy_candidate(best_candidate)
                        ),
                        best_feasible_cost=best_cost,
                        termination_reason=(
                            "duplicate_retry_exhausted"
                        ),
                        agent_step_count=len(actions),
                        accepted_action_count=len(
                            evaluations
                        ),
                        rejected_action_count=1,
                        duplicate_rejection_count=1,
                        bound_rejection_count=0,
                        invalid_finish_count=0,
                        format_repair_attempt_count=(
                            exhausted_retry_attempt
                        ),
                        batpac_evaluation_count=len(
                            evaluations
                        ),
                        termination_details=details,
                    )

                _record_terminal_event(
                    trace_recorder,
                    run_id,
                    scenario,
                    semantic_level,
                    model_key,
                    iteration,
                    messages,
                    "failure",
                    {
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    },
                )
                raise
            comparison_candidate = (
                evaluations[-1].get("candidate") if evaluations else None
            )
            action.variables_changed = _computed_variables_changed(
                comparison_candidate,
                action.candidate if action.action_type == ACTION_EVALUATE_CANDIDATE else action.final_candidate,
            )
            setattr(
                action,
                "computed_delta_from_previous_best",
                _computed_delta(
                    comparison_candidate or context.get("previous_best_design"),
                    action.candidate if action.action_type == ACTION_EVALUATE_CANDIDATE else action.final_candidate,
                ),
            )
            actions.append(action)

            result = None
            feasible = None
            cost = None
            sim_time_sec = 0.0
            if action.action_type == ACTION_FINISH and not evaluations:
                raise ValueError("ReAct finish is invalid before at least one candidate evaluation")
            candidate_to_evaluate = _candidate_to_evaluate(action)
            if candidate_to_evaluate is not None:
                sim_started = time.perf_counter()
                try:
                    result = self.evaluator(candidate_to_evaluate, condition)
                except Exception as exc:
                    result = {
                        "success": False,
                        "optimization_valid": False,
                        "failure_reason": "simulator_exception",
                        "cost": None,
                        "raw": {"error": f"{type(exc).__name__}: {exc}"},
                    }
                sim_time_sec = time.perf_counter() - sim_started
                feasible = is_feasible(result, condition)
                cost = _cost_or_none(result)
                evaluation = {
                    "candidate": dict(candidate_to_evaluate),
                    "result": dict(result),
                    "feasible": feasible,
                    "cost": cost,
                    "wall_time_sec": sim_time_sec,
                }
                evaluations.append(evaluation)
                observations.append(evaluation)
                if feasible and cost is not None and (best_cost is None or cost < best_cost):
                    best_candidate = dict(candidate_to_evaluate)
                    best_cost = cost

            if trace_recorder is not None:
                trace_recorder.record_step(
                    run_id=run_id,
                    scenario=scenario,
                    strategy="react",
                    optimizer=None,
                    semantic_level=semantic_level,
                    model_key=model_key,
                    iteration=iteration,
                    messages=(
                        repair_evidence_messages[-1]
                        if retry_attempt and repair_evidence_messages
                        else messages
                    ),
                    raw_response=raw_response,
                    parsed_output=_action_payload(action),
                    observations_used=action.observations_used,
                    reported_observations_used=action.observations_used,
                    resolved_observations_used=action.resolved_observations_used,
                    candidate=candidate_to_evaluate,
                    result=result,
                    feasible=feasible,
                    cost=cost,
                    llm_time_sec=llm_time_sec,
                    sim_time_sec=sim_time_sec,
                    retry_attempt=retry_attempt,
                    raw_reasoning=raw_reasoning,
                    generation_metadata=_generation_metadata(self.backend),
                    event_type="react_episode",
                    tool_call=(
                        {"name": ACTION_EVALUATE_CANDIDATE, "arguments": candidate_to_evaluate}
                        if candidate_to_evaluate is not None
                        else {"name": ACTION_FINISH, "arguments": {"final_candidate": action.final_candidate}}
                    ),
                    tool_result=(
                        dict(result)
                        if isinstance(result, Mapping)
                        else {
                            "status": "finished",
                            "final_candidate": action.final_candidate,
                        }
                    ),
                )

            if action.action_type == ACTION_FINISH:
                _record_terminal_event(
                    trace_recorder, run_id, scenario, semantic_level, model_key, iteration + 1,
                    messages, "explicit_finish", {"final_candidate": action.final_candidate},
                )
                return ReActSearchResult(
                    actions=actions,
                    evaluations=evaluations,
                    stopped=True,
                    best_feasible_candidate=best_candidate,
                    best_feasible_cost=best_cost,
                    termination_reason="explicit_finish",
                )

        _record_terminal_event(
            trace_recorder, run_id, scenario, semantic_level, model_key, self.max_steps,
            messages if self.max_steps else [], "max_steps_exhausted",
            {"evaluations": len(evaluations)},
        )
        return ReActSearchResult(
            actions=actions,
            evaluations=evaluations,
            stopped=False,
            best_feasible_candidate=best_candidate,
            best_feasible_cost=best_cost,
            termination_reason="max_steps_exhausted",
        )

    def _run_environment_observation(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        trace_recorder: Optional[TraceRecorder],
        run_id: str,
    ) -> ReActSearchResult:
        """Run v3.3.5 with semantic rejection represented as an Observation."""
        trajectory: List[Dict[str, Any]] = []
        actions: List[ReActAction] = []
        evaluations: List[Dict[str, Any]] = []
        best_candidate: Optional[Dict[str, Any]] = None
        best_cost: Optional[float] = None
        accepted_count = rejected_count = duplicate_count = 0
        bound_count = invalid_finish_count = format_repair_count = 0
        scenario = _scenario_name(context)
        condition = _target_condition(context)
        last_messages: List[Dict[str, str]] = []

        for agent_step in range(self.max_steps):
            incumbent_before = {
                "candidate": copy_candidate(best_candidate), "cost": best_cost
            }
            messages = build_react_messages(
                context,
                semantic_level,
                model_key,
                trajectory,
                prompt_builder=self.prompt_builder,
                prompt_style=self.prompt_style,
                protocol_version=self.protocol_version,
                include_protocol_demonstration=self.include_protocol_demonstration,
                max_agent_steps=self.max_steps,
                evaluation_budget=self.evaluation_budget,
            )
            last_messages = messages

            def repair_builder(
                invalid: Any, error: str, retry_context: Mapping[str, Any]
            ) -> List[Dict[str, str]]:
                return build_environment_observation_format_repair_messages(
                    invalid, error, trajectory
                )

            try:
                action, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning = _generate_parse_with_retries(
                    backend=self.backend,
                    messages=messages,
                    schema_name="ReActAction",
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    parse_retries=int(self.parse_retries),
                    parser=parse_environment_observation_react_action,
                    trace_recorder=trace_recorder,
                    run_id=run_id,
                    scenario=scenario,
                    strategy="react",
                    semantic_level=semantic_level,
                    model_key=model_key,
                    retry_context={
                        "trajectory_step": agent_step,
                        "current_iteration": agent_step,
                    },
                    repair_message_builder=repair_builder,
                )
            except Exception as exc:
                error_text = str(exc).lower()

                termination_reason = (
                    "infrastructure_context_overflow"
                    if (
                        "input-context overflow"
                        in error_text
                        or "maximum context length"
                        in error_text
                    )
                    else "serialization_failure"
                )

                partial_result = ReActSearchResult(
                    actions=list(actions),
                    evaluations=list(evaluations),
                    stopped=True,
                    best_feasible_candidate=(
                        copy_candidate(best_candidate)
                    ),
                    best_feasible_cost=best_cost,
                    termination_reason=termination_reason,
                    agent_step_count=len(trajectory),
                    accepted_action_count=accepted_count,
                    rejected_action_count=rejected_count,
                    duplicate_rejection_count=duplicate_count,
                    bound_rejection_count=bound_count,
                    invalid_finish_count=invalid_finish_count,
                    format_repair_attempt_count=(
                        format_repair_count
                    ),
                    batpac_evaluation_count=len(
                        evaluations
                    ),
                )

                setattr(
                    exc,
                    "partial_react_result",
                    partial_result,
                )
                setattr(
                    exc,
                    "react_termination_reason",
                    termination_reason,
                )

                _record_terminal_event(
                    trace_recorder,
                    run_id,
                    scenario,
                    semantic_level,
                    model_key,
                    agent_step,
                    messages,
                    termination_reason,
                    {
                        "error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                        "agent_steps": len(trajectory),
                        "evaluations": len(evaluations),
                        "partial_result_attached": True,
                    },
                )
                raise
            format_repair_count += int(retry_attempt)
            actions.append(action)
            candidate = (
                action.candidate
                if action.action_type == ACTION_EVALUATE_CANDIDATE
                else action.final_candidate
            )
            action.variables_changed = _computed_variables_changed(
                context.get("previous_best_design"), candidate
            )
            rejection = _environment_action_rejection(
                action, trajectory, evaluations, best_candidate,
                context.get("variable_bounds"),
            )
            result = None
            feasible = None
            cost = None
            sim_time_sec = 0.0
            accepted = rejection is None
            terminal_reason = None

            if rejection is not None:
                rejected_count += 1
                duplicate_count += rejection.get("error_code") == "duplicate_candidate"
                bound_count += rejection.get("error_code") == "candidate_out_of_bounds"
                invalid_finish_count += rejection.get("error_code") == "invalid_finish"
                observation = dict(rejection)
            elif action.action_type == ACTION_EVALUATE_CANDIDATE:
                if len(evaluations) >= int(self.evaluation_budget):
                    terminal_reason = "evaluation_budget_exhausted"
                    observation = {
                        "observation_type": "action_rejected",
                        "error_code": "evaluation_budget_exhausted",
                        "candidate": copy_candidate(candidate),
                        "message": "The BatPaC evaluation budget is exhausted.",
                        "batpac_evaluation_consumed": False,
                    }
                    rejected_count += 1
                    accepted = False
                else:
                    sim_started = time.perf_counter()
                    try:
                        result = self.evaluator(candidate or {}, condition)
                    except Exception as exc:
                        result = {
                            "success": False,
                            "optimization_valid": False,
                            "failure_reason": "simulator_exception",
                            "cost": None,
                            "raw": {"error": f"{type(exc).__name__}: {exc}"},
                        }
                    sim_time_sec = time.perf_counter() - sim_started
                    feasible = is_feasible(result, condition)
                    cost = _cost_or_none(result)
                    evaluation = {
                        "candidate": copy_candidate(candidate),
                        "result": dict(result),
                        "feasible": feasible,
                        "cost": cost,
                        "wall_time_sec": sim_time_sec,
                    }
                    evaluations.append(evaluation)
                    accepted_count += 1
                    if len(evaluations) >= int(self.evaluation_budget):
                        terminal_reason = "evaluation_budget_exhausted"
                    observation = {
                        "observation_type": "batpac_evaluation",
                        **evaluation,
                        "batpac_evaluation_consumed": True,
                    }
                    if feasible and cost is not None and (
                        best_cost is None or cost < best_cost
                    ):
                        best_candidate, best_cost = copy_candidate(candidate), cost
            else:
                accepted_count += 1
                terminal_reason = "explicit_finish"
                observation = {
                    "observation_type": "finish_accepted",
                    "final_candidate": copy_candidate(action.final_candidate),
                    "batpac_evaluation_consumed": False,
                }

            action_record: Dict[str, Any] = {"action": action.action_type}
            if action.action_type == ACTION_EVALUATE_CANDIDATE:
                action_record["candidate"] = copy_candidate(action.candidate)
            else:
                action_record["final_candidate"] = copy_candidate(
                    action.final_candidate
                )
            trajectory_step_record = {
                "trajectory_step": agent_step,
                "thought": action.reason,
                "observations_used": list(action.observations_used),
                "action": action_record,
                "observation": observation,
            }
            trajectory.append(trajectory_step_record)

            incumbent_after = {
                "candidate": copy_candidate(best_candidate), "cost": best_cost
            }
            if trace_recorder is not None:
                trace_payload = {
                    **_action_payload(action, environment_protocol=True),
                    "public_thought": action.reason,
                    "action_accepted": accepted,
                    "rejection_observation": rejection,
                    "trajectory_step_record": trajectory_step_record,
                    "evaluated_candidate_projection": [
                        copy_candidate(item.get("candidate")) for item in evaluations
                    ],
                    "incumbent_before": incumbent_before,
                    "incumbent_after": incumbent_after,
                    "agent_step_consumed": 1,
                    "batpac_budget_consumed": result is not None,
                    "format_repair_attempts": int(retry_attempt),
                    "agent_step_count": agent_step + 1,
                    "accepted_action_count": accepted_count,
                    "rejected_action_count": rejected_count,
                    "duplicate_rejection_count": duplicate_count,
                    "bound_rejection_count": bound_count,
                    "invalid_finish_count": invalid_finish_count,
                    "format_repair_attempt_count": format_repair_count,
                    "batpac_evaluation_count": len(evaluations),
                    "terminal_reason": terminal_reason,
                }
                trace_recorder.record_step(
                    run_id=run_id, scenario=scenario, strategy="react", optimizer=None,
                    semantic_level=semantic_level, model_key=model_key,
                    iteration=agent_step, messages=messages, raw_response=raw_response,
                    parsed_output=trace_payload, observations_used=action.observations_used,
                    candidate=candidate, result=result, feasible=feasible, cost=cost,
                    llm_time_sec=llm_time_sec, sim_time_sec=sim_time_sec,
                    retry_attempt=retry_attempt, raw_reasoning=raw_reasoning,
                    generation_metadata=_generation_metadata(self.backend),
                    event_type="react_environment_step",
                )
            if terminal_reason in {"explicit_finish", "evaluation_budget_exhausted"}:
                _record_terminal_event(
                    trace_recorder, run_id, scenario, semantic_level, model_key,
                    agent_step + 1, messages, terminal_reason,
                    {"agent_steps": len(trajectory), "evaluations": len(evaluations)},
                )
                return ReActSearchResult(
                    actions=actions, evaluations=evaluations, stopped=True,
                    best_feasible_candidate=best_candidate, best_feasible_cost=best_cost,
                    termination_reason=terminal_reason, agent_step_count=len(trajectory),
                    accepted_action_count=accepted_count,
                    rejected_action_count=rejected_count,
                    duplicate_rejection_count=duplicate_count,
                    bound_rejection_count=bound_count,
                    invalid_finish_count=invalid_finish_count,
                    format_repair_attempt_count=format_repair_count,
                    batpac_evaluation_count=len(evaluations),
                )

        termination_reason = (
            "evaluation_budget_exhausted"
            if len(evaluations) >= int(self.evaluation_budget)
            else "max_agent_steps_exhausted"
        )
        _record_terminal_event(
            trace_recorder, run_id, scenario, semantic_level, model_key,
            self.max_steps, last_messages, termination_reason,
            {"agent_steps": len(trajectory), "evaluations": len(evaluations)},
        )
        return ReActSearchResult(
            actions=actions, evaluations=evaluations, stopped=True,
            best_feasible_candidate=best_candidate, best_feasible_cost=best_cost,
            termination_reason=termination_reason,
            agent_step_count=len(trajectory), accepted_action_count=accepted_count,
            rejected_action_count=rejected_count,
            duplicate_rejection_count=duplicate_count, bound_rejection_count=bound_count,
            invalid_finish_count=invalid_finish_count,
            format_repair_attempt_count=format_repair_count,
            batpac_evaluation_count=len(evaluations),
        )


def build_react_messages(
    context: Mapping[str, Any],
    semantic_level: str,
    model_key: str,
    observations: List[Mapping[str, Any]],
    prompt_builder: Optional[PromptBuilder] = None,
    prompt_style: str = "compact_json",
    repair: Optional[Mapping[str, Any]] = None,
    protocol_version: str = "v3_3_source_reference_only",
    include_protocol_demonstration: bool = False,
    max_agent_steps: Optional[int] = None,
    evaluation_budget: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Build the exact canonical messages shared by runtime and fit audit."""
    messages = (prompt_builder or PromptBuilder()).react_messages(
        context, semantic_level, model_key, observations, prompt_style=prompt_style,
        protocol_version=protocol_version,
        include_protocol_demonstration=include_protocol_demonstration,
        max_agent_steps=max_agent_steps,
        evaluation_budget=evaluation_budget,
    )
    if repair is None:
        return messages
    return build_react_format_repair_messages(
        context,
        raw_response=repair.get("raw_response"),
        parse_error=str(repair.get("parse_error", "invalid ReActAction")),
        retry_context=repair.get("retry_context") or {},
        remaining_evaluation_budget=int(
            repair.get("remaining_evaluation_budget", max(0, 20 - len(observations)))
        ),
    )


def build_environment_observation_format_repair_messages(
    raw_response: Any,
    parse_error: str,
    trajectory: Sequence[Mapping[str, Any]],
) -> List[Dict[str, str]]:
    """
    Repair serialization only for the evaluate-only ReAct action.

    Engineering intent and candidate values must be preserved.
    """
    if isinstance(raw_response, str):
        invalid = raw_response
    else:
        invalid = json.dumps(
            raw_response,
            sort_keys=True,
            default=str,
        )

    payload = {
        "task": (
            "Repair JSON serialization only. "
            "Preserve the engineering decision."
        ),
        "serialization_error": parse_error,
        "preserved_raw_assistant_response": invalid,
        "allowed_schema": _environment_schema_copy(),
        "current_trajectory": (
            _project_environment_observation_trajectory(
                trajectory
            )
        ),
        "constraints": [
            "Return exactly one JSON object.",
            "Preserve thought when structurally present.",
            "Preserve the intended candidate values when structurally visible.",
            "Top-level action must be the string 'evaluate_candidate'.",
            "candidate must be a sibling top-level object.",
            "Do not emit observations_used.",
            "Do not emit finish.",
            "Do not emit final_candidate.",
            "Do not make a new engineering decision.",
            "Do not return prose, markdown, alternatives, or multiple objects.",
        ],
    }

    return [
        {
            "role": "system",
            "content": (
                "You perform strict JSON serialization repair only. "
                "Preserve the engineering decision and return exactly one JSON object."
            ),
        },
        {
            "role": "assistant",
            "content": invalid,
        },
        {
            "role": "user",
            "content": json.dumps(
                payload,
                sort_keys=False,
            ),
        },
    ]

def build_react_format_repair_messages(
    context: Mapping[str, Any],
    raw_response: Any,
    parse_error: str,
    retry_context: Mapping[str, Any],
    remaining_evaluation_budget: int,
) -> List[Dict[str, str]]:
    """Build a format-only repair request without engineering context."""
    if isinstance(raw_response, str):
        invalid = raw_response
    else:
        invalid = json.dumps(raw_response, sort_keys=True, default=str)
    current_iteration = int(retry_context.get("current_iteration", 0))
    available_observations = list(
        retry_context.get("available_previous_observations") or []
    )
    allowed_iterations = [
        int(item["iteration"])
        for item in available_observations
        if isinstance(item, Mapping) and isinstance(item.get("iteration"), int)
    ]
    evaluate_template = _compact_repair_action_template(
        "evaluate_candidate", allowed_iterations
    )
    feasible_incumbent_iteration = retry_context.get("feasible_incumbent_iteration")
    feasible_incumbent_candidate = retry_context.get("feasible_incumbent_candidate")
    finish_template = (
        _compact_repair_action_template(
            "finish",
            allowed_iterations,
            final_candidate=feasible_incumbent_candidate,
            preferred_observation_iteration=feasible_incumbent_iteration,
        )
        if feasible_incumbent_iteration is not None
        and isinstance(feasible_incumbent_candidate, Mapping)
        else None
    )
    if allowed_iterations:
        observation_constraints = [
            "observations_used must contain 1 to 3 references.",
            "Every observations_used iteration must be one of allowed_observation_iterations.",
            "Citing one relevant observation is sufficient.",
            "Never enumerate every allowed identifier.",
            "Only post-change BatPaC observations in the observation ledger may be cited.",
        ]
    else:
        observation_constraints = [
            "There are no prior post-change observations.",
            "observations_used must be exactly [].",
            "Do not cite any iteration identifier.",
            "Only post-change BatPaC observations in the observation ledger may be cited.",
        ]
    payload: Dict[str, Any] = {
        "task": "Repair JSON format only; return exactly one complete ReActAction JSON object.",
        "exact_action_templates": {
            "evaluate_candidate": evaluate_template,
            **({"finish": finish_template} if finish_template is not None else {}),
        },
        "controller_state": {
            "has_feasible_incumbent": finish_template is not None,
            "best_feasible_iteration": feasible_incumbent_iteration,
            "best_feasible_candidate": feasible_incumbent_candidate,
            "best_feasible_cost": retry_context.get("feasible_incumbent_cost"),
        },
        "allowed_observation_iterations": allowed_iterations,
        "remaining_evaluation_budget": max(0, int(remaining_evaluation_budget)),
        "constraints": [
            "Preserve the original engineering decision unless the parser error requires changing that field.",
            "No prose, markdown, partial patch, or explanation.",
            "finish requires final_candidate, never candidate.",
            "evaluate_candidate requires candidate, never final_candidate.",
            "finish is valid only when controller_state.has_feasible_incumbent is true and final_candidate exactly equals controller_state.best_feasible_candidate.",
            "success=true does not imply feasible=true; feasible=false always means infeasible.",
            "Cite only the smallest evidence set needed for this action. Never enumerate all available observation identifiers.",
            *observation_constraints,
        ],
    }
    if "finish_reason=length" in parse_error:
        payload["truncation_repair"] = (
            "The previous response was truncated. Do not continue from the cutoff. "
            "Return a new complete JSON object from the beginning."
        )
    if "candidate must be an object" in parse_error:
        payload["candidate_repair"] = {
            "instruction": (
                'Include the top-level key "candidate". Do not merely describe a candidate in decision_summary.'
            ),
            "format_example": {
                "candidate": {
                    "x_chem": "LFP", "x_T": 100.0, "x_S": 20, "x_MPR": 3, "x_R": 2,
                }
            },
        }
    if "observations_used must contain at least one prior observation" in parse_error:
        payload["observation_repair"] = (
            "observations_used cannot be [] after iteration 0. "
            f"Available previous observations: {available_observations}"
        )
    if "duplicates an already evaluated candidate" in parse_error:
        duplicate_candidate = _candidate_from_raw_response(invalid)
        forbidden_candidates = list(
            retry_context.get("forbidden_candidates")
            or retry_context.get("evaluated_candidates")
            or []
        )
        matching_index = next(
            (
                index for index, candidate in enumerate(forbidden_candidates)
                if duplicate_candidate == candidate
            ),
            None,
        )
        payload["duplicate_repair"] = {
            "instruction": (
                "The next candidate must differ in at least one design variable from every "
                "candidate in forbidden_candidates. Avoiding only the most recently rejected "
                "candidate is insufficient."
            ),
            "returned_duplicate": duplicate_candidate,
            "matching_forbidden_candidate_index": matching_index,
            "forbidden_candidates": forbidden_candidates,
            "feasible_incumbent_iteration": retry_context.get(
                "feasible_incumbent_iteration"
            ),
            "valid_choices": (
                [
                    "Propose a genuinely new candidate.",
                    "Return finish with final_candidate exactly matching the controller-identified best feasible candidate.",
                ]
                if finish_template is not None
                else ["Propose a genuinely new candidate; finish is not valid because no feasible incumbent exists."]
            ),
        }
    if "no feasible incumbent exists" in parse_error.lower():
        payload["finish_repair"] = (
            "The finish action is invalid because no evaluated candidate is feasible. "
            "Return evaluate_candidate with a new candidate."
        )
    if "best feasible evaluated candidate" in parse_error.lower():
        payload["finish_repair"] = {
            "instruction": "Return finish only with the exact controller-identified best feasible candidate.",
            "best_feasible_iteration": feasible_incumbent_iteration,
            "best_feasible_candidate": feasible_incumbent_candidate,
            "best_feasible_cost": retry_context.get("feasible_incumbent_cost"),
        }
    if (
        _candidate_value_error(parse_error)
        or "duplicates an already evaluated candidate" in parse_error
    ):
        payload["variable_bounds"] = context.get("variable_bounds", {})
    preamble = ""
    if "candidate must be an object" in parse_error:
        preamble = (
            'Include the top-level key "candidate". '
            "Do not merely describe a candidate in decision_summary.\n"
        )
    repair_system_instruction = (
        "You perform strict JSON repair. Change only fields required to resolve the exact "
        "parser error; do not revise unrelated engineering reasoning."
        if "duplicates an already evaluated candidate" in parse_error
        else "You perform strict JSON format repair only. Do not make a new engineering decision."
    )
    return [
        {
            "role": "system",
            "content": repair_system_instruction,
        },
        {"role": "assistant", "content": invalid},
        {
            "role": "user",
            "content": (
                f"Exact parser error: {parse_error}\n"
                + preamble
                + json.dumps(payload, sort_keys=False, indent=2)
            ),
        },
    ]


def _compact_repair_action_template(
    action: str,
    allowed_observation_iterations: List[int],
    final_candidate: Optional[Mapping[str, Any]] = None,
    preferred_observation_iteration: Optional[int] = None,
) -> Dict[str, Any]:
    candidate = {
        "x_chem": "...", "x_T": 0.0, "x_S": 0, "x_MPR": 0, "x_R": 0,
    }
    if action == ACTION_FINISH and isinstance(final_candidate, Mapping):
        candidate = _compact_candidate(final_candidate)
    selected_iteration = (
        int(preferred_observation_iteration)
        if preferred_observation_iteration is not None
        else (
            int(allowed_observation_iterations[-1])
            if allowed_observation_iterations
            else None
        )
    )
    observations_used = (
        [{"iteration": selected_iteration, "fields": ["feasible", "cost"]}]
        if selected_iteration is not None
        else []
    )
    return {
        "action": action,
        "candidate" if action == ACTION_EVALUATE_CANDIDATE else "final_candidate": candidate,
        "observations_used": observations_used,
        "decision_summary": "...",
        "evidence_used": ["..."],
        "variables_changed": [],
        "expected_effect": "...",
        "uncertainty": "...",
    }


def _candidate_from_raw_response(raw_response: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(raw_response)
    except (TypeError, json.JSONDecodeError):
        return None
    candidate = payload.get("candidate") if isinstance(payload, Mapping) else None
    return _compact_candidate(candidate) if isinstance(candidate, Mapping) else None


def _candidate_value_error(parse_error: str) -> bool:
    text = parse_error.lower()
    return any(marker in text for marker in (
        "outside the bounds", "unknown chemistry", "must be an integer",
        "must be finite", "invalid candidate value",
    ))


def react_retry_context(
    evaluations: List[Dict[str, Any]],
    current_iteration: int,
) -> Dict[str, Any]:
    """Return the exact compact repair context used by native runtime."""
    incumbent = _feasible_incumbent(evaluations)
    forbidden_candidates = _all_unique_evaluated_candidates(evaluations)
    return {
        "controller_state": _react_controller_state(evaluations),
        "evaluated_candidates": forbidden_candidates,
        "forbidden_candidates": forbidden_candidates,
        "available_previous_observations": _available_observations_context(evaluations),
        "feasible_incumbent_iteration": (
            incumbent[0] if incumbent is not None else None
        ),
        "feasible_incumbent_candidate": (
            _compact_candidate(incumbent[2]) if incumbent is not None else None
        ),
        "feasible_incumbent_cost": (
            incumbent[1] if incumbent is not None else None
        ),
        "current_iteration": current_iteration,
    }


def _feasible_incumbent(
    evaluations: List[Dict[str, Any]],
) -> Optional[tuple[int, float, Dict[str, Any]]]:
    feasible = []
    for index, evaluation in enumerate(evaluations):
        cost = evaluation.get("cost")
        candidate = evaluation.get("candidate")
        if not evaluation.get("feasible"):
            continue
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            continue
        if not isinstance(candidate, Mapping):
            continue
        feasible.append((int(index), float(cost), dict(candidate)))
    return min(feasible, key=lambda item: (item[1], item[0])) if feasible else None


def _feasible_incumbent_iteration(
    evaluations: List[Dict[str, Any]],
) -> Optional[int]:
    incumbent = _feasible_incumbent(evaluations)
    return incumbent[0] if incumbent is not None else None


# 목적: ReActAction을 trace 저장 가능한 dict로 변환한다.
# 입력: action dataclass.
# 출력: JSON-compatible dict.
def _action_payload(
    action: ReActAction, environment_protocol: bool = False
) -> Dict[str, Any]:
    if environment_protocol:
        return {
            "thought": action.reason,
            "observations_used": list(action.observations_used),
            "action": action.action_type,
            "candidate": action.candidate,
            "final_candidate": action.final_candidate,
            "variables_changed": list(action.variables_changed),
            "public_rationale": action.reason,
        }
    return {
        "action": action.action_type,
        "candidate": action.candidate,
        "final_candidate": action.final_candidate,
        "decision_summary": action.reason,
        "evidence_used": list(action.evidence_used),
        "observations_used": action.observations_used,
        "reported_observations_used": action.observations_used,
        "resolved_observations_used": action.resolved_observations_used,
        "variables_changed": action.variables_changed,
        "computed_variables_changed": action.variables_changed,
        "reported_variables_changed": action.reported_variables_changed,
        "computed_delta_from_previous_best": getattr(
            action, "computed_delta_from_previous_best", {}
        ),
        "expected_effect": action.expected_effect,
        "uncertainty": action.uncertainty,
        "public_rationale": {
            "decision_summary": action.reason,
            "evidence_used": list(action.evidence_used),
            "expected_effect": action.expected_effect,
            "uncertainty": action.uncertainty,
        },
    }


def copy_candidate(candidate: Any) -> Optional[Dict[str, Any]]:
    return dict(candidate) if isinstance(candidate, Mapping) else None


def _candidate_bound_violations(
    candidate: Mapping[str, Any],
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    from optimizers.core.domain import resolved_design_variable_bounds
    resolved = resolved_design_variable_bounds(variable_bounds)
    violations: List[Dict[str, Any]] = []
    chemistry = candidate.get("x_chem")
    if chemistry not in CHEMISTRIES:
        violations.append({
            "variable": "x_chem",
            "proposed_value": chemistry,
            "allowed_bound": {"enum": list(CHEMISTRIES)},
        })
    thickness = candidate.get("x_T")
    thickness_by_chemistry = resolved["x_T"]["bounds_by_chemistry"]
    if chemistry in thickness_by_chemistry:
        lower = thickness_by_chemistry[str(chemistry)]["lower"]
        upper = thickness_by_chemistry[str(chemistry)]["upper"]
        if not isinstance(thickness, (int, float)) or isinstance(thickness, bool) or not (
            lower <= float(thickness) <= upper
        ):
            violations.append({
                "variable": "x_T",
                "proposed_value": thickness,
                "allowed_bound": [lower, upper],
                "chemistry": chemistry,
            })
    for variable in INTEGER_BOUNDS:
        lower, upper = resolved[variable]["lower"], resolved[variable]["upper"]
        value = candidate.get(variable)
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            violations.append({
                "variable": variable,
                "proposed_value": value,
                "allowed_bound": [lower, upper],
            })
    return violations


def _environment_action_rejection(
    action: ReActAction,
    trajectory: List[Mapping[str, Any]],
    evaluations: List[Dict[str, Any]],
    best_candidate: Optional[Mapping[str, Any]],
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return factual rejection evidence without suggesting or mutating an action."""
    invalid_references = [
        value for value in action.observations_used
        if not isinstance(value, int) or value < 0 or value >= len(trajectory)
    ]
    if invalid_references:
        return {
            "observation_type": "action_rejected",
            "error_code": "invalid_observation_reference",
            "invalid_observation_steps": invalid_references,
            "message": "observations_used references a step absent from the prior trajectory.",
            "batpac_evaluation_consumed": False,
        }
    candidate = (
        action.candidate
        if action.action_type == ACTION_EVALUATE_CANDIDATE
        else action.final_candidate
    ) or {}
    violations = _candidate_bound_violations(candidate, variable_bounds)
    if violations:
        return {
            "observation_type": "action_rejected",
            "error_code": "candidate_out_of_bounds",
            "candidate": copy_candidate(candidate),
            "violated_variables": violations,
            "message": "The proposed action is outside one or more exact allowed bounds.",
            "batpac_evaluation_consumed": False,
        }
    if action.action_type == ACTION_EVALUATE_CANDIDATE:
        key = _candidate_key(candidate)
        for index, item in enumerate(evaluations):
            if isinstance(item.get("candidate"), Mapping) and _candidate_key(
                item["candidate"]
            ) == key:
                return {
                    "observation_type": "action_rejected",
                    "error_code": "duplicate_candidate",
                    "candidate": copy_candidate(candidate),
                    "matching_evaluated_candidate_index": index,
                    "message": "The proposed candidate exactly repeats an evaluated candidate.",
                    "batpac_evaluation_consumed": False,
                }
        return None
    if best_candidate is None:
        return {
            "observation_type": "action_rejected",
            "error_code": "invalid_finish",
            "reason": "no_feasible_incumbent",
            "has_feasible_incumbent": False,
            "best_feasible_candidate": None,
            "batpac_evaluation_consumed": False,
        }
    if _candidate_key(candidate) != _candidate_key(best_candidate):
        return {
            "observation_type": "action_rejected",
            "error_code": "invalid_finish",
            "reason": "final_candidate_not_best_feasible_candidate",
            "has_feasible_incumbent": True,
            "best_feasible_candidate": copy_candidate(best_candidate),
            "batpac_evaluation_consumed": False,
        }
    return None


def _record_terminal_event(
    recorder: Optional[TraceRecorder],
    run_id: str,
    scenario: str,
    semantic_level: str,
    model_key: str,
    iteration: int,
    messages: Any,
    reason: str,
    details: Mapping[str, Any],
) -> None:
    if recorder is None:
        return
    recorder.record_step(
        run_id=run_id,
        scenario=scenario,
        strategy="react",
        optimizer=None,
        semantic_level=semantic_level,
        model_key=model_key,
        iteration=iteration,
        messages=messages,
        raw_response={"termination_reason": reason, **dict(details)},
        parsed_output={"termination_reason": reason, **dict(details)},
        event_type="react_terminal",
    )


# 목적: evaluate action에서만 simulator candidate를 추출한다.
# 입력: ReAct action.
# 출력: 평가할 candidate 또는 None.
def _candidate_to_evaluate(action: ReActAction) -> Optional[Dict[str, Any]]:
    if action.action_type == ACTION_EVALUATE_CANDIDATE:
        return action.candidate
    return None


def _parse_state_valid_react_action(
    payload: Mapping[str, Any],
    evaluations: List[Dict[str, Any]],
    current_iteration: int,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> ReActAction:
    """Parse an action and enforce observation-dependent ReAct invariants."""
    action = parse_react_action(payload, variable_bounds=variable_bounds)
    action.resolved_observations_used = _resolve_observation_references(
        action.observations_used,
        evaluations,
        current_iteration,
    )
    evaluated_keys = {
        _candidate_key(item["candidate"])
        for item in evaluations
        if isinstance(item.get("candidate"), Mapping)
    }
    if action.action_type == ACTION_EVALUATE_CANDIDATE:
        candidate = action.candidate or {}
        if _candidate_key(candidate) in evaluated_keys:
            raise ValueError(
                "ReAct candidate duplicates an already evaluated candidate and cannot be returned again: "
                f"{json.dumps(_compact_candidate(candidate), sort_keys=True)}"
            )
    elif action.action_type == ACTION_FINISH:
        if not evaluations:
            raise ValueError("ReAct finish is invalid before at least one candidate evaluation")
        controller_state = _react_controller_state(evaluations)
        if not controller_state.get("finish_allowed", False):
            raise ValueError(
                "ReAct finish is invalid because the conservative exploration and no-improvement gate is not satisfied; "
                f"controller_state={json.dumps(controller_state, sort_keys=True, default=str)}"
            )
        incumbent = _feasible_incumbent(evaluations)
        if incumbent is None:
            raise ValueError(
                "ReAct finish is invalid because no feasible incumbent exists; "
                "return a unique evaluate_candidate action instead"
            )
        incumbent_iteration, incumbent_cost, incumbent_candidate = incumbent
        if _candidate_key(action.final_candidate or {}) != _candidate_key(incumbent_candidate):
            raise ValueError(
                "ReAct final_candidate must equal the best feasible evaluated candidate: "
                f"iteration={incumbent_iteration}, cost={incumbent_cost}, "
                f"candidate={json.dumps(_compact_candidate(incumbent_candidate), sort_keys=True)}"
            )
        references_incumbent = any(
            reference.get("iteration") == incumbent_iteration
            and "feasible" in reference.get("fields", [])
            and "cost" in reference.get("fields", [])
            for reference in action.observations_used
        )
        if not references_incumbent:
            raise ValueError(
                "ReAct finish must cite the best feasible observation using both feasible and cost fields"
            )
    return action


def _resolve_observation_references(
    references: List[Dict[str, Any]],
    evaluations: List[Dict[str, Any]],
    current_iteration: int,
) -> List[Dict[str, Any]]:
    if current_iteration == 0 and references:
        raise ValueError(
            "No post-change observations exist; observations_used must be exactly []."
        )
    if current_iteration >= 1 and not references:
        raise ValueError(
            "ReActAction.observations_used must contain at least one prior observation "
            "reference after iteration 0"
        )
    resolved: List[Dict[str, Any]] = []
    existing_iterations = set(range(len(evaluations)))
    for reference in references:
        referenced_iteration = int(reference["iteration"])
        if referenced_iteration >= current_iteration:
            raise ValueError(
                "ReActAction.observations_used references a future or current iteration: "
                f"{referenced_iteration} >= {current_iteration}"
            )
        if referenced_iteration not in existing_iterations:
            raise ValueError(
                "ReActAction.observations_used references a nonexistent iteration "
                f"absent from the post-change observation ledger: {referenced_iteration}"
            )
        available = _observation_values(evaluations[referenced_iteration])
        missing = [field for field in reference["fields"] if field not in available]
        if missing:
            raise ValueError(
                "ReActAction.observations_used references fields absent from observation "
                f"{referenced_iteration}: {missing}"
            )
        resolved.append(
            {
                "iteration": referenced_iteration,
                "values": {
                    field: available[field]
                    for field in reference["fields"]
                },
            }
        )
    return resolved


def _observation_values(evaluation: Mapping[str, Any]) -> Dict[str, Any]:
    values = dict(evaluation.get("result") or {})
    for field in ("candidate", "feasible", "cost", "wall_time_sec"):
        if field in evaluation:
            values[field] = evaluation[field]
    return values


def _available_observations_context(
    evaluations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    bounded = evaluations[-MAX_EVALUATED_CANDIDATE_CONTEXT:]
    offset = len(evaluations) - len(bounded)
    ledger = project_react_observations(bounded, iteration_offset=offset)
    fields = ledger["observation_fields"]
    return [
        {
            "iteration": row[0],
            "values": {
                field: value for field, value in zip(fields, row)
                if field not in {"iteration", "x_chem", "x_T", "x_S", "x_MPR", "x_R"}
            },
        }
        for row in ledger["observations"]
    ]


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(candidate.get(key) for key in ("x_chem", "x_T", "x_S", "x_MPR", "x_R"))


def _evaluated_candidates_context(
    evaluations: List[Dict[str, Any]],
    limit: int = MAX_EVALUATED_CANDIDATE_CONTEXT,
) -> List[Dict[str, Any]]:
    """Return bounded unique evaluated candidates for retry prompts."""
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in evaluations:
        candidate = item.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        compact = _compact_candidate(candidate)
        key = _candidate_key(compact)
        if key in seen:
            continue
        seen.add(key)
        unique.append(compact)
    if len(unique) <= limit:
        return unique
    return unique[-limit:]


def _compact_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only the design variable fields in error and retry context."""
    return {
        key: candidate[key]
        for key in ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
        if key in candidate
    }


def _computed_variables_changed(
    previous: Optional[Mapping[str, Any]],
    current: Optional[Mapping[str, Any]],
) -> List[str]:
    """Compute changes from persisted candidates rather than model self-report."""
    if not isinstance(current, Mapping):
        return []
    names = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
    if not isinstance(previous, Mapping):
        return list(names)
    return [name for name in names if previous.get(name) != current.get(name)]


def _computed_delta(
    previous: Any,
    current: Any,
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
        return {}
    return {
        name: {"previous": previous.get(name), "proposed": current.get(name)}
        for name in ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
        if previous.get(name) != current.get(name)
    }


# 목적: result에서 finite cost를 추출한다.
# 입력: evaluator result mapping.
# 출력: finite cost 또는 None.
def _cost_or_none(result: Mapping[str, Any]) -> Optional[float]:
    try:
        value = float(result.get("cost"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


# 목적: context에서 target condition을 evaluator용 dict로 추출한다.
# 입력: redesign context.
# 출력: condition dict.
def _target_condition(context: Mapping[str, Any]) -> Dict[str, Any]:
    target = context.get("changed_condition", context.get("target_condition"))
    if not isinstance(target, Mapping):
        raise ValueError("context.changed_condition is required for ReActSearchStrategy")
    return dict(target)


# 목적: context에서 scenario 이름을 안전하게 추출한다.
# 입력: redesign context.
# 출력: scenario 이름.
def _scenario_name(context: Mapping[str, Any]) -> str:
    target = context.get("changed_condition", context.get("target_condition"))
    if isinstance(target, Mapping):
        return str(target.get("name", "unknown"))
    return "unknown"
