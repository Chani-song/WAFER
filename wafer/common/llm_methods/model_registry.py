"""Model registry helpers for local Qwen model tiers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

DEFAULT_QWEN35_MODEL_CONFIG = Path("configs/qwen35_models.json")
DEFAULT_QWEN35_MLX_MODEL_CONFIG = Path("configs/qwen35_mlx_models.json")
DEFAULT_QWEN35_GGUF_MODEL_CONFIG = Path("configs/qwen35_gguf_models.json")


# 목적: Qwen model registry JSON을 로드하고 기본 구조를 검증한다.
# 입력: registry config path.
# 출력: model key별 registry dict.
def load_model_registry(config_path: Path = DEFAULT_QWEN35_MODEL_CONFIG) -> Dict[str, Dict[str, Any]]:
    """Load a Qwen model registry."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError(f"model registry must be a JSON object: {path}")

    registry: Dict[str, Dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"model registry entry must be an object: {key}")
        model_id = value.get("model_id")
        local_dir = value.get("local_dir")
        tier = value.get("tier")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(f"model registry entry is missing model_id: {key}")
        if not isinstance(local_dir, str) or not local_dir:
            raise ValueError(f"model registry entry is missing local_dir: {key}")
        if not isinstance(tier, str) or not tier:
            raise ValueError(f"model registry entry is missing tier: {key}")
        entry = dict(value)
        entry["model_id"] = model_id
        entry["local_dir"] = local_dir
        entry["tier"] = tier
        entry.setdefault("format", "hf")
        entry.setdefault("quantization", "full_precision")
        registry[str(key)] = entry
    return registry


# 목적: model key를 server에 전달할 model id로 변환한다.
# 입력: model key, 선택적 config path, fallback model 문자열.
# 출력: model id 문자열.
def resolve_model_id(
    model_key: str,
    config_path: Path = DEFAULT_QWEN35_MODEL_CONFIG,
    fallback_model: Optional[str] = None,
) -> str:
    """Resolve a model key to a model identifier."""
    registry = load_model_registry(config_path)
    if model_key in registry:
        return str(registry[model_key]["model_id"])
    if fallback_model:
        return fallback_model
    raise ValueError(f"Unknown model key {model_key!r}; expected one of {sorted(registry)}")


# 목적: local server request에 사용할 model 값을 registry에서 결정한다.
# 입력: model key, config path, fallback model 문자열.
# 출력: local_dir 우선의 serving model 문자열.
def resolve_serving_model(
    model_key: str,
    config_path: Path = DEFAULT_QWEN35_MODEL_CONFIG,
    fallback_model: Optional[str] = None,
) -> str:
    """Resolve a model key to a local server model argument."""
    registry = load_model_registry(config_path)
    if model_key in registry:
        return str(registry[model_key]["local_dir"])
    if fallback_model:
        return fallback_model
    raise ValueError(f"Unknown model key {model_key!r}; expected one of {sorted(registry)}")


# 목적: 여러 registry config에서 local server request model을 찾는다.
# 입력: model key, config path 리스트, fallback model 문자열.
# 출력: local_dir 우선의 serving model 문자열.
def resolve_serving_model_from_configs(
    model_key: str,
    config_paths: Sequence[Path],
    fallback_model: Optional[str] = None,
) -> str:
    """Resolve a serving model from several registries."""
    expected_keys = []
    for config_path in config_paths:
        path = Path(config_path)
        if not path.exists():
            continue
        registry = load_model_registry(path)
        expected_keys.extend(sorted(registry))
        if model_key in registry:
            return str(registry[model_key]["local_dir"])
    if fallback_model:
        return fallback_model
    raise ValueError(f"Unknown model key {model_key!r}; expected one of {sorted(set(expected_keys))}")


# 목적: registry entry의 local directory를 repo root 기준 Path로 변환한다.
# 입력: registry entry와 repo root path.
# 출력: local model directory path.
def local_model_path(entry: Mapping[str, Any], repo_root: Path) -> Path:
    """Return a local model directory path."""
    return repo_root / str(entry["local_dir"])
