"""Run real-BatPaC LLM strategy smoke matrix."""

from __future__ import annotations

import argparse
import csv
import json
import socket
import traceback
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from llm_methods.model_registry import DEFAULT_QWEN35_MODEL_CONFIG
from llm_methods.backends.qwen_server import QwenLengthMissingContentError
from llm_methods.scripts.run_llm_strategy_smoke import (
    MODEL_KEY_CHOICES,
    PROMPT_STYLE_CHOICES,
    SCENARIO_CHOICES,
    STRATEGY_CHOICES,
    make_backend,
    run_real_batpac_strategy,
    save_strategy_summary,
)
from llm_methods.traces.recorder import TraceRecorder
from llm_methods.context import SEMANTIC_LEVELS
from llm_methods.runtime import OPTIMIZER_CHOICES, save_summary_json
from llm_methods.runtime_isolation import build_cell_runtime_isolation
from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH

DEFAULT_MATRIX_STRATEGIES = ("warm_start", "variable_fixing", "react")

AGGREGATE_COLUMNS = [
    "model_key",
    "strategy",
    "optimizer",
    "effective_optimizer",
    "scenario",
    "semantic_level",
    "seed",
    "status",
    "error_type",
    "error_message",
    "real_batpac",
    "num_batpac_evaluations",
    "unique_real_batpac_evaluations",
    "selected_warm_start_count",
    "remaining_solver_budget",
    "feasible_count",
    "evaluations_to_first_feasible",
    "failed_count",
    "best_feasible_cost",
    "direct_reuse_feasible",
    "direct_reuse_cost",
    "improvement_over_direct_reuse",
    "feasibility_recovered_from_direct_reuse",
    "mean_eval_time_sec",
    "llm_seed",
    "optimizer_seed",
    "temperature",
    "top_p",
    "max_completion_tokens",
    "parse_retries",
    "llm_http_request_count",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_total_tokens",
    "llm_request_latency_sec",
    "total_wall_clock_sec",
    "numerical_optimizer_runtime_sec",
    "simulator_runtime_sec",
    "optimizer_overhead_sec",
    "exact_flops",
    "output_dir",
]


