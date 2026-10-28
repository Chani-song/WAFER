"""Provider-neutral LLM backend protocol."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


class LLMJSONExtractionError(RuntimeError):
    """Strict JSON extraction failure with complete provider evidence."""

    def __init__(
        self,
        message: str,
        *,
        raw_assistant_content: str,
        raw_server_response: str,
        finish_reason: Optional[str],
        usage: Mapping[str, Any],
        schema_name: str,
        extraction_error: BaseException,
    ) -> None:
        super().__init__(message)
        self.raw_assistant_content = raw_assistant_content
        self.raw_server_response = raw_server_response
        self.finish_reason = finish_reason
        self.usage = dict(usage)
        self.schema_name = schema_name
        self.extraction_error = extraction_error


class LLMBackend:
    """Backend that returns JSON-compatible model outputs."""

    # 목적: provider와 무관하게 message list를 JSON 응답으로 변환한다.
    # 입력: chat messages, schema 이름, temperature, max_tokens.
    # 출력: schema parser가 검증할 JSON-compatible dict.
    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        schema_name: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """Generate a JSON object."""
        raise NotImplementedError
