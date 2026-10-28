"""Run a mixed-variable pattern-search smoke test."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH, make_batpac_evaluator
from optimizers.core.base import Evaluator, OptimizationResult
from optimizers.pattern_search import PatternSearchOptimizer
from optimizers.scripts.run_real_batpac_smoke import (
    DEFAULT_INITIAL_CANDIDATE,
    make_timeout_batpac_evaluator,
    save_records_jsonl,
)


# 목적: BatPaC 없이 pattern search 흐름을 검증할 deterministic evaluator를 제공한다.
# 입력: candidate dict와 condition dict.
# 출력: expected evaluator schema를 따르는 synthetic result dict.
def fake_evaluator(candidate: Dict[str, Any], condition: Dict[str, Any]) -> Dict[str, Any]:
    """Return deterministic synthetic battery metrics."""
    module_count = float(candidate["x_S"] * candidate["x_MPR"] * candidate["x_R"])
    chemistry_cost = (
        float(condition["cam_cost_lfp"])
        if candidate["x_chem"] == "LFP"
        else float(condition["cam_cost_nmc811"])
    )
    energy_factor = 0.95 if candidate["x_chem"] == "LFP" else 1.05
    cost = 75.0 + chemistry_cost + 0.12 * candidate["x_T"] + 0.015 * module_count

    return {
        "success": True,
        "cost": cost,
        "total_pack_cost": cost * float(condition["E_req"]),
        "P": 120.0 + 3.5 * module_count,
        "E": 60.0 + 0.30 * candidate["x_T"] * energy_factor + 0.70 * module_count,
        "V": 240.0 + 10.0 * candidate["x_S"],
        "c": 28.0 + 0.35 * candidate["x_T"],
        "L_bat": 900.0 + 45.0 * candidate["x_MPR"],
        "W_bat": 550.0 + 150.0 * candidate["x_R"],
        "raw": None,
    }


# 목적: CLI option에 따라 fake 또는 real BatPaC evaluator를 만든다.
# 입력: argparse namespace.
# 출력: evaluator callable.
def make_smoke_evaluator(args: argparse.Namespace) -> Evaluator:
    """Create the smoke evaluator."""
    if not args.real_batpac:
        return fake_evaluator
    if args.timeout_sec > 0:
        return make_timeout_batpac_evaluator(
            timeout_sec=args.timeout_sec,
            persistent_workbook_path=args.persistent_workbook_path,
            visible=args.visible,
        )
    return make_batpac_evaluator(
        persistent_workbook_path=args.persistent_workbook_path,
        visible=args.visible,
    )


# 목적: pattern search smoke 결과를 stdout에 출력한다.
# 입력: OptimizationResult와 total runtime.
# 출력: 없음.
def print_smoke_summary(result: OptimizationResult, total_runtime_sec: float) -> None:
    """Print smoke result summary."""
    records = result.records
    failed_count = sum(
        1
        for record in records
        if not record.result.get("optimization_valid", record.result.get("success", False))
    )
    missing_cost_count = sum(
        1 for record in records if record.result.get("failure_reason") == "missing_cost"
    )
    feasible_count = sum(1 for record in records if record.feasible)
    eval_times = [float(record.wall_time_sec) for record in records]

    print(f"method name: {result.method_name}")
    print(f"condition: {result.condition_name}")
    print(f"budget: {result.budget}")
    print(f"evaluations: {len(records)}")
    print(f"feasible count: {feasible_count}")
    print(f"failed count: {failed_count}")
    print(f"missing-cost count: {missing_cost_count}")
    best_feasible_candidate = json.dumps(result.best_feasible_candidate, sort_keys=True)
    print(f"best feasible candidate: {best_feasible_candidate}")
    print(f"best feasible cost: {result.best_feasible_cost}")
    print(f"total runtime: {total_runtime_sec:.3f}")
    print(f"mean evaluation time: {statistics.mean(eval_times) if eval_times else 0.0:.3f}")


# 목적: pattern search smoke command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: JSONL records와 요약 정보를 남긴다.
def main() -> None:
    """Run the pattern-search smoke script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="s0")
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--real-batpac", action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--include-manual-initial", action="store_true")
    parser.add_argument(
        "--records-jsonl",
        default="outputs_mcp/smoke_records/pattern_search_records.jsonl",
    )
    args = parser.parse_args()

    optimizer = PatternSearchOptimizer(evaluator=make_smoke_evaluator(args))
    initial_candidates = [DEFAULT_INITIAL_CANDIDATE] if args.include_manual_initial else None
    started = time.perf_counter()
    result = optimizer.optimize(
        condition_name=args.condition,
        budget=args.budget,
        seed=args.seed,
        initial_candidates=initial_candidates,
    )
    total_runtime_sec = time.perf_counter() - started

    records_path = Path(args.records_jsonl)
    save_records_jsonl(records_path, result.records)
    print(f"evaluation records saved: {records_path}")
    print_smoke_summary(result, total_runtime_sec)


if __name__ == "__main__":
    main()