# 목적: matrix CLI arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="+", choices=STRATEGY_CHOICES, default=list(DEFAULT_MATRIX_STRATEGIES))
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIO_CHOICES, default=list(SCENARIO_CHOICES))
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--llm-seeds", nargs="+", type=int, default=None)
    parser.add_argument("--optimizer-seeds", nargs="+", type=int, default=None)
    parser.add_argument("--semantic-level", default="minimal")
    parser.add_argument("--semantic-levels", nargs="+", choices=SEMANTIC_LEVELS, default=None)
    parser.add_argument("--model-key", choices=MODEL_KEY_CHOICES, default="qwen35_4b")
    parser.add_argument("--backend", choices=("static", "qwen_server"), default="qwen_server")
    parser.add_argument(
        "--structured-output-transport",
        choices=("prompt_only", "json_schema"),
        default="prompt_only",
    )
    parser.add_argument(
        "--react-feedback-mode",
        choices=("binary", "quantitative"),
        default="quantitative",
    )
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--context-artifact", default=None)
    parser.add_argument(
        "--context-artifact-template",
        default=None,
        help="Scenario-specific path template, for example artifacts/{scenario}.json",
    )
    parser.add_argument("--compressed-manual-path", default=None)
    parser.add_argument("--full-manual-path", default=None)
    parser.add_argument("--model-config", default=str(DEFAULT_QWEN35_MODEL_CONFIG))
    parser.add_argument("--optimizer", choices=OPTIMIZER_CHOICES, default="pattern")
    parser.add_argument("--optimizers", nargs="+", choices=OPTIMIZER_CHOICES, default=None)
    parser.add_argument("--budget", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--prompt-style", choices=PROMPT_STYLE_CHOICES, default="standard")
    parser.add_argument("--react-engine", choices=("qwen_agent", "custom"), default="qwen_agent")
    parser.add_argument("--react-smoke-compact", action="store_true")
    parser.add_argument("--qwen-stop", nargs="*", default=None)
    parser.add_argument("--direct-eval", action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--strict-real-batpac", action="store_true")
    parser.add_argument("--output-dir", default="outputs_mcp/llm_strategy_real_batpac_matrix")
    parser.add_argument("--quantization", default="none")
    parser.add_argument("--prompt-version", choices=('v0', 'v2_canonical_react_vf', 'v3_3_5_environment_observation_react', 'v3_3_6_evaluate_only_react'), default="v0")
    parser.add_argument("--parse-retries", type=int, default=1)
    parser.add_argument("--leakage-diagnostics", action="store_true")
    parser.add_argument("--target-grid-optimum", default=None)
    parser.add_argument("--warm-start-count-mode", choices=("fixed", "model_selected"), default="fixed")
    parser.add_argument("--fixed-warm-start-count", type=int, default=3)
    parser.add_argument("--minimum-warm-start-count", type=int, default=1)
    parser.add_argument("--maximum-warm-start-count", type=int, default=None)
    parser.add_argument("--require-variable-assessments", action="store_true")
    return parser.parse_args()


# 목적: matrix의 단일 run용 argparse namespace를 만든다.
# 입력: base args와 strategy/scenario/seed.
# 출력: run args namespace.
def make_run_args(
    base_args: argparse.Namespace,
    strategy: str,
    scenario: str,
    semantic_level: str,
    seed: int,
    output_dir: Path,
    optimizer: Any = None,
    llm_seed: Any = None,
    optimizer_seed: Any = None,
) -> argparse.Namespace:
    """Create per-run arguments."""
    runtime = build_cell_runtime_isolation(output_dir)
    return argparse.Namespace(
        strategy=strategy,
        scenario=scenario,
        semantic_level=semantic_level,
        model_key=base_args.model_key,
        backend=base_args.backend,
        structured_output_transport=base_args.structured_output_transport,
        react_feedback_mode=base_args.react_feedback_mode,
        allow_mock=base_args.allow_mock,
        context_artifact=_context_artifact_for_scenario(base_args, scenario),
        compressed_manual_path=getattr(base_args, "compressed_manual_path", None),
        full_manual_path=getattr(base_args, "full_manual_path", None),
        run_optimizer=True,
        real_batpac=True,
        direct_eval=base_args.direct_eval,
        visible=base_args.visible,
        timeout_sec=base_args.timeout_sec,
        persistent_workbook_path=base_args.persistent_workbook_path,
        strict_real_batpac=base_args.strict_real_batpac,
        optimizer=None if strategy == "react" else (optimizer or base_args.optimizer),
        budget=base_args.budget,
        seed=seed,
        llm_seed=(
            int(llm_seed)
            if llm_seed is not None
            else int(seed)
        ),
        optimizer_seed=(
            int(optimizer_seed)
            if optimizer_seed is not None
            else int(seed)
        ),
        temperature=float(
            getattr(base_args, "temperature", 0.7)
        ),
        top_p=float(
            getattr(base_args, "top_p", 0.8)
        ),
        max_steps=base_args.max_steps,
        max_tokens=base_args.max_tokens,
        prompt_style=base_args.prompt_style,
        react_engine=base_args.react_engine,
        react_smoke_compact=base_args.react_smoke_compact,
        qwen_stop=base_args.qwen_stop,
        output_dir=str(output_dir),
        cell_runtime_isolation=runtime,
        model_config=base_args.model_config,
        model_path=None,
        quantization=getattr(base_args, "quantization", "none"),
        prompt_version=getattr(base_args, "prompt_version", "v0"),
        parse_retries=getattr(base_args, "parse_retries", 1),
        leakage_diagnostics=getattr(base_args, "leakage_diagnostics", False),
        target_grid_optimum=getattr(base_args, "target_grid_optimum", None),
        warm_start_count_mode=getattr(base_args, "warm_start_count_mode", "fixed"),
        fixed_warm_start_count=getattr(base_args, "fixed_warm_start_count", 3),
        minimum_warm_start_count=getattr(base_args, "minimum_warm_start_count", 1),
        maximum_warm_start_count=getattr(base_args, "maximum_warm_start_count", None),
        require_variable_assessments=getattr(base_args, "require_variable_assessments", False),
    )


def _context_artifact_for_scenario(base_args: argparse.Namespace, scenario: str) -> Any:
    template = getattr(base_args, "context_artifact_template", None)
    plain = getattr(base_args, "context_artifact", None)
    selected = template or plain
    if selected is None:
        return None
    text = str(selected)
    if "{scenario}" in text:
        return text.format(scenario=scenario)
    return text


# 목적: 성공 summary를 aggregate row로 변환한다.
# 입력: run metadata와 real BatPaC summary.
# 출력: aggregate CSV/JSON row.
def success_row(
    model_key: str,
    strategy: str,
    scenario: str,
    semantic_level: str,
    seed: int,
    summary: Mapping[str, Any],
    output_dir: Path,
    strategy_summary: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an aggregate row for a successful run."""
    ss = (
        strategy_summary
        if isinstance(strategy_summary, Mapping)
        else {}
    )

    llm = ss.get("llm_metrics")
    if not isinstance(llm, Mapping):
        llm = {}

    compute = ss.get("compute_metrics")
    if not isinstance(compute, Mapping):
        compute = {}

    generation = ss.get("generation_config")
    if not isinstance(generation, Mapping):
        generation = {}

    return {
        "model_key": model_key,
        "strategy": strategy,
        "optimizer": summary.get("optimizer"),
        "effective_optimizer": summary.get("effective_optimizer"),
        "scenario": scenario,
        "semantic_level": semantic_level,
        "seed": seed,
        "status": "success",
        "error_type": None,
        "error_message": None,
        "real_batpac": bool(summary.get("real_batpac")),
        "num_batpac_evaluations": summary.get(
            "num_batpac_evaluations", 0
        ),
        "unique_real_batpac_evaluations": summary.get(
            "unique_real_batpac_evaluations",
            summary.get("num_batpac_evaluations", 0),
        ),
        "selected_warm_start_count": summary.get(
            "selected_warm_start_count"
        ),
        "remaining_solver_budget": summary.get(
            "remaining_solver_budget"
        ),
        "feasible_count": summary.get("feasible_count", 0),
        "evaluations_to_first_feasible": summary.get(
            "evaluations_to_first_feasible"
        ),
        "failed_count": summary.get("failed_count", 0),
        "best_feasible_cost": summary.get("best_feasible_cost"),
        "direct_reuse_feasible": summary.get(
            "direct_reuse_feasible"
        ),
        "direct_reuse_cost": summary.get("direct_reuse_cost"),
        "improvement_over_direct_reuse": summary.get(
            "improvement_over_direct_reuse"
        ),
        "feasibility_recovered_from_direct_reuse": summary.get(
            "feasibility_recovered_from_direct_reuse"
        ),
        "mean_eval_time_sec": summary.get(
            "mean_eval_time_sec", 0.0
        ),
        "llm_seed": ss.get("llm_seed", seed),
        "optimizer_seed": ss.get(
            "optimizer_seed",
            summary.get("optimizer_seed"),
        ),
        "temperature": generation.get("temperature"),
        "top_p": generation.get("top_p"),
        "max_completion_tokens": generation.get(
            "max_completion_tokens"
        ),
        "parse_retries": generation.get("parse_retries"),
        "llm_http_request_count": llm.get(
            "recorded_http_request_count", 0
        ),
        "llm_prompt_tokens": llm.get("prompt_tokens", 0),
        "llm_completion_tokens": llm.get(
            "completion_tokens", 0
        ),
        "llm_total_tokens": llm.get("total_tokens", 0),
        "llm_request_latency_sec": llm.get(
            "request_latency_sec", 0.0
        ),
        "total_wall_clock_sec": compute.get(
            "total_wall_clock_sec"
        ),
        "numerical_optimizer_runtime_sec": compute.get(
            "numerical_optimizer_runtime_sec", 0.0
        ),
        "simulator_runtime_sec": compute.get(
            "simulator_runtime_sec", 0.0
        ),
        "optimizer_overhead_sec": compute.get(
            "optimizer_overhead_sec", 0.0
        ),
        "exact_flops": compute.get("exact_flops"),
        "output_dir": str(output_dir),
    }


# 목적: 실패 exception을 aggregate row로 변환한다.
# 입력: run metadata와 exception.
# 출력: aggregate CSV/JSON row.
def failure_row(
    model_key: str,
    strategy: str,
    scenario: str,
    semantic_level: str,
    seed: int,
    exc: BaseException,
    output_dir: Path,
) -> Dict[str, Any]:
    """Build an aggregate row for a failed run."""
    return {
        "model_key": model_key,
        "strategy": strategy,
        "optimizer": None,
        "effective_optimizer": None,
        "scenario": scenario,
        "semantic_level": semantic_level,
        "seed": seed,
        "status": "error",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "real_batpac": True,
        "num_batpac_evaluations": 0,
        "feasible_count": 0,
        "evaluations_to_first_feasible": None,
        "failed_count": 0,
        "best_feasible_cost": None,
        "direct_reuse_feasible": None,
        "direct_reuse_cost": None,
        "improvement_over_direct_reuse": None,
        "feasibility_recovered_from_direct_reuse": None,
        "mean_eval_time_sec": 0.0,
        "llm_seed": seed,
        "optimizer_seed": None,
        "temperature": None,
        "top_p": None,
        "max_completion_tokens": None,
        "parse_retries": None,
        "llm_http_request_count": 0,
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "llm_request_latency_sec": 0.0,
        "total_wall_clock_sec": None,
        "numerical_optimizer_runtime_sec": 0.0,
        "simulator_runtime_sec": 0.0,
        "optimizer_overhead_sec": 0.0,
        "exact_flops": None,
        "output_dir": str(output_dir),
    }


# 목적: aggregate matrix 결과를 JSON과 CSV로 저장한다.
# 입력: row list와 output directory.
# 출력: 저장된 path dict.
def write_aggregate(rows: Iterable[Mapping[str, Any]], output_dir: Path) -> Dict[str, Path]:
    """Write aggregate JSON and CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_list = [dict(row) for row in rows]
    json_path = output_dir / "aggregate_real_batpac_matrix.json"
    csv_path = output_dir / "aggregate_real_batpac_matrix.csv"
    json_path.write_text(json.dumps(rows_list, indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=AGGREGATE_COLUMNS)
        writer.writeheader()
        for row in rows_list:
            writer.writerow({column: row.get(column) for column in AGGREGATE_COLUMNS})
    return {"json": json_path, "csv": csv_path}


def _paired_seeds(
    args: argparse.Namespace,
) -> List[tuple[int, int]]:
    """Resolve independent paired LLM and optimizer seeds."""
    legacy = [
        int(x)
        for x in getattr(args, "seeds", [0])
    ]

    raw_llm = getattr(args, "llm_seeds", None)
    raw_opt = getattr(args, "optimizer_seeds", None)

    llm = (
        [int(x) for x in raw_llm]
        if raw_llm is not None
        else list(legacy)
    )

    opt = (
        [int(x) for x in raw_opt]
        if raw_opt is not None
        else list(legacy)
    )

    if not llm or not opt:
        raise ValueError("seed lists must not be empty")

    if len(llm) == 1 and len(opt) > 1:
        llm = llm * len(opt)

    if len(opt) == 1 and len(llm) > 1:
        opt = opt * len(llm)

    if len(llm) != len(opt):
        raise ValueError(
            "--llm-seeds and --optimizer-seeds must have equal "
            "lengths or one side must be a singleton"
        )

    return list(zip(llm, opt))


# 목적: strategy/scenario/seed matrix를 실패에 견고하게 실행한다.
# 입력: matrix CLI args.
# 출력: aggregate rows.
def run_matrix(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Run the real-BatPaC strategy matrix."""
    root = Path(args.output_dir)
    rows: List[Dict[str, Any]] = []
    semantic_levels = list(args.semantic_levels or [args.semantic_level])
    configured_optimizers = list(getattr(args, "optimizers", None) or [args.optimizer])
    for strategy in args.strategies:
        # ReAct performs direct tool search and is not crossed with a numerical
        # optimizer; repeating it once per optimizer would duplicate runs.
        strategy_optimizers = [None] if strategy == "react" else configured_optimizers
        for semantic_level in semantic_levels:
            for scenario in args.scenarios:
                for llm_seed, optimizer_seed in _paired_seeds(args):
                    seed = int(llm_seed)
                    for optimizer in strategy_optimizers:
                        optimizer_label = optimizer or "react_direct"
                        run_dir = root / (
                            f"{args.model_key}_{strategy}_{optimizer_label}_"
                            f"{semantic_level}_{scenario}_"
                            f"llmseed{llm_seed}_optseed{optimizer_seed}"
                        )
                        run_dir.mkdir(parents=True, exist_ok=True)
                        run_args = make_run_args(
                            args,
                            strategy,
                            scenario,
                            semantic_level,
                            seed,
                            run_dir,
                            optimizer=optimizer,
                            llm_seed=llm_seed,
                            optimizer_seed=optimizer_seed,
                        )
                        backend = None
                        recorder = TraceRecorder(run_dir)
                        started = time.perf_counter()
                        recorder.initialize_run(
                            {
                                "experiment_id": run_dir.name,
                                "scenario": scenario,
                                "model_name": args.model_key,
                                "quantization": getattr(args, "quantization", "none"),
                                "semantic_context_level": semantic_level,
                                "reoptimization_method": strategy,
                                "numerical_optimizer": optimizer_label,
                                "prompt_version": getattr(args, "prompt_version", "v0"),
                                "random_seed": seed,
                                "llm_seed": int(llm_seed),
                                "optimizer_seed": int(optimizer_seed),
                                "temperature": float(
                                    getattr(args, "temperature", 0.7)
                                ),
                                "top_p": float(
                                    getattr(args, "top_p", 0.8)
                                ),
                                "evaluation_budget": args.budget,
                                "context_artifact": run_args.context_artifact,
                                "structured_output_transport": run_args.structured_output_transport,
                                "react_feedback_mode": run_args.react_feedback_mode,
                                "cell_runtime_isolation": run_args.cell_runtime_isolation,
                                "configured_max_tokens": run_args.max_tokens,
                                "effective_max_tokens": (
                                    int(run_args.max_tokens) if run_args.max_tokens is not None else 1024
                                ),
                            }
                        )
                        try:
                            backend = make_backend(
                                args.backend,
                                strategy,
                                model_key=args.model_key,
                                model_config=args.model_config,
                                stop_sequences=args.qwen_stop,
                                allow_mock=bool(args.allow_mock),
                                structured_output_transport=args.structured_output_transport,
                                prompt_version=args.prompt_version,
                            )
                            setattr(
                                backend,
                                "seed",
                                int(run_args.llm_seed),
                            )
                            setattr(
                                backend,
                                "top_p",
                                float(run_args.top_p),
                            )
                            result = run_real_batpac_strategy(
                                run_args,
                                backend,
                                recorder,
                                run_dir,
                            )
                            strategy_summary = save_strategy_summary(
                                run_dir / "strategy_summary.json",
                                run_args,
                                result["payload"],
                                result["optimizer_summary"],
                            )

                            completed_wall_clock_sec = (
                                time.perf_counter() - started
                            )

                            strategy_summary[
                                "total_wall_clock_sec"
                            ] = completed_wall_clock_sec

                            strategy_summary[
                                "compute_metrics"
                            ][
                                "total_wall_clock_sec"
                            ] = completed_wall_clock_sec

                            save_summary_json(
                                run_dir / "strategy_summary.json",
                                strategy_summary,
                            )

                            rows.append(
                                success_row(
                                    args.model_key,
                                    strategy,
                                    scenario,
                                    semantic_level,
                                    seed,
                                    result["optimizer_summary"],
                                    run_dir,
                                    strategy_summary=strategy_summary,
                                )
                            )
                            recorder.finalize_run(
                                "success",
                                metadata={
                                    "total_wall_clock_sec": time.perf_counter() - started,
                                    "downstream_solver": result["optimizer_summary"].get("downstream_solver"),
                                    "total_evaluation_budget": args.budget,
                                    "selected_warm_start_count": result["optimizer_summary"].get("selected_warm_start_count"),
                                    "remaining_solver_budget": result["optimizer_summary"].get("remaining_solver_budget"),
                                    "unique_real_batpac_evaluations": result["optimizer_summary"].get("unique_real_batpac_evaluations"),
                                },
                            )
                        except Exception as exc:  # pragma: no cover - external smoke boundary
                            clean_error = is_clean_generation_failure(exc)
                            error_summary = {
                                "status": "error",
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
                                "clean_error": clean_error,
                                "optimizer": optimizer_label,
                                "backend_debug": backend_debug(backend),
                            }
                            if not clean_error:
                                error_summary["traceback"] = traceback.format_exc()
                            save_summary_json(run_dir / "error_summary.json", error_summary)
                            row = failure_row(
                                args.model_key, strategy, scenario, semantic_level, seed, exc, run_dir
                            )
                            row["optimizer"] = optimizer
                            row["effective_optimizer"] = optimizer_label
                            row["llm_seed"] = int(llm_seed)
                            row["optimizer_seed"] = int(optimizer_seed)
                            row["temperature"] = float(
                                getattr(args, "temperature", 0.7)
                            )
                            row["top_p"] = float(
                                getattr(args, "top_p", 0.8)
                            )
                            row["max_completion_tokens"] = (
                                int(run_args.max_tokens)
                                if run_args.max_tokens is not None
                                else 1024
                            )
                            row["parse_retries"] = int(
                                getattr(args, "parse_retries", 1)
                            )
                            rows.append(row)
                            recorder.finalize_run(
                                "failed",
                                failure_reason=f"{type(exc).__name__}: {exc}",
                                metadata={"total_wall_clock_sec": time.perf_counter() - started},
                            )
                            print(
                                f"ERROR {strategy} {optimizer_label} {semantic_level} "
                                f"{scenario} seed={seed}: {type(exc).__name__}: {exc}"
                            )
                        finally:
                            if backend is not None and hasattr(backend, "close"):
                                backend.close()
    return rows


# 목적: LLM generation 실패 중 실험 결과 row로 충분히 설명 가능한 오류를 구분한다.
# 입력: exception.
# 출력: clean error 여부.
def is_clean_generation_failure(exc: BaseException) -> bool:
    """Return true for expected LLM generation failures."""
    return isinstance(exc, (QwenLengthMissingContentError, TimeoutError, socket.timeout))


# 목적: trace 기록 전에 실패한 LLM backend 상태를 error summary에 보존한다.
# 입력: backend 객체 또는 None.
# 출력: JSON-compatible debug dict.
def backend_debug(backend: Any) -> Dict[str, Any]:
    """Return useful backend debug fields for failed runs."""
    if backend is None:
        return {}
    fields = (
        "last_request_model",
        "last_base_url",
        "last_latency_sec",
        "last_finish_reason",
        "last_message_content",
        "last_message_reasoning",
        "last_raw_response_text",
        "last_attempts",
    )
    return {field: getattr(backend, field) for field in fields if hasattr(backend, field)}


# 목적: script entry point를 실행한다.
# 입력: command line arguments.
# 출력: aggregate artifact paths.
def matrix_exit_code(rows: Iterable[Mapping[str, Any]], strict: bool = False) -> int:
    """Return nonzero whenever any requested matrix row failed."""
    rows_list = list(rows)
    failed = sum(1 for row in rows_list if row.get("status") != "success")
    return 1 if failed else 0


def main() -> int:
    """Run the matrix."""
    args = parse_args()
    rows = run_matrix(args)
    paths = write_aggregate(rows, Path(args.output_dir))
    succeeded = sum(1 for row in rows if row.get("status") == "success")
    failed = len(rows) - succeeded
    print(f"matrix final counts: requested={len(rows)} succeeded={succeeded} failed={failed}")
    print(f"aggregate json: {paths['json']}")
    print(f"aggregate csv: {paths['csv']}")
    return matrix_exit_code(rows, strict=bool(args.strict_real_batpac))


if __name__ == "__main__":
    raise SystemExit(main())
