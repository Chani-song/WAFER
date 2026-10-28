"""Qwen local-server backend for chat-completions protocol endpoints."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence

from llm_methods.protocols.json_utils import extract_json_object
from .base import LLMBackend, LLMJSONExtractionError

LOCAL_API_KEY = "EMPTY"
NO_THINK_SYSTEM_INSTRUCTION = "Do not provide hidden chain-of-thought or analysis outside the required JSON object. Return final JSON only."
STRONG_NO_THINK_SYSTEM_INSTRUCTION = (
    "Do not provide hidden chain-of-thought or analysis outside the required JSON object. Return final JSON only. "
    "Immediately output one JSON object in message.content. No markdown."
)
NO_THINK_TOKEN = "/no_think"
MAX_RETRY_TOKENS = 8192
SERVER_MODEL_CONTEXT_LIMIT = 32768
SERVER_CONTEXT_SAFETY_MARGIN = 512
SERVER_CONTEXT_ESTIMATE_PERCENT = 115

# Release: the tokenizer directory is configurable through WAFER_MODEL_DIR (default
# models/qwen35); the runs used <jmd-workspace>/models/qwen35/Qwen3.5-{4B,9B}.
# LLM_TOKENIZER_PATH still overrides both.
_MODEL_DIR = os.environ.get("WAFER_MODEL_DIR", "models/qwen35")
_LOCAL_TOKENIZER_PATHS = {
    "qwen35_4b": os.path.join(_MODEL_DIR, "Qwen3.5-4B"),
    "qwen35_9b": os.path.join(_MODEL_DIR, "Qwen3.5-9B"),
}
_TOKENIZER_CACHE: Dict[str, Any] = {}


def _resolve_local_tokenizer_path(
    model: str,
) -> Optional[str]:
    override = os.environ.get("LLM_TOKENIZER_PATH")

    if override:
        return override

    return _LOCAL_TOKENIZER_PATHS.get(str(model))


def _load_tokenizer_for_model(
    model: str,
) -> tuple[Optional[Any], Optional[str]]:
    path = _resolve_local_tokenizer_path(model)

    if path is None:
        return None, None

    if not os.path.isdir(path):
        raise RuntimeError(
            "infrastructure tokenizer path does not exist: "
            f"{path}"
        )

    if path not in _TOKENIZER_CACHE:
        try:
            from transformers import AutoTokenizer
        except Exception as exc:
            raise RuntimeError(
                "infrastructure tokenizer import failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            _TOKENIZER_CACHE[path] = (
                AutoTokenizer.from_pretrained(
                    path,
                    local_files_only=True,
                    trust_remote_code=True,
                )
            )
        except Exception as exc:
            raise RuntimeError(
                "infrastructure tokenizer load failed: "
                f"path={path}; "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    return _TOKENIZER_CACHE[path], path


def _exact_prompt_token_count(
    messages: Sequence[Mapping[str, str]],
    model: str,
) -> tuple[
    Optional[int],
    Optional[str],
    Optional[str],
]:
    tokenizer, tokenizer_path = (
        _load_tokenizer_for_model(model)
    )

    if tokenizer is None:
        return None, None, None

    try:
        rendered = tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        rendered = tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=True,
        )

    if not isinstance(rendered, str):
        raise RuntimeError(
            "infrastructure tokenizer returned a non-string "
            f"chat template: {type(rendered).__name__}"
        )

    token_ids = tokenizer.encode(
        rendered,
        add_special_tokens=False,
    )

    return (
        len(token_ids),
        tokenizer_path,
        type(tokenizer).__name__,
    )


class _MissingContentError(RuntimeError):
    """Raised when the model response has no final content."""

    def __init__(self, message: str, finish_reason: Optional[str]) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class QwenLengthMissingContentError(RuntimeError):
    """Raised when Qwen spends the token budget without final content."""


class QwenServerBackend(LLMBackend):
    """Call a Qwen server exposing a chat completions protocol."""

    # 목적: Qwen local/hosted endpoint 설정을 환경변수에서 읽는다.
    # 입력: 선택적 base_url, model, api_key, timeout.
    # 출력: QwenServerBackend instance.
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_sec: float = 600.0,
        no_think: bool = True,
        stop_sequences: Optional[Sequence[str]] = None,
        fixed_completion_budget: bool = False,
        response_json_schemas: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL is required for QwenServerBackend.")
        self.model = model or os.environ.get("LLM_MODEL")
        if not self.model:
            raise RuntimeError("LLM_MODEL is required for QwenServerBackend.")
        self.api_key = _resolve_api_key(api_key, self.base_url)
        self.timeout_sec = timeout_sec
        self.no_think = no_think
        self.stop_sequences = list(stop_sequences or [])
        self.fixed_completion_budget = bool(fixed_completion_budget)
        self.response_json_schemas = {
            str(name): dict(schema)
            for name, schema in (response_json_schemas or {}).items()
        }
        self.last_request_model: Optional[str] = None
        self.last_base_url: Optional[str] = None
        self.last_latency_sec: Optional[float] = None
        self.last_raw_response_text: Optional[str] = None
        self.last_message_content: Optional[str] = None
        self.last_message_reasoning: Optional[str] = None
        self.last_finish_reason: Optional[str] = None
        self.last_attempts: List[Dict[str, Any]] = []
        self.seed: Optional[int] = None
        self.top_p: float = 1.0
        self.last_usage: Dict[str, Any] = {}
        self.last_request_audit: Dict[str, Any] = {}
        self.last_request_messages: List[Dict[str, str]] = []

    # 목적: Qwen server에 message를 보내고 JSON object 응답을 추출한다.
    # 입력: chat messages, schema 이름, generation 설정.
    # 출력: strict schema parser에 전달할 dict.
    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        schema_name: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """Generate JSON from a Qwen chat completions endpoint."""
        self.last_attempts = []
        try:
            return self._generate_once(
                messages=messages,
                schema_name=schema_name,
                temperature=temperature,
                max_tokens=max_tokens,
                strong_no_think=False,
            )
        except _MissingContentError as exc:
            if exc.finish_reason != "length":
                raise RuntimeError(str(exc)) from exc
            if self.fixed_completion_budget:
                raise QwenLengthMissingContentError(
                    "Qwen server returned finish_reason=length at the configured fixed "
                    f"completion budget of {max_tokens} tokens."
                ) from exc

            retry_max_tokens = min(max(max_tokens * 2, max_tokens + 1), MAX_RETRY_TOKENS)
            try:
                return self._generate_once(
                    messages=messages,
                    schema_name=schema_name,
                    temperature=temperature,
                    max_tokens=retry_max_tokens,
                    strong_no_think=True,
                )
            except _MissingContentError as retry_exc:
                raise QwenLengthMissingContentError(
                    "Qwen server returned finish_reason=length with empty message.content after retry; "
                    "increase max_tokens, use compact_json prompt style, or reduce reasoning."
                ) from retry_exc

    # 목적: 단일 Qwen server request를 실행하고 response content를 JSON으로 파싱한다.
    # 입력: messages, schema 이름, generation 설정, no-think 강화 여부.
    # 출력: parsed JSON object.
    def _generate_once(
        self,
        messages: Sequence[Mapping[str, str]],
        schema_name: str,
        temperature: float,
        max_tokens: int,
        strong_no_think: bool,
    ) -> Dict[str, Any]:
        prepared_messages = _prepare_messages_for_qwen(
            messages,
            no_think=self.no_think,
            strong_no_think=strong_no_think,
        )
        serialized_prompt = json.dumps(
            prepared_messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        estimated_prompt_tokens = max(
            1,
            (len(serialized_prompt) + 3) // 4,
        )
        conservative_prompt_tokens = max(
            estimated_prompt_tokens,
            (
                estimated_prompt_tokens
                * SERVER_CONTEXT_ESTIMATE_PERCENT
                + 99
            )
            // 100,
        )

        (
            exact_prompt_tokens,
            tokenizer_path,
            tokenizer_class,
        ) = _exact_prompt_token_count(
            prepared_messages,
            self.model,
        )

        guarded_prompt_tokens = (
            int(exact_prompt_tokens)
            if exact_prompt_tokens is not None
            else int(conservative_prompt_tokens)
        )
        projected_total_tokens = (
            guarded_prompt_tokens
            + int(max_tokens)
            + SERVER_CONTEXT_SAFETY_MARGIN
        )

        self.last_request_messages = [
            dict(message)
            for message in prepared_messages
        ]
        self.last_request_audit = {
            "estimated_prompt_tokens": (
                estimated_prompt_tokens
            ),
            "conservative_prompt_tokens": (
                conservative_prompt_tokens
            ),
            "exact_prompt_tokens": (
                exact_prompt_tokens
            ),
            "guarded_prompt_tokens": (
                guarded_prompt_tokens
            ),
            "token_count_method": (
                "local_chat_template_tokenizer"
                if exact_prompt_tokens is not None
                else "conservative_character_estimate"
            ),
            "tokenizer_path": tokenizer_path,
            "tokenizer_class": tokenizer_class,
            "estimate_multiplier_percent": (
                SERVER_CONTEXT_ESTIMATE_PERCENT
            ),
            "requested_completion_tokens": int(
                max_tokens
            ),
            "safety_margin_tokens": (
                SERVER_CONTEXT_SAFETY_MARGIN
            ),
            "projected_total_tokens": (
                projected_total_tokens
            ),
            "model_context_limit": (
                SERVER_MODEL_CONTEXT_LIMIT
            ),
        }

        if projected_total_tokens > SERVER_MODEL_CONTEXT_LIMIT:
            raise RuntimeError(
                "infrastructure input-context overflow before request: "
                f"guarded_prompt_tokens={guarded_prompt_tokens}, "
                f"exact_prompt_tokens={exact_prompt_tokens}, "
                f"requested_completion_tokens={max_tokens}, "
                f"safety_margin_tokens={SERVER_CONTEXT_SAFETY_MARGIN}, "
                f"projected_total_tokens={projected_total_tokens}, "
                f"model_context_limit={SERVER_MODEL_CONTEXT_LIMIT}, "
                f"token_count_method="
                f"{self.last_request_audit['token_count_method']}"
            )
        payload = {
            "model": self.model,
            "messages": prepared_messages,
            "temperature": temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens,
        }
        if self.seed is not None:
            payload["seed"] = int(self.seed)
        if self.stop_sequences:
            payload["stop"] = list(self.stop_sequences)
        response_schema = self.response_json_schemas.get(schema_name)
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": response_schema,
                    "strict": True,
                },
            }
        self.last_request_model = self.model
        self.last_base_url = self.base_url
        started = time.perf_counter()
        body = self._post_chat_completions(payload)
        self.last_latency_sec = time.perf_counter() - started
        self.last_raw_response_text = body
        attempt = {
            "request_model": self.model,
            "base_url": self.base_url,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": self.top_p,
            "messages": prepared_messages,
            "latency_sec": self.last_latency_sec,
            "raw_response_text": body,
            "strong_no_think": strong_no_think,
            "pre_request_token_audit": dict(self.last_request_audit),
            "response_format": payload.get("response_format"),
        }
        self.last_attempts.append(attempt)

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            attempt["error"] = f"response_not_json: {exc.msg}"
            raise RuntimeError(f"Qwen server response was not valid JSON: {exc.msg}") from exc
        message = _extract_first_message(parsed, body)
        finish_reason = _extract_finish_reason(parsed)
        self.last_finish_reason = finish_reason
        self.last_message_reasoning = _extract_message_reasoning(message)
        usage = parsed.get("usage", {})
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else {}
        raw_content = message.get("content")
        self.last_message_content = str(raw_content) if raw_content is not None else None
        attempt["finish_reason"] = finish_reason
        attempt["reasoning"] = self.last_message_reasoning
        attempt["content"] = self.last_message_content
        attempt["usage"] = dict(self.last_usage)
        try:
            content = _extract_message_content(message, body, finish_reason)
        except _MissingContentError as exc:
            attempt["error"] = str(exc)
            if finish_reason == "length":
                raise LLMJSONExtractionError(
                    (
                        f"Qwen server model output was truncated for {schema_name}: "
                        "finish_reason=length with no complete assistant content"
                    ),
                    raw_assistant_content=self.last_message_content or "",
                    raw_server_response=body,
                    finish_reason=finish_reason,
                    usage=self.last_usage,
                    schema_name=schema_name,
                    extraction_error=exc,
                ) from exc
            raise
        self.last_message_content = content.strip()
        attempt["content"] = self.last_message_content
        if finish_reason == "length":
            extraction_error = ValueError("response ended with finish_reason=length")
            attempt["error"] = "completion_truncated"
            raise LLMJSONExtractionError(
                (
                    f"Qwen server model output was truncated for {schema_name}: "
                    "finish_reason=length"
                ),
                raw_assistant_content=self.last_message_content,
                raw_server_response=body,
                finish_reason=finish_reason,
                usage=self.last_usage,
                schema_name=schema_name,
                extraction_error=extraction_error,
            ) from extraction_error
        try:
            return extract_json_object(content.strip())
        except ValueError as exc:
            attempt["error"] = f"content_not_parseable: {exc}"
            raise LLMJSONExtractionError(
                f"Qwen server model output was not parseable for {schema_name}: {exc}",
                raw_assistant_content=self.last_message_content,
                raw_server_response=body,
                finish_reason=finish_reason,
                usage=self.last_usage,
                schema_name=schema_name,
                extraction_error=exc,
            ) from exc

    # 목적: chat completions endpoint에 HTTP 요청을 보내고 raw response text를 받는다.
    # 입력: request payload dict.
    # 출력: raw HTTP response text.
    def _post_chat_completions(self, payload: Mapping[str, Any]) -> str:
        """Post a chat completions request."""
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen server request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Qwen server was not reachable: {exc}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("Qwen server request timed out") from exc


# 목적: chat completions response에서 첫 message object를 추출한다.
# 입력: parsed HTTP JSON과 raw body.
# 출력: message mapping.
def _extract_first_message(parsed: Mapping[str, Any], raw_body: str) -> Mapping[str, Any]:
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Qwen server response did not contain message content: {raw_body}") from exc
    if not isinstance(message, Mapping):
        raise RuntimeError(f"Qwen server response message was not an object: {raw_body}")
    return message


# 목적: chat completions response에서 finish_reason을 추출한다.
# 입력: parsed HTTP JSON.
# 출력: finish reason 문자열 또는 None.
def _extract_finish_reason(parsed: Mapping[str, Any]) -> Optional[str]:
    try:
        finish_reason = parsed["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return str(finish_reason) if finish_reason is not None else None


# 목적: assistant content를 최종 JSON output으로 추출하고 비어 있으면 명확히 실패한다.
# 입력: message mapping, raw body, finish reason.
# 출력: content 문자열.
def _extract_message_content(
    message: Mapping[str, Any],
    raw_body: str,
    finish_reason: Optional[str],
) -> str:
    content = message.get("content")
    if content is None or not str(content).strip():
        suffix = ""
        if finish_reason == "length":
            suffix = " The response appears truncated; increase max_tokens and retry."
        raise _MissingContentError(
            f"Qwen server response did not contain non-empty message content: {raw_body}{suffix}",
            finish_reason,
        )
    return str(content)


# 목적: MLX/Qwen reasoning field를 final JSON parsing과 분리해 debug metadata로 저장한다.
# 입력: message mapping.
# 출력: reasoning 문자열 또는 None.
def _extract_message_reasoning(message: Mapping[str, Any]) -> Optional[str]:
    reasoning = message.get("reasoning")
    if reasoning is None:
        return None
    return str(reasoning)


# 목적: Qwen3.5 local server가 final JSON을 바로 쓰도록 no-think instruction을 추가한다.
# 입력: 원본 chat messages와 no-think 설정.
# 출력: server request용 message list.
def _prepare_messages_for_qwen(
    messages: Sequence[Mapping[str, str]],
    no_think: bool,
    strong_no_think: bool = False,
) -> List[Dict[str, str]]:
    prepared = [dict(message) for message in messages]
    if not no_think:
        return prepared

    instruction = STRONG_NO_THINK_SYSTEM_INSTRUCTION if strong_no_think else NO_THINK_SYSTEM_INSTRUCTION
    _prepend_system_instruction(prepared, instruction)
    _append_no_think_to_last_user(prepared, strong_no_think=strong_no_think)
    return prepared


# 목적: system message에 no-think instruction을 앞쪽에 추가한다.
# 입력: message list와 instruction 문자열.
# 출력: 없음.
def _prepend_system_instruction(messages: List[Dict[str, str]], instruction: str) -> None:
    for message in messages:
        if message.get("role") == "system":
            content = str(message.get("content", ""))
            if instruction not in content:
                message["content"] = f"{instruction}\n{content}".strip()
            return
    messages.insert(0, {"role": "system", "content": instruction})


# 목적: 마지막 user message에 /no_think token과 JSON-only instruction을 추가한다.
# 입력: message list와 no-think 강화 여부.
# 출력: 없음.
def _append_no_think_to_last_user(messages: List[Dict[str, str]], strong_no_think: bool) -> None:
    user_index = None
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            user_index = index
    if user_index is None:
        messages.append({"role": "user", "content": NO_THINK_TOKEN})
        return

    content = str(messages[user_index].get("content", "")).rstrip()
    if NO_THINK_TOKEN not in content:
        content = f"{content}\n{NO_THINK_TOKEN}".strip()
    if strong_no_think:
        content = f"{content}\nReturn final JSON now. No reasoning. No explanation."
    messages[user_index]["content"] = content


# 목적: local endpoint에서는 API key 생략을 허용하고 원격 endpoint에서는 key를 요구한다.
# 입력: explicit key와 base URL.
# 출력: API key 문자열.
def _resolve_api_key(api_key: Optional[str], base_url: str) -> str:
    if api_key:
        return api_key
    env_key = os.environ.get("LLM_API_KEY")
    if env_key:
        return env_key
    if _is_local_base_url(base_url):
        return LOCAL_API_KEY
    raise RuntimeError("LLM_API_KEY is required for non-local Qwen server endpoints.")


# 목적: API key 없는 local 실행인지 판단한다.
# 입력: base URL 문자열.
# 출력: local endpoint 여부.
def _is_local_base_url(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".localhost")
