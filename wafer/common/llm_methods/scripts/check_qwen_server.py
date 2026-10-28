"""Check a local Qwen chat-completions server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from llm_methods.backends.qwen_server import QwenServerBackend
from llm_methods.model_registry import (
    DEFAULT_QWEN35_GGUF_MODEL_CONFIG,
    DEFAULT_QWEN35_MLX_MODEL_CONFIG,
    DEFAULT_QWEN35_MODEL_CONFIG,
    resolve_serving_model_from_configs,
)


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", default=str(DEFAULT_QWEN35_MODEL_CONFIG))
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--max-tokens", type=int, default=512)
    return parser.parse_args()


# 목적: model key 또는 raw model id를 server request model로 변환한다.
# 입력: CLI model 값과 registry config path.
# 출력: model id 문자열.
def resolve_server_model(model: str, config_path: Path) -> str:
    """Resolve a server model name."""
    return resolve_serving_model_from_configs(
        model,
        [config_path, DEFAULT_QWEN35_MLX_MODEL_CONFIG, DEFAULT_QWEN35_MODEL_CONFIG, DEFAULT_QWEN35_GGUF_MODEL_CONFIG],
        fallback_model=model,
    )


# 목적: Qwen server에 tiny JSON-only prompt를 보내 health check를 수행한다.
# 입력: base URL, model id, timeout.
# 출력: health check summary dict.
def check_server(
    base_url: Optional[str],
    model: str,
    timeout_sec: float = 30.0,
    max_tokens: int = 512,
) -> Dict[str, Any]:
    """Check a Qwen server."""
    backend = QwenServerBackend(base_url=base_url, model=model, timeout_sec=timeout_sec)
    messages = [
        {
            "role": "system",
            "content": "Return JSON only.",
        },
        {
            "role": "user",
            "content": 'Return exactly this JSON object and no other text: {"ok": true}',
        },
    ]
    try:
        parsed = backend.generate_json(
            messages,
            schema_name="HealthCheck",
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except RuntimeError as exc:
        if backend.last_raw_response_text is None:
            raise
        return {
            "base_url": backend.last_base_url,
            "model": backend.last_request_model,
            "reachable": True,
            "generation_success": False,
            "latency_sec": backend.last_latency_sec,
            "raw_response": backend.last_raw_response_text,
            "content": backend.last_message_content,
            "reasoning": backend.last_message_reasoning,
            "finish_reason": backend.last_finish_reason,
            "parsed_json": None,
            "error": str(exc),
        }
    return _success_summary(backend, parsed)


# 목적: successful health check summary를 만든다.
# 입력: backend debug metadata와 parsed JSON.
# 출력: summary dict.
def _success_summary(backend: QwenServerBackend, parsed: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "base_url": backend.last_base_url,
        "model": backend.last_request_model,
        "reachable": True,
        "generation_success": True,
        "latency_sec": backend.last_latency_sec,
        "raw_response": backend.last_raw_response_text,
        "content": backend.last_message_content,
        "reasoning": backend.last_message_reasoning,
        "finish_reason": backend.last_finish_reason,
        "parsed_json": dict(parsed),
        "error": None,
    }


# 목적: server health check 결과를 stdout에 출력한다.
# 입력: summary mapping.
# 출력: 없음.
def print_summary(summary: Mapping[str, Any]) -> None:
    """Print health check summary."""
    print(f"base_url: {summary['base_url']}")
    print(f"model: {summary['model']}")
    print(f"reachable: {summary['reachable']}")
    print(f"generation_success: {summary['generation_success']}")
    print(f"latency: {summary['latency_sec']}")
    print(f"finish_reason: {summary['finish_reason']}")
    print(f"reasoning: {summary['reasoning']}")
    print(f"raw response: {summary['raw_response']}")
    print(f"parsed JSON: {json.dumps(summary['parsed_json'], sort_keys=True)}")
    if summary.get("error"):
        print(f"error: {summary['error']}")


# 목적: Qwen server health check entry point를 실행한다.
# 입력: command line arguments.
# 출력: stdout summary.
def main() -> None:
    """Run a Qwen server health check."""
    args = parse_args()
    model = resolve_server_model(args.model, Path(args.config))
    try:
        summary = check_server(args.base_url, model, timeout_sec=args.timeout_sec, max_tokens=args.max_tokens)
    except RuntimeError as exc:
        print(f"base_url: {args.base_url}")
        print(f"model: {model}")
        print("reachable: False")
        print("generation_success: False")
        print(f"error: {exc}")
        raise SystemExit(1) from exc
    print_summary(summary)


if __name__ == "__main__":
    main()
