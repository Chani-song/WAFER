"""Prompt builder for method-section LLM strategy factors."""

from __future__ import annotations

import hashlib

import copy
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from llm_methods.context import project_model_visible_context

from llm_methods.protocols.actions import AVAILABLE_REACT_TOOLS
from llm_methods.protocols.schemas import (
    HYBRID_POLICY_JSON_SCHEMA,
    NEUTRAL_REOPTIMIZATION_JSON_SCHEMA,
    REACT_ACTION_JSON_SCHEMA,
    REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA,
    VARIABLE_FIXING_ALL_FREE_FALLBACK,
    VARIABLE_FIXING_JSON_SCHEMA,
    WARM_START_JSON_SCHEMA,
)
from optimizers.core.domain import INTEGER_BOUNDS, THICKNESS_BOUNDS_BY_CHEMISTRY

MAX_EVALUATED_CANDIDATE_CONTEXT = 12
REACT_OBSERVATION_ENCODING_VERSION = "jmd.react_observation_columns.v1"
REACT_OBSERVATION_FIELDS = (
    "iteration", "x_chem", "x_T", "x_S", "x_MPR", "x_R",
    "success", "feasible", "cost", "E", "P", "V", "c", "L_bat", "W_bat",
)
ENVIRONMENT_OBSERVATION_REACT_VERSION = "v3_3_6_evaluate_only_react"
ENVIRONMENT_OBSERVATION_REACT_SYSTEM_CONTRACT = """You are a ReAct agent for constrained, simulator-driven engineering redesign.

At each step:

1. read the accumulated trajectory and simulator observations
2. produce a brief public Thought explaining the next engineering decision
3. propose exactly one new candidate for evaluation

The only action is evaluate_candidate.

The deterministic controller manages trajectory bookkeeping, observation indexing, incumbent tracking, termination, and the BatPaC evaluation budget.

BatPaC observations are authoritative. Never invent objective values, constraint values, feasibility, or simulator outcomes.

A proposed candidate is only an action request. It is not feasible or successful until the environment evaluates it.

If an action is rejected because it is duplicate or outside bounds, the environment returns that rejection as the next Observation. Revise the next candidate accordingly.

Return exactly one JSON object. Do not return markdown, commentary, multiple alternatives, finish, final_candidate, observations_used, or text outside the JSON object.

The Thought is a concise public decision rationale for scientific audit. It is not a request for hidden chain-of-thought. The Thought must contain only the reasoning needed to justify the single next action. Keep it to one short paragraph, target 150 words or fewer. Do not repeat prior reasoning or candidate descriptions."""

DOMAIN_NEUTRAL_REACT_DEMONSTRATION = {
    "notice": "Illustrative protocol only. This is not an output-format example.",
    "trace": [
        {
            "phase": "Thought",
            "content": "The previous request was rejected because it repeated an accepted action.",
        },
        {
            "phase": "Action",
            "content": "Submit a distinct admissible action request.",
        },
        {
            "phase": "Observation",
            "content": "The environment evaluates accepted requests only.",
        },
    ],
}

DESIGN_VARIABLE_GLOSSARY = {
    "x_chem": {"meaning": "cell chemistry", "units": None},
    "x_T": {"meaning": "cathode thickness", "units": "micrometers"},
    "x_S": {"meaning": "number of cells connected in series per module", "units": "cells/module"},
    "x_MPR": {"meaning": "number of modules per row", "units": "modules"},
    "x_R": {"meaning": "number of pack rows", "units": "rows"},
}

PROMPT_SECTION_NAMES = (
    "global_system_prompt",
    "problem_definition_prompt",
    "redesign_context_prompt",
    "domain_knowledge_prompt",
    "candidate_action_prompt",
    "warm_start_guidance_prompt",
    "variable_fixing_hybrid_guidance_prompt",
)


