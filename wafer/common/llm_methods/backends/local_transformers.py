"""Direct local HuggingFace transformers backend."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from llm_methods.model_registry import DEFAULT_QWEN35_MODEL_CONFIG, load_model_registry
from llm_methods.protocols.json_utils import extract_json_object

from .base import LLMBackend
from .qwen_server import _prepare_messages_for_qwen


DEFAULT_LOCAL_MODEL_PATHS = {
    "qwen35_4b": "models/qwen35/Qwen3.5-4B",
    "qwen35_9b": "models/qwen35/Qwen3.5-9B",
    "qwen35_27b": "models/qwen35/Qwen3.5-27B",
}
LOCAL_JSON_ONLY_SUFFIX = (
    "\n\nYou must now output ONLY the required JSON object. Do not include Thinking Process, "
    "reasoning, markdown, explanation, or code fences.\n"
)
JSON_PREFILL_TEXT = "{\n"


class LocalTransformersBackend(LLMBackend):
    """Generate JSON with a locally loaded transformers causal LM."""

    _CACHE: Dict[str, Tuple[Any, Any]] = {}

    # 목적: local HF model path를 해석하고 필요하면 tokenizer/model을 한 번 로드한다.
    # 입력: model path, model key, registry path, no-think 설정, preload 여부.
    # 출력: LocalTransformersBackend instance.
    def __init__(
        self,
        model_path: Optional[str] = None,
        model_key: str = "qwen35_4b",
        model_config: str = str(DEFAULT_QWEN35_MODEL_CONFIG),
        no_think: bool = True,
        preload: bool = True,
        debug_output_dir: Optional[Path] = None,
    ) -> None:
        self.model_key = model_key
        self.model_path = resolve_local_transformers_model_path(
            model_path=model_path,
            model_key=model_key,
            model_config=Path(model_config),
        )
        self.no_think = no_think
        self.model_load_time_sec = 0.0
        self.loaded_from_cache = False
        self.last_latency_sec: Optional[float] = None
        self.last_raw_response_text: Optional[str] = None
        self.last_prompt_text: Optional[str] = None
        self.last_generation_kwargs: Dict[str, Any] = {}
        self.last_model_path: Optional[str] = None
        self.debug_output_dir = Path(debug_output_dir) if debug_output_dir is not None else None
        self.last_json_prefill_enabled = False
        self.seed: Optional[int] = None
        self.last_usage: Dict[str, Any] = {}
        if preload:
            self.load_model()

    # 목적: class-level cache에서 model/tokenizer를 가져오거나 새로 로드한다.
    # 입력: 없음.
    # 출력: tokenizer, model tuple.
    def load_model(self) -> Tuple[Any, Any]:
        """Load or return the cached tokenizer/model."""
        max_memory = _local_max_memory()
        cache_key = f"{self.model_path}|max_memory={max_memory!r}"
        cached = self._CACHE.get(cache_key)
        if cached is not None:
            self.loaded_from_cache = True
            self.model_load_time_sec = 0.0
            return cached

        transformers, torch = _import_transformers_and_torch()
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        started = time.perf_counter()
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(self.model_path),
            trust_remote_code=True,
        )
        model = transformers.AutoModelForCausalLM.from_pretrained(
            str(self.model_path),
            dtype=dtype,
            device_map="auto",
            max_memory=max_memory,
            trust_remote_code=True,
        )
        self.model_load_time_sec = time.perf_counter() - started
        self.loaded_from_cache = False
        self._CACHE[cache_key] = (tokenizer, model)
        return tokenizer, model

    # 목적: unit 실행 후 model/tokenizer 참조를 제거하고 GPU cache를 비운다.
    # 입력: 없음.
    # 출력: 없음.
    def unload_model(self) -> None:
        """Unload this backend's cached model/tokenizer when possible."""
        cache_key = f"{self.model_path}|max_memory={_local_max_memory()!r}"
        cached = self._CACHE.pop(cache_key, None)
        if cached is not None:
            tokenizer, model = cached
            del model
            del tokenizer
        _collect_memory()

    # 목적: chat messages를 local transformers model에 전달하고 JSON object를 추출한다.
    # 입력: messages, schema 이름, generation 설정.
    # 출력: strict schema parser에 전달할 dict.
    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        schema_name: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """Generate a JSON object from the local model."""
        tokenizer, model = self.load_model()
        prepared_messages = _prepare_messages_for_qwen(
            messages,
            no_think=self.no_think,
            strong_no_think=False,
        )
        prompt_text = _apply_chat_template_json_mode(tokenizer, prepared_messages)
        prompt_text = f"{prompt_text}{LOCAL_JSON_ONLY_SUFFIX}"
        json_prefill_enabled = _json_prefill_enabled()
        if json_prefill_enabled:
            prompt_text = f"{prompt_text}{JSON_PREFILL_TEXT}"
        self.last_json_prefill_enabled = json_prefill_enabled
        self.last_prompt_text = str(prompt_text)
        _write_debug_text(self.debug_output_dir, "local_transformers_prompt.txt", self.last_prompt_text)
        inputs = tokenizer(prompt_text, return_tensors="pt")
        inputs = _move_inputs_to_model_device(inputs, model)
        generation_kwargs = _generation_kwargs(tokenizer, model, temperature, max_tokens)
        self.last_generation_kwargs = dict(generation_kwargs)
        self.last_model_path = str(self.model_path)

        _, torch = _import_transformers_and_torch()
        if self.seed is not None:
            torch.manual_seed(int(self.seed))
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(int(self.seed))
        started = time.perf_counter()
        with torch.no_grad():
            output_ids = model.generate(**inputs, **generation_kwargs)
        self.last_latency_sec = time.perf_counter() - started

        input_length = _input_sequence_length(inputs)
        generated_ids = _generated_token_ids(output_ids, input_length)
        try:
            completion_tokens = int(generated_ids.shape[-1])
        except Exception:
            completion_tokens = len(generated_ids)
        self.last_usage = {
            "prompt_tokens": int(input_length),
            "completion_tokens": completion_tokens,
            "total_tokens": int(input_length) + completion_tokens,
        }
        content = tokenizer.decode(generated_ids, skip_special_tokens=True)
        if json_prefill_enabled:
            content = f"{JSON_PREFILL_TEXT}{content}"
        self.last_raw_response_text = content
        _write_debug_text(self.debug_output_dir, "raw_llm_response.txt", content)
        try:
            return extract_json_object(content)
        except ValueError as exc:
            raise RuntimeError(
                f"Local transformers model output was not parseable for {schema_name}: {exc}"
            ) from exc


