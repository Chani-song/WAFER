"""Run a mixed-variable GA smoke test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH, make_batpac_evaluator
from optimizers.mixed_ga import MixedVariableGAOptimizer
from optimizers.scripts.run_real_batpac_smoke import (
    extract_failure_message,
    make_timeout_batpac_evaluator,
    save_records_jsonl,
)

DEFAULT_INITIAL_CANDIDATE: Dict[str, Any] = {
    "x_chem": "NMC811",
    "x_T": 100.0,
    "x_S": 20,
    "x_MPR": 4,
    "x_R": 4,
}


# 목적: BatPaC 없이 GA 흐름을 검증할 deterministic evaluator를 제공한다.
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
def make_smoke_evaluator(args: argparse.Namespace):
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


# 목적: mixed GA smoke 결과와 실패 내역을 stdout에 출력한다.
# 입력: OptimizationResult.
# 출력: 없음.
def print_smoke_summary(result: Any) -> None:
    """Print smoke result summary."""
    failed_records = [record for record in result.records if not record.result.get("success", False)]
    feasible_count = sum(1 for record in result.records if record.feasible)

    print(f"method name: {result.method_name}")
    print(f"condition: {result.condition_name}")
    print(f"budget: {result.budget}")
    print(f"number of evaluations: {len(result.records)}")
    print(f"feasible evaluations: {feasible_count}")
    print(f"best feasible candidate: {json.dumps(result.best_feasible_candidate, sort_keys=True)}")
    print(f"best feasible cost: {result.best_feasible_cost}")

    if not failed_records:
        print("failed evaluations: none")
        return

    print("failed evaluations:")
    for record in failed_records:
        print(f"  - iteration {record.iteration}: {extract_failure_message(record.result)}")


# 목적: mixed GA smoke command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: JSONL records와 요약 정보를 남긴다.
def main() -> None:
    """Run the mixed-variable GA smoke script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="s0")
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--real-batpac", action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--no-initial-candidate", action="store_true")
    parser.add_argument(
        "--records-jsonl",
        default="outputs_mcp/smoke_records/mixed_ga_records.jsonl",
    )
    args = parser.parse_args()

    optimizer = MixedVariableGAOptimizer(
        evaluator=make_smoke_evaluator(args),
        population_size=args.population_size,
    )
    initial_candidates = None if args.no_initial_candidate else [DEFAULT_INITIAL_CANDIDATE]
    result = optimizer.optimize(
        condition_name=args.condition,
        budget=args.budget,
        seed=args.seed,
        initial_candidates=initial_candidates,
    )

    records_path = Path(args.records_jsonl)
    save_records_jsonl(records_path, result.records)
    print(f"evaluation records saved: {records_path}")
    print_smoke_summary(result)


if __name__ == "__main__":
    main()
