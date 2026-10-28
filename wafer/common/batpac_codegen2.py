# -*- coding: utf-8 -*-
"""BatPaC interface: Dashboard input names and cell addresses; no workbook values are stored here."""
from __future__ import annotations
from dataclasses import dataclass, field, fields as dc_fields, make_dataclass
from pathlib import Path
from typing import Dict, Any, Union
import time, re, warnings, pandas as pd, xlwings as xw

D6_RECALC_MODE = "pulse"

# Release: the copy that ran stored 159 default values taken from a BatPaC 5.0 workbook in the
# class BatPaCInputs. Those values are not distributed. The names and Dashboard cell addresses
# are kept below, and default values are read from the user's own workbook at run time
# (batpac_workbook.read_workbook_defaults). Only overrides are written to the workbook
# (write_defaults=False in both runners), so no evaluation depends on the removed values.

CELL_ADDRESS_MAP: Dict[str,str] = {
    'electrode_couplecurrent_selection': 'D13',
    'positive_electrode_lifepo4positive_active_material_specific_capacity_mah_gcurrent_selection': 'D15',
    'positive_electrode_lifepo4void_volume_fraction_pct_of_positive_electrodecurrent_selection': 'D16',
    'positive_electrode_lifepo4positive_foil_thickness_mmcurrent_selection': 'D17',
    'positive_electrode_lifepo4maximum_positive_electrode_thickness_umcurrent_selection': 'D18',
    'negative_electrode_graphitenegative_active_material_specific_capacity_mah_gcurrent_selection': 'D21',
    'negative_electrode_graphiten_p_capacity_ratio_after_formationcurrent_selection': 'D22',
    'negative_electrode_graphitevoid_volume_fraction_pct_of_negative_electrodecurrent_selection': 'D23',
    'negative_electrode_graphitenegative_current_collector_thickness_umcurrent_selection': 'D24',
    'separator_thickness_umcurrent_selection': 'D26',
    'maximum_charging_current_density_ma_cm_current_selection': 'D28',
    'vehicle_typecurrent_selection': 'C33',
    'calculate_charging_requirements_current_selection': 'C34',
    'oem_upper_cuttoff_soc': 'H32',
    'oem_lower_cutoff_soc': 'H33',
    'useable_battery_energy_pct_of_total_ev': 'H34',
    'battery_1_duration_at_rated_power_s': 'D42',
    'battery_2_duration_at_rated_power_s': 'E42',
    'battery_3_duration_at_rated_power_s': 'F42',
    'battery_4_duration_at_rated_power_s': 'G42',
    'battery_5_duration_at_rated_power_s': 'H42',
    'battery_6_duration_at_rated_power_s': 'I42',
    'battery_7_duration_at_rated_power_s': 'J42',
    'battery_1_operating_initial_soc_for_rated_peak_power_pct': 'D44',
    'battery_2_operating_initial_soc_for_rated_peak_power_pct': 'E44',
    'battery_3_operating_initial_soc_for_rated_peak_power_pct': 'F44',
    'battery_4_operating_initial_soc_for_rated_peak_power_pct': 'G44',
    'battery_5_operating_initial_soc_for_rated_peak_power_pct': 'H44',
    'battery_6_operating_initial_soc_for_rated_peak_power_pct': 'I44',
    'battery_7_operating_initial_soc_for_rated_peak_power_pct': 'J44',
    'positive_electrode_kgactive_material_kg': 'D86',
    'positive_electrode_kgcarbon_additive_kg': 'D87',
    'positive_electrode_kgbinder_kg': 'D88',
    'positive_electrode_kgsolvent_nmp_kg': 'D89',
    'negative_electrode_kgactive_material_kg': 'D92',
    'negative_electrode_kgcarbon_additive_kg': 'D93',
    'negative_electrode_kgbinder_kg': 'D94',
    'negative_electrode_kgsolvent_water_kg': 'D95',
    'al_foil_m': 'D97',
    'cu_foil_m': 'D98',
    'separator_m': 'D99',
    'electrolyte_l': 'D100',
    'battery_1_average_temperature_during_discharge_default_30_c_c': 'D63',
    'battery_2_average_temperature_during_discharge_default_30_c_c': 'E63',
    'battery_3_average_temperature_during_discharge_default_30_c_c': 'F63',
    'battery_4_average_temperature_during_discharge_default_30_c_c': 'G63',
    'battery_5_average_temperature_during_discharge_default_30_c_c': 'H63',
    'battery_6_average_temperature_during_discharge_default_30_c_c': 'I63',
    'battery_7_average_temperature_during_discharge_default_30_c_c': 'J63',
    'cell_yield_pct_of_built_cells_that_pass_inspection': 'D112',
    'reset_calculation': 'D6',
    'electorode_couplechoose': 'E13',
    'vehicle_typechoose': 'E33',
    'calculate_charging_requirements_choose': 'E34',
    'battery_1_target_pack_rated_peak_power_kw': 'D38',
    'battery_2_target_pack_rated_peak_power_kw': 'E38',
    'battery_3_target_pack_rated_peak_power_kw': 'F38',
    'battery_4_target_pack_rated_peak_power_kw': 'G38',
    'battery_5_target_pack_rated_peak_power_kw': 'H38',
    'battery_6_target_pack_rated_peak_power_kw': 'I38',
    'battery_7_target_pack_rated_peak_power_kw': 'J38',
    'use_default_power_requirements_for_vehicle': 'D40',
    'battery_1_total_pack_capacity_0_100pct_soc_ah': 'D51',
    'battery_2_total_pack_capacity_0_100pct_soc_ah': 'E51',
    'battery_3_total_pack_capacity_0_100pct_soc_ah': 'F51',
    'battery_4_total_pack_capacity_0_100pct_soc_ah': 'G51',
    'battery_5_total_pack_capacity_0_100pct_soc_ah': 'H51',
    'battery_6_total_pack_capacity_0_100pct_soc_ah': 'I51',
    'battery_7_total_pack_capacity_0_100pct_soc_ah': 'J51',
    'battery_1_total_pack_energy_0_100pct_soc_kwh': 'D52',
    'battery_2_total_pack_energy_0_100pct_soc_kwh': 'E52',
    'battery_3_total_pack_energy_0_100pct_soc_kwh': 'F52',
    'battery_4_total_pack_energy_0_100pct_soc_kwh': 'G52',
    'battery_5_total_pack_energy_0_100pct_soc_kwh': 'H52',
    'battery_6_total_pack_energy_0_100pct_soc_kwh': 'I52',
    'battery_7_total_pack_energy_0_100pct_soc_kwh': 'J52',
    'battery_1_time_to_recharge_from_15pct_to_95pct_soc_min': 'D57',
    'battery_2_time_to_recharge_from_15pct_to_95pct_soc_min': 'E57',
    'battery_3_time_to_recharge_from_15pct_to_95pct_soc_min': 'F57',
    'battery_4_time_to_recharge_from_15pct_to_95pct_soc_min': 'G57',
    'battery_5_time_to_recharge_from_15pct_to_95pct_soc_min': 'H57',
    'battery_6_time_to_recharge_from_15pct_to_95pct_soc_min': 'I57',
    'battery_7_time_to_recharge_from_15pct_to_95pct_soc_min': 'J57',
    'battery_1_number_of_cells_per_module': 'D67',
    'battery_2_number_of_cells_per_module': 'E67',
    'battery_3_number_of_cells_per_module': 'F67',
    'battery_4_number_of_cells_per_module': 'G67',
    'battery_5_number_of_cells_per_module': 'H67',
    'battery_6_number_of_cells_per_module': 'I67',
    'battery_7_number_of_cells_per_module': 'J67',
    'battery_1_number_of_cells_in_parallel': 'D68',
    'battery_2_number_of_cells_in_parallel': 'E68',
    'battery_3_number_of_cells_in_parallel': 'F68',
    'battery_4_number_of_cells_in_parallel': 'G68',
    'battery_5_number_of_cells_in_parallel': 'H68',
    'battery_6_number_of_cells_in_parallel': 'I68',
    'battery_7_number_of_cells_in_parallel': 'J68',
    'battery_1_number_of_modules_in_row': 'D69',
    'battery_2_number_of_modules_in_row': 'E69',
    'battery_3_number_of_modules_in_row': 'F69',
    'battery_4_number_of_modules_in_row': 'G69',
    'battery_5_number_of_modules_in_row': 'H69',
    'battery_6_number_of_modules_in_row': 'I69',
    'battery_7_number_of_modules_in_row': 'J69',
    'battery_1_number_of_rows_of_modules_per_pack': 'D70',
    'battery_2_number_of_rows_of_modules_per_pack': 'E70',
    'battery_3_number_of_rows_of_modules_per_pack': 'F70',
    'battery_4_number_of_rows_of_modules_per_pack': 'G70',
    'battery_5_number_of_rows_of_modules_per_pack': 'H70',
    'battery_6_number_of_rows_of_modules_per_pack': 'I70',
    'battery_7_number_of_rows_of_modules_per_pack': 'J70',
    'battery_1_number_of_modules_in_parallel': 'D71',
    'battery_2_number_of_modules_in_parallel': 'E71',
    'battery_3_number_of_modules_in_parallel': 'F71',
    'battery_4_number_of_modules_in_parallel': 'G71',
    'battery_5_number_of_modules_in_parallel': 'H71',
    'battery_6_number_of_modules_in_parallel': 'I71',
    'battery_7_number_of_modules_in_parallel': 'J71',
    'battery_1_number_of_packs_manufactured_per_year': 'D105',
    'battery_2_number_of_packs_manufactured_per_year': 'E105',
    'battery_3_number_of_packs_manufactured_per_year': 'F105',
    'battery_4_number_of_packs_manufactured_per_year': 'G105',
    'battery_5_number_of_packs_manufactured_per_year': 'H105',
    'battery_6_number_of_packs_manufactured_per_year': 'I105',
    'battery_7_number_of_packs_manufactured_per_year': 'J105',
    'method_to_calculate_plant_utilization': 'D107',
    'method_for_manufacturing_pack': 'D116',
    'option_for_pack_cost': 'D118',
    'positive_electrode_lifepo4positive_active_material_specific_capacity_mah_goverride': 'E15',
    'positive_electrode_lifepo4void_volume_fraction_pct_of_positive_electrodeoverride': 'E16',
    'positive_electrode_lifepo4positive_foil_thickness_mmoverride': 'E17',
    'positive_electrode_lifepo4maximum_positive_electrode_thickness_umoverride': 'E18',
    'negative_electrode_graphitenegative_active_material_specific_capacity_mah_goverride': 'E21',
    'negative_electrode_graphiten_p_capacity_ratio_after_formationoverride': 'E22',
    'negative_electrode_graphitevoid_volume_fraction_pct_of_negative_electrodeoverride': 'E23',
    'negative_electrode_graphitenegative_current_collector_thickness_umoverride': 'E24',
    'separator_thickness_umoverride': 'E26',
    'maximum_charging_current_density_ma_cm_override': 'E28',
    'oem_upper_cuttoff_soc_2': 'J32',
    'oem_lower_cutoff_soc_2': 'J33',
    'battery_1_optional_positive_electrode_thickness_override_um': 'D60',
    'battery_2_optional_positive_electrode_thickness_override_um': 'E60',
    'battery_3_optional_positive_electrode_thickness_override_um': 'F60',
    'battery_4_optional_positive_electrode_thickness_override_um': 'G60',
    'battery_5_optional_positive_electrode_thickness_override_um': 'H60',
    'battery_6_optional_positive_electrode_thickness_override_um': 'I60',
    'battery_7_optional_positive_electrode_thickness_override_um': 'J60',
    'positive_electrode_kgactive_material_kg_override': 'E86',
    'positive_electrode_kgcarbon_additive_kgoverride': 'E87',
    'positive_electrode_kgbinder_kgoverride': 'E88',
    'positive_electrode_kgsolvent_nmp_kgoverride': 'E89',
    'negative_electrode_kgactive_material_kgoverride': 'E92',
    'negative_electrode_kgcarbon_additive_kgoverride': 'E93',
    'negative_electrode_kgbinder_kgoverride': 'E94',
    'negative_electrode_kgsolvent_water_kgoverride': 'E95',
    'al_foil_m_override': 'E97',
    'cu_foil_m_override': 'E98',
    'separator_m_override': 'E99',
    'electrolyte_loverride': 'E100',
}