class PromptBuilder:
    """Build model-independent prompts for BatPaC redesign strategies."""

    def neutral_reoptimization_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
    ) -> List[Dict[str, str]]:
        """Optional v1 prompt; v0 method prompts remain unchanged."""
        guidance = {
            "prompt_version": "v1_structured_neutral",
            "instructions": [
                "Describe what changed and what prior evidence remains usable.",
                "Select a reoptimization strategy only from supplied evidence.",
                "An empty variables_to_fix list is valid.",
                "Do not infer a preferred chemistry or variable change from the scenario name.",
                "Separate the proposed decision from the configuration actually executed.",
            ],
            "output_schema": NEUTRAL_REOPTIMIZATION_JSON_SCHEMA,
        }
        return self._messages(
            context,
            semantic_level,
            model_key,
            strategy="neutral_reoptimization",
            strategy_prompt=guidance,
        )

    # 목적: warm-start strategy용 JSON-only message를 만든다.
    # 입력: context, semantic level, model key, candidate 개수.
    # 출력: chat message list.
    def warm_start_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        k: int,
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        minimum_count: Optional[int] = None,
        maximum_count: Optional[int] = None,
        solver_description: Optional[str] = None,
        solver_initialization_behavior: Optional[str] = None,
        model_selected_count: bool = False,
    ) -> List[Dict[str, str]]:
        """Build warm-start messages."""
        return self._messages(
            context,
            semantic_level,
            model_key,
            strategy="warm_start",
            strategy_prompt=self.warm_start_guidance_prompt(
                k,
                optimizer_name,
                total_budget=total_budget,
                minimum_count=minimum_count,
                maximum_count=maximum_count,
                solver_description=solver_description,
                solver_initialization_behavior=solver_initialization_behavior,
                model_selected_count=model_selected_count,
            ),
        )

    # 목적: variable-fixing strategy용 JSON-only message를 만든다.
    # 입력: context, semantic level, model key.
    # 출력: chat message list.
    def variable_fixing_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        solver_description: Optional[str] = None,
        require_assessments: bool = False,
    ) -> List[Dict[str, str]]:
        """Build variable-fixing messages."""
        return self._messages(
            context,
            semantic_level,
            model_key,
            strategy="variable_fixing",
            strategy_prompt=self.variable_fixing_hybrid_guidance_prompt(
                "variable_fixing",
                optimizer_name=optimizer_name,
                total_budget=total_budget,
                solver_description=solver_description,
                require_assessments=require_assessments,
            ),
            sort_keys=False,
        )

    # 목적: ReAct loop에서 다음 action을 선택하는 message를 만든다.
    # 입력: context, semantic level, model key, observation history.
    # 출력: chat message list.
    def react_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        observations: Sequence[Mapping[str, Any]],
        prompt_style: str = "standard",
        protocol_version: str = "v3_3_source_reference_only",
        include_protocol_demonstration: bool = False,
        max_agent_steps: Optional[int] = None,
        evaluation_budget: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """Build ReAct action messages."""
        if protocol_version == ENVIRONMENT_OBSERVATION_REACT_VERSION:
            if max_agent_steps is None or evaluation_budget is None:
                raise ValueError(
                    "v3.3.6 evaluate-only ReAct prompts require max_agent_steps and evaluation_budget"
                )
            return self._environment_observation_react_messages(
                context,
                semantic_level,
                observations,
                include_protocol_demonstration=include_protocol_demonstration,
                max_agent_steps=int(max_agent_steps),
                evaluation_budget=int(evaluation_budget),
            )
        if prompt_style == "compact_json":
            return self._compact_react_messages(context, semantic_level, model_key, observations)
        if prompt_style == "qwen_agent":
            return self._qwen_agent_react_messages(context, semantic_level, model_key)
        if prompt_style != "standard":
            raise ValueError(f"Unknown ReAct prompt style: {prompt_style}")

        return self._messages(
            context,
            semantic_level,
            model_key,
            strategy="react",
            strategy_prompt=self.candidate_action_prompt(observations),
            sort_keys=False,
        )

    def _environment_observation_react_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        trajectory: Sequence[Mapping[str, Any]],
        include_protocol_demonstration: bool = False,
        max_agent_steps: int = 0,
        evaluation_budget: int = 0,
    ) -> List[Dict[str, str]]:
        """Render the version-gated Thought-Action-Observation protocol."""
        evaluated = [
            dict(item["observation"])
            for item in trajectory
            if isinstance(item.get("observation"), Mapping)
            and item["observation"].get("observation_type") == "batpac_evaluation"
        ]
        controller_state = _environment_observation_controller_state(
            trajectory,
            max_agent_steps=max_agent_steps,
            evaluation_budget=evaluation_budget,
        )
        payload: Dict[str, Any] = {
            "react_protocol": ENVIRONMENT_OBSERVATION_REACT_VERSION,
            "problem_definition": self.problem_definition_prompt(context),
            "source_reference_only_redesign_context": self.redesign_context_prompt(context),
            "semantic_context": self.domain_knowledge_prompt(context, semantic_level),
            "controller_state": controller_state,
            "trajectory": _project_environment_observation_trajectory(
                trajectory
            ),
            "trajectory_projection": {
                "projection_version": "jmd.react_environment_evalonly.v1",
                "full_step_count": len(trajectory),
                "model_visible_step_count": len(trajectory),
                "raw_batpac_payloads_omitted_from_prompt": True,
                "full_observations_retained_in_trace": True,
            },
            "accepted_evaluated_candidates": [
                copy.deepcopy(item.get("candidate")) for item in evaluated
            ],
            "chemistry_specific_variable_bounds": {
                "x_chem": list(THICKNESS_BOUNDS_BY_CHEMISTRY),
                "x_T_by_x_chem": {
                    key: list(value)
                    for key, value in THICKNESS_BOUNDS_BY_CHEMISTRY.items()
                },
                **{key: list(value) for key, value in INTEGER_BOUNDS.items()},
            },
            "output_instruction": [
                "Return exactly one evaluate_candidate action.",
                "action must be the JSON string 'evaluate_candidate'.",
                "candidate must be a sibling top-level object containing exactly x_chem, x_T, x_S, x_MPR, and x_R.",
                "Use the supplied trajectory and observations directly; the controller handles all observation bookkeeping.",
                "Do not emit observations_used.",
                "Do not emit finish or final_candidate.",
                "Do not repeat an already evaluated candidate.",
                "The output_schema is authoritative.",
            ],
            "output_schema": copy.deepcopy(
                REACT_ENVIRONMENT_OBSERVATION_ACTION_JSON_SCHEMA
            ),
        }
        if include_protocol_demonstration:
            payload["domain_neutral_protocol_demonstration"] = copy.deepcopy(
                DOMAIN_NEUTRAL_REACT_DEMONSTRATION
            )
        return [
            {"role": "system", "content": ENVIRONMENT_OBSERVATION_REACT_SYSTEM_CONTRACT},
            {"role": "user", "content": _json(payload, sort_keys=False)},
        ]

    def _qwen_agent_react_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
    ) -> List[Dict[str, str]]:
        """Build a task query for the vendored Qwen-Agent native ReAct protocol."""
        payload = {
            "method_factors": {
                "redesign_scenario": _scenario_name(context),
                "semantic_level": semantic_level,
                "strategy": "react",
            },
            "problem_definition_prompt": self.problem_definition_prompt(context),
            "redesign_context_prompt": self.redesign_context_prompt(context),
            "domain_knowledge_prompt": self.domain_knowledge_prompt(context, semantic_level),
            "task": [
                "Use the available BatPaC evaluation tool for complete, valid candidates.",
                "Evaluate at most one new candidate per tool call and use each returned observation before the next decision.",
                "Do not repeat a candidate and do not claim feasibility without a tool observation.",
                "When finished, identify the best feasible evaluated candidate in a concise final answer.",
            ],
        }
        return [
            {
                "role": "system",
                "content": (
                    "You are an LLM decision module for BatPaC redesign. Follow the agent's native "
                    "tool protocol. The tool observations are authoritative."
                ),
            },
            {"role": "user", "content": _json(payload)},
        ]

    # 목적: real BatPaC smoke에서 ReAct가 reasoning에 token을 쓰지 않도록 최소 prompt를 만든다.
    # 입력: context, semantic level, model key.
    # 출력: compact JSON-only chat message list.
    def _compact_react_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        observations: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, str]]:
        """Build a compact, auditable ReAct turn for the BatPaC environment."""
        current_iteration = len(observations)
        allowed_iterations = _react_allowed_observation_iterations(observations)
        controller_state = _react_controller_state(observations)
        forbidden_candidates = _all_unique_evaluated_candidates(observations)
        instructions = [
            "Follow the ReAct cycle: reason briefly from the current observations, choose one action, then use the next simulator observation before deciding again.",
            "Return exactly one JSON object immediately; no markdown and no hidden chain-of-thought.",
            "Use decision_summary, evidence_used, expected_effect, and uncertainty as concise public rationale; never provide hidden chain of thought.",
            "Action is evaluate_candidate, or finish only when controller_state.has_feasible_incumbent is true.",
            "Candidates contain x_chem, x_T, x_S, x_MPR, x_R and obey supplied bounds.",
            "The next candidate must differ in at least one design variable from every candidate in forbidden_candidates.",
            "Avoiding only the most recently rejected candidate is insufficient.",
            "The simulator fields are literal and authoritative: success=true does not imply feasible=true, and feasible=false always means infeasible.",
            "Compare cost only among observations with feasible=true and a finite cost.",
            "finish.final_candidate must exactly match controller_state.best_feasible_candidate.",
            "When no feasible incumbent exists, continue with a unique evaluate_candidate action until the evaluation budget is exhausted; do not finish.",
            "Use at most 3 observations_used references and cite only the smallest evidence set needed for this action.",
            "Each skeleton is a format example only and is not a recommended candidate.",
        ]
        instructions.extend(
            _react_observation_state_instructions(current_iteration, controller_state)
        )
        state = {
            "react_scaffold": {
                "provenance": "Adapted from the interleaved Thought-Action-Observation structure of ReAct (Yao et al., ICLR 2023).",
                "turn_mapping": {
                    "Public decision": "decision_summary plus evidence_used",
                    "Action": "action plus candidate or final_candidate",
                    "Observation": "the next row appended to observation_ledger by BatPaC",
                },
            },
            "instructions": instructions,
            "redesign_context": self.redesign_context_prompt(context),
            "domain_knowledge_prompt": self.domain_knowledge_prompt(context, semantic_level),
            "design_variable_glossary": _design_variable_glossary(),
            "variable_bounds": context.get("variable_bounds", {}),
            "observation_encoding_version": REACT_OBSERVATION_ENCODING_VERSION,
            "observation_ledger": project_react_observations(observations),
            "forbidden_candidates": forbidden_candidates,
            "controller_state": controller_state,
            "current_iteration": current_iteration,
            "allowed_observation_iterations": allowed_iterations,
            "output_contract": {
                "schema": _react_action_schema(
                    current_iteration,
                    has_feasible_incumbent=bool(
                        controller_state["has_feasible_incumbent"]
                    ),
                ),
                **_react_state_examples(
                    current_iteration,
                    allowed_iterations,
                    controller_state,
                ),
            },
        }
        content = _json(state, sort_keys=False)
        system = (
            "Act as a ReAct decision module for BatPaC. Return final JSON only. "
            "Do not include hidden chain-of-thought. Simulator observations and controller_state are authoritative."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

    # 목적: hybrid policy strategy 선택 message를 만든다.
    # 입력: context, semantic level, model key.
    # 출력: chat message list.
    def hybrid_policy_messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
    ) -> List[Dict[str, str]]:
        """Build hybrid policy messages."""
        return self._messages(
            context,
            semantic_level,
            model_key,
            strategy="hybrid_policy",
            strategy_prompt=self.variable_fixing_hybrid_guidance_prompt("hybrid_policy"),
        )

    def global_system_prompt(self) -> str:
        """Prompt 1: stable role and output contract shared by every strategy."""
        return (
            "You are an LLM decision module for constrained, simulator-driven EV battery-pack redesign. "
            "You receive a best-known design from a previous source condition, its evaluated source-condition "
            "result, and a changed target condition. When provided, you also receive semantic reference "
            "information describing BatPaC variables, mechanisms, equations, or model behavior. Use the source "
            "design and semantic reference information to interpret the redesign problem and form testable "
            "decisions or hypotheses. Do not treat semantic reference information as an observed simulator result. "
            "BatPaC evaluations are authoritative for objective values, constraints, and feasibility. Never invent "
            "or present an unevaluated BatPaC result as fact. Follow the requested redesign strategy and return "
            "exactly one valid JSON object matching the supplied schema, with concise explicit decision "
            "justifications and no markdown or surrounding prose."
        )

    def problem_definition_prompt(self, context: Mapping[str, Any]) -> Dict[str, Any]:
        """Prompt 2: invariant objective, constraints, and mixed-variable domain."""
        return {
            "objective": context.get("objective"),
            "constraints": context.get("constraints"),
            "design_variable_glossary": _design_variable_glossary(),
            "variable_bounds": context.get("variable_bounds"),
            "simulator_outputs_used": [
                "cost_per_kWh",
                "energy_kWh",
                "power_kW",
                "nominal_voltage_V",
                "cell_capacity_Ah",
                "pack_length_mm",
                "pack_width_mm",
            ],
            "rules": [
                "A candidate is feasible only after simulator evaluation.",
                "Do not clip, round, or silently repair an invalid proposal.",
                "Do not assume direct access to BatPaC outside declared tools.",
            ],
        }

    def redesign_context_prompt(self, context: Mapping[str, Any]) -> Dict[str, Any]:
        """Prompt 3: source reference, changed state, and target reevaluation."""
        projected = project_model_visible_context(context)
        keys = (
            "source_reference_design",
            "evaluated_source_result",
            "target_condition_change",
            "target_condition",
            "target_objective",
            "target_constraints",
            "design_variable_definitions_and_bounds",
        )
        return {
            **{key: projected.get(key) for key in keys},
            "prompt_projection_metadata": _model_visible_projection_metadata(
                projected.get("prompt_projection_metadata")
            ),
        }

    def domain_knowledge_prompt(
        self, context: Mapping[str, Any], semantic_level: str
    ) -> Dict[str, Any]:
        """Prompt 4: semantic factor content and auditable artifact metadata."""
        return {
            "semantic_level": semantic_level,
            "content": context.get("semantic_information", ""),
            "artifact": _model_visible_semantic_artifact(
                context.get("semantic_artifact")
            ),
            "manual_lookup_metrics": context.get("manual_lookup_metrics", {}),
            "interpretation": (
                "This is reference information, not a substitute for evaluation. "
                "Only BatPaC observations establish objective and constraint values."
            ),
        }

    def candidate_action_prompt(
        self, observations: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        """Prompt 5: ReAct action contract adapted to BatPaC."""
        evaluated_candidates = _unique_evaluated_candidates(observations)
        current_iteration = len(observations)
        allowed_iterations = _react_allowed_observation_iterations(observations)
        controller_state = _react_controller_state(observations)
        instructions = [
            "Return one concise public engineering decision and one action; do not provide hidden chain of thought.",
            "decision_summary, evidence_used, expected_effect, and uncertainty are public rationale fields stored with the action.",
            "Select exactly one next action from the protocol schema.",
            "For evaluate_candidate, provide exactly one complete candidate for BatPaC that has not been evaluated before.",
            "The next candidate must differ in at least one design variable from every candidate in forbidden_candidates.",
            "Avoiding only the most recently rejected candidate is insufficient.",
            "Read simulator fields literally. success=true means the simulator returned a result; it does not mean the design is feasible.",
            "A row with feasible=false is infeasible regardless of its cost. Compare cost only among rows with feasible=true and finite cost.",
            "For finish, final_candidate must exactly match controller_state.best_feasible_candidate.",
            "Finish is invalid unless controller_state.finish_allowed is true.",
            "When finish_allowed is false, continue proposing unique candidates until the evaluation budget is exhausted.",
            "Even when finish_allowed is true, finish only when the observed search trajectory supports a conservative conclusion that further meaningful improvement is unlikely.",
            "The JSON skeletons demonstrate structure only and contain placeholders rather than recommended candidate values.",
            "Base the brief rationale only on the source reference, semantic information, and post-change observations; do not predict unobserved outputs as facts.",
            "Describe an unevaluated candidate as a test, with effects unknown until BatPaC returns an observation.",
            "Return observations_used as explicit references to the supplied post-change observations used for this decision.",
            "Use 1 to 3 observations_used references after iteration 0 and cite only the smallest evidence set needed.",
            "Every referenced iteration must exist in allowed_observation_iterations and every referenced field must be present in that observation.",
            "Return variables_changed, expected_effect as a testable hypothesis, and explicit uncertainty.",
            "Use x_T units as micrometers and do not relabel x_T as cell capacity.",
        ]
        instructions.extend(
            _react_observation_state_instructions(current_iteration, controller_state)
        )
        return {
            "strategy": "react",
            "react_scaffold": {
                "provenance": "Adapted from the interleaved Thought-Action-Observation structure of ReAct (Yao et al., ICLR 2023).",
                "turn_mapping": {
                    "Thought": "rationale",
                    "Action": "action plus candidate or final_candidate",
                    "Observation": "the next BatPaC simulator row",
                },
            },
            "instructions": instructions,
            "protocol_actions": AVAILABLE_REACT_TOOLS,
            "observation_instruction": "observations contains all authoritative BatPaC feedback obtained after the condition change.",
            "controller_state": controller_state,
            "current_iteration": current_iteration,
            "allowed_observation_iterations": allowed_iterations,
            "evaluated_candidates": evaluated_candidates,
            "forbidden_candidates": _all_unique_evaluated_candidates(observations),
            "observations": _bounded_observations(observations),
            "output_schema": _react_action_schema(
                current_iteration,
                has_feasible_incumbent=bool(
                    controller_state["finish_allowed"]
                ),
            ),
            "json_format_examples": _react_state_examples(
                current_iteration,
                allowed_iterations,
                controller_state,
            ),
        }

    def warm_start_guidance_prompt(
        self,
        k: int,
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        minimum_count: Optional[int] = None,
        maximum_count: Optional[int] = None,
        solver_description: Optional[str] = None,
        solver_initialization_behavior: Optional[str] = None,
        model_selected_count: bool = False,
    ) -> Dict[str, Any]:
        """Prompt 6: informed warm-start output contract."""
        if model_selected_count:
            literal = (
                "You are allocating a fixed target-condition BatPaC evaluation budget between "
                "LLM-selected warm-start candidates and a downstream numerical optimizer.\n\n"
                f"Downstream solver:\n- name: {optimizer_name}\n"
                f"- search behavior: {solver_description}\n"
                f"- use of warm-start points: {solver_initialization_behavior}\n\n"
                f"Evaluation accounting:\n- total target-condition BatPaC evaluation cap: {total_budget}\n"
                f"- allowed warm-start candidate count: {minimum_count} to {maximum_count}\n"
                "- every warm-start candidate is evaluated first and consumes one evaluation\n"
                f"- the downstream solver can use at most {total_budget} minus warm_start_count additional evaluations\n"
                "- no candidate may be evaluated twice merely because it is both a warm-start candidate and a solver initialization\n\n"
                "Choose the number of warm-start candidates yourself.\n\n"
                "Do not maximize the count by default. Add another candidate only when its nonredundant information "
                "or search role is worth losing one adaptive downstream-solver evaluation.\n\n"
                "Useful nonredundant roles may include:\n- reuse or local repair of the source incumbent;\n"
                "- a distinct engineering-mechanism hypothesis;\n- a constraint-boundary probe;\n"
                "- a deliberately diverse fallback in another plausible basin.\n\n"
                "Candidates must be distinct in design-variable values.\n\n"
                "Use only:\n- the supplied source reference design and its evaluated result;\n"
                "- explicitly stated requirement changes;\n- supplied semantic BatPaC information;\n"
                "- retrieved BatPaC manual passages when available.\n\n"
                "Do not use, infer, mention, or reconstruct the hidden target optimum.\n\n"
                "Do not claim target-condition feasibility, cost, geometry, voltage, capacity, or other BatPaC "
                "results before the candidate is actually evaluated.\n\n"
                "Provide concise auditable decision justifications, not hidden chain-of-thought."
            )
        else:
            literal = f"Return exactly {int(k)} candidates for the fixed-count ablation."
        minimum = int(minimum_count if model_selected_count else k)
        maximum = int(maximum_count if model_selected_count else k)
        schema = copy.deepcopy(WARM_START_JSON_SCHEMA)
        schema["properties"]["warm_start_count"].update(
            {"minimum": minimum, "maximum": maximum}
        )
        schema["properties"]["candidates"].update(
            {"minItems": minimum, "maxItems": maximum}
        )
        return {
            "strategy": "warm_start",
            "optimizer": str(optimizer_name),
            "optimizer_initialization_contract": _optimizer_initialization_contract(optimizer_name),
            "instructions": [
                literal,
                "Make candidates distinct from one another.",
                "Each candidate must contain x_chem, x_T, x_S, x_MPR, x_R, rationale, evidence_references, search_role, expected_effects, tradeoffs, and uncertainty.",
                "Candidate values must already satisfy their declared type and bounds.",
                "The candidates will seed a numerical optimizer under the same fixed simulator budget.",
                "Use only the supplied source reference design and its evaluated result as evidence for proposing target-condition initial points.",
                "rationale must be non-empty and concise.",
                "evidence_references must identify supplied record or changed-condition fields motivating the test.",
                "search_role must state how the candidate contributes to the ordered initialization set.",
                "evidence_references, expected_effects, tradeoffs, and uncertainty are always JSON arrays of non-empty strings.",
                "uncertainty is always a JSON array, never a string.",
                "Candidates must be distinct in the five design variables x_chem, x_T, x_S, x_MPR, and x_R.",
                "Candidate values must satisfy chemistry-dependent bounds before simulator use.",
                "Do not assert unobserved causal or directional effects; candidate effects remain unknown until BatPaC evaluation.",
                "Use x_T units as micrometers and do not describe x_T as capacity.",
            ],
            "output_schema": schema,
            "output_description": "Return only the schema keys; no concrete candidate example is supplied.",
        }

    def variable_fixing_hybrid_guidance_prompt(
        self,
        mode: str,
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        solver_description: Optional[str] = None,
        require_assessments: bool = False,
    ) -> Dict[str, Any]:
        """Prompt 7: evidence-based variable reuse or optional meta-policy."""
        if mode == "variable_fixing":
            contract = _variable_fixing_output_contract()
            assessments_required = bool(require_assessments)
            field_order = (
                "free_variables, fixed_variables, variable_assessments, rationale, warnings"
                if assessments_required
                else "free_variables, fixed_variables, rationale, warnings"
            )
            return {
                "strategy": mode,
                "decision_context": {
                    "downstream_solver": {
                        "name": str(optimizer_name),
                        "search_behavior": solver_description,
                        "target_condition_evaluation_budget": total_budget,
                    },
                    "what_happens_next": [
                        "A free variable remains in the downstream optimizer search space and may receive new values according to that optimizer's initialization and proposal rules.",
                        (
                            "Because the downstream optimizer is random search, each free variable is sampled randomly within its valid bounds for every proposed candidate."
                            if str(optimizer_name).lower() == "random"
                            else "The downstream optimizer determines free-variable values according to its declared search behavior."
                        ),
                        "Every fixed variable is removed from that search space and held at the source-reference value for every downstream BatPaC evaluation.",
                        "Variable fixing itself consumes no BatPaC evaluation.",
                        "Fixing variables reduces the search dimension and can make a limited-budget search more sample-efficient. It does not reduce the runtime of an individual BatPaC evaluation.",
                        "A fixed variable cannot take alternative values in the downstream search, so the partition should balance dimensionality reduction and redesign flexibility.",
                    ],
                },
                "instructions": [
                    "Decide whether each design variable should be fixed at its source-reference value or remain free under the changed target condition.",
                    "Assess all five variables individually using the source reference design, the stated condition change, and any provided semantic reference information.",
                    "Variable fixing is intended to reduce the dimension of the downstream optimization problem so that a limited evaluation budget can be used more efficiently, while retaining sufficient redesign flexibility to adapt to the target condition.",
                    "Assess all five variables individually. For each variable, consider whether retaining its source-reference value is appropriate under the target condition and whether allowing the variable to change could be useful for redesign.",
                    "Consider fixing a variable when the available information supports reusing its source-reference value under the target condition.",
                    "Keep a variable free when the available information supports exploring alternative values under the target condition.",
                    "When the available information is mixed or indirect, weigh the benefit of reducing the downstream search space against the value of retaining redesign flexibility rather than defaulting automatically to either fixing or freeing the variable.",
                    "Any partition is allowed, including all-free, provided that at least one variable remains free. Every fixed value must exactly equal the corresponding source reference value.",
                    "The only valid values for each assessment's \"decision\" field are exactly the lowercase strings \"fix\" and \"free\".",
                "The only valid values for each assessment's \"confidence\" field are exactly the lowercase strings \"low\", \"medium\", and \"high\".",
                    "Do not use, infer, mention, or reconstruct the hidden target optimum.",
                    "Do not claim unobserved target-condition BatPaC results.",
                    "Return concise explicit decision justifications rather than an unrestricted reasoning transcript.",
                    f"Return exactly one complete JSON object with fields in this order: {field_order}.",
                    "\"free_variables\" and \"fixed_variables\" must be a disjoint, exhaustive partition of x_chem, x_T, x_S, x_MPR, x_R.",
                    "\"rationale\" must summarize the overall partition in one or two concise sentences of at most 80 words.",
                    "\"warnings\" must be [] unless a concrete unresolved issue should be recorded.",
                    (
                        "\"variable_assessments\" must be a JSON list with exactly one object for each variable. Each object must include variable, source_value, decision, justification, and confidence. decision must agree with the final partition."
                        if assessments_required
                        else "variable_assessments is omitted only for the backward-compatible ablation."
                    ),
                ],
                "compact_json_contract": contract,
                "output_schema": (
                    VARIABLE_FIXING_JSON_SCHEMA
                    if assessments_required
                    else {
                        **VARIABLE_FIXING_JSON_SCHEMA,
                        "required": [
                            "free_variables", "fixed_variables", "rationale", "warnings"
                        ],
                        "field_order": [
                            "free_variables", "fixed_variables", "rationale", "warnings"
                        ],
                    }
                ),
            }
        if mode == "hybrid_policy":
            return {
                "strategy": mode,
                "instructions": [
                    "Select one of warm_start, variable_fixing, or react.",
                    "Base selection only on supplied redesign evidence and semantic context.",
                ],
                "output_schema": HYBRID_POLICY_JSON_SCHEMA,
            }
        raise ValueError(f"Unknown prompt mode: {mode!r}")

    # 목적: method-section 공통 factor와 task instruction을 결합한다.
    # 입력: context, semantic level, model key, task line 리스트.
    # 출력: chat message list.
    def _messages(
        self,
        context: Mapping[str, Any],
        semantic_level: str,
        model_key: str,
        strategy: str,
        strategy_prompt: Mapping[str, Any],
        sort_keys: bool = True,
    ) -> List[Dict[str, str]]:
        scenario = _scenario_name(context)
        system = self.global_system_prompt()
        user_payload = {
            "method_factors": {
                "redesign_scenario": scenario,
                "semantic_level": semantic_level,
                "strategy": strategy,
                "model_independent_instructions": [
                    "Use the same context contract for every LLM backbone.",
                    "Validate outputs without clipping before simulator use.",
                ],
            },
            "problem_definition_prompt": self.problem_definition_prompt(context),
            "redesign_context_prompt": self.redesign_context_prompt(context),
            "domain_knowledge_prompt": self.domain_knowledge_prompt(context, semantic_level),
            "strategy_prompt": dict(strategy_prompt),
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": _json(user_payload, sort_keys=sort_keys)},
        ]


def _model_visible_semantic_artifact(value: Any) -> Dict[str, Any]:
    """Remove filesystem provenance while retaining stable semantic metadata."""
    if not isinstance(value, Mapping):
        return {}
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"path", "source_path"}
    }


