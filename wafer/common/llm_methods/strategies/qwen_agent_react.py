"""Backward-compatible name for the repository-native local ReAct loop.

This module intentionally imports neither ``qwen_agent`` nor ``openai``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from llm_methods.backends.base import LLMBackend
from llm_methods.prompts.builder import PromptBuilder
from llm_methods.strategies.react_search import ReActSearchResult, ReActSearchStrategy
from optimizers.core.base import Evaluator


class QwenAgentReActSearchStrategy:
    """Compatibility adapter that now delegates to native strict ReAct."""

    def __init__(
        self,
        evaluator: Evaluator,
        backend: LLMBackend,
        max_steps: int = 3,
        prompt_builder: Optional[PromptBuilder] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        parse_retries: int = 2,
        prompt_style: str = "standard",
        llm_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        # llm_config remains accepted only so older callers fail neither import
        # nor construction; the native LLMBackend is the sole runtime client.
        self.llm_config = dict(llm_config or {})
        self.engine_label = "native_local_react"
        self._strategy = ReActSearchStrategy(
            backend=backend,
            evaluator=evaluator,
            prompt_builder=prompt_builder or PromptBuilder(),
            max_steps=max_steps,
            max_tokens=max_tokens,
            temperature=temperature,
            parse_retries=parse_retries,
            prompt_style=prompt_style,
        )

    def run(
        self,
        context: Mapping[str, Any],
        semantic_level: str = "minimal",
        model_key: str = "qwen35_4b",
        trace_recorder: Optional[Any] = None,
        run_id: str = "llm_strategy_smoke",
    ) -> ReActSearchResult:
        return self._strategy.run(
            context,
            semantic_level=semantic_level,
            model_key=model_key,
            trace_recorder=trace_recorder,
            run_id=run_id,
        )
