"""BatPaC tool layer.

This module is intentionally an adapter around the existing Excel runner. It
does not move or rewrite the legacy BatPaC scripts.
"""

from __future__ import annotations

import ast
import inspect
import importlib.util
import math
import os
import re
import shutil
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# BatPaC wrapper 파일은 repo/src/tools 아래에 있으므로 parents[2]가 repository root다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKBOOK = PROJECT_ROOT / "BatPaC 5.0 2022-02-09.xlsm"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs_mcp" / "batpac_evaluate"
BATPAC_RUNTIME_DIR = PROJECT_ROOT / "batpac_runtime"
DEFAULT_BATPAC_BACKEND = "excel_xlwings"
LIBREOFFICE_BATPAC_BACKEND = "libreoffice_cli"
_CELL_ADDRESS_RE = re.compile(r"^[A-Z]+[0-9]+$")
_SPREADSHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

_FALLBACK_RESULT_ATTRIBUTES = [
    "Configuration Errors",
    "Configuration Warnings",
    "Battery system total energy, kWh",
    "Battery system capacity, Ah",
    "Battery system rated power, kW",
    "Battery system nominal voltage (average OCV), V",
    "Pack power to energy ratio (with respect to rated power)",
    "Pack energy density, Wh/L",
    "Pack specific energy, Wh/kg",
    "Number of modules per pack",
    "Number of cells per pack",
    "Pack volume, L",
    "Pack total mass, kg",
    "Cell capacity, Ah",
    "Cell nominal voltage (average OCV), V",
    "Positive electrode thickness, um",
    "Positive electode areal capacity, mAh/cm2",
    "Negative electrode thickness, um",
    "Negative electrode areal capacity, mAh/cm2",
    "Cell energy density, Wh/L",
    "Cell specific energy, Wh/kg",
    "Pack cost, $/pack",
    "Pack cost, $/kWh",
    "Pack cost, $/kWh(Useable)",
    "Cell cost, $/kWh",
    "Plant Size, GWh",
    "Packs manufactured per year (packs/year)",
]

_FRIENDLY_ALIAS_TO_ATTRIBUTE = {
    "cost_per_kwh": "pack cost, $/kwh",
    "pack_cost": "pack cost, $/pack",
    "energy_kwh": "battery system total energy, kwh",
    "power_kw": "battery system rated power, kw",
    "volume_l": "pack volume, l",
    "mass_kg": "pack total mass, kg",
    "capacity_ah": "battery system capacity, ah",
}

_ATTRIBUTE_TO_LEGACY_NAME = {
    "configuration errors": "batpac_output_configuration_errors",
    "configuration warnings": "batpac_output_configuration_warnings",
    "battery system total energy, kwh": "batpac_output_energy_kwh",
    "battery system capacity, ah": "batpac_output_capacity_ah",
    "battery system rated power, kw": "batpac_output_power_kw",
    "battery system nominal voltage (average ocv), v": "batpac_output_nominal_voltage_v",
    "pack power to energy ratio (with respect to rated power)": "batpac_output_power_to_energy_ratio",
    "pack energy density, wh/l": "batpac_output_pack_energy_density_wh_l",
    "pack specific energy, wh/kg": "batpac_output_pack_specific_energy_wh_kg",
    "number of modules per pack": "batpac_output_modules_per_pack",
    "number of cells per pack": "batpac_output_cells_per_pack",
    "pack volume, l": "batpac_output_pack_volume_l",
    "pack total mass, kg": "batpac_output_mass_kg",
    "cell capacity, ah": "batpac_output_cell_capacity_ah",
    "cell nominal voltage (average ocv), v": "batpac_output_cell_nominal_voltage_v",
    "positive electrode thickness, um": "batpac_output_positive_electrode_thickness_um",
    "positive electode areal capacity, mah/cm2": "batpac_output_positive_areal_capacity_mah_cm2",
    "negative electrode thickness, um": "batpac_output_negative_electrode_thickness_um",
    "negative electrode areal capacity, mah/cm2": "batpac_output_negative_areal_capacity_mah_cm2",
    "cell energy density, wh/l": "batpac_output_cell_energy_density_wh_l",
    "cell specific energy, wh/kg": "batpac_output_cell_specific_energy_wh_kg",
    "pack cost, $/pack": "batpac_output_pack_cost_per_pack_usd",
    "pack cost, $/kwh": "batpac_output_pack_cost_per_kwh",
    "pack cost, $/kwh(useable)": "batpac_output_pack_cost_per_kwh_useable",
    "cell cost, $/kwh": "batpac_output_cell_cost_per_kwh",
    "plant size, gwh": "batpac_output_plant_size_gwh",
    "packs manufactured per year (packs/year)": "batpac_output_packs_manufactured_per_year",
}

