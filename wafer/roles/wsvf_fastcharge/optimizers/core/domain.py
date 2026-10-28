"""Domain utilities for the battery-pack design problem."""

from __future__ import annotations

import random
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Mapping, Optional, Tuple

Candidate = Dict[str, Any]
Condition = Dict[str, Any]

CHEMISTRIES = ("LFP", "NMC811")
THICKNESS_BOUNDS_BY_CHEMISTRY: Dict[str, Tuple[float, float]] = {
    "LFP": (50.0, 165.0),
    "NMC811": (50.0, 155.0),
}
INTEGER_BOUNDS: Dict[str, Tuple[int, int]] = {
    "x_S": (1, 30),
    "x_MPR": (1, 50),
    "x_R": (1, 4),
}
REQUIRED_CANDIDATE_KEYS = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
_ACTIVE_DESIGN_VARIABLE_BOUNDS: ContextVar[Optional[Mapping[str, Any]]] = ContextVar(
    "active_design_variable_bounds", default=None
)
CONDITION_PROFILE_VERSION = "ioniq5_84kwh_2026-07-11"

_BASE_CONDITION: Condition = {
    "name": "s0",
    "condition_profile_version": CONDITION_PROFILE_VERSION,
    "P_req": 239.0,
    "E_req": 84.0,
    "V_min": 200.0,
    "V_max": 1000.0,
    "c_min": 40.0,
    "c_max": 120.0,
    "L_max": 2200.0,
    "W_max": 1500.0,
    "cam_cost_nmc811": 19.63,
    "cam_cost_lfp": 8.70,
}


# 목적: 이름으로 정의된 실험 조건을 생성한다.
# 입력: condition 이름 문자열.
# 출력: optimizer와 evaluator가 공유하는 condition dict.
def make_condition(name: str) -> Condition:
    """Return a named optimization condition."""
    condition = dict(_BASE_CONDITION)
    condition["name"] = name

    if name == "s0":
        return condition
    if name == "s_cost":
        condition["cam_cost_nmc811"] = 25.75
        return condition
    if name == "s_vehicle":
        condition["P_req"] = 478.0
        return condition
    if name == "s_vehicle_energy":
        condition["E_req"] = 105.0
        return condition
    if name == "s_space":
        condition["W_max"] = 1100.0
        return condition

    raise ValueError(f"Unknown condition name: {name!r}")


