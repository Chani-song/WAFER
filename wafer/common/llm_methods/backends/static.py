"""Static backend for deterministic tests and smoke runs."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Sequence, Union

from .base import LLMBackend


class StaticBackend(LLMBackend):
    """Return predefined JSON responses."""

    # 목적: 테스트와 smoke에서 network 없이 순차 응답을 제공한다.
    # 입력: 단일 dict, schema별 dict, 또는 순차 dict 리스트.
    # 출력: StaticBackend instance.
    def __init__(
        self,
        responses: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
    ) -> None:
        if isinstance(responses, Mapping):
            self._schema_responses = {
                str(key): dict(value) for key, value in responses.items() if isinstance(value, Mapping)
            }
            self._sequence: List[Dict[str, Any]] = [] if self._schema_responses else [dict(responses)]
        else:
            self._schema_responses = {}
            self._sequence = [dict(item) for item in responses]
        if not self._schema_responses and not self._sequence:
            raise ValueError("StaticBackend requires at least one response")
        self.calls: List[Dict[str, Any]] = []
        self._index = 0

    # 목적: 저장된 JSON 응답을 schema 이름 또는 순서에 따라 반환한다.
    # 입력: chat messages, schema 이름, generation 설정.
    # 출력: JSON-compatible dict copy.
    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        schema_name: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """Return a static JSON response."""
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "schema_name": schema_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if schema_name in self._schema_responses:
            return copy.deepcopy(self._schema_responses[schema_name])
        response = self._sequence[min(self._index, len(self._sequence) - 1)]
        self._index += 1
        return copy.deepcopy(response)