def _model_visible_projection_metadata(value: Any) -> Dict[str, Any]:
    """Remove run-location-dependent derived metadata from the LLM prompt."""
    if not isinstance(value, Mapping):
        return {}
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "projected_context_character_count"
    }


# 목적: prompt payload를 안정적으로 JSON 문자열로 변환한다.
# 입력: JSON-compatible value.
# 출력: JSON 문자열.
def _json(value: Any, sort_keys: bool = True) -> str:
    return json.dumps(value, indent=2, sort_keys=sort_keys)


_ENVIRONMENT_RESULT_FIELDS = (
    "E",
    "P",
    "V",
    "c",
    "L_bat",
    "W_bat",
    "total_pack_cost",
)


def _compact_environment_observation(
    observation: Mapping[str, Any],
) -> Dict[str, Any]:
    """Project one observation without repeating raw BatPaC payloads."""
    source = dict(observation)
    observation_type = str(
        source.get("observation_type", "unknown")
    )

    if observation_type == "batpac_evaluation":
        result = (
            dict(source.get("result"))
            if isinstance(source.get("result"), Mapping)
            else {}
        )
        candidate = source.get("candidate")

        compact_result_fields = (
            "success",
            "optimization_valid",
            "objective_available",
            "constraint_outputs_available",
            "failure_reason",
            "cost",
            "E",
            "P",
            "V",
            "c",
            "L_bat",
            "W_bat",
            "total_pack_cost",
        )
        compact_result = {
            field: copy.deepcopy(result[field])
            for field in compact_result_fields
            if field in result
        }

        compact: Dict[str, Any] = {
            "observation_type": observation_type,
            "result": compact_result,
            "candidate": (
                _compact_candidate(candidate)
                if isinstance(candidate, Mapping)
                else None
            ),
            "success": bool(
                result.get(
                    "success",
                    source.get("success", False),
                )
            ),
            "optimization_valid": bool(
                result.get(
                    "optimization_valid",
                    source.get(
                        "optimization_valid",
                        False,
                    ),
                )
            ),
            "feasible": bool(
                source.get("feasible", False)
            ),
            "cost": _number_or_none(
                source.get(
                    "cost",
                    result.get("cost"),
                )
            ),
            "failure_reason": result.get(
                "failure_reason",
                source.get("failure_reason"),
            ),
            "batpac_evaluation_consumed": bool(
                source.get(
                    "batpac_evaluation_consumed",
                    True,
                )
            ),
        }

        for field in _ENVIRONMENT_RESULT_FIELDS:
            compact[field] = _number_or_none(
                source.get(
                    field,
                    result.get(field),
                )
            )

        return compact

    allowed_fields = (
        "observation_type",
        "error_code",
        "candidate",
        "final_candidate",
        "violated_variables",
        "matching_evaluated_candidate_index",
        "message",
        "reason",
        "has_feasible_incumbent",
        "best_feasible_candidate",
        "batpac_evaluation_consumed",
    )

    compact = {
        key: copy.deepcopy(source[key])
        for key in allowed_fields
        if key in source
    }

    for key in (
        "candidate",
        "final_candidate",
        "best_feasible_candidate",
    ):
        candidate = compact.get(key)
        if isinstance(candidate, Mapping):
            compact[key] = _compact_candidate(candidate)

    compact.setdefault(
        "observation_type",
        observation_type,
    )
    return compact


