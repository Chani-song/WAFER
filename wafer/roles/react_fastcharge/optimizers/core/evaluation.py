"""Evaluation helpers for optimizer results."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

Result = Dict[str, Any]
Condition = Mapping[str, Any]

REQUIRED_RESULT_KEYS = ("cost", "P", "E", "V", "c", "L_bat", "W_bat")
CONSTRAINT_RESULT_KEYS = ("P", "E", "V", "c", "L_bat", "W_bat")
CONSTRAINT_NAMES = (
    "P_min",
    "E_min",
    "V_min",
    "V_max",
    "c_min",
    "c_max",
    "L_bat_max",
    "W_bat_max",
)


# 목적: evaluator result의 status flag를 일관되게 계산한다.
# 입력: result mapping과 선택적 simulator_call_success override.
# 출력: status flag dict.
def decompose_evaluator_status(
    result: Mapping[str, Any],
    simulator_call_success: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return evaluator status flags."""
    call_success = (
        _infer_simulator_call_success(result)
        if simulator_call_success is None
        else bool(simulator_call_success)
    )
    objective_available = _finite_float_or_none(result.get("cost")) is not None
    constraint_outputs_available = all(
        _finite_float_or_none(result.get(key)) is not None
        for key in CONSTRAINT_RESULT_KEYS
    )
    optimization_valid = call_success and objective_available and constraint_outputs_available
    return {
        "simulator_call_success": call_success,
        "objective_available": objective_available,
        "constraint_outputs_available": constraint_outputs_available,
        "optimization_valid": optimization_valid,
        "success": optimization_valid,
        "failure_reason": _failure_reason(
            result,
            call_success,
            objective_available,
            constraint_outputs_available,
        ),
    }


# 목적: evaluator 결과에서 제약별 양수 위반량을 계산한다.
# 입력: result mapping과 condition mapping.
# 출력: constraint 이름별 raw violation dict.
def compute_constraint_violations(result: Mapping[str, Any], condition: Condition) -> Dict[str, float]:
    """Return positive raw constraint violations."""
    values = {key: _finite_float_or_none(result.get(key)) for key in REQUIRED_RESULT_KEYS}

    # 제약 위반량은 양수만 남겨 penalty 계산에 사용한다.
    return {
        "P_min": _lower_bound_violation(values["P"], float(condition["P_req"])),
        "E_min": _lower_bound_violation(values["E"], float(condition["E_req"])),
        "V_min": _lower_bound_violation(values["V"], float(condition["V_min"])),
        "V_max": _upper_bound_violation(values["V"], float(condition["V_max"])),
        "c_min": _lower_bound_violation(values["c"], float(condition["c_min"])),
        "c_max": _upper_bound_violation(values["c"], float(condition["c_max"])),
        "L_bat_max": _upper_bound_violation(values["L_bat"], float(condition["L_max"])),
        "W_bat_max": _upper_bound_violation(values["W_bat"], float(condition["W_max"])),
    }


# 목적: BatPaC 또는 fake evaluator 결과가 모든 constraint를 만족하는지 판단한다.
# 입력: result mapping과 condition mapping.
# 출력: feasible 여부.
def is_feasible(result: Mapping[str, Any], condition: Condition) -> bool:
    """Return whether a result satisfies success and all constraints."""
    if not _result_optimization_valid(result):
        return False
    violations = compute_constraint_violations(result, condition)
    return all(value <= 0.0 for value in violations.values())


# 목적: 서로 다른 단위의 constraint violation을 정규화해 합산한다.
# 입력: result mapping과 condition mapping.
# 출력: normalized violation 총합 G.
def normalized_constraint_violation(result: Mapping[str, Any], condition: Condition) -> float:
    """Return the summed normalized positive constraint violation."""
    violations = compute_constraint_violations(result, condition)
    scales = _constraint_scales(condition)
    total = 0.0
    for name in CONSTRAINT_NAMES:
        total += violations[name] / scales[name]
    return total


