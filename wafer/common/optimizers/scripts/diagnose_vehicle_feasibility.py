"""Diagnose feasibility of the s_vehicle power requirement."""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.core.domain import make_condition, repair_candidate
from optimizers.core.evaluation import (
    compute_constraint_violations,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)
from optimizers.scripts.run_real_batpac_smoke import (
    DEFAULT_INITIAL_CANDIDATE,
    _jsonable,
    make_timeout_batpac_evaluator,
)

THICKNESS_VALUES = (50.0, 60.0, 80.0, 100.0, 120.0, 140.0)
SERIES_VALUES = (20, 24, 28, 30)
MODULE_PARALLEL_VALUES = (4, 8, 12, 16, 20)
ROW_VALUES = (2, 3, 4)
CHEMISTRIES = ("NMC811", "LFP")
CSV_FIELDNAMES = (
    "condition_name",
    "iteration",
    "candidate_json",
    "x_chem",
    "x_T",
    "x_S",
    "x_MPR",
    "x_R",
    "cost_per_kWh",
    "total_pack_cost",
    "P",
    "E",
    "V",
    "c",
    "L_bat",
    "W_bat",
    "feasible",
    "P_min_violation",
    "E_min_violation",
    "V_min_violation",
    "V_max_violation",
    "c_min_violation",
    "c_max_violation",
    "L_bat_max_violation",
    "W_bat_max_violation",
    "normalized_constraint_violation",
    "penalized_loss",
    "failure_reason",
    "optimization_valid",
    "wall_time_sec",
)


# 목적: s_vehicle 진단용 candidate 목록을 deterministic 순서로 만든다.
# 입력: 최대 evaluation 수.
# 출력: 중복 제거된 repaired candidate 리스트.
def build_candidate_set(max_evals: int) -> List[Dict[str, Any]]:
    """Build deterministic diagnostic candidates."""
    if max_evals < 0:
        raise ValueError("max_evals must be non-negative")

    candidates: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, ...]] = set()
    for candidate in _candidate_stream():
        if len(candidates) >= max_evals:
            break
        repaired = repair_candidate(candidate)
        key = _candidate_key(repaired)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(repaired)
    return candidates


# 목적: power 한계 확인에 유리한 순서로 raw candidate를 생성한다.
# 입력: 없음.
# 출력: raw candidate iterable.
def _candidate_stream() -> Iterable[Dict[str, Any]]:
    yield dict(DEFAULT_INITIAL_CANDIDATE)

    # manual layout에서 두께와 chemistry만 바꾸면 power mapping의 민감도를 먼저 확인할 수 있다.
    for chemistry in CHEMISTRIES:
        for thickness in THICKNESS_VALUES:
            yield {
                "x_chem": chemistry,
                "x_T": thickness,
                "x_S": DEFAULT_INITIAL_CANDIDATE["x_S"],
                "x_MPR": DEFAULT_INITIAL_CANDIDATE["x_MPR"],
                "x_R": DEFAULT_INITIAL_CANDIDATE["x_R"],
            }

    # 큰 module count 조합을 먼저 평가해 현재 bounds 안에서 도달 가능한 최대 power를 확인한다.
    for layout in _layout_values_by_descending_module_count():
        for chemistry in CHEMISTRIES:
            for thickness in (50.0, 100.0):
                yield {
                    "x_chem": chemistry,
                    "x_T": thickness,
                    "x_S": layout["x_S"],
                    "x_MPR": layout["x_MPR"],
                    "x_R": layout["x_R"],
                }

    for chemistry in CHEMISTRIES:
        for thickness in THICKNESS_VALUES:
            for layout in _layout_values_by_descending_module_count():
                yield {
                    "x_chem": chemistry,
                    "x_T": thickness,
                    "x_S": layout["x_S"],
                    "x_MPR": layout["x_MPR"],
                    "x_R": layout["x_R"],
                }


# 목적: 큰 pack layout을 우선 평가하도록 layout 조합을 정렬한다.
# 입력: 없음.
# 출력: x_S, x_MPR, x_R dict 리스트.
def _layout_values_by_descending_module_count() -> List[Dict[str, int]]:
    layouts = [
        {"x_S": series, "x_MPR": module_parallel, "x_R": rows}
        for series in SERIES_VALUES
        for module_parallel in MODULE_PARALLEL_VALUES
        for rows in ROW_VALUES
    ]
    return sorted(
        layouts,
        key=lambda item: (
            -(item["x_S"] * item["x_MPR"] * item["x_R"]),
            -item["x_S"],
            -item["x_MPR"],
            -item["x_R"],
        ),
    )