# 목적: local transformers backend의 model path 우선순위를 적용한다.
# 입력: optional explicit path, model key, registry path.
# 출력: resolved Path.
def resolve_local_transformers_model_path(
    model_path: Optional[str],
    model_key: str,
    model_config: Path = DEFAULT_QWEN35_MODEL_CONFIG,
) -> Path:
    """Resolve the local HF model path."""
    explicit = model_path or os.environ.get("LLM_MODEL_PATH")
    if explicit:
        return Path(explicit)

    path_text = DEFAULT_LOCAL_MODEL_PATHS.get(model_key)
    if path_text is None and Path(model_config).exists():
        registry = load_model_registry(Path(model_config))
        if model_key in registry:
            path_text = str(registry[model_key]["local_dir"])
    if path_text is None:
        expected = sorted(set(DEFAULT_LOCAL_MODEL_PATHS))
        if Path(model_config).exists():
            expected = sorted(set(expected) | set(load_model_registry(Path(model_config))))
        raise ValueError(f"Unknown model key {model_key!r}; expected one of {expected}")
    return Path(path_text)


# 목적: transformers와 torch를 lazy import해 package import time dependency를 피한다.
# 입력: 없음.
# 출력: transformers module, torch module.
def _import_transformers_and_torch() -> Tuple[Any, Any]:
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise ImportError(
            "LocalTransformersBackend requires `transformers` and `torch`. "
            "Install them in the runtime environment before using --backend local_transformers."
        ) from exc
    return transformers, torch


# 목적: Python/GPU memory를 가능한 범위에서 정리한다.
# 입력: 없음.
# 출력: 없음.
def _collect_memory() -> None:
    import gc

    gc.collect()
    try:
        import torch
    except ImportError:
        return
    cuda = getattr(torch, "cuda", None)
    empty_cache = getattr(cuda, "empty_cache", None)
    if callable(empty_cache):
        empty_cache()


def _local_max_memory() -> Optional[Dict[Any, str]]:
    """Optionally force Accelerate to distribute model layers across visible GPUs."""
    raw = os.environ.get("LLM_LOCAL_MAX_MEMORY_GIB_PER_GPU")
    if not raw:
        return None
    try:
        gib = float(raw)
    except ValueError as exc:
        raise ValueError("LLM_LOCAL_MAX_MEMORY_GIB_PER_GPU must be numeric") from exc
    if gib <= 0:
        raise ValueError("LLM_LOCAL_MAX_MEMORY_GIB_PER_GPU must be positive")
    _, torch = _import_transformers_and_torch()
    memory: Dict[Any, str] = {
        index: f"{gib:g}GiB" for index in range(torch.cuda.device_count())
    }
    memory["cpu"] = os.environ.get("LLM_LOCAL_CPU_MAX_MEMORY", "64GiB")
    return memory


