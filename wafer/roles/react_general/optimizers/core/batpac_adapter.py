"""BatPaC evaluator adapter for optimizers."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .domain import candidate_to_batpac_inputs, repair_candidate
from .evaluation import decompose_evaluator_status

# BatPaC 결과 이름은 workbook 버전에 따라 달라질 수 있으므로 확인된 이름만 지정한다.
BATPAC_OUTPUT_MAP: Dict[str, Optional[str]] = {
    "cost": "cost_per_kwh",
    "total_pack_cost": "pack_cost",
    "P": "power_kw",
    "E": "energy_kwh",
    "V": "batpac_output_nominal_voltage_v",
    "c": "batpac_output_cell_capacity_ah",
}

# 이 값들은 batpac_get_available_outputs()가 아니라 명시적 workbook cell 읽기로 얻는다.
BATPAC_CELL_OUTPUT_MAP: Dict[str, str] = {
    "L_bat": "pack_length_mm",
    "W_bat": "pack_width_mm",
}

# BatPaC workbook 구조가 바뀌면 이 sheet/cell 위치를 다시 확인해야 한다.
BATPAC_CELL_OUTPUTS: Dict[str, Dict[str, str]] = {
    "pack_length_mm": {"sheet": "Summary of Results", "cell": "G52"},
    "pack_width_mm": {"sheet": "Summary of Results", "cell": "G53"},
}

_REQUIRED_SCHEMA_KEYS = ("cost", "P", "E", "V", "c", "L_bat", "W_bat")
_OPTIONAL_SCHEMA_KEYS = ("total_pack_cost",)
_EXPECTED_SCHEMA_KEYS = (*_REQUIRED_SCHEMA_KEYS, *_OPTIONAL_SCHEMA_KEYS)
DEFAULT_PERSISTENT_WORKBOOK_PATH = "optimizer_batpac_working.xlsm"
LIBREOFFICE_BATPAC_BACKEND = "libreoffice_cli"
_REPO_MARKER_RELATIVE_PATHS = (
    Path("src") / "tools" / "batpac.py",
    Path("batpac_codegen2.py"),
)


# 목적: adapter 위치가 바뀌어도 repository root를 marker 파일 기준으로 찾는다.
# 입력: 탐색 시작 path, 기본값은 현재 파일 경로.
# 출력: src/tools/batpac.py와 batpac_codegen2.py를 모두 포함하는 repo root.
def find_repo_root(start_path: Optional[Path] = None) -> Path:
    """Find the repository root for BatPaC support files."""
    start = Path(start_path).resolve() if start_path is not None else Path(__file__).resolve()
    current = start if start.is_dir() else start.parent
    checked_paths: List[Path] = []

    for directory in (current, *current.parents):
        candidate_paths = [directory / relative_path for relative_path in _REPO_MARKER_RELATIVE_PATHS]
        checked_paths.extend(candidate_paths)
        if all(path.exists() for path in candidate_paths):
            return directory

    checked_text = ", ".join(str(path) for path in checked_paths)
    raise ImportError(
        "Could not find repository root containing src/tools/batpac.py "
        f"and batpac_codegen2.py. Checked paths: {checked_text}"
    )


# 목적: 기존 BatPaC wrapper를 optimizer evaluator interface로 감싼다.
# 입력: 선택적 BatPaC output 이름 목록과 stable runtime workbook 경로.
# 출력: evaluator(candidate, condition) callable.
def make_batpac_evaluator(
    outputs: Optional[Sequence[str]] = None,
    persistent_workbook_path: Optional[str] = DEFAULT_PERSISTENT_WORKBOOK_PATH,
    visible: bool = False,
    backend: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]:
    """Create an evaluator backed by the existing BatPaC wrapper."""
    batpac_functions = _load_batpac_functions()
    batpac_evaluate = batpac_functions["batpac_evaluate"]
    available_output_names = _available_output_names(batpac_functions["batpac_get_available_outputs"])
    _validate_output_map(available_output_names)
    requested_outputs = _requested_outputs(outputs)
    selected_backend = _optimizer_backend_name(backend)

    # 목적: 내부 candidate와 condition을 BatPaC 입력으로 변환하고 결과를 schema에 맞춘다.
    # 입력: candidate dict와 condition dict.
    # 출력: optimizer evaluator result dict.
    def evaluator(candidate: Dict[str, Any], condition: Dict[str, Any]) -> Dict[str, Any]:
        repaired = repair_candidate(candidate)
        batpac_inputs = candidate_to_batpac_inputs(repaired, condition)
        _apply_condition_material_cost(batpac_inputs, repaired, condition)
        _apply_runtime_workbook_options(
            batpac_inputs,
            persistent_workbook_path,
            visible,
            selected_backend,
        )
        if output_dir is not None:
            batpac_inputs["output_dir"] = str(Path(output_dir).resolve())
        batpac_inputs["cell_outputs"] = BATPAC_CELL_OUTPUTS
        try:
            raw = batpac_evaluate(batpac_inputs, outputs=requested_outputs)
        except Exception as exc:
            return _adapter_failure(raw={"error": repr(exc), "batpac_inputs": batpac_inputs})

        # 조건 입력이 BatPaC에 실제로 전달됐는지 진단할 수 있도록 raw metadata에 보존한다.
        raw = {**raw, "batpac_inputs": dict(batpac_inputs)}
        return _map_batpac_result(raw)

    return evaluator


# 목적: macOS file-access prompt를 줄이기 위해 stable runtime workbook을 지정한다.
# 입력: BatPaC input dict와 persistent workbook path.
# 출력: 없음, BatPaC input dict를 수정한다.
def _apply_runtime_workbook_options(
    batpac_inputs: Dict[str, Any],
    persistent_workbook_path: Optional[str],
    visible: bool,
    backend: Optional[str] = None,
) -> None:
    if visible:
        batpac_inputs["visible"] = True
    if persistent_workbook_path is None:
        pass
    else:
        # 매번 새 복사본을 만들면 Excel 권한 prompt가 반복될 수 있어 하나의 파일을 재사용한다.
        batpac_inputs["persistent_workbook_path"] = persistent_workbook_path
    if backend:
        batpac_inputs["backend"] = backend


def _optimizer_backend_name(explicit_backend: Optional[str]) -> Optional[str]:
    """Resolve an optimizer backend without falling into xlwings on Linux."""
    value = explicit_backend or os.environ.get("BATPAC_BACKEND")
    if value:
        return str(value).strip().lower()
    if sys.platform.startswith("linux"):
        return LIBREOFFICE_BATPAC_BACKEND
    return None


# 목적: optimizer condition의 CAM cost를 BatPaC legacy override로 전달한다.
# 입력: BatPaC input dict, repaired candidate, condition dict.
# 출력: 없음, BatPaC input dict를 수정한다.
def _apply_condition_material_cost(
    batpac_inputs: Dict[str, Any],
    candidate: Mapping[str, Any],
    condition: Mapping[str, Any],
) -> None:
    # s0 같은 optimizer condition 이름은 BatPaC scenario alias가 아니므로 직접 가격을 쓴다.
    batpac_inputs.pop("scenario", None)
    if candidate["x_chem"] == "LFP":
        batpac_inputs["positive_electrode_kgactive_material_kg_override"] = condition["cam_cost_lfp"]
        return
    batpac_inputs["positive_electrode_kgactive_material_kg_override"] = condition["cam_cost_nmc811"]


# 목적: package import 실패 가능성을 고려해 BatPaC 평가 함수를 불러온다.
# 입력: 없음.
# 출력: public BatPaC function callable dict.
def _load_batpac_functions() -> Dict[str, Callable[..., Dict[str, Any]]]:
    try:
        module = importlib.import_module("src.tools.batpac")
    except Exception:
        module = _load_batpac_module_from_file()

    return {
        "batpac_get_available_outputs": getattr(module, "batpac_get_available_outputs"),
        "batpac_evaluate": getattr(module, "batpac_evaluate"),
    }


# 목적: src package import가 깨질 때 파일 경로로 BatPaC module을 직접 로드한다.
# 입력: 없음.
# 출력: 로드된 module 객체.
def _load_batpac_module_from_file() -> Any:
    repo_root = find_repo_root()
    module_path = repo_root / "src" / "tools" / "batpac.py"
    if not module_path.exists():
        raise ImportError(f"Could not find BatPaC adapter module at {module_path}")

    spec = importlib.util.spec_from_file_location("_batpac_tool_direct", str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load BatPaC adapter module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 목적: adapter가 BatPaC에 요청할 output 이름 목록을 만든다.
# 입력: 사용자 추가 output 이름 목록.
# 출력: 중복 제거된 output 이름 리스트.
def _requested_outputs(outputs: Optional[Sequence[str]]) -> List[str]:
    cell_output_names = set(BATPAC_CELL_OUTPUTS)
    mapped = [
        name
        for name in BATPAC_OUTPUT_MAP.values()
        if name and name not in cell_output_names
    ]
    if outputs is None:
        return _unique_strings(mapped)
    user_outputs = [name for name in outputs if name not in cell_output_names]
    return _unique_strings([*mapped, *user_outputs])


# 목적: BatPaC raw output을 optimizer evaluator schema로 매핑한다.
# 입력: batpac_evaluate raw response.
# 출력: expected evaluator result dict.
def _map_batpac_result(raw: Mapping[str, Any]) -> Dict[str, Any]:
    output_values = raw.get("outputs")
    if not isinstance(output_values, Mapping):
        return _adapter_failure(raw=raw)

    simulator_call_success = raw.get("status") == "success"
    mapped: Dict[str, Any] = {"success": simulator_call_success}
    missing_keys = []
    optional_missing_keys = []
    for schema_key in _REQUIRED_SCHEMA_KEYS:
        output_name = _output_name_for_schema_key(schema_key)
        if not output_name or output_name not in output_values:
            mapped[schema_key] = None
            missing_keys.append(schema_key)
            continue
        mapped[schema_key] = _float_or_none(output_values[output_name])
        if mapped[schema_key] is None:
            missing_keys.append(schema_key)

    for schema_key in _OPTIONAL_SCHEMA_KEYS:
        output_name = _output_name_for_schema_key(schema_key)
        if not output_name or output_name not in output_values:
            mapped[schema_key] = None
            optional_missing_keys.append(schema_key)
            continue
        mapped[schema_key] = _float_or_none(output_values[output_name])
        if mapped[schema_key] is None:
            optional_missing_keys.append(schema_key)

    mapped["raw"] = raw
    status = decompose_evaluator_status(mapped, simulator_call_success=simulator_call_success)
    mapped.update(status)
    if missing_keys or not mapped["optimization_valid"]:
        mapped["raw"] = {
            "batpac": raw,
            "missing_output_keys": missing_keys,
            "optional_missing_output_keys": optional_missing_keys,
            "output_map": dict(BATPAC_OUTPUT_MAP),
            "cell_output_map": dict(BATPAC_CELL_OUTPUT_MAP),
            "failure_reason": mapped["failure_reason"],
        }
    elif optional_missing_keys:
        mapped["raw"] = {
            "batpac": raw,
            "optional_missing_output_keys": optional_missing_keys,
            "output_map": dict(BATPAC_OUTPUT_MAP),
            "cell_output_map": dict(BATPAC_CELL_OUTPUT_MAP),
        }
    return mapped


# 목적: BatPaC adapter 실패를 optimizer가 비교 가능한 result로 변환한다.
# 입력: 실패 원인을 담은 raw 객체.
# 출력: expected evaluator schema를 따르는 실패 result dict.
def _adapter_failure(raw: Any) -> Dict[str, Any]:
    result = {key: None for key in _EXPECTED_SCHEMA_KEYS}
    result["raw"] = raw
    result.update(decompose_evaluator_status(result, simulator_call_success=False))
    return result


# 목적: BatPaC output 값을 float 또는 None으로 정규화한다.
# 입력: raw output 값.
# 출력: float 또는 None.
def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 목적: schema key가 일반 BatPaC output인지 cell output인지 확인한다.
# 입력: expected evaluator schema key.
# 출력: raw output에서 찾을 output 이름 또는 None.
def _output_name_for_schema_key(schema_key: str) -> Optional[str]:
    return BATPAC_OUTPUT_MAP.get(schema_key) or BATPAC_CELL_OUTPUT_MAP.get(schema_key)


# 목적: public output catalog에서 사용 가능한 이름을 추출한다.
# 입력: batpac_get_available_outputs callable.
# 출력: accepted output name set.
def _available_output_names(batpac_get_available_outputs: Callable[..., Dict[str, Any]]) -> Set[str]:
    catalog = batpac_get_available_outputs()
    names = catalog.get("accepted_output_names", [])
    return {str(name) for name in names}


# 목적: BATPAC_OUTPUT_MAP이 검증된 BatPaC output 이름만 쓰는지 확인한다.
# 입력: batpac_get_available_outputs()에서 얻은 이름 set.
# 출력: 없음, 검증 실패 시 ValueError.
def _validate_output_map(available_output_names: Set[str]) -> None:
    unknown = [
        name
        for name in BATPAC_OUTPUT_MAP.values()
        if name is not None and name not in available_output_names
    ]
    if unknown:
        raise ValueError(
            "BATPAC_OUTPUT_MAP contains names not advertised by "
            f"batpac_get_available_outputs(): {unknown}"
        )


# 목적: output 요청 목록의 순서를 유지하면서 중복을 제거한다.
# 입력: 문자열 iterable.
# 출력: unique string list.
def _unique_strings(values: Iterable[str]) -> List[str]:
    unique: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