# 목적: candidate 중복 제거를 위한 canonical key를 만든다.
# 입력: repaired candidate mapping.
# 출력: hashable tuple key.
def _candidate_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (
        candidate["x_chem"],
        round(float(candidate["x_T"]), 10),
        int(candidate["x_S"]),
        int(candidate["x_MPR"]),
        int(candidate["x_R"]),
    )


# 목적: candidate 하나를 real evaluator로 평가해 diagnostic record로 변환한다.
# 입력: iteration, candidate, condition, evaluator callable.
# 출력: JSONL/CSV에 저장할 record dict.
def evaluate_candidate(
    iteration: int,
    candidate: Mapping[str, Any],
    condition: Mapping[str, Any],
    evaluator: Any,
) -> Dict[str, Any]:
    """Evaluate one diagnostic candidate."""
    repaired = repair_candidate(candidate)
    started = time.perf_counter()
    result = evaluator(dict(repaired), dict(condition))
    wall_time_sec = time.perf_counter() - started

    violations = compute_constraint_violations(result, condition)
    feasible = is_feasible(result, condition)
    normalized_violation = normalized_constraint_violation(result, condition)
    loss = penalized_loss(result, condition)
    return {
        "iteration": iteration,
        "condition_name": condition["name"],
        "candidate": repaired,
        "cost_per_kWh": result.get("cost"),
        "total_pack_cost": result.get("total_pack_cost"),
        "P": result.get("P"),
        "E": result.get("E"),
        "V": result.get("V"),
        "c": result.get("c"),
        "L_bat": result.get("L_bat"),
        "W_bat": result.get("W_bat"),
        "feasible": feasible,
        "constraint_violations": violations,
        "normalized_constraint_violation": normalized_violation,
        "penalized_loss": loss,
        "failure_reason": result.get("failure_reason"),
        "optimization_valid": result.get("optimization_valid", result.get("success", False)),
        "wall_time_sec": wall_time_sec,
        "result": result,
    }


# 목적: condition 하나에 대해 모든 diagnostic candidate를 평가한다.
# 입력: condition 이름, candidate 리스트, evaluator.
# 출력: diagnostic record 리스트.
def evaluate_condition(
    condition_name: str,
    candidates: List[Dict[str, Any]],
    evaluator: Any,
) -> List[Dict[str, Any]]:
    """Evaluate candidates for one condition."""
    condition = make_condition(condition_name)
    records = []
    for iteration, candidate in enumerate(candidates):
        records.append(evaluate_candidate(iteration, candidate, condition, evaluator))
    return records


# 목적: diagnostic records를 JSONL로 저장한다.
# 입력: 저장 경로와 records.
# 출력: 없음.
def save_diagnostic_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Save diagnostic records as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_jsonable(record), sort_keys=True) + "\n")


# 목적: power violation 기준으로 정렬한 CSV summary를 저장한다.
# 입력: 저장 경로와 records.
# 출력: 없음.
def save_diagnostic_csv(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Save flattened diagnostic records as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(records, key=_power_violation_sort_key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDNAMES))
        writer.writeheader()
        for record in sorted_rows:
            writer.writerow(_csv_row(record))


# 목적: power violation이 작은 record가 먼저 오도록 정렬 key를 만든다.
# 입력: diagnostic record.
# 출력: sort key tuple.
def _power_violation_sort_key(record: Mapping[str, Any]) -> Tuple[float, float, int]:
    violations = record.get("constraint_violations")
    power_violation = _finite_float_or_default(
        violations.get("P_min") if isinstance(violations, Mapping) else None,
        math.inf,
    )
    power = _finite_float_or_default(record.get("P"), -math.inf)
    iteration = int(record.get("iteration", 0))
    return (power_violation, -power, iteration)