# 목적: cost와 constraint violation을 하나의 optimizer loss로 변환한다.
# 입력: result, condition, penalty 계수.
# 출력: 비교 가능한 finite loss 값.
def penalized_loss(
    result: Mapping[str, Any],
    condition: Condition,
    lambda_base: float = 1e6,
    lambda_gap: float = 1e5,
) -> float:
    """Return cost with finite penalties for infeasible results."""
    cost = _finite_float_or_none(result.get("cost"))
    violation = normalized_constraint_violation(result, condition)

    # 실패한 평가는 optimizer가 비교할 수 있도록 큰 유한값으로 변환한다.
    if not _result_optimization_valid(result):
        base_cost = cost if cost is not None else 0.0
        return float(base_cost + lambda_base + lambda_gap * max(violation, 1.0))

    if is_feasible(result, condition):
        return float(cost)

    if cost is not None:
        return float(cost + lambda_base + lambda_gap * violation)
    return float(lambda_base + lambda_gap * violation)


# 목적: 필수 evaluator 출력이 모두 finite 숫자인지 확인한다.
# 입력: result mapping.
# 출력: 누락 여부.
def _has_missing_required_outputs(result: Mapping[str, Any]) -> bool:
    return any(_finite_float_or_none(result.get(key)) is None for key in REQUIRED_RESULT_KEYS)


# 목적: result가 optimizer 관점에서 유효한지 판단한다.
# 입력: evaluator result mapping.
# 출력: optimization_valid 여부.
def _result_optimization_valid(result: Mapping[str, Any]) -> bool:
    if "optimization_valid" in result:
        return bool(result.get("optimization_valid"))
    return bool(result.get("success", False)) and not _has_missing_required_outputs(result)


# 목적: simulator call 성공 여부를 legacy result에서 추론한다.
# 입력: evaluator result mapping.
# 출력: simulator_call_success 여부.
def _infer_simulator_call_success(result: Mapping[str, Any]) -> bool:
    raw = result.get("raw")
    if isinstance(raw, Mapping) and "error" in raw:
        return False
    return bool(result.get("success", False))


# 목적: status flag 조합에 맞는 실패 사유를 생성한다.
# 입력: result와 계산된 status flags.
# 출력: failure_reason 문자열 또는 None.
def _failure_reason(
    result: Mapping[str, Any],
    simulator_call_success: bool,
    objective_available: bool,
    constraint_outputs_available: bool,
) -> Optional[str]:
    if not simulator_call_success:
        return "simulator_exception"
    if not objective_available:
        return _objective_failure_reason(result)
    if not constraint_outputs_available:
        return _constraint_failure_reason(result)
    return None


# 목적: objective 누락과 nonfinite objective를 구분한다.
# 입력: evaluator result mapping.
# 출력: missing_cost 또는 nonfinite_objective.
def _objective_failure_reason(result: Mapping[str, Any]) -> str:
    if _is_missing_value(result.get("cost")):
        return "missing_cost"
    return "nonfinite_objective"


# 목적: constraint output 누락과 nonfinite constraint output을 구분한다.
# 입력: evaluator result mapping.
# 출력: missing_constraint_output 또는 nonfinite_constraint_output.
def _constraint_failure_reason(result: Mapping[str, Any]) -> str:
    for key in CONSTRAINT_RESULT_KEYS:
        if _is_missing_value(result.get(key)):
            return "missing_constraint_output"
    return "nonfinite_constraint_output"


# 목적: 값이 없음으로 간주되는지 확인한다.
# 입력: 임의 값.
# 출력: missing 여부.
def _is_missing_value(value: Any) -> bool:
    return value is None


# 목적: 임의 값을 finite float 또는 None으로 정규화한다.
# 입력: 변환할 값.
# 출력: finite float 또는 None.
def _finite_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