# 목적: Qwen chat template에서 thinking mode를 끄고 지원하지 않으면 안전하게 fallback한다.
# 입력: tokenizer와 prepared messages.
# 출력: prompt text.
def _apply_chat_template_json_mode(tokenizer: Any, messages: Sequence[Mapping[str, str]]) -> str:
    template_messages = [dict(message) for message in messages]
    try:
        return str(
            tokenizer.apply_chat_template(
                template_messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        )
    except TypeError:
        return str(
            tokenizer.apply_chat_template(
                template_messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        )


# 목적: JSON prefill 환경변수 사용 여부를 판단한다.
# 입력: 없음.
# 출력: bool.
def _json_prefill_enabled() -> bool:
    return os.environ.get("LLM_JSON_PREFILL") == "1"


# 목적: prompt/raw response debug text를 output directory에 저장한다.
# 입력: output dir, filename, text.
# 출력: 없음.
def _write_debug_text(output_dir: Optional[Path], filename: str, text: str) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / filename).write_text(text, encoding="utf-8")


# 목적: generation 옵션을 temperature 설정과 model turn terminator에 맞게 만든다.
# 입력: tokenizer, model, temperature, max_tokens.
# 출력: model.generate keyword dict.
def _generation_kwargs(tokenizer: Any, model: Any, temperature: float, max_tokens: int) -> Dict[str, Any]:
    eos_token_ids = _eos_token_ids(tokenizer, model)
    kwargs: Dict[str, Any] = {
        "max_new_tokens": int(max_tokens),
        "do_sample": bool(temperature and temperature > 0.0),
        "pad_token_id": _pad_token_id(tokenizer, eos_token_ids),
    }
    if eos_token_ids:
        kwargs["eos_token_id"] = eos_token_ids[0] if len(eos_token_ids) == 1 else eos_token_ids
    if kwargs["do_sample"]:
        kwargs["temperature"] = float(temperature)
    return kwargs


def _eos_token_ids(tokenizer: Any, model: Any) -> List[int]:
    """Collect model-specific EOS and chat-turn terminator token ids."""
    token_ids: List[int] = []
    unknown_id = getattr(tokenizer, "unk_token_id", None)
    _append_token_id(token_ids, getattr(tokenizer, "eos_token_id", None), unknown_id=unknown_id)

    convert = getattr(tokenizer, "convert_tokens_to_ids", None)
    if callable(convert):
        try:
            im_end_id = convert("<|im_end|>")
        except Exception:
            im_end_id = None
        _append_token_id(token_ids, im_end_id, unknown_id=unknown_id)

    generation_config = getattr(model, "generation_config", None)
    config_eos = getattr(generation_config, "eos_token_id", None)
    if isinstance(config_eos, (list, tuple)):
        for token_id in config_eos:
            _append_token_id(token_ids, token_id, unknown_id=unknown_id)
    else:
        _append_token_id(token_ids, config_eos, unknown_id=unknown_id)
    return token_ids


def _append_token_id(token_ids: List[int], token_id: Any, unknown_id: Any = None) -> None:
    """Append one valid, non-unknown token id while preserving order."""
    if isinstance(token_id, bool) or not isinstance(token_id, int):
        return
    if token_id < 0:
        return
    if isinstance(unknown_id, int) and token_id == unknown_id:
        return
    if token_id not in token_ids:
        token_ids.append(token_id)


def _pad_token_id(tokenizer: Any, eos_token_ids: Sequence[int]) -> Optional[int]:
    """Choose a conservative pad token id for generation."""
    pad_token_id = getattr(tokenizer, "pad_token_id", None)
    if isinstance(pad_token_id, int) and pad_token_id >= 0:
        return pad_token_id
    tokenizer_eos = getattr(tokenizer, "eos_token_id", None)
    if isinstance(tokenizer_eos, int) and tokenizer_eos >= 0:
        return tokenizer_eos
    if eos_token_ids:
        return int(eos_token_ids[0])
    return None


# 목적: tokenized input을 model device로 이동한다.
# 입력: tokenizer output과 model.
# 출력: device 이동된 input object.
def _move_inputs_to_model_device(inputs: Any, model: Any) -> Any:
    target_device = getattr(model, "device", None)
    if target_device is not None and hasattr(inputs, "to"):
        return inputs.to(target_device)
    return inputs


# 목적: prompt token 길이를 추출한다.
# 입력: tokenizer output.
# 출력: input token count.
def _input_sequence_length(inputs: Any) -> int:
    input_ids = inputs["input_ids"] if isinstance(inputs, Mapping) else getattr(inputs, "input_ids")
    shape = getattr(input_ids, "shape", None)
    if shape is not None:
        return int(shape[-1])
    as_list = _to_list(input_ids)
    if as_list and isinstance(as_list[0], list):
        return len(as_list[0])
    return len(as_list)


# 목적: model.generate output에서 새로 생성된 token id만 추출한다.
# 입력: output ids와 prompt token 길이.
# 출력: generated token id list.
def _generated_token_ids(output_ids: Any, input_length: int) -> List[int]:
    sequence = _to_list(output_ids)
    if sequence and isinstance(sequence[0], list):
        sequence = sequence[0]
    return list(sequence[input_length:])


# 목적: tensor-like/list 값을 Python list로 정규화한다.
# 입력: tensor-like value.
# 출력: list.
def _to_list(value: Any) -> List[Any]:
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)