# Same field names and order as the class that ran; no baked-in defaults.
BatPaCInputs = make_dataclass(
    "BatPaCInputs", [(name, Any, field(default=None)) for name in CELL_ADDRESS_MAP])

def _inputs_to_addrvals(inp: BatPaCInputs, overrides: Dict[str, Any], write_defaults: bool = False) -> Dict[str, Any]:
    """
    기본 동작: 오버라이드만 쓴다(부작용 방지).
    write_defaults=True 로 주면 None이 아닌 기본값도 함께 씀.
    """
    addrvals: Dict[str, Any] = {}

    # 1) 이름 기반 오버라이드 → 셀주소로 매핑
    for k, v in overrides.items():
        if k in CELL_ADDRESS_MAP:
            addrvals[CELL_ADDRESS_MAP[k]] = v

    # 2) 셀주소 직접 오버라이드 허용 (예: D13=0.012)
    for k, v in overrides.items():
        if re.fullmatch(r'[A-Z]+[0-9]+', str(k).upper()):
            addrvals[str(k).upper()] = v

    if write_defaults:
        # None이 아닌 기본값만 추가(이미 overrides에 준 건 건너뜀)
        for f in dc_fields(inp):
            name = f.name
            if name in CELL_ADDRESS_MAP and name not in overrides:
                val = getattr(inp, name)
                if val is not None:
                    addrvals[CELL_ADDRESS_MAP[name]] = val

    return addrvals