def _project_environment_observation_trajectory(
    trajectory: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep every step while compacting only older model-visible thoughts."""
    projected: List[Dict[str, Any]] = []
    total_steps = len(trajectory)

    if total_steps <= 8:
        keep_recent_full = total_steps
        older_excerpt_characters = None
        recent_thought_character_cap = None
        compaction_policy = "full_thoughts"
    elif total_steps <= 12:
        keep_recent_full = 6
        older_excerpt_characters = 160
        recent_thought_character_cap = None
        compaction_policy = "keep_6_full_excerpt_160"
    else:
        keep_recent_full = 2
        older_excerpt_characters = 0
        recent_thought_character_cap = 320
        compaction_policy = "short_archive_keep2_cap320"

    compact_before_index = max(
        0,
        total_steps - keep_recent_full,
    )

    for fallback_step, item in enumerate(trajectory):
        source = dict(item)
        raw_step = source.get(
            "trajectory_step",
            fallback_step,
        )

        try:
            trajectory_step = int(raw_step)
        except (TypeError, ValueError):
            trajectory_step = int(fallback_step)

        historical_action = (
            copy.deepcopy(dict(source["action"]))
            if isinstance(source.get("action"), Mapping)
            else copy.deepcopy(source.get("action"))
        )

        observation = (
            _compact_environment_observation(
                source["observation"]
            )
            if isinstance(
                source.get("observation"),
                Mapping,
            )
            else {
                "observation_type": "missing",
                "batpac_evaluation_consumed": False,
            }
        )

        source_thought = source.get("thought")
        projected_thought = source_thought

        if isinstance(source_thought, str):
            thought_digest = hashlib.sha256(
                source_thought.encode("utf-8")
            ).hexdigest()[:16]

            if fallback_step < compact_before_index:
                if compaction_policy == (
                    "short_archive_keep2_cap320"
                ):
                    projected_thought = (
                        f"[t:{thought_digest}]"
                    )
                else:
                    excerpt = ""
                    if older_excerpt_characters:
                        excerpt = source_thought[
                            :older_excerpt_characters
                        ].strip()

                    projected_thought = (
                        "[Older public thought compacted for "
                        "model context. The full verbatim "
                        "thought remains in trace.jsonl. "
                        f"policy={compaction_policy}; "
                        f"sha256={thought_digest}"
                    )

                    if excerpt:
                        projected_thought += (
                            f"; excerpt={excerpt}"
                        )

                    projected_thought += "]"

            elif (
                recent_thought_character_cap is not None
                and len(source_thought)
                > recent_thought_character_cap
            ):
                projected_thought = (
                    source_thought[
                        :recent_thought_character_cap
                    ].rstrip()
                    + (
                        " [truncated for model context; "
                        "full thought in trace.jsonl; "
                        f"t:{thought_digest}]"
                    )
                )

        projected_record: Dict[str, Any] = {
            "trajectory_step": trajectory_step,
            "thought": projected_thought,
            "action": None,
            "observation": observation,
        }

        # The model-visible historical action deliberately uses the same
        # flat serialization required for the next ReActAction output.
        # The internal trace still retains the nested trajectory-step
        # representation.
        if isinstance(historical_action, Mapping):
            projected_record["action"] = historical_action.get(
                "action"
            )

            if historical_action.get(
                "candidate"
            ) is not None:
                projected_record[
                    "candidate"
                ] = copy.deepcopy(
                    historical_action.get(
                        "candidate"
                    )
                )

            if historical_action.get(
                "final_candidate"
            ) is not None:
                projected_record[
                    "final_candidate"
                ] = copy.deepcopy(
                    historical_action.get(
                        "final_candidate"
                    )
                )
        else:
            projected_record[
                "action"
            ] = historical_action

        projected.append(
            projected_record
        )

    return projected


# 목적: context에서 target scenario 이름을 추출한다.
# 입력: redesign context.
# 출력: scenario 이름 문자열.
def _scenario_name(context: Mapping[str, Any]) -> str:
    changed = context.get("changed_condition", context.get("target_condition"))
    if isinstance(changed, Mapping):
        return str(changed.get("name", "unknown"))
    return "unknown"


# 목적: compact ReAct prompt에 필요한 target condition 핵심값만 남긴다.
# 입력: target condition mapping 또는 기타 값.
# 출력: compact condition dict.
def _compact_condition(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    # Keep every target-condition field. Scenario definitions use fields such
    # as L_max, W_max, and chemistry-specific CAM costs; dropping them gives
    # compact ReAct a different task from the other reoptimization methods.
    return dict(value)


def _unique_evaluated_candidates(
    observations: Sequence[Mapping[str, Any]],
    limit: int = MAX_EVALUATED_CANDIDATE_CONTEXT,
) -> Dict[str, Any]:
    """Return a bounded unique candidate list for ReAct prompt context."""
    unique: List[Dict[str, Any]] = []
    seen = set()
    for observation in observations:
        candidate = observation.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        compact = _compact_candidate(candidate)
        key = tuple(compact.get(name) for name in ("x_chem", "x_T", "x_S", "x_MPR", "x_R"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(compact)
    if len(unique) <= limit:
        return unique
    return unique[-limit:]


def _all_unique_evaluated_candidates(
    observations: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return every first-seen unique design tuple for strict exclusion."""
    unique: List[Dict[str, Any]] = []
    seen = set()
    for observation in observations:
        candidate = observation.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        compact = _compact_candidate(candidate)
        key = tuple(
            compact.get(name)
            for name in ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(compact)
    return unique


def _bounded_observations(
    observations: Sequence[Mapping[str, Any]],
    limit: int = MAX_EVALUATED_CANDIDATE_CONTEXT,
) -> List[Dict[str, Any]]:
    """Keep ReAct observation context finite without inventing or altering candidates."""
    bounded = list(observations)[-limit:]
    offset = len(observations) - len(bounded)
    return project_react_observations(bounded, iteration_offset=offset)


def project_react_observations(
    observations: Sequence[Mapping[str, Any]],
    iteration_offset: int = 0,
) -> List[Dict[str, Any]]:
    """Project full authoritative evaluations to the model-visible ledger."""
    rows = []
    for index, observation in enumerate(observations):
        result = observation.get("result")
        result = result if isinstance(result, Mapping) else {}
        candidate = observation.get("candidate")
        compact = _compact_candidate(candidate) if isinstance(candidate, Mapping) else {}
        rows.append([
            iteration_offset + index,
            compact.get("x_chem"), compact.get("x_T"), compact.get("x_S"),
            compact.get("x_MPR"), compact.get("x_R"),
            bool(result.get("success", observation.get("success", False))),
            bool(observation.get("feasible", False)),
            _number_or_none(observation.get("cost", result.get("cost"))),
            _number_or_none(result.get("E")), _number_or_none(result.get("P")),
            _number_or_none(result.get("V")), _number_or_none(result.get("c")),
            _number_or_none(result.get("L_bat")), _number_or_none(result.get("W_bat")),
        ])
    return {
        "observation_fields": list(REACT_OBSERVATION_FIELDS),
        "observations": rows,
    }


def _number_or_none(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _compact_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only machine-readable design variables in prompt candidate context."""
    keys = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
    return {key: candidate[key] for key in keys if key in candidate}


def _react_evaluate_candidate_skeleton() -> Dict[str, Any]:
    return {
        "action": "evaluate_candidate",
        "candidate": {
            "x_chem": "<valid chemistry>",
            "x_T": "<valid number within chemistry-dependent bounds>",
            "x_S": "<valid integer>",
            "x_MPR": "<valid integer>",
            "x_R": "<valid integer>",
        },
        "observations_used": [],
        "decision_summary": "Test a bounded unevaluated candidate.",
        "evidence_used": ["source reference"],
        "variables_changed": ["x_chem", "x_T", "x_S", "x_MPR", "x_R"],
        "expected_effect": "Hypothesis to test with BatPaC.",
        "uncertainty": "The simulator result is unknown.",
    }


def _react_action_schema(
    current_iteration: int,
    has_feasible_incumbent: bool = False,
) -> Dict[str, Any]:
    """Project the strict schema to the current observation state."""
    schema = copy.deepcopy(REACT_ACTION_JSON_SCHEMA)
    minimum = 0 if current_iteration == 0 else 1
    maximum = 0 if current_iteration == 0 else 3
    for action_schema in schema["oneOf"]:
        action_schema["properties"]["observations_used"]["minItems"] = minimum
        action_schema["properties"]["observations_used"]["maxItems"] = maximum
    if not has_feasible_incumbent:
        schema["oneOf"] = [
            item for item in schema["oneOf"] if item.get("title") == "evaluate_candidate"
        ]
    return schema


def _react_finish_skeleton(
    observation_iteration: int,
    final_candidate: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "action": "finish",
        "final_candidate": dict(final_candidate),
        "observations_used": [
            {"iteration": observation_iteration, "fields": ["feasible", "cost"]}
        ],
        "decision_summary": "Finish with the controller-identified best feasible evaluated candidate.",
        "evidence_used": ["best feasible observation"],
        "variables_changed": [],
        "expected_effect": "Return the best feasible result observed in this ReAct run.",
        "uncertainty": "The conclusion is limited to evaluated candidates.",
    }


def _react_later_candidate_skeleton(observation_iteration: int) -> Dict[str, Any]:
    example = _react_evaluate_candidate_skeleton()
    example["observations_used"] = [
        {"iteration": observation_iteration, "fields": ["feasible", "cost"]}
    ]
    example["decision_summary"] = "Use the prior observation to choose one distinct next test."
    return example


def _react_allowed_observation_iterations(
    observations: Sequence[Mapping[str, Any]],
) -> List[int]:
    ledger = project_react_observations(observations)
    return [int(row[0]) for row in ledger["observations"]]


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be nonnegative, got {value}")
    return value


REACT_TOTAL_BUDGET = _positive_int_env("JMD_REACT_TOTAL_BUDGET", 20)
REACT_MIN_EVALUATIONS_BEFORE_FINISH = _positive_int_env(
    "JMD_REACT_MIN_EVALUATIONS", 15
)
REACT_MIN_FEASIBLE_COUNT = _positive_int_env("JMD_REACT_MIN_FEASIBLE_COUNT", 2)
REACT_MIN_EVALUATIONS_AFTER_FIRST_FEASIBLE = _positive_int_env(
    "JMD_REACT_MIN_AFTER_FIRST_FEASIBLE", 5
)
REACT_MIN_NO_IMPROVEMENT_EVALUATIONS = _positive_int_env(
    "JMD_REACT_MIN_NO_IMPROVEMENT", 4
)


def _environment_observation_controller_state(
    trajectory: Sequence[Mapping[str, Any]],
    max_agent_steps: int,
    evaluation_budget: int,
) -> Dict[str, Any]:
    """Compute v3.3.5 counters without legacy minimum-evaluation finish gates."""
    evaluated = [
        item["observation"] for item in trajectory
        if isinstance(item.get("observation"), Mapping)
        and item["observation"].get("observation_type") == "batpac_evaluation"
    ]
    feasible = []
    for index, item in enumerate(evaluated):
        candidate = item.get("candidate")
        cost = item.get("cost")
        if (
            item.get("feasible") is True
            and isinstance(candidate, Mapping)
            and isinstance(cost, (int, float))
            and not isinstance(cost, bool)
        ):
            feasible.append((float(cost), index, dict(candidate)))
    best = min(feasible, key=lambda row: (row[0], row[1])) if feasible else None
    rejected = [
        item["observation"] for item in trajectory
        if isinstance(item.get("observation"), Mapping)
        and item["observation"].get("observation_type") == "action_rejected"
    ]
    return {
        "agent_step_count": len(trajectory),
        "accepted_action_count": len(evaluated),
        "rejected_action_count": len(rejected),
        "duplicate_rejection_count": sum(
            item.get("error_code") == "duplicate_candidate" for item in rejected
        ),
        "bound_rejection_count": sum(
            item.get("error_code") == "candidate_out_of_bounds" for item in rejected
        ),
        "invalid_finish_count": sum(
            item.get("error_code") == "invalid_finish" for item in rejected
        ),
        "batpac_evaluation_count": len(evaluated),
        "has_feasible_incumbent": best is not None,
        "best_feasible_candidate": best[2] if best is not None else None,
        "best_feasible_cost": best[0] if best is not None else None,
        "max_agent_steps": int(max_agent_steps),
        "evaluation_budget": int(evaluation_budget),
        "remaining_agent_steps": max(0, int(max_agent_steps) - len(trajectory)),
        "remaining_batpac_evaluations": max(
            0, int(evaluation_budget) - len(evaluated)
        ),
    }


def _react_controller_state(
    observations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compute the feasible incumbent and conservative finish gate."""
    feasible_rows = []
    first_feasible_iteration = None
    best_so_far = None
    last_improvement_iteration = None
    for iteration, observation in enumerate(observations):
        if not bool(observation.get("feasible", False)):
            continue
        cost = observation.get("cost")
        candidate = observation.get("candidate")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            continue
        if not isinstance(candidate, Mapping):
            continue
        numeric_cost = float(cost)
        feasible_rows.append((numeric_cost, int(iteration), dict(candidate)))
        if first_feasible_iteration is None:
            first_feasible_iteration = int(iteration)
        if best_so_far is None or numeric_cost < best_so_far:
            best_so_far = numeric_cost
            last_improvement_iteration = int(iteration)

    evaluations_used = len(observations)
    remaining_budget = max(0, REACT_TOTAL_BUDGET - evaluations_used)
    feasible_count = len(feasible_rows)
    after_first = (
        evaluations_used - 1 - first_feasible_iteration
        if first_feasible_iteration is not None else 0
    )
    since_improvement = (
        evaluations_used - 1 - last_improvement_iteration
        if last_improvement_iteration is not None else 0
    )

    base = {
        "evaluations_used": evaluations_used,
        "remaining_budget": remaining_budget,
        "feasible_count": feasible_count,
        "first_feasible_iteration": first_feasible_iteration,
        "evaluations_since_first_feasible": after_first,
        "evaluations_since_last_best_cost_improvement": since_improvement,
        "finish_thresholds": {
            "minimum_evaluations": REACT_MIN_EVALUATIONS_BEFORE_FINISH,
            "minimum_feasible_count": REACT_MIN_FEASIBLE_COUNT,
            "minimum_evaluations_after_first_feasible": REACT_MIN_EVALUATIONS_AFTER_FIRST_FEASIBLE,
            "minimum_no_improvement_evaluations": REACT_MIN_NO_IMPROVEMENT_EVALUATIONS,
        },
    }

    if not feasible_rows:
        return {
            **base,
            "has_feasible_incumbent": False,
            "best_feasible_iteration": None,
            "best_feasible_candidate": None,
            "best_feasible_cost": None,
            "finish_allowed": False,
        }

    cost, iteration, candidate = min(feasible_rows, key=lambda row: (row[0], row[1]))
    finish_allowed = bool(
        evaluations_used >= REACT_MIN_EVALUATIONS_BEFORE_FINISH
        and feasible_count >= REACT_MIN_FEASIBLE_COUNT
        and after_first >= REACT_MIN_EVALUATIONS_AFTER_FIRST_FEASIBLE
        and since_improvement >= REACT_MIN_NO_IMPROVEMENT_EVALUATIONS
    )
    return {
        **base,
        "has_feasible_incumbent": True,
        "best_feasible_iteration": iteration,
        "best_feasible_candidate": candidate,
        "best_feasible_cost": cost,
        "finish_allowed": finish_allowed,
    }


def _react_state_examples(
    current_iteration: int,
    allowed_iterations: Sequence[int],
    controller_state: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if current_iteration == 0:
        return {
            "episode_0_evaluate_candidate": _react_evaluate_candidate_skeleton(),
        }
    example_iteration = int(allowed_iterations[-1])
    examples: Dict[str, Any] = {
        "later_episode_evaluate_candidate": _react_later_candidate_skeleton(
            example_iteration
        ),
    }
    state = dict(controller_state or {})
    if state.get("finish_allowed"):
        examples["finish"] = _react_finish_skeleton(
            int(state["best_feasible_iteration"]),
            state["best_feasible_candidate"],
        )
    return examples


def _react_observation_state_instructions(
    current_iteration: int,
    controller_state: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    state = dict(controller_state or {})
    if current_iteration == 0:
        return [
            (
                'No post-change BatPaC observations exist yet. Return exactly '
                '"observations_used": []. The numbered entries in '
                "source-condition records are not valid ReAct observation identifiers."
            ),
            "No feasible incumbent exists, so the only valid action is evaluate_candidate.",
        ]
    instructions = [
        (
            "observations_used must contain 1 to 3 references whose iteration is "
            "listed in allowed_observation_iterations."
        ),
        (
            "Source-condition records are not post-change ReAct observation identifiers."
        ),
    ]
    if state.get("finish_allowed"):
        instructions.append(
            "The conservative controller gate is open. finish remains optional and is appropriate only when the observed search appears saturated; otherwise return a distinct evaluate_candidate action."
        )
    elif state.get("has_feasible_incumbent"):
        instructions.append(
            "A feasible incumbent exists, but the conservative exploration and no-improvement thresholds are not yet satisfied. finish is invalid; return a distinct evaluate_candidate action."
        )
    else:
        instructions.append(
            "No feasible incumbent exists. finish is invalid; return a distinct evaluate_candidate action."
        )
    return instructions


def _design_variable_glossary() -> Dict[str, Dict[str, Any]]:
    """Return a fresh copy of the canonical BatPaC design-variable meanings."""
    return {
        name: dict(definition)
        for name, definition in DESIGN_VARIABLE_GLOSSARY.items()
    }


def _variable_fixing_output_contract() -> Dict[str, Any]:
    """Return the concise evidence-based variable-fixing JSON contract."""
    all_variables = ["x_chem", "x_T", "x_S", "x_MPR", "x_R"]
    return {
        "field_order": [
            "free_variables",
            "fixed_variables",
            "variable_assessments",
            "rationale",
            "warnings",
        ],
        "semantics": {
            "free_variables": "variable names retained in the downstream optimizer search space",
            "fixed_variables": "source-incumbent values removed from the downstream optimizer search space",
            "variable_assessments": "one concise fix-or-free decision for every design variable",
        },
        "invariant": {
            "union": all_variables,
            "intersection": [],
            "minimum_free_variables": 1,
            "fixed_values_equal_source_incumbent": True,
        },
        "assessment_fields": [
            "variable",
            "source_value",
            "decision",
            "justification",
            "confidence",
        ],
        "decision_enum": "The only valid values for each assessment's \"decision\" field are exactly the lowercase strings \"fix\" and \"free\".",
        "confidence_enum": "The only valid values for each assessment's \"confidence\" field are exactly the lowercase strings \"low\", \"medium\", and \"high\".",
        "partition_rule": "At least one variable must be free.",
    }


def _invariant_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    return project_model_visible_context(context)


def _optimizer_initialization_contract(optimizer_name: str) -> str:
    """Describe only how the implemented optimizer consumes warm-start candidates."""
    contracts = {
        "random": "Candidates are evaluated first; remaining budget uses the optimizer's ordinary random sampling.",
        "pattern": "Candidates are evaluated first; the best available evaluated point initializes local pattern refinement.",
        "mixed_ga": "Candidates are inserted into the initial mixed-variable population before evolutionary search.",
        "axbo": "Candidates are registered as initial observations before model-based proposals.",
        "mads": "Candidates are evaluated as initial points before the MADS search proceeds.",
        "unspecified": "Candidates are evaluated as initial points before the optimizer spends its remaining budget.",
    }
    return contracts.get(str(optimizer_name), contracts["unspecified"])