_HIGH_LEVEL_INPUT_ALIASES = ("chemistry", "x_T", "x_S", "x_MPR", "x_R", "scenario")
_CHEMISTRY_ALIASES = {
    "NMC811": "NMC811-G (Energy)",
    "NMC811-G": "NMC811-G (Energy)",
    "NMC811-G (ENERGY)": "NMC811-G (Energy)",
    "LFP": "LFP-G (Energy)",
    "LFP-G": "LFP-G (Energy)",
    "LFP-G (ENERGY)": "LFP-G (Energy)",
}
_CHEMISTRY_FAMILY_ALIASES = {
    "NMC811": "NMC811",
    "NMC811-G": "NMC811",
    "NMC811-G (ENERGY)": "NMC811",
    "LFP": "LFP",
    "LFP-G": "LFP",
    "LFP-G (ENERGY)": "LFP",
}
_DESIGN_ALIAS_TO_LEGACY_KEY = {
    "x_T": "battery_1_optional_positive_electrode_thickness_override_um",
    "x_S": "battery_1_number_of_cells_per_module",
    "x_MPR": "battery_1_number_of_modules_in_row",
    "x_R": "battery_1_number_of_rows_of_modules_per_pack",
}
_ACTIVE_MATERIAL_PRICE_BY_CHEMISTRY_AND_SCENARIO = {
    "NMC811": {
        "no_shock": 19.63,
        "lithium_4x": 44.65,
        "cobalt_4x": 25.75,
        "cobalt_10x": 37.19,
    },
    "LFP": {
        "no_shock": 8.70,
        "lithium_4x": 24.16,
        "cobalt_4x": 8.70,
        "cobalt_10x": 8.70,
    },
}


def batpac_get_available_inputs() -> Dict[str, Any]:
    """Return BatPaC Dashboard inputs accepted by ``batpac_evaluate``."""
    _, cell_address_map = _load_batpac_codegen_metadata()
    # Release: defaults are read from the user's BatPaC 5.0 workbook instead of being baked in.
    from batpac_workbook import read_workbook_defaults

    field_defaults = read_workbook_defaults(DEFAULT_WORKBOOK, cell_address_map)
    legacy_inputs = []
    for name, default in field_defaults.items():
        legacy_inputs.append(
            {
                "name": name,
                "address": cell_address_map.get(name),
                "default": _jsonable(default),
            }
        )
    high_level_aliases = _high_level_input_alias_specs()
    return {
        "source": "batpac_codegen2.BatPaCInputs",
        "count": len(high_level_aliases) + len(legacy_inputs),
        "high_level_alias_count": len(high_level_aliases),
        "high_level_aliases": high_level_aliases,
        "legacy_input_count": len(legacy_inputs),
        "legacy_inputs": legacy_inputs,
        "inputs": legacy_inputs,
        "direct_cell_addresses_allowed": True,
        "direct_cell_address_rule": "Excel cell addresses matching ^[A-Z]+[0-9]+$, for example D60.",
        "runtime_directory": str(BATPAC_RUNTIME_DIR),
        "default_backend": DEFAULT_BATPAC_BACKEND,
        "selected_backend": _selected_backend_name(),
        "notes": [
            "High-level aliases may be passed top-level or under inputs.overrides.",
            "Legacy names may be passed under inputs.overrides, or pass Dashboard cell addresses such as D60.",
            "Only overrides are written; BatPaC defaults are not rewritten by this adapter.",
            "Official D6 convention: Dashboard!D6 is pulsed and outputs are read with D6 off.",
            f"Grant Excel file access to the stable runtime folder once: {BATPAC_RUNTIME_DIR}",
            "Relative persistent_workbook_path values are resolved inside batpac_runtime.",
        ],
    }


def batpac_get_available_outputs() -> Dict[str, Any]:
    """Return BatPaC output names accepted by ``batpac_evaluate``."""
    metadata = _load_result_block_metadata(DEFAULT_WORKBOOK)
    catalog = _build_output_catalog(metadata["attributes"])
    friendly_aliases = [
        {
            "name": alias,
            "attribute": catalog[alias]["attribute"],
            "legacy_name": catalog[alias]["legacy_name"],
        }
        for alias in _FRIENDLY_ALIAS_TO_ATTRIBUTE
        if alias in catalog
    ]
    legacy_names = [
        {
            "name": spec["legacy_name"],
            "attribute": spec["attribute"],
        }
        for spec in catalog.values()
        if spec.get("kind") == "legacy"
    ]
    return {
        "source": "batpac_codegen2.get_results_block(Dashboard!B187:J214)",
        "count": len(catalog),
        "friendly_aliases": friendly_aliases,
        "raw_attributes": metadata["attributes"],
        "legacy_names": _unique_dicts_by_name(legacy_names),
        "accepted_output_names": list(catalog),
        "result_block": {
            "sheet": "Dashboard",
            "range": "B187:J214",
            "headers": metadata["headers"],
            "default_battery_column": "Battery 1",
        },
    }