def _write_dashboard(wb, sheet: str, addrvals: Dict[str,Any], safe_only: bool=True):
    sht = wb.sheets[sheet]
    for addr, val in addrvals.items():
        a = str(addr).upper().strip()
        m = re.fullmatch(r'([A-Z]+)(\d+)', a)
        if not m: continue
        col, row = m.group(1), int(m.group(2))
        if safe_only and (row<1 or row>400 or col>'L'):
            continue
        sht.range(a).value = val

def _reset_recalc(wb, dashboard_sheet='Dashboard', reset_cell='D6', sleep_sec=0.5, mode=D6_RECALC_MODE):
    """
    mode='pulse' : Dashboard!D6 is a restart/reconvergence trigger. Pulse it, then read outputs with D6 off.
    """
    if mode == 'hold':
        warnings.warn(
            "Dashboard!D6 hold mode is deprecated and invalid for this project; "
            "use pulse/off so outputs are read with D6=0.",
            RuntimeWarning,
            stacklevel=2,
        )
        raise ValueError("Dashboard!D6 hold mode is deprecated; use mode='pulse'.")
    if mode != 'pulse':
        raise ValueError(f"Unsupported Dashboard!D6 recalculation mode: {mode!r}")

    sht = wb.sheets[dashboard_sheet]
    try:
        sht.range(reset_cell).value = 1
        time.sleep(sleep_sec)
        sht.range(reset_cell).value = 0
    except Exception:
        pass
    # 전체 계산 트리거
    try:
        wb.app.api.CalculateFullRebuild()
    except Exception:
        try:
            wb.app.api.CalculateFull()
        except Exception:
            wb.app.calculate()
    time.sleep(sleep_sec)

