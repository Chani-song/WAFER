"""Run real BatPaC condition-level optimizer smoke benchmarks."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.core.base import BaseOptimizer, Evaluator
from optimizers.mixed_ga import MixedVariableGAOptimizer
from optimizers.pattern_search import PatternSearchOptimizer
from optimizers.random_search import RandomSearchOptimizer
from optimizers.scripts.run_real_batpac_smoke import (
    DEFAULT_INITIAL_CANDIDATE,
    _jsonable,
    batpac_evaluator_mode,
    make_timeout_batpac_evaluator,
    save_records_jsonl,
)

CONDITIONS = ("s0", "s_cost", "s_vehicle", "s_space")


# 목적: command line에서 선택한 optimizer 객체를 만든다.
# 입력: optimizer 이름과 evaluator callable.
# 출력: BaseOptimizer 구현체.
def make_optimizer(name: str, evaluator: Evaluator, surrogate_slices_path: Optional[Path] = None) -> BaseOptimizer:
    """Create an optimizer."""
    if name == "random":
        return RandomSearchOptimizer(evaluator=evaluator)
    if name == "axbo":
        from optimizers.ax_bayes import AxBayesOptOptimizer

        return AxBayesOptOptimizer(
            evaluator=evaluator,
            surrogate_slices_path=str(surrogate_slices_path) if surrogate_slices_path else None,
        )
    if name == "mixed_ga":
        return MixedVariableGAOptimizer(evaluator=evaluator, population_size=10)
    if name == "pattern":
        return PatternSearchOptimizer(evaluator=evaluator)
    raise ValueError(f"Unknown optimizer: {name}")


# 목적: condition 하나에 대해 real BatPaC benchmark를 실행한다.
# 입력: optimizer 이름, condition 이름, budget, seed, evaluator, initial candidate 옵션.
# 출력: summary dict와 OptimizationResult.
def run_condition(
    optimizer_name: str,
    condition_name: str,
    budget: int,
    seed: int,
    evaluator: Evaluator,
    include_manual_initial: bool,
    surrogate_slices_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run one condition benchmark."""
    optimizer = make_optimizer(optimizer_name, evaluator, surrogate_slices_path=surrogate_slices_path)
    initial_candidates = [DEFAULT_INITIAL_CANDIDATE] if include_manual_initial else None

    started = time.perf_counter()
    result = optimizer.optimize(
        condition_name=condition_name,
        budget=budget,
        seed=seed,
        initial_candidates=initial_candidates,
    )
    total_runtime_sec = time.perf_counter() - started

    return {
        "result": result,
        "summary": summarize_result(
            optimizer_name=optimizer_name,
            condition_name=condition_name,
            budget=budget,
            result=result,
            total_runtime_sec=total_runtime_sec,
        ),
    }


# 목적: OptimizationResult를 condition-level summary row로 변환한다.
# 입력: optimizer 이름, condition 이름, budget, result, runtime.
# 출력: summary dict.
def summarize_result(
    optimizer_name: str,
    condition_name: str,
    budget: int,
    result: Any,
    total_runtime_sec: float,
    evaluator_mode: str = "subprocess",
) -> Dict[str, Any]:
    """Summarize one benchmark result."""
    records = result.records
    eval_times = [float(record.wall_time_sec) for record in records]
    feasible_count = sum(1 for record in records if record.feasible)
    failed_count = sum(1 for record in records if not record.result.get("optimization_valid", record.result.get("success", False)))
    missing_cost_count = sum(1 for record in records if record.result.get("failure_reason") == "missing_cost")
    missing_constraint_count = sum(
        1
        for record in records
        if record.result.get("failure_reason") in {"missing_constraint_output", "nonfinite_constraint_output"}
    )
    simulator_exception_count = sum(
        1 for record in records if record.result.get("failure_reason") == "simulator_exception"
    )

    return {
        "optimizer": optimizer_name,
        "condition": condition_name,
        "evaluator_mode": evaluator_mode,
        "budget": budget,
        "evaluations": len(records),
        "feasible_count": feasible_count,
        "feasible_rate": feasible_count / len(records) if records else 0.0,
        "failed_count": failed_count,
        "missing_cost_count": missing_cost_count,
        "missing_constraint_count": missing_constraint_count,
        "simulator_exception_count": simulator_exception_count,
        "best_feasible_cost": result.best_feasible_cost,
        "best_feasible_candidate": result.best_feasible_candidate,
        "total_runtime_sec": total_runtime_sec,
        "mean_eval_time_sec": statistics.mean(eval_times) if eval_times else 0.0,
        "median_eval_time_sec": statistics.median(eval_times) if eval_times else 0.0,
    }