def batpac_evaluate(inputs: Dict[str, Any], outputs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Run BatPaC once through the existing Excel runner adapter.

    Parameters
    ----------
    inputs:
        A JSON-serializable mapping. Reserved keys configure the adapter:
        ``workbook_path``/``batpac_workbook``, ``output_dir``, ``visible``,
        ``recalc_sleep``, ``recalc_mode``, ``persistent_workbook``, and
        ``persistent_workbook_path``. Named workbook cells may be requested
        under ``cell_outputs``. BatPaC overrides may be supplied under
        ``overrides`` or directly as top-level input names/cell addresses.
        High-level aliases ``chemistry``, ``scenario``, ``x_T``, ``x_S``,
        ``x_MPR``, and ``x_R`` are translated before legacy validation.
        Runtime workbook copies are created under ``batpac_runtime/`` at the
        project root so Excel access can be granted to that folder once.
    outputs:
        Optional list of output names from ``batpac_get_available_outputs``.
        If omitted, all known outputs are returned.
    """
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a mapping")

    backend_name = _selected_backend_name(inputs.get("backend"))
    if backend_name == LIBREOFFICE_BATPAC_BACKEND:
        return _batpac_evaluate_libreoffice_cli(inputs, outputs)
    if backend_name != DEFAULT_BATPAC_BACKEND:
        raise ValueError(
            f"Unsupported BATPAC_BACKEND={backend_name!r}. "
            f"Supported values: {DEFAULT_BATPAC_BACKEND!r}, {LIBREOFFICE_BATPAC_BACKEND!r}."
        )

    return _batpac_evaluate_with_runner_module(
        inputs=inputs,
        outputs=outputs,
        runner_module=_legacy_codegen_module(),
        backend_name=DEFAULT_BATPAC_BACKEND,
    )


def _batpac_evaluate_with_runner_module(
    inputs: Dict[str, Any],
    outputs: Optional[Sequence[str]],
    runner_module: Any,
    backend_name: str,
) -> Dict[str, Any]:
    workbook_path = _resolve_workbook_path(inputs)
    result_metadata = _load_result_block_metadata(workbook_path)
    output_catalog = _build_output_catalog(result_metadata["attributes"])
    requested_outputs = _normalize_requested_outputs(outputs, output_catalog)
    cell_outputs = _normalize_cell_outputs(inputs.get("cell_outputs"))
    _, cell_address_map = _load_batpac_codegen_metadata()
    legacy_inputs, alias_overrides = _consume_high_level_input_aliases(inputs)
    legacy_overrides = _extract_overrides(legacy_inputs, cell_address_map)
    overrides = {**alias_overrides, **legacy_overrides}
    output_dir = Path(inputs.get("output_dir") or DEFAULT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not workbook_path.exists():
        raise FileNotFoundError(f"BatPaC workbook not found: {workbook_path}")

    visible = bool(inputs.get("visible", False))
    debug = bool(inputs.get("debug", False) or inputs.get("debug_raw_result_block", False))
    battery_column = str(inputs.get("battery_column", "Battery 1"))

    started = time.perf_counter()
    workbook_to_open = _prepare_workbook_to_open(inputs, workbook_path, output_dir)
    cleanup_runtime_workbook = (
        not inputs.get("persistent_workbook_path")
        and not inputs.get("persistent_workbook", False)
        and _is_transient_runtime_workbook(workbook_to_open)
    )

    try:
        runner_result = _call_batpac_by_python(
            runner_module=runner_module,
            workbook_to_open=workbook_to_open,
            visible=visible,
            cell_outputs=cell_outputs,
            overrides=overrides,
        )
        result_block, timings, cell_values = _normalize_batpac_by_python_result(
            runner_result=runner_result,
            requested_cell_outputs=bool(cell_outputs),
        )

        table_values = _extract_result_table_values(result_block, battery_column, runner_module)
        output_values = {
            name: _jsonable(_value_for_output_spec(spec, table_values))
            for name, spec in requested_outputs.items()
        }
        # Dashboard 결과표 밖의 값은 명시적으로 요청된 이름으로만 outputs에 추가한다.
        output_values.update({name: _jsonable(value) for name, value in cell_values.items()})
        elapsed_s = time.perf_counter() - started
        response = {
            "status": "success",
            "outputs": output_values,
            "metadata": {
                "workbook_path": str(workbook_path),
                "workbook_opened": str(workbook_to_open),
                "runtime_directory": str(BATPAC_RUNTIME_DIR),
                "output_dir": str(output_dir),
                "backend": backend_name,
                "input_count": len(overrides),
                "elapsed_s": elapsed_s,
                "timings": {key: _jsonable(value) for key, value in timings.items()},
                "battery_column": table_values["battery_column"],
                "cell_outputs": cell_outputs,
            },
        }
        if debug:
            response["debug"] = {
                "raw_result_block_columns": [str(col) for col in result_block.columns],
                "raw_result_block_records": _dataframe_records(result_block),
            }
        return response
    finally:
        if cleanup_runtime_workbook:
            active_exception = sys.exc_info()[0] is not None
            try:
                _remove_transient_runtime_workbook(workbook_to_open)
            except OSError:
                # Preserve the runner's original exception.  If evaluation
                # otherwise succeeded, surface cleanup failure to prevent a
                # silent recurrence of runtime-file accumulation.
                if not active_exception:
                    raise


def _legacy_codegen_module() -> Any:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import batpac_codegen2

    return batpac_codegen2


def _selected_backend_name(explicit_backend: Any = None) -> str:
    raw_backend = explicit_backend or os.environ.get("BATPAC_BACKEND", DEFAULT_BATPAC_BACKEND)
    backend = str(raw_backend or DEFAULT_BATPAC_BACKEND).strip().lower()
    return backend or DEFAULT_BATPAC_BACKEND


def _batpac_evaluate_libreoffice_cli(inputs: Dict[str, Any], outputs: Optional[Sequence[str]]) -> Dict[str, Any]:
    module = _libreoffice_cli_module()
    if hasattr(module, "batpac_evaluate"):
        # LibreOffice cannot rely on Excel macros; outputs should be read in the post-restart/off state.
        # 서버 CLI backend는 Dashboard!D6=0 최종 상태를 기준으로 Excel pulse/off 결과와 맞춘다.
        return module.batpac_evaluate(inputs, outputs=outputs)
    if hasattr(module, "batpac_by_python"):
        # LibreOffice cannot rely on Excel macros; outputs should be read in the post-restart/off state.
        # 서버 CLI backend는 Dashboard!D6=0 최종 상태를 기준으로 Excel pulse/off 결과와 맞춘다.
        return _batpac_evaluate_with_runner_module(
            inputs=inputs,
            outputs=outputs,
            runner_module=module,
            backend_name=LIBREOFFICE_BATPAC_BACKEND,
        )
    raise ImportError(
        "BATPAC_BACKEND=libreoffice_cli requires batpac_codegen_lo_cli.py to expose either "
        "batpac_evaluate(inputs, outputs=None) or batpac_by_python(...)."
    )


def _call_batpac_by_python(
    runner_module: Any,
    workbook_to_open: Path,
    visible: bool,
    cell_outputs: Dict[str, Dict[str, str]],
    overrides: Dict[str, Any],
) -> Any:
    runner = getattr(runner_module, "batpac_by_python", None)
    if runner is None:
        raise ImportError("BatPaC runner module does not expose batpac_by_python(...).")

    positional_args = []
    call_kwargs: Dict[str, Any] = {}
    parameters = inspect.signature(runner).parameters
    accepts_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    if _accepts_keyword(parameters, accepts_var_keyword, "xlsm_path"):
        call_kwargs["xlsm_path"] = workbook_to_open
    elif _accepts_keyword(parameters, accepts_var_keyword, "workbook_path"):
        call_kwargs["workbook_path"] = workbook_to_open
    else:
        positional_args.append(workbook_to_open)

    optional_kwargs = {
        "visible": visible,
        "return_timing": True,
        "extra_cells": cell_outputs if cell_outputs else None,
    }
    for key, value in optional_kwargs.items():
        if _accepts_keyword(parameters, accepts_var_keyword, key):
            call_kwargs[key] = value

    for key, value in overrides.items():
        if _accepts_keyword(parameters, accepts_var_keyword, key):
            call_kwargs[key] = value

    return runner(*positional_args, **call_kwargs)


def _accepts_keyword(
    parameters: Mapping[str, inspect.Parameter],
    accepts_var_keyword: bool,
    key: str,
) -> bool:
    return accepts_var_keyword or key in parameters


def _normalize_batpac_by_python_result(
    runner_result: Any,
    requested_cell_outputs: bool,
) -> Tuple[Any, Dict[str, Any], Dict[str, Any]]:
    if isinstance(runner_result, tuple):
        if len(runner_result) >= 3:
            return runner_result[0], _dict_or_empty(runner_result[1]), _dict_or_empty(runner_result[2])
        if len(runner_result) == 2:
            return runner_result[0], _dict_or_empty(runner_result[1]), {}
        if len(runner_result) == 1:
            return runner_result[0], {}, {}
    if requested_cell_outputs:
        return runner_result, {}, {}
    return runner_result, {}, {}


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _libreoffice_cli_module() -> Any:
    candidates = [
        PROJECT_ROOT / "batpac_codegen_lo_cli.py",
        PROJECT_ROOT / "src" / "tools" / "batpac_codegen_lo_cli.py",
    ]
    for path in candidates:
        if path.exists():
            spec = importlib.util.spec_from_file_location("batpac_codegen_lo_cli", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load LibreOffice BatPaC backend module at {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules["batpac_codegen_lo_cli"] = module
            try:
                spec.loader.exec_module(module)
            except ImportError as exc:
                raise ImportError(
                    "BATPAC_BACKEND=libreoffice_cli was requested, but its backend module "
                    f"could not be imported from {path}: {exc}"
                ) from exc
            return module
    checked = ", ".join(str(path) for path in candidates)
    raise ImportError(
        "BATPAC_BACKEND=libreoffice_cli was requested, but no LibreOffice BatPaC backend "
        f"module was found. Checked: {checked}. The server backend should implement D6=0 "
        "post-restart/off reads and only keep calculated xlsx files when BATPAC_KEEP_LO_XLSX=1."
    )


def _load_batpac_codegen_metadata() -> Tuple[Dict[str, Any], Dict[str, str]]:
    tree = ast.parse((PROJECT_ROOT / "batpac_codegen2.py").read_text(encoding="utf-8"))
    field_defaults: Dict[str, Any] = {}
    cell_address_map: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BatPaCInputs":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_defaults[stmt.target.id] = ast.literal_eval(stmt.value) if stmt.value else None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "CELL_ADDRESS_MAP" and node.value is not None:
                cell_address_map = ast.literal_eval(node.value)
    # Release: batpac_codegen2.py stores no default values; names come from CELL_ADDRESS_MAP.
    field_defaults = field_defaults or dict.fromkeys(cell_address_map)
    if not field_defaults or not cell_address_map:
        raise RuntimeError("Could not read BatPaC input metadata from batpac_codegen2.py")
    return field_defaults, cell_address_map


def _load_result_block_metadata(workbook_path: Path) -> Dict[str, Any]:
    try:
        return _read_xlsm_result_block_metadata(workbook_path)
    except Exception:
        return {
            "headers": [],
            "attributes": list(_FALLBACK_RESULT_ATTRIBUTES),
        }


def _normalize_requested_outputs(
    outputs: Optional[Sequence[str]],
    output_catalog: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    if outputs is None:
        requested = _default_output_names(output_catalog)
    elif isinstance(outputs, str):
        requested = [outputs]
    else:
        requested = list(outputs)
    resolved: Dict[str, Dict[str, Any]] = {}
    unknown = []
    for name in requested:
        name_text = str(name)
        spec = output_catalog.get(name_text) or output_catalog.get(_normalize_attr(name_text))
        if spec is None:
            unknown.append(name_text)
        else:
            resolved[name_text] = spec
    if unknown:
        available = _available_output_names_for_error(output_catalog)
        raise ValueError(f"Unknown BatPaC output names: {unknown}. Available outputs: {available}")
    return resolved


def _build_output_catalog(attributes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    attr_by_norm = {
        _normalize_attr(attr): str(attr)
        for attr in attributes
        if str(attr or "").strip()
    }
    for attr in _FALLBACK_RESULT_ATTRIBUTES:
        attr_by_norm.setdefault(_normalize_attr(attr), attr)

    catalog: Dict[str, Dict[str, Any]] = {}
    for alias, attr_norm in _FRIENDLY_ALIAS_TO_ATTRIBUTE.items():
        attribute = attr_by_norm.get(attr_norm)
        if attribute is None:
            continue
        catalog[alias] = {
            "kind": "friendly",
            "attribute": attribute,
            "attribute_norm": attr_norm,
            "legacy_name": _ATTRIBUTE_TO_LEGACY_NAME.get(attr_norm),
        }

    for attr_norm, attribute in attr_by_norm.items():
        legacy_name = _ATTRIBUTE_TO_LEGACY_NAME.get(attr_norm)
        catalog.setdefault(
            attribute,
            {
                "kind": "raw_attribute",
                "attribute": attribute,
                "attribute_norm": attr_norm,
                "legacy_name": legacy_name,
            },
        )
        catalog.setdefault(
            attr_norm,
            {
                "kind": "raw_attribute_normalized",
                "attribute": attribute,
                "attribute_norm": attr_norm,
                "legacy_name": legacy_name,
            },
        )
        if legacy_name:
            catalog[legacy_name] = {
                "kind": "legacy",
                "attribute": attribute,
                "attribute_norm": attr_norm,
                "legacy_name": legacy_name,
            }
    return catalog


def _default_output_names(output_catalog: Dict[str, Dict[str, Any]]) -> Sequence[str]:
    names = [name for name in _FRIENDLY_ALIAS_TO_ATTRIBUTE if name in output_catalog]
    for legacy_name in _ATTRIBUTE_TO_LEGACY_NAME.values():
        if legacy_name in output_catalog and legacy_name not in names:
            names.append(legacy_name)
    return names


def _available_output_names_for_error(output_catalog: Dict[str, Dict[str, Any]]) -> Dict[str, Sequence[str]]:
    friendly = [name for name in _FRIENDLY_ALIAS_TO_ATTRIBUTE if name in output_catalog]
    legacy = [
        spec["legacy_name"]
        for spec in output_catalog.values()
        if spec.get("kind") == "legacy" and spec.get("legacy_name")
    ]
    raw_attributes = [
        spec["attribute"]
        for spec in output_catalog.values()
        if spec.get("kind") == "raw_attribute"
    ]
    return {
        "friendly_aliases": friendly,
        "legacy_names": sorted(set(legacy)),
        "raw_attributes": raw_attributes,
    }


def _read_xlsm_result_block_metadata(workbook_path: Path) -> Dict[str, Any]:
    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)
    with zipfile.ZipFile(workbook_path) as workbook:
        shared_strings = _read_shared_strings(workbook)
        dashboard_path = _dashboard_sheet_path(workbook)
        sheet = ET.fromstring(workbook.read(dashboard_path))

    cells = {}
    for cell in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = cell.attrib.get("r")
        if not ref:
            continue
        col = "".join(ch for ch in ref if ch.isalpha())
        row_text = "".join(ch for ch in ref if ch.isdigit())
        if col < "B" or col > "J" or not row_text:
            continue
        row = int(row_text)
        if row < 187 or row > 214:
            continue
        cells[ref] = _cell_value(cell, shared_strings)

    headers = [cells.get(f"{col}187") for col in "BCDEFGHIJ"]
    attributes = [cells.get(f"C{row}") for row in range(188, 215)]
    return {
        "headers": [_jsonable(header) for header in headers],
        "attributes": [str(attr) for attr in attributes if attr not in {None, ""}],
    }


def _read_shared_strings(workbook: zipfile.ZipFile) -> Sequence[str]:
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings = []
    for item in root.findall("m:si", _SPREADSHEET_NS):
        strings.append(
            "".join(
                text.text or ""
                for text in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
            )
        )
    return strings


def _dashboard_sheet_path(workbook: zipfile.ZipFile) -> str:
    workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_xml}
    sheets = workbook_xml.find("m:sheets", _SPREADSHEET_NS)
    if sheets is None:
        raise RuntimeError("Workbook has no sheets collection")
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sheet in sheets:
        if sheet.attrib.get("name") == "Dashboard":
            target = rels[sheet.attrib[rel_attr]]
            return target if target.startswith("xl/") else f"xl/{target}"
    raise RuntimeError("Workbook has no Dashboard sheet")


def _cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> Any:
    value_node = cell.find("m:v", _SPREADSHEET_NS)
    if value_node is None:
        return None
    value = value_node.text
    if cell.attrib.get("t") == "s" and value is not None:
        return shared_strings[int(value)]
    return value


def _extract_result_table_values(result_block: Any, battery_column: str, codegen: Any) -> Dict[str, Any]:
    if result_block is None or result_block.empty or "Attribute" not in result_block.columns:
        raise RuntimeError("BatPaC result block is empty or missing the Attribute column")
    if battery_column not in result_block.columns:
        battery_candidates = [col for col in result_block.columns if str(col).startswith("Battery ")]
        if battery_candidates:
            battery_column = str(battery_candidates[0])
        elif len(result_block.columns) >= 3:
            battery_column = str(result_block.columns[2])
        else:
            raise RuntimeError("BatPaC result block has no Battery result columns")

    values_by_attr: Dict[str, Any] = {}
    raw_attrs: Dict[str, str] = {}
    for _, row in result_block.iterrows():
        attribute = row.get("Attribute")
        attr_norm = _normalize_attr(attribute)
        if not attr_norm:
            continue
        value = row.get(battery_column)
        if attr_norm in {"configuration errors", "configuration warnings"}:
            parsed = value
        else:
            parsed = _parse_result_numeric(value, codegen)
        values_by_attr[attr_norm] = parsed
        raw_attrs[attr_norm] = str(attribute)
    return {
        "battery_column": battery_column,
        "values_by_attr": values_by_attr,
        "raw_attrs": raw_attrs,
    }


def _parse_result_numeric(value: Any, codegen: Any) -> Any:
    parser = getattr(codegen, "_parse_numeric", None)
    if callable(parser):
        try:
            return parser(value)
        except Exception:
            pass

    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text or text.upper() in {"#N/A", "N/A", "NA", "NONE", "NULL", "NAN"}:
        return None
    normalized = text.replace("$", "").replace(",", "").strip()
    if normalized.endswith("%"):
        normalized = normalized[:-1].strip()
    try:
        return float(normalized)
    except ValueError:
        return value


def _value_for_output_spec(spec: Dict[str, Any], table_values: Dict[str, Any]) -> Any:
    return table_values["values_by_attr"].get(spec["attribute_norm"])


def _normalize_attr(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\u00b5", "u").replace("\u03bc", "u").replace("\u00b2", "2")
    return " ".join(text.split())


def _dataframe_records(df: Any) -> Sequence[Dict[str, Any]]:
    records = df.to_dict(orient="records")
    return [
        {str(key): _jsonable(value) for key, value in record.items()}
        for record in records
    ]


def _unique_dicts_by_name(items: Sequence[Dict[str, Any]]) -> Sequence[Dict[str, Any]]:
    out = []
    seen = set()
    for item in items:
        name = item.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(item)
    return out


def _high_level_input_alias_specs() -> Sequence[Dict[str, Any]]:
    return [
        {
            "name": "chemistry",
            "accepted_values": ["NMC811", "LFP"],
            "legacy_keys": [
                "electrode_couplecurrent_selection",
                "electorode_couplechoose",
            ],
            "notes": "Maps to the BatPaC chemistry selection text.",
        },
        {
            "name": "x_T",
            "legacy_key": _DESIGN_ALIAS_TO_LEGACY_KEY["x_T"],
            "units": "um",
        },
        {
            "name": "x_S",
            "legacy_key": _DESIGN_ALIAS_TO_LEGACY_KEY["x_S"],
            "units": "cells/module",
        },
        {
            "name": "x_MPR",
            "legacy_key": _DESIGN_ALIAS_TO_LEGACY_KEY["x_MPR"],
            "units": "modules/row",
        },
        {
            "name": "x_R",
            "legacy_key": _DESIGN_ALIAS_TO_LEGACY_KEY["x_R"],
            "units": "rows/pack",
        },
        {
            "name": "scenario",
            "requires": ["chemistry"],
            "legacy_key": "positive_electrode_kgactive_material_kg_override",
            "price_table": _ACTIVE_MATERIAL_PRICE_BY_CHEMISTRY_AND_SCENARIO,
        },
    ]


def _consume_high_level_input_aliases(inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    legacy_inputs = dict(inputs)
    high_level_values: Dict[str, Any] = {}

    nested = legacy_inputs.get("overrides", {})
    if isinstance(nested, dict):
        nested_legacy = dict(nested)
        for alias in _HIGH_LEVEL_INPUT_ALIASES:
            if alias in nested_legacy:
                high_level_values[alias] = nested_legacy.pop(alias)
        legacy_inputs["overrides"] = nested_legacy

    for alias in _HIGH_LEVEL_INPUT_ALIASES:
        if alias in legacy_inputs:
            high_level_values[alias] = legacy_inputs.pop(alias)

    return legacy_inputs, _translate_high_level_input_aliases(high_level_values)


def _translate_high_level_input_aliases(values: Dict[str, Any]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    if "chemistry" in values:
        batpac_chemistry = _map_high_level_chemistry(values["chemistry"])
        overrides["electrode_couplecurrent_selection"] = batpac_chemistry
        overrides["electorode_couplechoose"] = batpac_chemistry

    for alias, legacy_key in _DESIGN_ALIAS_TO_LEGACY_KEY.items():
        if alias in values:
            overrides[legacy_key] = values[alias]

    if "scenario" in values:
        if "chemistry" not in values:
            raise ValueError(
                "High-level BatPaC input 'scenario' requires high-level input 'chemistry'. "
                f"Recognized high-level aliases: {list(_HIGH_LEVEL_INPUT_ALIASES)}"
            )
        chemistry_key = _chemistry_family_key(values["chemistry"])
        scenario_key = _scenario_key(values["scenario"])
        try:
            price = _ACTIVE_MATERIAL_PRICE_BY_CHEMISTRY_AND_SCENARIO[chemistry_key][scenario_key]
        except KeyError as exc:
            raise ValueError(
                "Unknown BatPaC high-level chemistry/scenario combination: "
                f"chemistry={values['chemistry']!r}, scenario={values['scenario']!r}. "
                f"Available price table: {_ACTIVE_MATERIAL_PRICE_BY_CHEMISTRY_AND_SCENARIO}"
            ) from exc
        overrides["positive_electrode_kgactive_material_kg_override"] = price
    return overrides


def _map_high_level_chemistry(value: Any) -> str:
    text = str(value).strip()
    key = text.upper()
    if key not in _CHEMISTRY_ALIASES:
        raise ValueError(
            f"Unknown BatPaC high-level chemistry {value!r}. "
            f"Accepted chemistry aliases: {sorted(_CHEMISTRY_ALIASES)}"
        )
    return _CHEMISTRY_ALIASES[key]


def _chemistry_family_key(value: Any) -> str:
    text = str(value).strip()
    key = text.upper()
    if key not in _CHEMISTRY_FAMILY_ALIASES:
        raise ValueError(
            f"Unknown BatPaC high-level chemistry {value!r}. "
            f"Accepted chemistry aliases: {sorted(_CHEMISTRY_FAMILY_ALIASES)}"
        )
    return _CHEMISTRY_FAMILY_ALIASES[key]


def _scenario_key(value: Any) -> str:
    return str(value).strip().lower()


# 목적: BatPaC 결과표 밖의 workbook cell output 요청을 검증하고 정규화한다.
# 입력: None 또는 output 이름별 sheet/cell spec.
# 출력: codegen이 읽을 수 있는 normalized cell output spec dict.
def _normalize_cell_outputs(raw_cell_outputs: Any) -> Dict[str, Dict[str, str]]:
    if raw_cell_outputs is None:
        return {}
    if not isinstance(raw_cell_outputs, dict):
        raise TypeError("inputs.cell_outputs must be a mapping when provided")

    normalized: Dict[str, Dict[str, str]] = {}
    for name, raw_spec in raw_cell_outputs.items():
        output_name = str(name).strip()
        if not output_name:
            raise ValueError("inputs.cell_outputs contains an empty output name")
        normalized[output_name] = _normalize_cell_output_spec(raw_spec)
    return normalized


# 목적: cell output spec의 sheet 이름과 cell 주소를 명확한 형태로 바꾼다.
# 입력: {"sheet": str, "cell": str} 또는 "Sheet!G52" 문자열.
# 출력: {"sheet": str, "cell": str}.
def _normalize_cell_output_spec(raw_spec: Any) -> Dict[str, str]:
    if isinstance(raw_spec, str):
        if "!" not in raw_spec:
            raise ValueError(f"Cell output string must use 'Sheet!A1' format: {raw_spec!r}")
        sheet, cell = raw_spec.rsplit("!", 1)
    elif isinstance(raw_spec, dict):
        sheet = raw_spec.get("sheet")
        cell = raw_spec.get("cell") or raw_spec.get("address")
    else:
        raise TypeError(f"Cell output spec must be a mapping or string, got {raw_spec!r}")

    sheet_text = str(sheet or "").strip().strip("'")
    cell_text = str(cell or "").strip().upper()
    if not sheet_text:
        raise ValueError(f"Cell output spec is missing sheet name: {raw_spec!r}")
    if not _CELL_ADDRESS_RE.fullmatch(cell_text):
        raise ValueError(f"Cell output spec has invalid cell address: {raw_spec!r}")
    return {"sheet": sheet_text, "cell": cell_text}


def _extract_overrides(inputs: Dict[str, Any], cell_address_map: Dict[str, str]) -> Dict[str, Any]:
    reserved = {
        "batpac_workbook",
        "workbook_path",
        "output_dir",
        "visible",
        "recalc_sleep",
        "recalc_mode",
        "persistent_workbook",
        "persistent_workbook_path",
        "backend",
        "battery_column",
        "debug",
        "debug_raw_result_block",
        "overrides",
        "cell_outputs",
    }
    raw_overrides: Dict[str, Any] = {}
    nested = inputs.get("overrides", {})
    if nested is not None:
        if not isinstance(nested, dict):
            raise TypeError("inputs.overrides must be a mapping when provided")
        raw_overrides.update(nested)
    for key, value in inputs.items():
        if key not in reserved:
            raw_overrides[key] = value

    unknown = [
        key
        for key in raw_overrides
        if key not in cell_address_map and not _CELL_ADDRESS_RE.fullmatch(str(key).upper())
    ]
    if unknown:
        available = {
            "recognized_high_level_aliases": list(_HIGH_LEVEL_INPUT_ALIASES),
            "recognized_legacy_batpac_keys": sorted(cell_address_map),
            "recognized_direct_excel_cell_addresses": "strings matching ^[A-Z]+[0-9]+$, for example D60",
        }
        raise ValueError(f"Unknown BatPaC input names: {unknown}. Recognized inputs: {available}")
    return raw_overrides


def _resolve_workbook_path(inputs: Dict[str, Any]) -> Path:
    raw_path = inputs.get("workbook_path") or inputs.get("batpac_workbook") or DEFAULT_WORKBOOK
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _prepare_workbook_to_open(
    inputs: Dict[str, Any],
    workbook_path: Path,
    output_dir: Path,
) -> Path:
    persistent_path = inputs.get("persistent_workbook_path")
    if persistent_path:
        target = Path(persistent_path)
        if not target.is_absolute():
            target = BATPAC_RUNTIME_DIR / target
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(workbook_path, target)
        return target

    if inputs.get("persistent_workbook", False):
        target = BATPAC_RUNTIME_DIR / "BatPaC_working.xlsm"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(workbook_path, target)
        return target

    BATPAC_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    runtime_workbook = BATPAC_RUNTIME_DIR / _runtime_workbook_name(workbook_path)
    try:
        shutil.copy2(workbook_path, runtime_workbook)
    except BaseException:
        # copy2 may create a partial destination before raising (for example,
        # when the runtime filesystem becomes full).  Never leave that partial
        # workbook behind.
        try:
            runtime_workbook.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return runtime_workbook


def _runtime_workbook_name(workbook_path: Path) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{workbook_path.stem}_runtime_{timestamp}_{time.time_ns()}{workbook_path.suffix}"


def _is_transient_runtime_workbook(path: Path) -> bool:
    """Return True only for adapter-generated, non-persistent workbook copies."""
    try:
        in_runtime_directory = path.parent.resolve() == BATPAC_RUNTIME_DIR.resolve()
    except OSError:
        return False
    return in_runtime_directory and "_runtime_" in path.stem


def _remove_transient_runtime_workbook(path: Path) -> None:
    """Remove one adapter-generated runtime workbook without touching stable workbooks."""
    if _is_transient_runtime_workbook(path):
        path.unlink(missing_ok=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