def get_results_block(wb, result_sheet='Dashboard',
                      header_row=187, data_start=188, data_end=214,
                      start_col='B', end_col='J'):
    """Read B187:J214; row 187 is header."""
    sht = wb.sheets[result_sheet]
    header_rng = f"{start_col}{header_row}:{end_col}{header_row}"
    headers = sht.range(header_rng).value
    data_rng = f"{start_col}{data_start}:{end_col}{data_end}"
    data = sht.range(data_rng).value
    if not headers or all(h is None or str(h).strip()=='' for h in headers):
        n_cols = ord(end_col) - ord(start_col) + 1
        headers = ['Index','Attribute'] + [f'Battery {i}' for i in range(1, n_cols-1)]
    df = pd.DataFrame(data, columns=[str(h).strip() if h is not None else '' for h in headers])
    if df.columns.size >= 2:
        df.rename(columns={df.columns[0]:'Index', df.columns[1]:'Attribute'}, inplace=True)
    return df

# 목적: Dashboard 결과표에 없는 workbook cell 값을 Excel이 열린 상태에서 읽는다.
# 입력: 이름별 sheet/cell spec dict.
# 출력: 이름별 cell value dict.
def get_named_cell_values(wb, cell_specs: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for name, spec in (cell_specs or {}).items():
        sheet = str(spec['sheet'])
        cell = str(spec['cell']).upper()
        values[name] = wb.sheets[sheet].range(cell).value
    return values

def batpac_by_python(xlsm_path: Union[str,Path],
                     dashboard_sheet='Dashboard',
                     result_sheet='Dashboard',
                     visible=False,
                     out_csv: Union[None, str, Path, bool]=None,
                     return_timing: bool=True,
                     extra_cells: Union[None, Dict[str, Dict[str, str]]]=None,
                     **kwargs):
    xlsm_path = Path(xlsm_path)

    t0 = time.perf_counter()
    base = BatPaCInputs()
    # 이전: addrvals = _inputs_to_addrvals(base, kwargs)
    addrvals = _inputs_to_addrvals(base, kwargs, write_defaults=False)  # 오버라이드만!
    t_inputs = time.perf_counter()

    app = xw.App(visible=visible, add_book=False)
    try:
        t_open0 = time.perf_counter()
        wb = app.books.open(str(xlsm_path))
        t_open1 = time.perf_counter()

        _write_dashboard(wb, dashboard_sheet, addrvals, safe_only=True)
        t_write = time.perf_counter()

        # Dashboard!D6 is a restart/reconvergence trigger. Pulse it, then read outputs with D6 off.
        _reset_recalc(wb, dashboard_sheet, 'D6', 0.5, mode=D6_RECALC_MODE)
        t_recalc = time.perf_counter()

        df = get_results_block(wb, result_sheet, header_row=187, data_start=188, data_end=214, start_col='B', end_col='J')
        t_read = time.perf_counter()
        # pack 길이/너비처럼 Dashboard 결과표 밖에 있는 값은 저장 전에 직접 읽는다.
        extra_values = get_named_cell_values(wb, extra_cells or {})
        t_extra = time.perf_counter()

        # Optional CSV output
        if out_csv is not None:
            if out_csv is True:
                import datetime
                ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                out_path = xlsm_path.with_suffix(f'.B187_J214_{ts}.csv')
            else:
                out_path = Path(out_csv)
            df.to_csv(out_path, index=False)

        t1 = time.perf_counter()

        if return_timing:
            timings = {
                'build_inputs_s': t_inputs - t0,
                'excel_open_s':   t_open1 - t_open0,
                'write_inputs_s': t_write - t_open1,
                'recalc_s':       t_recalc - t_write,
                'read_results_s': t_read - t_recalc,
                'read_extra_cells_s': t_extra - t_read,
                'total_s':        t1 - t0
            }
            if extra_cells is not None:
                return df, timings, extra_values
            return df, timings
        if extra_cells is not None:
            return df, extra_values
        return df
    finally:
        app.quit()
