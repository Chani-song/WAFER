"""Hybrid policy strategy for selecting a reoptimization path."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from llm_methods.backends.base import LLMBackend
from llm_methods.prompts.builder import PromptBuilder
from llm_methods.protocols.schemas import HybridPolicyOutput, parse_hybrid_policy_output
from llm_methods.strategies.warm_start import _generate_parse_with_retries
from llm_methods.traces.recorder import TraceRecorder


@dataclass
class HybridPolicyStrategy:
    """Select a method-level reoptimization strategy."""

    backend: LLMBackend
    prompt_builder: PromptBuilder = PromptBuilder()
    temperature: float = 0.0
    max_tokens: int = 1024
    parse_retries: int = 1

    # 목적: target scenario에 사용할 LLM strategy를 선택한다.
    # 입력: redesign context, semantic level, model key, 선택적 trace recorder.
    # 출력: 검증된 HybridPolicyOutput.
    def run(
        self,
        context: Mapping[str, Any],
        semantic_level: str = "minimal",
        model_key: str = "qwen35_4b",
        trace_recorder: Optional[TraceRecorder] = None,
        run_id: str = "llm_strategy_smoke",
    ) -> HybridPolicyOutput:
        """Run hybrid policy strategy."""
        messages = self.prompt_builder.hybrid_policy_messages(context, semantic_level, model_key)
        output, response, raw_response, llm_time_sec, retry_attempt, raw_reasoning = _generate_parse_with_retries(
            backend=self.backend,
            messages=messages,
            schema_name="HybridPolicyOutput",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            parse_retries=self.parse_retries,
            parser=parse_hybrid_policy_output,
            trace_recorder=trace_recorder,
            run_id=run_id,
            scenario=_scenario_name(context),
            strategy="hybrid",
            semantic_level=semantic_level,
            model_key=model_key,
        )
        if trace_recorder is not None:
            trace_recorder.record_step(
                run_id=run_id,
                scenario=_scenario_name(context),
                strategy="hybrid",
                optimizer=None,
                semantic_level=semantic_level,
                model_key=model_key,
                iteration=0,
                messages=messages,
                raw_response=raw_response,
                parsed_output={
                    "selected_strategy": output.selected_strategy,
                    "rationale": output.rationale,
                    "strategy_args": output.strategy_args,
                    "optimizer_recommendation": output.optimizer_recommendation,
                },
                llm_time_sec=llm_time_sec,
                retry_attempt=retry_attempt,
                raw_reasoning=raw_reasoning,
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
