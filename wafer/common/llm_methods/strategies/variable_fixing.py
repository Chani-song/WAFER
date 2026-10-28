"""Variable-fixing strategy driven by structured LLM output."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from llm_methods.backends.base import LLMBackend
from llm_methods.prompts.builder import PromptBuilder
from llm_methods.protocols.schemas import VariableFixingOutput, parse_variable_fixing_output
from llm_methods.strategies.warm_start import _generate_parse_with_retries, _generation_metadata
from llm_methods.traces.recorder import TraceRecorder
from llm_methods.runtime import solver_description


@dataclass
class VariableFixingStrategy:
    """Generate fixed variables for reoptimization."""

    backend: LLMBackend
    prompt_builder: PromptBuilder = PromptBuilder()
    temperature: float = 0.0
    max_tokens: int = 1024
    parse_retries: int = 1

    # 목적: context 기반 fixed_variables 선택을 LLM backend에서 생성한다.
    # 입력: redesign context, semantic level, model key, 선택적 trace recorder.
    # 출력: 검증된 VariableFixingOutput.
    def run(
        self,
        context: Mapping[str, Any],
        semantic_level: str = "minimal",
        model_key: str = "qwen35_4b",
        trace_recorder: Optional[TraceRecorder] = None,
        run_id: str = "llm_strategy_smoke",
        optimizer_name: str = "unspecified",
        total_budget: Optional[int] = None,
        require_assessments: bool = False,
    ) -> VariableFixingOutput:
        """Run variable-fixing strategy."""
        solver = (
            solver_description(optimizer_name)
            if optimizer_name != "unspecified"
            else {"description": "Unspecified numerical search."}
        )
        messages = self.prompt_builder.variable_fixing_messages(
            context,
            semantic_level,
            model_key,
            optimizer_name=optimizer_name,
            total_budget=total_budget,
            solver_description=solver["description"],
            require_assessments=require_assessments,
        )
        output, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning = _generate_parse_with_retries(
            backend=self.backend,
            messages=messages,
            schema_name="VariableFixingOutput",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            parse_retries=self.parse_retries,
            parser=lambda payload: parse_variable_fixing_output(
                payload,
                source_candidate=context.get("previous_best_design"),
                require_assessments=require_assessments,
                variable_bounds=context.get("variable_bounds"),
            ),
            trace_recorder=trace_recorder,
            run_id=run_id,
            scenario=_scenario_name(context),
            strategy="variable_fixing",
            semantic_level=semantic_level,
            model_key=model_key,
            retry_context={
                "source_candidate": context.get("previous_best_design"),
                "require_assessments": require_assessments,
            },
        )
        if trace_recorder is not None:
            trace_recorder.record_step(
                run_id=run_id,
                scenario=_scenario_name(context),
                strategy="variable_fixing",
                optimizer=None,
                semantic_level=semantic_level,
                model_key=model_key,
                iteration=0,
                messages=messages,
                raw_response=raw_response,
                parsed_output={
                    "fixed_variables": output.fixed_variables,
                    "free_variables": output.free_variables,
                    "rationale": output.rationale,
                    "warnings": output.warnings,
                    "variable_assessments": output.variable_assessments,
                },
                llm_time_sec=llm_time_sec,
                retry_attempt=retry_attempt,
                raw_reasoning=raw_reasoning,
                generation_metadata=_generation_metadata(self.backend),
            )
        return output


# 목적: context에서 scenario 이름을 안전하게 추출한다.
# 입력: redesign context.
# 출력: scenario 이름.
def _scenario_name(context: Mapping[str, Any]) -> str:
    target = context.get("changed_condition", context.get("target_condition"))
    if isinstance(target, Mapping):
        return str(target.get("name", "unknown"))
    return "unknown"