# 목적: diagnostic record를 CSV 한 행으로 평탄화한다.
# 입력: diagnostic record.
# 출력: CSV field dict.
def _csv_row(record: Mapping[str, Any]) -> Dict[str, Any]:
    candidate = record.get("candidate")
    violations = record.get("constraint_violations")
    candidate_map = candidate if isinstance(candidate, Mapping) else {}
    violation_map = violations if isinstance(violations, Mapping) else {}
    return {
        "condition_name": record.get("condition_name"),
        "iteration": record.get("iteration"),
        "candidate_json": json.dumps(_jsonable(candidate), sort_keys=True),
        "x_chem": candidate_map.get("x_chem"),
        "x_T": candidate_map.get("x_T"),
        "x_S": candidate_map.get("x_S"),
        "x_MPR": candidate_map.get("x_MPR"),
        "x_R": candidate_map.get("x_R"),
        "cost_per_kWh": record.get("cost_per_kWh"),
        "total_pack_cost": record.get("total_pack_cost"),
        "P": record.get("P"),
        "E": record.get("E"),
        "V": record.get("V"),
        "c": record.get("c"),
        "L_bat": record.get("L_bat"),
        "W_bat": record.get("W_bat"),
        "feasible": record.get("feasible"),
        "P_min_violation": violation_map.get("P_min"),
        "E_min_violation": violation_map.get("E_min"),
        "V_min_violation": violation_map.get("V_min"),
        "V_max_violation": violation_map.get("V_max"),
        "c_min_violation": violation_map.get("c_min"),
        "c_max_violation": violation_map.get("c_max"),
        "L_bat_max_violation": violation_map.get("L_bat_max"),
        "W_bat_max_violation": violation_map.get("W_bat_max"),
        "normalized_constraint_violation": record.get("normalized_constraint_violation"),
        "penalized_loss": record.get("penalized_loss"),
        "failure_reason": record.get("failure_reason"),
        "optimization_valid": record.get("optimization_valid"),
        "wall_time_sec": record.get("wall_time_sec"),
    }