# 목적: 화학 조성별 양극 두께 허용 범위를 제공한다.
# 입력: x_chem 값.
# 출력: x_T의 하한과 상한 튜플.
def resolved_design_variable_bounds(
    override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a validated, serializable domain without changing legacy constants."""
    resolved: Dict[str, Any] = {
        "x_chem": {"type": "categorical", "choices": list(CHEMISTRIES)},
        "x_T": {
            "type": "real",
            "bounds_by_chemistry": {
                chemistry: {"lower": lower, "upper": upper}
                for chemistry, (lower, upper) in THICKNESS_BOUNDS_BY_CHEMISTRY.items()
            },
        },
        **{
            name: {"type": "integer", "lower": lower, "upper": upper}
            for name, (lower, upper) in INTEGER_BOUNDS.items()
        },
    }
    if override is None:
        return resolved
    if not isinstance(override, Mapping):
        raise ValueError("design_variable_bounds_override must be an object")
    unknown = sorted(set(override) - set(REQUIRED_CANDIDATE_KEYS))
    if unknown:
        raise ValueError(f"design_variable_bounds_override contains unknown keys: {unknown}")
    for name, value in override.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"design_variable_bounds_override.{name} must be an object")
        merged = dict(resolved[name])
        merged.update(dict(value))
        resolved[name] = merged
    _validate_resolved_bounds(resolved)
    return resolved


def _validate_resolved_bounds(bounds: Mapping[str, Any]) -> None:
    chemistry = bounds.get("x_chem")
    if not isinstance(chemistry, Mapping) or chemistry.get("type") != "categorical" or list(chemistry.get("choices", [])) != list(CHEMISTRIES):
        raise ValueError("Resolved x_chem must preserve the existing chemistry choices")
    thickness = bounds.get("x_T")
    if not isinstance(thickness, Mapping) or thickness.get("type") != "real":
        raise ValueError("Resolved x_T must preserve the existing chemistry-specific real bounds")
    expected_t = resolved_design_variable_bounds()["x_T"]["bounds_by_chemistry"]
    if thickness.get("bounds_by_chemistry") != expected_t:
        raise ValueError("Resolved x_T bounds must preserve the existing chemistry-specific limits")
    for name in INTEGER_BOUNDS:
        spec = bounds.get(name)
        if not isinstance(spec, Mapping) or spec.get("type") != "integer":
            raise ValueError(f"Resolved {name} must be an integer domain")
        lower, upper = spec.get("lower"), spec.get("upper")
        if isinstance(lower, bool) or isinstance(upper, bool) or not isinstance(lower, int) or not isinstance(upper, int) or lower > upper:
            raise ValueError(f"Resolved {name} bounds must be ordered integers")


def active_design_variable_bounds() -> Dict[str, Any]:
    active = _ACTIVE_DESIGN_VARIABLE_BOUNDS.get()
    return resolved_design_variable_bounds(active) if active is not None else resolved_design_variable_bounds()


@contextmanager
def use_design_variable_bounds(bounds: Optional[Mapping[str, Any]]):
    """Scope all legacy domain helpers to one already-resolved scenario domain."""
    resolved = resolved_design_variable_bounds(bounds) if bounds is not None else None
    token = _ACTIVE_DESIGN_VARIABLE_BOUNDS.set(resolved)
    try:
        yield resolved or resolved_design_variable_bounds()
    finally:
        _ACTIVE_DESIGN_VARIABLE_BOUNDS.reset(token)


def active_integer_bounds() -> Dict[str, Tuple[int, int]]:
    bounds = active_design_variable_bounds()
    return {name: (int(bounds[name]["lower"]), int(bounds[name]["upper"])) for name in INTEGER_BOUNDS}


def get_thickness_bounds(
    x_chem: Any, bounds: Optional[Mapping[str, Any]] = None,
) -> Tuple[float, float]:
    """Return chemistry-specific thickness bounds."""
    chemistry = _normalize_chemistry(x_chem)
    # 화학 조성에 따라 허용되는 전극 두께 상한이 달라지므로 먼저 조성을 확정한다.
    resolved = resolved_design_variable_bounds(bounds) if bounds is not None else active_design_variable_bounds()
    spec = resolved["x_T"]["bounds_by_chemistry"][chemistry]
    return float(spec["lower"]), float(spec["upper"])


def thickness_from_unit_interval(x_chem: Any, u_T: Any) -> float:
    """Map normalized u_T in [0, 1] to chemistry-specific physical x_T."""
    lower, upper = get_thickness_bounds(x_chem)
    numeric = _as_float(u_T, "u_T")
    if numeric < 0.0 or numeric > 1.0:
        raise ValueError(f"u_T={numeric!r} is outside the bounds [0.0, 1.0]")
    return lower + numeric * (upper - lower)


def unit_interval_from_thickness(x_chem: Any, x_T: Any) -> float:
    """Map physical x_T to normalized u_T for the selected chemistry."""
    lower, upper = get_thickness_bounds(x_chem)
    thickness = _as_float(x_T, "x_T")
    if thickness < lower or thickness > upper:
        raise ValueError(
            f"x_T={thickness!r} is outside the bounds [{lower}, {upper}] for {_normalize_chemistry(x_chem)}"
        )
    return (thickness - lower) / (upper - lower)


# 목적: candidate가 내부 schema와 bounds를 만족하는지 검사한다.
# 입력: candidate mapping.
# 출력: 유효하면 True, 아니면 ValueError.
def validate_candidate(
    candidate: Mapping[str, Any], bounds: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Validate a candidate and raise ValueError on failure."""
    missing = [key for key in REQUIRED_CANDIDATE_KEYS if key not in candidate]
    if missing:
        raise ValueError(f"Candidate is missing required keys: {missing}")

    chemistry = _normalize_chemistry(candidate["x_chem"])
    resolved = resolved_design_variable_bounds(bounds) if bounds is not None else active_design_variable_bounds()
    lower, upper = get_thickness_bounds(chemistry, resolved)
    thickness = _as_float(candidate["x_T"], "x_T")
    if thickness < lower or thickness > upper:
        raise ValueError(
            f"x_T={thickness!r} is outside the bounds [{lower}, {upper}] for {chemistry}"
        )

    for key, integer_bounds in {
        name: (int(resolved[name]["lower"]), int(resolved[name]["upper"]))
        for name in INTEGER_BOUNDS
    }.items():
        value = _as_int(candidate[key], key)
        lower_int, upper_int = integer_bounds
        if value < lower_int or value > upper_int:
            raise ValueError(
                f"{key}={value!r} is outside the bounds [{lower_int}, {upper_int}]"
            )

    return True


# 목적: optimizer가 만든 candidate를 BatPaC 평가 전에 유효 범위로 보정한다.
# 입력: candidate mapping.
# 출력: 보정된 candidate dict.
def repair_candidate(
    candidate: Mapping[str, Any], bounds: Optional[Mapping[str, Any]] = None,
) -> Candidate:
    """Return a repaired copy of a candidate."""
    missing = [key for key in REQUIRED_CANDIDATE_KEYS if key not in candidate]
    if missing:
        raise ValueError(f"Candidate is missing required keys: {missing}")

    chemistry = _normalize_chemistry(candidate["x_chem"])
    resolved = resolved_design_variable_bounds(bounds) if bounds is not None else active_design_variable_bounds()
    lower, upper = get_thickness_bounds(chemistry, resolved)

    # 후보 생성 과정에서 생긴 범위 밖 값은 BatPaC 호출 전에 보정한다.
    repaired: Candidate = {
        "x_chem": chemistry,
        "x_T": _clip(_as_float(candidate["x_T"], "x_T"), lower, upper),
    }
    for key in INTEGER_BOUNDS:
        lower_int, upper_int = int(resolved[key]["lower"]), int(resolved[key]["upper"])
        rounded_value = int(round(_as_float(candidate[key], key)))
        repaired[key] = int(_clip(rounded_value, lower_int, upper_int))

    return repaired


# 목적: mixed design domain에서 무작위 candidate를 생성한다.
# 입력: random number generator와 선택적 fixed variables.
# 출력: fixed variables가 반영된 보정 candidate dict.
def sample_random_candidate(
    rng: random.Random,
    fixed_variables: Optional[Mapping[str, Any]] = None,
) -> Candidate:
    """Sample a random candidate and apply optional fixed variables."""
    fixed = fixed_variables or {}
    _validate_fixed_variable_keys(fixed)

    chemistry = _normalize_chemistry(fixed.get("x_chem", rng.choice(CHEMISTRIES)))
    resolved = active_design_variable_bounds()
    integer_bounds = active_integer_bounds()
    lower, upper = get_thickness_bounds(chemistry, resolved)
    candidate: Candidate = {
        "x_chem": chemistry,
        "x_T": rng.uniform(lower, upper),
        "x_S": rng.randint(*integer_bounds["x_S"]),
        "x_MPR": rng.randint(*integer_bounds["x_MPR"]),
        "x_R": rng.randint(*integer_bounds["x_R"]),
    }
    return repair_candidate(apply_fixed_variables(candidate, fixed))


# 목적: 사용자가 고정한 design variable을 candidate에 반영한다.
# 입력: candidate mapping과 fixed variables mapping.
# 출력: 고정값이 덮어써진 candidate dict.
def apply_fixed_variables(
    candidate: Mapping[str, Any],
    fixed_variables: Optional[Mapping[str, Any]],
) -> Candidate:
    """Return a copy of candidate with fixed variables applied."""
    fixed = fixed_variables or {}
    _validate_fixed_variable_keys(fixed)
    updated = dict(candidate)

    # 고정 변수는 샘플링 값보다 우선하므로 마지막에 덮어쓴다.
    for key, value in fixed.items():
        updated[key] = value
    return updated


# 목적: 내부 candidate를 BatPaC adapter가 받는 high-level input으로 변환한다.
# 입력: candidate mapping과 condition mapping.
# 출력: BatPaC high-level alias 기반 input dict.
def candidate_to_batpac_inputs(
    candidate: Mapping[str, Any], condition: Mapping[str, Any],
    bounds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Translate an internal candidate into BatPaC high-level inputs."""
    repaired = repair_candidate(candidate, bounds)
    return {
        "chemistry": repaired["x_chem"],
        "x_T": repaired["x_T"],
        "x_S": repaired["x_S"],
        "x_MPR": repaired["x_MPR"],
        "x_R": repaired["x_R"],
        "scenario": condition["name"],
        # BatPaC 기본 vehicle power requirement를 끄고 optimizer condition을 명시적으로 전달한다.
        "battery_1_target_pack_rated_peak_power_kw": condition["P_req"],
        "battery_1_total_pack_energy_0_100pct_soc_kwh": condition["E_req"],
        "use_default_power_requirements_for_vehicle": "no",
    }


# 목적: chemistry 표기를 내부 표준 문자열로 정규화한다.
# 입력: 임의의 chemistry 값.
# 출력: 표준 chemistry 문자열.
def _normalize_chemistry(value: Any) -> str:
    text = str(value).strip().upper()
    if text not in CHEMISTRIES:
        raise ValueError(f"Unknown chemistry: {value!r}. Expected one of {CHEMISTRIES}")
    return text


# 목적: fixed variables가 허용된 candidate key만 사용하는지 확인한다.
# 입력: fixed variables mapping.
# 출력: 없음, 잘못된 key가 있으면 ValueError.
def _validate_fixed_variable_keys(fixed_variables: Mapping[str, Any]) -> None:
    unknown = [key for key in fixed_variables if key not in REQUIRED_CANDIDATE_KEYS]
    if unknown:
        raise ValueError(f"Unknown fixed variable keys: {unknown}")


# 목적: 입력값을 float로 변환하고 실패를 명확한 오류로 바꾼다.
# 입력: 변환할 값과 변수 이름.
# 출력: float 값.
def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc


# 목적: 입력값이 정수로 해석 가능한지 확인한다.
# 입력: 변환할 값과 변수 이름.
# 출력: int 값.
def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    if not numeric.is_integer():
        raise ValueError(f"{name} must be an integer, got {value!r}")
    return int(numeric)


# 목적: 숫자 값을 지정된 범위 안으로 제한한다.
# 입력: 값, 하한, 상한.
# 출력: 범위 안으로 clip된 값.
def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


# === FASTCHARGE_V1_DOMAIN_PATCH ===
#
# Isolated JMD fast-charge redesign:
# source requirement = 90 min
# target requirement = 30 min
#
# The numerical optimizer sees the same five design variables as the
# existing JMD experiments. Fast-charge feasibility is evaluated from
# BatPaC's charging-derived positive-electrode-thickness limit.

_original_make_condition_fastcharge_v1 = make_condition
_original_candidate_to_batpac_inputs_fastcharge_v1 = candidate_to_batpac_inputs


def make_condition(name: str) -> Condition:
    """Extended condition resolver for the isolated fast-charge study."""
    if name != "s_fastcharge":
        return _original_make_condition_fastcharge_v1(name)

    condition = dict(
        _original_make_condition_fastcharge_v1("s0")
    )
    condition["name"] = "s_fastcharge"

    # Metadata used by the isolated feasibility extension.
    condition["calculate_charging_requirements"] = True
    condition["charge_time_min"] = 30.0
    condition["source_charge_time_min"] = 90.0

    return condition


def candidate_to_batpac_inputs(
    candidate,
    condition,
    bounds=None,
):
    """Add BatPaC fast-charge inputs and charging-limit outputs."""
    payload = _original_candidate_to_batpac_inputs_fastcharge_v1(
        candidate,
        condition,
        bounds,
    )

    if condition.get("name") != "s_fastcharge":
        return payload

    # This redesign has no material-cost or E/P shock.
    # Use the same no-shock BatPaC physics as the certification grid.
    payload["scenario"] = "s0"

    # Charging requirements ON.
    payload["D34"] = "Yes"

    # 15 -> 95% SOC target charging time.
    payload["D57"] = float(
        condition.get("charge_time_min", 30.0)
    )

    # Request the physical charging limit and the actual thickness override.
    payload["cell_outputs"] = {
        "G259_fastcharge_limit_um": {
            "sheet": "Battery Design",
            "cell": "G259",
        },
        "Chem_C27_min_um": {
            "sheet": "Chem",
            "cell": "C27",
        },
        "G269_override_um": {
            "sheet": "Battery Design",
            "cell": "G269",
        },
        "G276_selected_um": {
            "sheet": "Battery Design",
            "cell": "G276",
        },
    }

    return payload