# 목적: lower-bound constraint의 양수 위반량을 계산한다.
# 입력: 관측값과 하한.
# 출력: raw violation 값.
def _lower_bound_violation(value: Optional[float], lower: float) -> float:
    if value is None:
        return _scale(lower)
    return max(0.0, lower - value)


# 목적: upper-bound constraint의 양수 위반량을 계산한다.
# 입력: 관측값과 상한.
# 출력: raw violation 값.
def _upper_bound_violation(value: Optional[float], upper: float) -> float:
    if value is None:
        return _scale(upper)
    return max(0.0, value - upper)


# 목적: constraint별 정규화 기준값을 만든다.
# 입력: condition mapping.
# 출력: constraint 이름별 scale dict.
def _constraint_scales(condition: Condition) -> Dict[str, float]:
    return {
        "P_min": _scale(float(condition["P_req"])),
        "E_min": _scale(float(condition["E_req"])),
        "V_min": _scale(float(condition["V_min"])),
        "V_max": _scale(float(condition["V_max"])),
        "c_min": _scale(float(condition["c_min"])),
        "c_max": _scale(float(condition["c_max"])),
        "L_bat_max": _scale(float(condition["L_max"])),
        "W_bat_max": _scale(float(condition["W_max"])),
    }


# 목적: 0 또는 작은 기준값 때문에 정규화가 불안정해지지 않게 한다.
# 입력: 기준값.
# 출력: 최소 1.0 이상의 scale.
def _scale(value: float) -> float:
    return max(1.0, abs(value))


# === FASTCHARGE_V1_EVALUATION_PATCH ===

_original_compute_constraint_violations_fastcharge_v1 = compute_constraint_violations
_original_constraint_scales_fastcharge_v1 = _constraint_scales

if "fastcharge" not in CONSTRAINT_NAMES:
    CONSTRAINT_NAMES = tuple(CONSTRAINT_NAMES) + ("fastcharge",)


def _fastcharge_recursive_find(obj, target_keys):
    """Find a requested key anywhere in the BatPaC result/raw structure."""
    targets = {str(x) for x in target_keys}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key) in targets:
                return value

        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _fastcharge_recursive_find(
                    value,
                    targets,
                )
                if found is not None:
                    return found

    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (dict, list)):
                found = _fastcharge_recursive_find(
                    value,
                    targets,
                )
                if found is not None:
                    return found

    return None


def _fastcharge_float_or_none(value):
    try:
        x = float(value)
    except Exception:
        return None

    import math
    return x if math.isfinite(x) else None


def compute_constraint_violations(result, condition):
    violations = dict(
        _original_compute_constraint_violations_fastcharge_v1(
            result,
            condition,
        )
    )

    if condition.get("name") != "s_fastcharge":
        return violations

    g259 = _fastcharge_float_or_none(
        _fastcharge_recursive_find(
            result,
            {
                "G259_fastcharge_limit_um",
            },
        )
    )

    c27 = _fastcharge_float_or_none(
        _fastcharge_recursive_find(
            result,
            {
                "Chem_C27_min_um",
            },
        )
    )

    # Prefer the explicit BatPaC override output.
    # Fall back to the original candidate input if needed.
    x_t = _fastcharge_float_or_none(
        _fastcharge_recursive_find(
            result,
            {
                "G269_override_um",
                "x_T",
            },
        )
    )

    if (
        g259 is None
        or c27 is None
        or x_t is None
    ):
        # Missing physical outputs must never be interpreted as feasible.
        violations["fastcharge"] = 1.0e6
        return violations

    allowable = max(g259, c27)

    # <= 0 means feasible.
    violations["fastcharge"] = max(
        0.0,
        x_t - allowable,
    )

    return violations


def _constraint_scales(condition):
    scales = dict(
        _original_constraint_scales_fastcharge_v1(
            condition
        )
    )

    if condition.get("name") == "s_fastcharge":
        # Normalize by the widest declared electrode-thickness span:
        # LFP 50–165 um => 115 um.
        scales["fastcharge"] = 115.0

    return scales