# 목적: diagnostic 결과를 사람이 읽을 수 있는 summary로 계산한다.
# 입력: condition 이름과 records.
# 출력: summary dict.
def summarize_records(condition_name: str, records: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize diagnostic records."""
    feasible_records = [record for record in records if bool(record.get("feasible"))]
    finite_power_records = [
        record for record in records if _finite_float_or_none(record.get("P")) is not None
    ]
    smallest_power_violation = min(records, key=_power_violation_sort_key) if records else None
    highest_power = max(
        finite_power_records,
        key=lambda record: float(record["P"]),
        default=None,
    )
    p_values = [float(record["P"]) for record in finite_power_records]
    power_range = max(p_values) - min(p_values) if p_values else 0.0
    p_changes = power_range > 1e-6
    p_req = float(make_condition(condition_name)["P_req"])
    p_constant_at_requirement = bool(p_values) and all(abs(value - p_req) <= 1e-6 for value in p_values)
    best_feasible = min(
        feasible_records,
        key=lambda record: _finite_float_or_default(record.get("cost_per_kWh"), math.inf),
        default=None,
    )

    return {
        "condition_name": condition_name,
        "evaluations": len(records),
        "feasible_count": len(feasible_records),
        "best_feasible_candidate": _record_candidate(best_feasible),
        "best_feasible_cost": best_feasible.get("cost_per_kWh") if best_feasible else None,
        "smallest_power_violation_candidate": _record_candidate(smallest_power_violation),
        "smallest_power_violation": _record_power_violation(smallest_power_violation),
        "smallest_power_violation_P": smallest_power_violation.get("P") if smallest_power_violation else None,
        "highest_power_candidate": _record_candidate(highest_power),
        "highest_power_P": highest_power.get("P") if highest_power else None,
        "p_min": min(p_values) if p_values else None,
        "p_max": max(p_values) if p_values else None,
        "p_changes_across_candidates": p_changes,
        "p_constant_at_requirement": p_constant_at_requirement,
        "p_appears": _power_interpretation_text(p_values, p_req, p_changes, p_constant_at_requirement),
    }


# 목적: record에서 candidate dict를 안전하게 추출한다.
# 입력: diagnostic record 또는 None.
# 출력: candidate dict 또는 None.
def _record_candidate(record: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    candidate = record.get("candidate")
    return dict(candidate) if isinstance(candidate, Mapping) else None


# 목적: record의 power violation을 안전하게 추출한다.
# 입력: diagnostic record 또는 None.
# 출력: P_min violation 또는 None.
def _record_power_violation(record: Optional[Mapping[str, Any]]) -> Optional[float]:
    if record is None:
        return None
    violations = record.get("constraint_violations")
    if not isinstance(violations, Mapping):
        return None
    return _finite_float_or_none(violations.get("P_min"))


# 목적: P 값들이 design에 따라 변하는지 해석 문구를 만든다.
# 입력: finite P 값, P requirement, 변화 여부, requirement 상수 여부.
# 출력: stdout에 표시할 판단 문구.
def _power_interpretation_text(
    p_values: List[float],
    p_req: float,
    p_changes: bool,
    p_constant_at_requirement: bool,
) -> str:
    if not p_values:
        return "no finite P values were returned"
    if p_constant_at_requirement:
        return "P appears constant at the requirement value"
    if p_changes:
        return "P varies with design"
    return "P is constant, but not at the requirement value"


# 목적: diagnostic summary를 stdout에 출력한다.
# 입력: summary dict.
# 출력: 없음.
def print_summary(summary: Mapping[str, Any]) -> None:
    """Print diagnostic summary."""
    print(f"condition: {summary['condition_name']}")
    print(f"evaluations: {summary['evaluations']}")
    print(f"number of feasible candidates: {summary['feasible_count']}")
    print(
        "best feasible candidate: "
        f"{json.dumps(_jsonable(summary['best_feasible_candidate']), sort_keys=True)}"
    )
    print(f"best feasible cost: {summary['best_feasible_cost']}")
    print(
        "candidate with smallest power violation: "
        f"{json.dumps(_jsonable(summary['smallest_power_violation_candidate']), sort_keys=True)}"
    )
    print(
        "smallest power violation: "
        f"{summary['smallest_power_violation']} at P={summary['smallest_power_violation_P']}"
    )
    print(
        "candidate with highest achieved P: "
        f"{json.dumps(_jsonable(summary['highest_power_candidate']), sort_keys=True)}"
    )
    print(f"highest achieved P: {summary['highest_power_P']}")
    print(
        "P changes across candidates: "
        f"{summary['p_changes_across_candidates']} "
        f"(min={summary['p_min']}, max={summary['p_max']})"
    )
    print(f"P interpretation: {summary['p_appears']}")


# 목적: 첫 번째 record에서 BatPaC에 전달된 raw input dict를 추출한다.
# 입력: diagnostic records.
# 출력: raw BatPaC input dict 또는 None.
def representative_batpac_inputs(records: List[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return representative BatPaC inputs from records."""
    for record in records:
        result = record.get("result")
        raw = result.get("raw") if isinstance(result, Mapping) else None
        if isinstance(raw, Mapping) and isinstance(raw.get("batpac_inputs"), Mapping):
            return dict(raw["batpac_inputs"])
    return None


# 목적: 대표 BatPaC input dict를 stdout에 출력한다.
# 입력: condition 이름과 diagnostic records.
# 출력: 없음.
def print_representative_batpac_inputs(
    condition_name: str,
    records: List[Mapping[str, Any]],
) -> None:
    """Print representative BatPaC inputs."""
    inputs = representative_batpac_inputs(records)
    print(f"representative raw BatPaC inputs for {condition_name}:")
    print(json.dumps(_jsonable(inputs), indent=2, sort_keys=True))


# 목적: s0와 s_vehicle 사이에서 P target이 바뀌었는지 요약한다.
# 입력: condition별 diagnostic records.
# 출력: comparison summary dict.
def summarize_condition_switch(
    records_by_condition: Mapping[str, List[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Summarize s0 versus s_vehicle power behavior."""
    s0_values = _finite_power_values(records_by_condition.get("s0", []))
    vehicle_values = _finite_power_values(records_by_condition.get("s_vehicle", []))
    return {
        "s0_unique_P": sorted(set(s0_values)),
        "s_vehicle_unique_P": sorted(set(vehicle_values)),
        "p_changes_from_source_to_vehicle": (
            bool(s0_values)
            and bool(vehicle_values)
            and set(s0_values) == {float(make_condition("s0")["P_req"])}
            and set(vehicle_values) == {float(make_condition("s_vehicle")["P_req"])}
        ),
        "s0_p_varies_across_candidates": _values_vary(s0_values),
        "s_vehicle_p_varies_across_candidates": _values_vary(vehicle_values),
        "s_vehicle_feasible_count": sum(
            1 for record in records_by_condition.get("s_vehicle", []) if record.get("feasible")
        ),
        "representative_s_vehicle_batpac_inputs": representative_batpac_inputs(
            records_by_condition.get("s_vehicle", [])
        ),
    }


# 목적: comparison summary를 JSON으로 저장한다.
# 입력: 저장 경로와 summary dict.
# 출력: 없음.
def save_summary_json(path: Path, summary: Mapping[str, Any]) -> None:
    """Save a summary JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(summary), indent=2, sort_keys=True), encoding="utf-8")


# 목적: comparison summary를 stdout에 출력한다.
# 입력: comparison summary dict.
# 출력: 없음.
def print_condition_switch_summary(summary: Mapping[str, Any]) -> None:
    """Print s0 versus s_vehicle summary."""
    print("condition switch summary:")
    print(f"  s0 unique P: {summary['s0_unique_P']}")
    print(f"  s_vehicle unique P: {summary['s_vehicle_unique_P']}")
    print(
        "  P changes from source to vehicle requirement: "
        f"{summary['p_changes_from_source_to_vehicle']}"
    )
    print(f"  s0 P varies across candidates: {summary['s0_p_varies_across_candidates']}")
    print(
        "  s_vehicle P varies across candidates: "
        f"{summary['s_vehicle_p_varies_across_candidates']}"
    )
    print(f"  s_vehicle feasible candidates: {summary['s_vehicle_feasible_count']}")


# 목적: finite P 값만 추출한다.
# 입력: diagnostic records.
# 출력: finite P 값 리스트.
def _finite_power_values(records: List[Mapping[str, Any]]) -> List[float]:
    return [
        float(record["P"])
        for record in records
        if _finite_float_or_none(record.get("P")) is not None
    ]


# 목적: 값 목록이 실제로 변화하는지 판단한다.
# 입력: float 값 리스트.
# 출력: 변화 여부.
def _values_vary(values: List[float]) -> bool:
    if not values:
        return False
    return max(values) - min(values) > 1e-6


# 목적: condition별 output path를 일관되게 만든다.
# 입력: output directory와 condition 이름.
# 출력: JSONL path와 CSV path tuple.
def output_paths(output_dir: Path, condition_name: str) -> Tuple[Path, Path]:
    """Return diagnostic output paths."""
    stem = f"vehicle_feasibility_{condition_name}"
    return output_dir / f"{stem}.jsonl", output_dir / f"{stem}.csv"


# 목적: 값이 finite float이면 반환하고 아니면 None을 반환한다.
# 입력: 임의 값.
# 출력: finite float 또는 None.
def _finite_float_or_none(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


# 목적: 값이 finite float이면 반환하고 아니면 기본값을 반환한다.
# 입력: 임의 값과 기본값.
# 출력: finite float 또는 기본값.
def _finite_float_or_default(value: Any, default: float) -> float:
    numeric = _finite_float_or_none(value)
    return default if numeric is None else numeric


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-evals", type=int, default=40)
    parser.add_argument("--compare-s0", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--output-dir", default="outputs_mcp/diagnostics")
    return parser.parse_args()


# 목적: s_vehicle feasibility diagnostic command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: JSONL/CSV 파일과 stdout summary.
def main() -> None:
    """Run vehicle feasibility diagnostics."""
    args = parse_args()
    candidates = build_candidate_set(args.max_evals)
    evaluator = make_timeout_batpac_evaluator(
        timeout_sec=args.timeout_sec,
        persistent_workbook_path=args.persistent_workbook_path,
        visible=args.visible,
    )
    condition_names = ["s_vehicle"]
    if args.compare_s0:
        condition_names.append("s0")

    output_dir = Path(args.output_dir)
    records_by_condition: Dict[str, List[Mapping[str, Any]]] = {}
    for condition_name in condition_names:
        records = evaluate_condition(condition_name, candidates, evaluator)
        records_by_condition[condition_name] = records
        jsonl_path, csv_path = output_paths(output_dir, condition_name)
        save_diagnostic_jsonl(jsonl_path, records)
        save_diagnostic_csv(csv_path, records)
        print(f"records saved: {jsonl_path}")
        print(f"CSV summary saved: {csv_path}")
        print_summary(summarize_records(condition_name, records))
        if condition_name == "s_vehicle":
            print_representative_batpac_inputs(condition_name, records)

    if args.compare_s0:
        comparison = summarize_condition_switch(records_by_condition)
        comparison_path = output_dir / "vehicle_feasibility_condition_switch_summary.json"
        save_summary_json(comparison_path, comparison)
        print(f"condition switch summary saved: {comparison_path}")
        print_condition_switch_summary(comparison)


if __name__ == "__main__":
    main()