# 목적: summary rows를 JSON과 CSV로 저장한다.
# 입력: output prefix와 summary row 리스트.
# 출력: 저장된 path dict.
def save_summaries(output_prefix: Path, summaries: List[Dict[str, Any]]) -> Dict[str, Path]:
    """Save benchmark summaries."""
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    json_path.write_text(json.dumps(_jsonable(summaries), indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]) if summaries else [])
        writer.writeheader()
        for summary in summaries:
            row = dict(summary)
            row["best_feasible_candidate"] = json.dumps(_jsonable(summary["best_feasible_candidate"]), sort_keys=True)
            writer.writerow(row)
    return {"json": json_path, "csv": csv_path}


# 목적: benchmark summary를 stdout에 출력한다.
# 입력: summary row 리스트.
# 출력: 없음.
def print_summaries(summaries: List[Dict[str, Any]]) -> None:
    """Print benchmark summaries."""
    for summary in summaries:
        print(
            f"{summary['optimizer']} {summary['condition']}: "
            f"mode={summary.get('evaluator_mode', 'subprocess')} "
            f"evals={summary['evaluations']} "
            f"feasible={summary['feasible_count']} "
            f"failed={summary['failed_count']} "
            f"missing_cost={summary['missing_cost_count']} "
            f"best_cost={summary['best_feasible_cost']} "
            f"mean_eval_s={summary['mean_eval_time_sec']:.3f}"
        )


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--optimizer",
        choices=["random", "mixed_ga", "pattern", "axbo"],
        required=True,
        help="Optimizer to run: random, mixed_ga, pattern, or axbo.",
    )
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--direct-eval", action="store_true")
    parser.add_argument("--include-manual-initial", action="store_true")
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--output-dir", default="outputs_mcp")
    return parser.parse_args()


# 목적: condition-level real BatPaC benchmark entry point를 실행한다.
# 입력: command line arguments.
# 출력: condition별 JSONL records와 summary JSON/CSV.
def main() -> None:
    """Run condition-level benchmarks."""
    args = parse_args()
    output_dir = Path(args.output_dir)
    evaluator_mode = batpac_evaluator_mode(use_subprocess_timeout=not args.direct_eval)
    if args.direct_eval and args.timeout_sec > 0:
        print("warning: timeout is disabled in direct BatPaC evaluator mode", flush=True)
    print(f"evaluator mode: {evaluator_mode}", flush=True)
    evaluator = make_timeout_batpac_evaluator(
        timeout_sec=args.timeout_sec,
        persistent_workbook_path=args.persistent_workbook_path,
        visible=args.visible,
        use_subprocess_timeout=not args.direct_eval,
    )

    summaries = []
    for offset, condition_name in enumerate(args.conditions):
        surrogate_slices_path = None
        if args.optimizer == "axbo":
            surrogate_slices_path = output_dir / "benchmark_records" / f"condition_smoke_{args.optimizer}_{condition_name}_axbo_surrogate_slices.jsonl"
        payload = run_condition(
            optimizer_name=args.optimizer,
            condition_name=condition_name,
            budget=args.budget,
            seed=args.seed + offset,
            evaluator=evaluator,
            include_manual_initial=args.include_manual_initial,
            surrogate_slices_path=surrogate_slices_path,
        )
        result = payload["result"]
        records_path = output_dir / "benchmark_records" / f"condition_smoke_{args.optimizer}_{condition_name}.jsonl"
        save_records_jsonl(records_path, result.records)
        summary = payload["summary"]
        summary["evaluator_mode"] = evaluator_mode
        summary["records_jsonl"] = str(records_path)
        summaries.append(summary)
        print(f"records saved: {records_path}")

    paths = save_summaries(output_dir / "benchmark_summaries" / f"condition_smoke_{args.optimizer}_summary", summaries)
    print(f"summary JSON saved: {paths['json']}")
    print(f"summary CSV saved: {paths['csv']}")
    print_summaries(summaries)


if __name__ == "__main__":
    main()
