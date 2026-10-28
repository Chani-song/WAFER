"""Run a small random-search smoke test."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from optimizers.core.batpac_adapter import make_batpac_evaluator
from optimizers.random_search import RandomSearchOptimizer


# 목적: BatPaC 없이 random search 흐름을 검증할 deterministic evaluator를 제공한다.
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

    return {
        "success": True,
        "cost": 75.0 + chemistry_cost + 0.12 * candidate["x_T"] + 0.015 * module_count,
        "P": 120.0 + 3.5 * module_count,
        "E": 60.0 + 0.30 * candidate["x_T"] * energy_factor + 0.70 * module_count,
        "V": 240.0 + 10.0 * candidate["x_S"],
        "c": 28.0 + 0.35 * candidate["x_T"],
        "L_bat": 900.0 + 45.0 * candidate["x_MPR"],
        "W_bat": 550.0 + 150.0 * candidate["x_R"],
        "raw": None,
    }


# 목적: smoke script command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: best feasible candidate, cost, evaluation 수, feasibility rate를 stdout에 출력한다.
def main() -> None:
    """Run the smoke script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="s0")
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--real-batpac", action="store_true")
    args = parser.parse_args()

    # 실제 BatPaC 실행은 Excel 권한과 workbook 접근이 준비된 환경에서만 사용한다.
    evaluator = make_batpac_evaluator() if args.real_batpac else fake_evaluator
    optimizer = RandomSearchOptimizer(evaluator=evaluator)
    result = optimizer.optimize(
        condition_name=args.condition,
        budget=args.budget,
        seed=args.seed,
    )

    print(f"best feasible candidate: {json.dumps(result.best_feasible_candidate, sort_keys=True)}")
    print(f"best feasible cost: {result.best_feasible_cost}")
    print(f"total evaluations: {len(result.records)}")
    print(f"feasibility rate: {result.metadata['feasibility_rate']:.3f}")


if __name__ == "__main__":
    main()
