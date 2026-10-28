"""Run a local smoke test for method-aligned LLM strategies."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import statistics
import traceback
import hashlib
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from llm_methods.backends.base import LLMBackend
from llm_methods.backends.local_transformers import LocalTransformersBackend
from llm_methods.backends.qwen_server import QwenServerBackend
from llm_methods.backends.static import StaticBackend
from llm_methods.protocols.transport_schemas import strategy_transport_schemas
from llm_methods.runtime_isolation import build_cell_runtime_isolation, runtime_environment
from llm_methods.context import SEMANTIC_LEVELS, build_smoke_context, load_context_artifact, with_semantic_information
from llm_methods.semantic_lookup import augment_context_with_manual_lookup
from llm_methods.model_registry import (
    DEFAULT_QWEN35_GGUF_MODEL_CONFIG,
    DEFAULT_QWEN35_MLX_MODEL_CONFIG,
    DEFAULT_QWEN35_MODEL_CONFIG,
    resolve_serving_model_from_configs,
)
from llm_methods.strategies.hybrid_policy import HybridPolicyStrategy
from llm_methods.strategies.qwen_agent_react import QwenAgentReActSearchStrategy
from llm_methods.strategies.react_search import ReActSearchStrategy
from llm_methods.strategies.variable_fixing import VariableFixingStrategy
from llm_methods.strategies.warm_start import WarmStartStrategy
from llm_methods.traces.recorder import TraceRecorder
from llm_methods.leakage import diagnose_source_history_leakage
from llm_methods.runtime import (
    OPTIMIZER_CHOICES,
    fake_evaluator,
    finite_discrete_optimizer_plan,
    jsonable,
    make_optimizer_with_options,
    optimize_with_design_variable_bounds,
    make_real_batpac_evaluator,
    save_records_jsonl,
    save_summary_json,
    summarize_optimizer_result,
)
from optimizers.core.base import EvaluationRecord
from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.core.domain import (
    candidate_to_batpac_inputs, make_condition, repair_candidate,
    use_design_variable_bounds,
)
from optimizers.core.evaluation import is_feasible, normalized_constraint_violation, penalized_loss

DEFAULT_REAL_BATPAC_BUDGET = 3
RUNTIME_WORKBOOK_DIR = Path("batpac_runtime")

STRATEGY_CHOICES = ("baseline", "source_warm_baseline", "warm_start", "variable_fixing", "react", "hybrid")
SCENARIO_CHOICES = ('s_cost', 's_space', 's_vehicle_energy', 's_energy130_relaxed_pack')
SEMANTIC_LEVEL_CHOICES = SEMANTIC_LEVELS
MODEL_KEY_CHOICES = (
    "qwen35_4b",
    "qwen35_9b",
    "qwen35_27b",
    "qwen35_4b_mlx4",
    "qwen35_9b_mlx4",
    "qwen35_27b_mlx4",
)
BACKEND_CHOICES = ("static", "qwen_server", "local_transformers", "hf_local")
PROMPT_STYLE_CHOICES = ("standard", "compact_json")
REACT_ENGINE_CHOICES = ("qwen_agent", "custom", "native")
STRUCTURED_OUTPUT_TRANSPORT_CHOICES = ("prompt_only", "json_schema")


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=STRATEGY_CHOICES, required=True)
    parser.add_argument("--scenario", choices=SCENARIO_CHOICES, default="s_cost")
    parser.add_argument("--semantic-level", choices=SEMANTIC_LEVEL_CHOICES, default="minimal")
    parser.add_argument("--model-key", choices=MODEL_KEY_CHOICES, default="qwen35_4b")
    parser.add_argument("--backend", choices=BACKEND_CHOICES, default="qwen_server")
    parser.add_argument(
        "--structured-output-transport",
        choices=STRUCTURED_OUTPUT_TRANSPORT_CHOICES,
        default="prompt_only",
    )
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--context-artifact", default=None)
    parser.add_argument("--compressed-manual-path", default=None)
    parser.add_argument("--full-manual-path", default=None)
    parser.add_argument("--manual-lookup-max-calls", type=int, default=3)
    parser.add_argument("--manual-lookup-top-k", type=int, default=3)
    parser.add_argument("--manual-lookup-max-return-tokens", type=int, default=1800)
    parser.add_argument("--manual-lookup-completion-tokens", type=int, default=2048)
    parser.add_argument("--run-optimizer", action="store_true")
    parser.add_argument("--real-batpac", action="store_true")
    parser.add_argument("--direct-eval", action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--strict-real-batpac", action="store_true")
    parser.add_argument("--optimizer", choices=OPTIMIZER_CHOICES, default="pattern")
    parser.add_argument("--budget", type=int, default=DEFAULT_REAL_BATPAC_BUDGET)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--llm-seed", type=int, default=None)
    parser.add_argument("--optimizer-seed", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--model-context-limit", type=int, default=16384)
    parser.add_argument("--prompt-style", choices=PROMPT_STYLE_CHOICES, default="standard")
    parser.add_argument("--react-engine", choices=REACT_ENGINE_CHOICES, default="qwen_agent")
    parser.add_argument("--react-smoke-compact", action="store_true")
    parser.add_argument("--qwen-stop", nargs="*", default=None)
    parser.add_argument("--output-dir", default="outputs_mcp/llm_strategy_smoke")
    parser.add_argument("--model-config", default=str(DEFAULT_QWEN35_MODEL_CONFIG))
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--quantization", default="none")
    parser.add_argument(
        "--prompt-version",
        choices=(
            "v0",
            "v2_canonical_react_vf",
            "v3_source_reference_conservative_react",
            "v3_2_semantic_lookup",
            "v3_3_source_reference_only",
            "v3_3_5_environment_observation_react",
        ),
        default="v0",
    )
    parser.add_argument(
        "--react-protocol-demonstration",
        choices=("none", "domain_neutral"),
        default="none",
    )
    parser.add_argument("--parse-retries", type=int, default=1)
    parser.add_argument(
        "--warm-start-count-mode",
        choices=("fixed", "model_selected"),
        default="fixed",
    )
    parser.add_argument("--fixed-warm-start-count", type=int, default=3)
    parser.add_argument("--minimum-warm-start-count", type=int, default=1)
    parser.add_argument("--maximum-warm-start-count", type=int, default=None)
    parser.add_argument("--require-variable-assessments", action="store_true")
    parser.add_argument("--leakage-diagnostics", action="store_true")
    parser.add_argument("--target-grid-optimum", default=None)
    return parser.parse_args()


def _optimizer_seed(args: argparse.Namespace) -> int:
    """Resolve numerical-optimizer seed independently from LLM generation."""
    explicit = getattr(args, "optimizer_seed", None)
    if explicit is not None:
        return int(explicit)
    return int(getattr(args, "seed", 0))


# 목적: smoke script 설정에 맞는 LLM backend를 만든다.
# 입력: backend 이름과 strategy 이름.
# 출력: LLMBackend instance.
def make_backend(
    name: str,
    strategy: str,
    model_key: str = "qwen35_4b",
    model_config: str = str(DEFAULT_QWEN35_MODEL_CONFIG),
    stop_sequences: Optional[List[str]] = None,
    model_path: Optional[str] = None,
    allow_mock: bool = False,
    structured_output_transport: str = "prompt_only",
    prompt_version: str = "v3_3_5_environment_observation_react",
) -> LLMBackend:
    """Create a backend for the smoke script."""
    if name == "static":
        if not allow_mock:
            raise RuntimeError("--allow-mock is required to use the static backend.")
        return StaticBackend(_static_response(strategy))
    if name == "qwen_server":
        serving_model = os.environ.get("LLM_MODEL") or resolve_serving_model_from_configs(
            model_key,
            [
                Path(model_config),
                DEFAULT_QWEN35_MLX_MODEL_CONFIG,
                DEFAULT_QWEN35_MODEL_CONFIG,
                DEFAULT_QWEN35_GGUF_MODEL_CONFIG,
            ],
        )
        return QwenServerBackend(
            model=serving_model,
            stop_sequences=stop_sequences,
            fixed_completion_budget=True,
            response_json_schemas=(
                strategy_transport_schemas(prompt_version)
                if structured_output_transport == "json_schema"
                else None
            ),
        )
    if name in {"local_transformers", "hf_local"}:
        return LocalTransformersBackend(
            model_path=model_path,
            model_key=model_key,
            model_config=model_config,
        )
    raise ValueError(f"Unknown backend: {name}")


# 목적: strategy별 deterministic static 응답을 제공한다.
# 입력: strategy 이름.
# 출력: StaticBackend response payload.
def _static_response(strategy: str) -> Any:
    if strategy in {"baseline", "source_warm_baseline"}:
        return {"BaselineOutput": {"method": "normal_optimization"}}
    if strategy == "warm_start":
        return {
            "WarmStartOutput": {
                "candidates": [
                    _static_warm_candidate("LFP", 120.0, 20, 4, 4, "Static smoke candidate."),
                    _static_warm_candidate("NMC811", 90.0, 24, 8, 4, "Static smoke candidate."),
                ]
            }
        }
    if strategy == "variable_fixing":
        return {
            "VariableFixingOutput": {
                "fixed_variables": {"x_chem": "LFP"},
                "free_variables": ["x_T", "x_S", "x_MPR", "x_R"],
                "rationale": "Static smoke fixes chemistry only.",
                "warnings": [],
            }
        }
    if strategy == "hybrid":
        return {
            "HybridPolicyOutput": {
                "selected_strategy": "warm_start",
                "rationale": "Static smoke selects warm_start.",
                "strategy_args": {"k": 3},
                "optimizer_recommendation": "pattern",
            },
            "WarmStartOutput": {
                "candidates": [
                    _static_warm_candidate("LFP", 120.0, 20, 4, 4, "Hybrid static candidate."),
                    _static_warm_candidate("NMC811", 90.0, 24, 8, 4, "Hybrid static candidate."),
                    _static_warm_candidate("LFP", 150.0, 24, 6, 3, "Hybrid static candidate."),
                ]
            }
        }
    if strategy == "react":
        candidate = {"x_chem": "NMC811", "x_T": 100.0, "x_S": 20, "x_MPR": 4, "x_R": 4}
        return [
            {
                "action": "evaluate_candidate",
                "candidate": candidate,
                "observations_used": [],
                "rationale": "Evaluate the source-like baseline first.",
                "variables_changed": list(candidate),
                "expected_effect": "Test the source-like baseline.",
                "uncertainty": "The changed-condition result is unknown.",
            },
            {
                "action": "finish",
                "final_candidate": candidate,
                "observations_used": [
                    {"iteration": 0, "fields": ["feasible", "cost"]}
                ],
                "rationale": "Static smoke stops after one observation.",
                "variables_changed": [],
                "expected_effect": "Retain the observed candidate.",
                "uncertainty": "The conclusion is limited to one evaluation.",
            },
        ]
    raise ValueError(f"Unknown strategy: {strategy}")


def _static_warm_candidate(
    chemistry: str, thickness: float, series: int, mpr: int, rows: int, rationale: str
) -> Dict[str, Any]:
    return {
        "x_chem": chemistry,
        "x_T": thickness,
        "x_S": series,
        "x_MPR": mpr,
        "x_R": rows,
        "rationale": rationale,
        "evidence_references": ["static_smoke_fixture"],
        "search_role": "deterministic smoke initialization",
        "expected_effects": ["exercise ordered optimizer initialization"],
        "tradeoffs": ["not intended as a performance claim"],
        "uncertainty": ["simulator result is unknown before evaluation"],
    }


# 목적: method-section factor가 담긴 redesign context를 만든다.
# 입력: scenario와 semantic level.
# 출력: context dict.
def build_context(
    scenario: str,
    semantic_level: str,
    context_artifact_path: Optional[str] = None,
    semantic_artifact_dir: Optional[Path] = None,
    compressed_manual_path: Optional[str] = None,
    full_manual_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a redesign context."""
    if context_artifact_path:
        context = load_context_artifact(context_artifact_path)
    else:
        context = build_smoke_context(scenario)
    changed = context.get("changed_condition", {})
    if isinstance(changed, Mapping) and changed.get("name") != scenario:
        raise ValueError(
            f"Context artifact changed_condition.name={changed.get('name')!r} does not match scenario={scenario!r}"
        )
    return with_semantic_information(
        context,
        semantic_level,
        artifact_dir=semantic_artifact_dir,
        compressed_manual_path=compressed_manual_path,
        full_manual_path=full_manual_path,
    )


def _augment_level3_context(
    context: Mapping[str, Any],
    args: argparse.Namespace,
    backend: LLMBackend,
    recorder: TraceRecorder,
    run_id: str,
    strategy: str,
) -> Dict[str, Any]:
    """Run the bounded manual-lookup phase only for semantic level 3."""
    return augment_context_with_manual_lookup(
        context=context,
        semantic_level=str(args.semantic_level),
        backend=backend,
        strategy=strategy,
        model_key=str(args.model_key),
        trace_recorder=recorder,
        run_id=run_id,
        temperature=0.0,
        max_tokens=int(getattr(args, "manual_lookup_completion_tokens", 2048)),
        strategy_completion_tokens=_strategy_max_tokens(args),
        max_lookups=int(getattr(args, "manual_lookup_max_calls", 3)),
        top_k=int(getattr(args, "manual_lookup_top_k", 3)),
        max_return_tokens=int(
            getattr(args, "manual_lookup_max_return_tokens", 1800)
        ),
    )


# 목적: 선택한 LLM strategy를 실행한다.
# 입력: args, backend, trace recorder.
# 출력: strategy output과 optimizer input metadata.
def run_strategy(args: argparse.Namespace, backend: LLMBackend, recorder: TraceRecorder) -> Dict[str, Any]:
    """Run one strategy."""
    context = build_context(
        args.scenario,
        args.semantic_level,
        context_artifact_path=getattr(args, "context_artifact", None),
        semantic_artifact_dir=recorder.output_dir / "semantic_artifacts",
        compressed_manual_path=getattr(args, "compressed_manual_path", None),
        full_manual_path=getattr(args, "full_manual_path", None),
    )
    run_id = f"{args.strategy}_{args.scenario}_{args.model_key}"
    context = _augment_level3_context(
        context, args, backend, recorder, run_id, str(args.strategy)
    )
    max_tokens = _strategy_max_tokens(args)
    if args.strategy == "baseline":
        recorder.record_step(
            run_id=run_id,
            scenario=args.scenario,
            strategy="baseline",
            optimizer=getattr(args, "optimizer", None),
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            iteration=0,
            messages=[],
            raw_response=None,
            parsed_output={"method": "normal_optimization", "llm_used": False},
            event_type="baseline_configuration",
        )
        return {
            "context": context,
            "output": {"method": "normal_optimization"},
            "initial_candidates": None,
            "fixed_variables": None,
        }
    if args.strategy == "source_warm_baseline":
        source_candidate = context.get("previous_best_design")
        if not isinstance(source_candidate, Mapping):
            raise ValueError(
                "source_warm_baseline requires context.previous_best_design"
            )
        recorder.record_step(
            run_id=run_id,
            scenario=args.scenario,
            strategy="source_warm_baseline",
            optimizer=getattr(args, "optimizer", None),
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            iteration=0,
            messages=[],
            raw_response=None,
            parsed_output={
                "method": "source_incumbent_numerical_warm_start",
                "llm_used": False,
                "source_candidate": dict(source_candidate),
            },
            event_type="baseline_configuration",
        )
        return {
            "context": context,
            "output": {
                "method": "source_incumbent_numerical_warm_start",
                "llm_used": False,
            },
            "initial_candidates": [dict(source_candidate)],
            "fixed_variables": None,
        }

    if args.strategy == "warm_start":
        count_mode = getattr(args, "warm_start_count_mode", "fixed")
        fixed_count = min(
            int(getattr(args, "fixed_warm_start_count", 3)),
            max(1, int(args.budget) - 1),
        )
        maximum_count = getattr(args, "maximum_warm_start_count", None)
        if maximum_count is None:
            maximum_count = min(5, int(args.budget) - 1)
        output = WarmStartStrategy(
            backend=backend,
            temperature=float(getattr(args, "temperature", 0.0)),
            max_tokens=max_tokens,
            parse_retries=getattr(args, "parse_retries", 1),
        ).run(
            context,
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            k=fixed_count,
            optimizer_name=getattr(args, "optimizer", "unspecified"),
            total_budget=int(args.budget),
            minimum_count=int(getattr(args, "minimum_warm_start_count", 1)),
            maximum_count=int(maximum_count),
            model_selected_count=count_mode == "model_selected",
            trace_recorder=recorder,
            run_id=run_id,
        )
        return {"context": context, "output": output, "initial_candidates": output.candidates, "fixed_variables": None}
    if args.strategy == "variable_fixing":
        output = VariableFixingStrategy(
            backend=backend, temperature=float(getattr(args, "temperature", 0.0)), max_tokens=max_tokens, parse_retries=getattr(args, "parse_retries", 1)
        ).run(
            context,
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            trace_recorder=recorder,
            run_id=run_id,
            optimizer_name=getattr(args, "optimizer", "unspecified"),
            total_budget=int(args.budget),
            require_assessments=bool(getattr(args, "require_variable_assessments", False)),
        )
        return {"context": context, "output": output, "initial_candidates": None, "fixed_variables": output.fixed_variables}
    if args.strategy == "hybrid":
        output = HybridPolicyStrategy(
            backend=backend,
            temperature=float(getattr(args, "temperature", 0.0)),
            max_tokens=max_tokens,
        ).run(
            context,
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            trace_recorder=recorder,
            run_id=run_id,
        )
        return {"context": context, "output": output, "initial_candidates": None, "fixed_variables": None}
    if args.strategy == "react":
        output = ReActSearchStrategy(
            backend=backend,
            evaluator=fake_evaluator,
            max_steps=args.max_steps,
            temperature=float(getattr(args, "temperature", 0.0)),
            max_tokens=max_tokens,
            prompt_style=_react_prompt_style(args),
            parse_retries=getattr(args, "parse_retries", 2),
            protocol_version=getattr(args, "prompt_version", "v0"),
            include_protocol_demonstration=(
                getattr(args, "react_protocol_demonstration", "none")
                == "domain_neutral"
            ),
            evaluation_budget=int(getattr(args, "budget", 20)),
        ).run(
            context,
            semantic_level=args.semantic_level,
            model_key=args.model_key,
            trace_recorder=recorder,
            run_id=run_id,
        )
        return {"context": context, "output": output, "initial_candidates": None, "fixed_variables": None}
    raise ValueError(f"Unknown strategy: {args.strategy}")


# 목적: backend별 기본 generation token budget을 결정한다.
# 입력: argparse namespace.
# 출력: max_tokens 정수.
def _strategy_max_tokens(args: argparse.Namespace) -> int:
    explicit = getattr(args, "max_tokens", None)
    if explicit is not None:
        return int(explicit)
    return 1024


# 목적: real BatPaC ReAct smoke에서는 compact JSON prompt를 기본으로 강제한다.
# 입력: argparse namespace.
# 출력: ReAct prompt style 문자열.
def _react_prompt_style(args: argparse.Namespace) -> str:
    if bool(getattr(args, "react_smoke_compact", False)):
        return "compact_json"
    return str(getattr(args, "prompt_style", "standard"))


# 목적: 선택적으로 fake optimizer 또는 real BatPaC warm-start 평가를 실행한다.
# 입력: args와 strategy payload, output directory.
# 출력: optimizer/evaluator summary 또는 None.
def maybe_run_optimizer(args: argparse.Namespace, payload: Mapping[str, Any], output_dir: Path) -> Optional[Dict[str, Any]]:
    """Optionally run a smoke evaluation."""
    if getattr(args, "real_batpac", False):
        raise RuntimeError("--real-batpac is handled by run_real_batpac_strategy")
    if not args.run_optimizer:
        return None
    if not getattr(args, "allow_mock", False):
        raise RuntimeError("--allow-mock is required to run mock/static optimizer evaluations.")
    if args.strategy == "react":
        return summarize_mock_react(args, payload, output_dir)
    if args.strategy == "hybrid":
        raise RuntimeError(
            "Mock hybrid execution must dispatch its selected child strategy explicitly; "
            "it cannot be represented by an unrelated numerical optimizer run."
        )
    optimizer = make_optimizer_with_options(
        args.optimizer,
        fake_evaluator,
        axbo_surrogate_slices_path=_axbo_surrogate_slices_path(args.optimizer, output_dir),
    )
    started = time.perf_counter()
    result = optimize_with_design_variable_bounds(
        optimizer,
        (payload.get("context") or {}).get("variable_bounds"),
        condition_name=args.scenario,
        budget=args.budget,
        seed=_optimizer_seed(args),
        initial_candidates=payload.get("initial_candidates"),
        fixed_variables=payload.get("fixed_variables"),
    )
    total_runtime_sec = time.perf_counter() - started
    summary = summarize_optimizer_result(
        optimizer_name=args.optimizer,
        semantic_strategy=f"llm_methods_{args.strategy}",
        condition_name=args.scenario,
        budget=args.budget,
        seed=_optimizer_seed(args),
        k=len(payload.get("initial_candidates") or []),
        generated_warm_start_candidates=payload.get("initial_candidates") or [],
        fixed_variables=payload.get("fixed_variables") or {},
        result=result,
        total_runtime_sec=total_runtime_sec,
    )
    records_path = output_dir / "optimizer_records.jsonl"
    summary_path = output_dir / "optimizer_summary.json"
    save_records_jsonl(records_path, result.records)
    save_summary_json(summary_path, summary)
    return summary


def summarize_mock_react(
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    output_dir: Path,
) -> Dict[str, Any]:
    """Summarize the evaluations already performed inside a mock ReAct loop."""
    output = payload["output"]
    condition = make_condition(args.scenario)
    records = react_evaluations_to_records(
        list(output.evaluations), condition, (payload.get("context") or {}).get("variable_bounds")
    )
    feasible_records = [record for record in records if record.feasible]
    best_record = _best_feasible_record(feasible_records)
    summary = {
        "run_optimizer": True,
        "real_batpac": False,
        "strategy": "react",
        "optimizer": None,
        "effective_optimizer": "react_direct",
        "scenario": args.scenario,
        "semantic_level": getattr(args, "semantic_level", None),
        "model_key": getattr(args, "model_key", None),
        "backend": getattr(args, "backend", None),
        "react_engine": _react_engine(args),
        "seed": getattr(args, "seed", None),
        "optimizer_seed": _optimizer_seed(args),
        "budget": args.budget,
        "num_batpac_evaluations": len(records),
        "evaluations": len(records),
        "feasible_count": len(feasible_records),
        "failed_count": sum(1 for record in records if not record.result.get("success", False)),
        "best_feasible_candidate": copy.deepcopy(best_record.candidate) if best_record else None,
        "best_feasible_cost": _finite_cost(best_record.result) if best_record else None,
        "actions": [_react_action_summary(action) for action in output.actions],
        "stopped": bool(output.stopped),
        "termination_reason": output.termination_reason,
    }
    save_records_jsonl(output_dir / "optimizer_records.jsonl", records)
    save_summary_json(output_dir / "optimizer_summary.json", summary)
    return summary


# 목적: AxBO 실행일 때만 surrogate slice artifact 경로를 제공한다.
# 입력: optimizer 이름과 run output directory.
# 출력: axbo_surrogate_slices.jsonl path 또는 None.
def _axbo_surrogate_slices_path(optimizer_name: str, output_dir: Path) -> Optional[Path]:
    """Return the AxBO surrogate slice artifact path for a run."""
    if optimizer_name != "axbo":
        return None
    return output_dir / "axbo_surrogate_slices.jsonl"


# 목적: --real-batpac 실행에서 strategy별 실제 BatPaC 평가 경로를 선택한다.
# 입력: args, backend, trace recorder, output directory, 선택적 evaluator override.
# 출력: strategy payload와 real BatPaC summary.
def run_real_batpac_strategy(
    args: argparse.Namespace,
    backend: LLMBackend,
    recorder: TraceRecorder,
    output_dir: Path,
    evaluator: Optional[Any] = None,
    evaluator_mode: Optional[str] = None,
    preflight: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run an LLM strategy through real BatPaC evaluation only."""
    enforce_runner_safety(args)
    evaluator, evaluator_mode, preflight = prepare_real_batpac_evaluator(
        args,
        evaluator=evaluator,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
    )
    leakage_report = None
    if bool(getattr(args, "leakage_diagnostics", False)):
        diagnostic_context = build_context(
            args.scenario,
            args.semantic_level,
            context_artifact_path=getattr(args, "context_artifact", None),
            semantic_artifact_dir=output_dir / "semantic_artifacts",
            compressed_manual_path=getattr(args, "compressed_manual_path", None),
            full_manual_path=getattr(args, "full_manual_path", None),
        )
        grid_path = getattr(args, "target_grid_optimum", None)
        grid_optimum = (
            json.loads(Path(grid_path).read_text(encoding="utf-8")) if grid_path else None
        )
        leakage_report = diagnose_source_history_leakage(
            diagnostic_context, evaluator, grid_optimum
        )
        save_summary_json(output_dir / "source_history_leakage.json", leakage_report)
    if args.strategy == "warm_start":
        payload = run_strategy(args, backend, recorder)
        summary = evaluate_warm_start_with_batpac(
            args,
            payload,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    elif args.strategy == "baseline":
        payload = run_strategy(args, backend, recorder)
        summary = evaluate_baseline_with_batpac(
            args,
            payload,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    elif args.strategy == "source_warm_baseline":
        payload = run_strategy(args, backend, recorder)
        summary = evaluate_source_warm_baseline_with_batpac(
            args,
            payload,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    elif args.strategy == "variable_fixing":
        payload = run_strategy(args, backend, recorder)
        summary = evaluate_variable_fixing_with_batpac(
            args,
            payload,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    elif args.strategy == "react":
        payload, summary = evaluate_react_with_batpac(
            args,
            backend,
            recorder,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    elif args.strategy == "hybrid":
        payload, summary = evaluate_hybrid_with_batpac(
            args,
            backend,
            recorder,
            output_dir,
            evaluator=evaluator,
            evaluator_mode=evaluator_mode,
            preflight=preflight,
        )
    else:
        raise ValueError(f"Unknown strategy: {args.strategy}")

    apply_real_batpac_guard(summary, strict=bool(getattr(args, "strict_real_batpac", False)))
    if leakage_report is not None:
        summary["source_history_leakage"] = {
            key: leakage_report.get(key)
            for key in (
                "history_policy",
                "source_history_points",
                "target_feasible_count",
                "target_feasible_fraction",
                "source_trajectory_target_best",
                "near_target_grid_matches",
                "leakage_warning",
                "warnings",
            )
        }
        summary["leakage_diagnostic_evaluations_outside_method_budget"] = len(
            leakage_report.get("evaluations", [])
        )
        save_summary_json(output_dir / "real_batpac_summary.json", summary)
    return {"payload": payload, "optimizer_summary": summary}


def evaluate_baseline_with_batpac(
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    output_dir: Path,
    evaluator: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Dict[str, Any]:
    """Run the numerical optimizer without LLM initialization or fixing."""
    optimizer = make_optimizer_with_options(
        args.optimizer,
        evaluator,
        axbo_surrogate_slices_path=_axbo_surrogate_slices_path(args.optimizer, output_dir),
    )
    started = time.perf_counter()
    result = optimize_with_design_variable_bounds(
        optimizer,
        (payload.get("context") or {}).get("variable_bounds"),
        condition_name=args.scenario,
        budget=int(args.budget),
        seed=_optimizer_seed(args),
        initial_candidates=None,
        fixed_variables=None,
    )
    elapsed = time.perf_counter() - started
    save_records_jsonl(output_dir / "real_batpac_records.jsonl", result.records)
    summary = summarize_real_optimizer_records(
        args=args,
        records=result.records,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
        total_runtime_sec=elapsed,
        variable_bounds=(payload.get("context") or {}).get("variable_bounds"),
        strategy_metadata={"llm_used": False},
    )
    add_direct_reuse_metrics(summary, payload.get("context"))
    save_summary_json(output_dir / "real_batpac_summary.json", summary)
    return summary


def evaluate_source_warm_baseline_with_batpac(
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    output_dir: Path,
    evaluator: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Dict[str, Any]:
    """Run the optimizer initialized only with the source incumbent."""
    initial = [
        dict(candidate)
        for candidate in payload.get("initial_candidates") or []
    ]

    if len(initial) != 1:
        raise ValueError(
            "source_warm_baseline requires exactly one source incumbent"
        )

    source_candidate = initial[0]

    evaluator, unique_counter = _cached_counting_evaluator(
        evaluator
    )

    optimizer = make_optimizer_with_options(
        args.optimizer,
        evaluator,
        axbo_surrogate_slices_path=_axbo_surrogate_slices_path(
            args.optimizer,
            output_dir,
        ),
    )

    started = time.perf_counter()

    result = optimize_with_design_variable_bounds(
        optimizer,
        (payload.get("context") or {}).get("variable_bounds"),
        condition_name=args.scenario,
        budget=int(args.budget),
        seed=_optimizer_seed(args),
        initial_candidates=[dict(source_candidate)],
        fixed_variables=None,
    )

    elapsed = time.perf_counter() - started
    records = list(result.records)

    if len(records) > int(args.budget):
        raise AssertionError(
            "source_warm_baseline exceeded total evaluation budget"
        )

    if not any(
        dict(record.candidate) == source_candidate
        for record in records
    ):
        raise AssertionError(
            "source incumbent was not evaluated"
        )

    save_records_jsonl(
        output_dir / "real_batpac_records.jsonl",
        records,
    )

    summary = summarize_real_optimizer_records(
        args=args,
        records=records,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
        total_runtime_sec=elapsed,
        variable_bounds=(payload.get("context") or {}).get(
            "variable_bounds"
        ),
        strategy_metadata={
            "llm_used": False,
            "baseline_type": "source_incumbent_only",
            "source_initial_candidate": dict(source_candidate),
            "num_generated_candidates": 0,
            "selected_warm_start_count": 1,
            "remaining_solver_budget": max(
                0,
                int(args.budget) - 1,
            ),
            "total_evaluation_budget": int(args.budget),
            "unique_real_batpac_evaluations": unique_counter["calls"],
        },
    )

    add_direct_reuse_metrics(
        summary,
        payload.get("context"),
    )

    save_summary_json(
        output_dir / "real_batpac_summary.json",
        summary,
    )

    return summary


# 목적: CLI 옵션에서 real BatPaC evaluator와 workbook preflight 정보를 준비한다.
# 입력: args와 선택적 test evaluator override.
# 출력: evaluator, mode, preflight tuple.
def prepare_real_batpac_evaluator(
    args: argparse.Namespace,
    evaluator: Optional[Any] = None,
    evaluator_mode: Optional[str] = None,
    preflight: Optional[Mapping[str, Any]] = None,
) -> Tuple[Any, str, Mapping[str, Any]]:
    """Prepare a real BatPaC evaluator."""
    runtime = getattr(args, "cell_runtime_isolation", None)
    if runtime is None:
        runtime = build_cell_runtime_isolation(Path(args.output_dir))
        args.cell_runtime_isolation = runtime
    if preflight is None:
        persistent_workbook_path = str(Path(runtime["persistent_workbook_path"]).resolve())
        preflight = preflight_runtime_workbook(persistent_workbook_path)
    if evaluator is not None:
        return evaluator, evaluator_mode or "mocked_real", preflight

    print(f"BatPaC runtime workbook: {preflight['runtime_workbook_path']}")
    print(f"BatPaC runtime workbook exists: {preflight['runtime_workbook_exists']}")
    evaluator, mode = make_real_batpac_evaluator(
        real_batpac=True,
        direct_eval=bool(args.direct_eval),
        timeout_sec=float(args.timeout_sec),
        persistent_workbook_path=str(preflight["persistent_workbook_path"]),
        visible=bool(args.visible),
        runtime_environment=runtime_environment(runtime),
        output_dir=str(Path(runtime["batpac_output_dir"]).resolve()),
    )
    return evaluator, mode, preflight


# 목적: Qwen warm-start 후보만 real BatPaC evaluator로 직접 평가한다.
# 입력: CLI args, strategy payload, output directory, 선택적 evaluator override.
# 출력: real BatPaC smoke summary dict.
def evaluate_warm_start_with_batpac(
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    output_dir: Path,
    evaluator: Optional[Any] = None,
    evaluator_mode: Optional[str] = None,
    preflight: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Seed the requested numerical optimizer with LLM candidates and refine."""
    if args.strategy != "warm_start":
        raise ValueError("evaluate_warm_start_with_batpac requires --strategy warm_start")

    candidates = [dict(candidate) for candidate in payload.get("initial_candidates") or []]
    evaluator, evaluator_mode, preflight = prepare_real_batpac_evaluator(
        args,
        evaluator=evaluator,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
    )
    evaluator, unique_counter = _cached_counting_evaluator(evaluator)

    optimizer_name = str(getattr(args, "optimizer", "pattern"))
    optimizer = make_optimizer_with_options(
        optimizer_name,
        evaluator,
        axbo_surrogate_slices_path=_axbo_surrogate_slices_path(optimizer_name, output_dir),
    )
    started = time.perf_counter()
    result = optimize_with_design_variable_bounds(
        optimizer,
        (payload.get("context") or {}).get("variable_bounds"),
        condition_name=args.scenario,
        budget=int(args.budget),
        seed=_optimizer_seed(args),
        initial_candidates=candidates,
        fixed_variables=None,
    )
    # Keep exact ordered proposals alongside normalized simulator candidates.
    previous_best = (payload.get("context") or {}).get("previous_best_design")
    initialization_metadata = [
        _warm_start_proposal_metadata(candidate, previous_best)
        for candidate in candidates
    ]
    result.metadata["warm_start_initialization"] = copy.deepcopy(initialization_metadata)
    initial_records = [
        record for record in result.records if record.metadata.get("source") == "initial"
    ]
    for proposal_index, (record, proposal) in enumerate(
        zip(initial_records, initialization_metadata)
    ):
        record.metadata["warm_start_proposal_index"] = proposal_index
        record.metadata["warm_start_proposal"] = copy.deepcopy(proposal)
    total_runtime_sec = time.perf_counter() - started
    records = result.records
    records_path = output_dir / "real_batpac_records.jsonl"
    save_records_jsonl(records_path, records)
    summary = summarize_real_optimizer_records(
        args=args,
        records=records,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
        total_runtime_sec=total_runtime_sec,
        variable_bounds=(payload.get("context") or {}).get("variable_bounds"),
        strategy_metadata={
            "num_generated_candidates": len(candidates),
            "generated_warm_start_candidates": copy.deepcopy(candidates),
            "warm_start_rationales": [candidate.get("rationale") for candidate in candidates],
            "optimizer_initialization_metadata": copy.deepcopy(
                result.metadata.get("warm_start_initialization")
            ),
            "warm_start_candidates_evaluated": sum(
                1 for record in records if record.metadata.get("source") == "initial"
            ),
            "downstream_solver": optimizer_name,
            "solver_description_key": optimizer_name,
            "total_evaluation_budget": int(args.budget),
            "allowed_warm_start_count_min": int(
                getattr(args, "minimum_warm_start_count", len(candidates))
            ),
            "allowed_warm_start_count_max": int(
                getattr(args, "maximum_warm_start_count", None) or len(candidates)
            ),
            "selected_warm_start_count": int(
                getattr(payload.get("output"), "warm_start_count", len(candidates))
            ),
            "remaining_solver_budget": int(args.budget) - len(candidates),
            "allocation_rationale": getattr(
                payload.get("output"), "allocation_rationale", None
            ),
            "warm_candidate_evaluation_order": list(range(len(candidates))),
            "unique_real_batpac_evaluations": unique_counter["calls"],
            "manual_lookup_metrics": copy.deepcopy(
                (payload.get("context") or {}).get("manual_lookup_metrics", {})
            ),
        },
    )
    add_direct_reuse_metrics(summary, payload.get("context"))
    summary_path = output_dir / "real_batpac_summary.json"
    save_summary_json(summary_path, summary)
    print(f"real BatPaC records saved: {records_path}")
    print(f"real BatPaC summary saved: {summary_path}")
    print_real_batpac_summary(summary)
    return summary


def _cached_counting_evaluator(evaluator: Any) -> Any:
    """Cache exact candidate/condition calls and expose the unique call count."""
    cache: Dict[Any, Dict[str, Any]] = {}
    counter = {"calls": 0}

    def wrapped(candidate: Mapping[str, Any], condition: Mapping[str, Any]) -> Dict[str, Any]:
        candidate_key = tuple(candidate.get(name) for name in ("x_chem", "x_T", "x_S", "x_MPR", "x_R"))
        condition_key = json.dumps(jsonable(condition), sort_keys=True)
        key = (candidate_key, condition_key)
        if key not in cache:
            cache[key] = dict(evaluator(dict(candidate), dict(condition)))
            counter["calls"] += 1
        return copy.deepcopy(cache[key])

    return wrapped, counter


def _warm_start_proposal_metadata(
    proposal: Mapping[str, Any],
    previous_best: Any,
) -> Dict[str, Any]:
    """Add computed deltas while preserving every model-provided proposal field."""
    metadata = copy.deepcopy(dict(proposal))
    names = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")
    previous = previous_best if isinstance(previous_best, Mapping) else {}
    changed = [name for name in names if previous.get(name) != proposal.get(name)]
    metadata["computed_variables_changed"] = changed
    metadata["computed_delta_from_previous_best"] = {
        name: {
            "previous": previous.get(name),
            "proposed": proposal.get(name),
        }
        for name in changed
    }
    return metadata


# 목적: variable-fixing output을 real BatPaC evaluator가 주입된 optimizer로 평가한다.
# 입력: args, strategy payload, output directory, real evaluator.
# 출력: real BatPaC summary.
def evaluate_variable_fixing_with_batpac(
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    output_dir: Path,
    evaluator: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Dict[str, Any]:
    """Run the selected optimizer with real BatPaC and fixed variables."""
    output = payload["output"]
    fixed_variables = dict(getattr(output, "fixed_variables", payload.get("fixed_variables") or {}))
    free_variables = list(getattr(output, "free_variables", []))
    variable_bounds = (payload.get("context") or {}).get("variable_bounds")
    finite_plan = finite_discrete_optimizer_plan(
        optimizer_name=args.optimizer,
        variable_bounds=variable_bounds,
        fixed_variables=fixed_variables,
        free_variables=free_variables,
        requested_budget=int(args.budget),
        seed=_optimizer_seed(args),
    )
    optimizer = make_optimizer_with_options(
        args.optimizer,
        evaluator,
        axbo_surrogate_slices_path=_axbo_surrogate_slices_path(args.optimizer, output_dir),
    )
    started = time.perf_counter()
    result = optimize_with_design_variable_bounds(
        optimizer,
        variable_bounds,
        condition_name=args.scenario,
        budget=int(finite_plan["effective_evaluation_budget"]),
        seed=_optimizer_seed(args),
        initial_candidates=finite_plan["initial_candidates"],
        fixed_variables=fixed_variables or None,
    )
    if set(free_variables) & set(fixed_variables):
        raise AssertionError("AxBO free dimensions overlap fixed dimensions")

    record_candidates: List[Mapping[str, Any]] = []
    for record in result.records:
        candidate = getattr(record, "candidate", None)
        if candidate is None and isinstance(record, Mapping):
            candidate = record.get("candidate")
        if not isinstance(candidate, Mapping):
            raise TypeError(
                "optimizer record must expose a candidate mapping either as "
                "record.candidate or record['candidate']"
            )
        record_candidates.append(candidate)
        changed_fixed = {
            name: {"expected": value, "observed": candidate.get(name)}
            for name, value in fixed_variables.items()
            if candidate.get(name) != value
        }
        if changed_fixed:
            raise AssertionError(
                f"fixed dimensions changed in evaluated candidate: {changed_fixed}"
            )

    candidate_keys = [
        json.dumps(jsonable(candidate), sort_keys=True)
        for candidate in record_candidates
    ]
    unique_evaluation_count = len(set(candidate_keys))
    duplicate_evaluation_count = len(candidate_keys) - unique_evaluation_count
    if finite_plan["sampling_without_replacement"] and duplicate_evaluation_count:
        raise AssertionError(
            "finite reduced-space execution repeated evaluated candidates: "
            f"{duplicate_evaluation_count} duplicates"
        )

    result_termination_reason = (getattr(result, "metadata", {}) or {}).get(
        "termination_reason"
    )
    termination_reason = result_termination_reason or finite_plan[
        "planned_termination_reason"
    ]
    requested_budget = int(finite_plan["requested_evaluation_budget"])
    completed_evaluations = len(result.records)
    unused_budget = max(0, requested_budget - completed_evaluations)
    if (
        finite_plan["finite_search_space_cardinality"] is not None
        and completed_evaluations >= finite_plan["finite_search_space_cardinality"]
        and requested_budget >= finite_plan["finite_search_space_cardinality"]
    ):
        termination_reason = "search_space_exhausted"

    total_runtime_sec = time.perf_counter() - started
    records_path = output_dir / "real_batpac_records.jsonl"
    save_records_jsonl(records_path, result.records)
    summary = summarize_real_optimizer_records(
        args=args,
        records=result.records,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
        total_runtime_sec=total_runtime_sec,
        variable_bounds=(payload.get("context") or {}).get("variable_bounds"),
        fixed_variables=fixed_variables,
        strategy_metadata={
            "rationale": getattr(output, "rationale", getattr(output, "reason", None)),
            "warnings": getattr(output, "warnings", []),
            "variable_assessments": getattr(output, "variable_assessments", []),
            "downstream_solver": args.optimizer,
            "total_evaluation_budget": int(args.budget),
            "requested_evaluation_budget": requested_budget,
            "effective_evaluation_budget": int(
                finite_plan["effective_evaluation_budget"]
            ),
            "completed_evaluations": completed_evaluations,
            "unused_evaluation_budget": unused_budget,
            "finite_search_space_cardinality": finite_plan[
                "finite_search_space_cardinality"
            ],
            "sampling_without_replacement": finite_plan[
                "sampling_without_replacement"
            ],
            "unique_evaluation_count": unique_evaluation_count,
            "duplicate_evaluation_count": duplicate_evaluation_count,
            "termination_reason": termination_reason,
            "search_space_exhausted": termination_reason == "search_space_exhausted",
            "search_dimension_before": 5,
            "search_dimension_after": len(free_variables),
            "effective_optimizer_dimensionality": len(free_variables),
            "free_variables": free_variables,
            "fixed_values": fixed_variables,
            "fixed_count": len(fixed_variables),
            "variable_fixing_applied": bool(fixed_variables),
            "abstained": not bool(fixed_variables),
            "abstention_reason": (
                "no_variable_reasonably_supported_for_reuse"
                if not fixed_variables else None
            ),
            "manual_lookup_metrics": copy.deepcopy(
                (payload.get("context") or {}).get("manual_lookup_metrics", {})
            ),
        },
    )
    add_direct_reuse_metrics(summary, payload.get("context"))
    summary_path = output_dir / "real_batpac_summary.json"
    save_summary_json(summary_path, summary)
    print(f"real BatPaC records saved: {records_path}")
    print(f"real BatPaC summary saved: {summary_path}")
    print_real_batpac_summary(summary)
    return summary


# 목적: ReAct action 후보를 real BatPaC evaluator로 직접 평가한다.
# 입력: args, backend, recorder, output directory, real evaluator.
# 출력: strategy payload와 real BatPaC summary.
def evaluate_react_with_batpac(
    args: argparse.Namespace,
    backend: LLMBackend,
    recorder: TraceRecorder,
    output_dir: Path,
    evaluator: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run ReAct with real BatPaC evaluations."""
    context = build_context(
        args.scenario,
        args.semantic_level,
        context_artifact_path=getattr(args, "context_artifact", None),
        semantic_artifact_dir=output_dir / "semantic_artifacts",
        compressed_manual_path=getattr(args, "compressed_manual_path", None),
        full_manual_path=getattr(args, "full_manual_path", None),
    )
    run_id = f"react_{args.scenario}_{args.model_key}"
    context = _augment_level3_context(
        context, args, backend, recorder, run_id, "react"
    )
    max_steps = max(1, min(int(args.max_steps), int(args.budget)))
    if _react_engine(args) == "qwen_agent":
        react_strategy = QwenAgentReActSearchStrategy(
            evaluator=evaluator,
            backend=backend,
            max_steps=max_steps,
            max_tokens=_strategy_max_tokens(args),
            temperature=float(getattr(args, "temperature", 0.0)),
            parse_retries=int(getattr(args, "parse_retries", 1)),
            prompt_style=_react_prompt_style(args),
        )
    else:
        react_strategy = ReActSearchStrategy(
            backend=backend,
            evaluator=evaluator,
            max_steps=max_steps,
            temperature=float(getattr(args, "temperature", 0.0)),
            max_tokens=_strategy_max_tokens(args),
            prompt_style=_react_prompt_style(args),
            parse_retries=int(getattr(args, "parse_retries", 2)),
            protocol_version=getattr(args, "prompt_version", "v0"),
            include_protocol_demonstration=(
                getattr(args, "react_protocol_demonstration", "none")
                == "domain_neutral"
            ),
            evaluation_budget=int(args.budget),
        )
    run_exception = None
    run_traceback = None

    with use_design_variable_bounds(
        context.get("variable_bounds")
    ):
        try:
            output = react_strategy.run(
                context,
                semantic_level=args.semantic_level,
                model_key=args.model_key,
                trace_recorder=recorder,
                run_id=run_id,
            )
        except Exception as exc:
            partial_output = getattr(
                exc,
                "partial_react_result",
                None,
            )

            if partial_output is None:
                raise

            output = partial_output
            run_exception = exc
            run_traceback = exc.__traceback__
    condition = make_condition(args.scenario)
    records = react_evaluations_to_records(
        output.evaluations, condition, context.get("variable_bounds")
    )
    records_path = output_dir / "real_batpac_records.jsonl"
    save_records_jsonl(records_path, records)
    summary = summarize_real_optimizer_records(
        args=args,
        records=records,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
        total_runtime_sec=sum(record.wall_time_sec for record in records),
        variable_bounds=context.get("variable_bounds"),
        strategy_metadata={
            "actions": [_react_action_summary(action) for action in output.actions],
            "stopped": output.stopped,
            "termination_reason": output.termination_reason,
            "agent_step_count": getattr(output, "agent_step_count", len(output.actions)),
            "accepted_action_count": getattr(output, "accepted_action_count", len(output.actions)),
            "rejected_action_count": getattr(output, "rejected_action_count", 0),
            "duplicate_rejection_count": getattr(output, "duplicate_rejection_count", 0),
            "bound_rejection_count": getattr(output, "bound_rejection_count", 0),
            "invalid_finish_count": getattr(output, "invalid_finish_count", 0),
            "format_repair_attempt_count": getattr(output, "format_repair_attempt_count", 0),
            "batpac_evaluation_count": getattr(output, "batpac_evaluation_count", len(output.evaluations)),
            "partial_failure": (
                run_exception is not None
            ),
            "partial_records_persisted": (
                run_exception is not None
            ),
            "partial_failure_type": (
                type(run_exception).__name__
                if run_exception is not None
                else None
            ),
            "partial_failure_message": (
                str(run_exception)
                if run_exception is not None
                else None
            ),
            "termination_details": copy.deepcopy(
                getattr(
                    output,
                    "termination_details",
                    None,
                )
            ),
            "manual_lookup_metrics": copy.deepcopy(
                context.get("manual_lookup_metrics", {})
            ),
        },
    )
    add_direct_reuse_metrics(summary, context)

    summary_path = (
        output_dir
        / "real_batpac_summary.json"
    )
    partial_failure_path = (
        output_dir
        / "partial_real_batpac_failure.json"
    )

    if run_exception is not None:
        partial_failure = {
            "status": "partial_failure",
            "exception_type": (
                type(run_exception).__name__
            ),
            "message": str(run_exception),
            "termination_reason": (
                output.termination_reason
            ),
            "agent_step_count": getattr(
                output,
                "agent_step_count",
                len(output.actions),
            ),
            "evaluation_count": len(records),
            "records_path": str(records_path),
            "summary_path": str(summary_path),
            "records_persisted_before_reraise": True,
        }

        summary["partial_failure"] = dict(
            partial_failure
        )

    save_summary_json(summary_path, summary)

    print(
        f"real BatPaC records saved: {records_path}"
    )
    print(
        f"real BatPaC summary saved: {summary_path}"
    )
    print_real_batpac_summary(summary)

    if run_exception is not None:
        save_summary_json(
            partial_failure_path,
            partial_failure,
        )

        setattr(
            run_exception,
            "partial_records_path",
            str(records_path),
        )
        setattr(
            run_exception,
            "partial_summary_path",
            str(summary_path),
        )

        print(
            "partial real BatPaC failure saved: "
            f"{partial_failure_path}"
        )

        raise run_exception.with_traceback(
            run_traceback
        )

    return {
        "context": context,
        "output": output,
        "initial_candidates": None,
        "fixed_variables": None,
    }, summary


# 목적: hybrid policy가 선택한 strategy를 같은 real BatPaC evaluator로 실행한다.
# 입력: args, backend, recorder, output directory, real evaluator.
# 출력: hybrid payload와 nested real BatPaC summary.
def evaluate_hybrid_with_batpac(
    args: argparse.Namespace,
    backend: LLMBackend,
    recorder: TraceRecorder,
    output_dir: Path,
    evaluator: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run hybrid policy and dispatch the selected real strategy."""
    payload = run_strategy(args, backend, recorder)
    output = payload["output"]
    selected_strategy = str(output.selected_strategy)
    child_args = copy.copy(args)
    child_args.strategy = selected_strategy
    child_dir = output_dir / f"hybrid_{selected_strategy}"
    child_dir.mkdir(parents=True, exist_ok=True)
    child_result = run_real_batpac_strategy(
        child_args,
        backend,
        recorder,
        child_dir,
        evaluator=evaluator,
        evaluator_mode=evaluator_mode,
        preflight=preflight,
    )
    nested_summary = child_result["optimizer_summary"]
    summary = {
        "run_optimizer": True,
        "real_batpac": True,
        "strategy": "hybrid",
        "selected_strategy": selected_strategy,
        "optimizer": getattr(args, "optimizer", None),
        "effective_optimizer": _effective_optimizer_for_strategy(
            selected_strategy,
            getattr(args, "optimizer", None),
            nested_summary,
        ),
        "scenario": args.scenario,
        "semantic_level": getattr(args, "semantic_level", None),
        "model_key": getattr(args, "model_key", None),
        "backend": getattr(args, "backend", None),
        "react_engine": _react_engine(args),
        "seed": getattr(args, "seed", None),
        "optimizer_seed": _optimizer_seed(args),
        "rationale": output.rationale,
        "strategy_args": output.strategy_args,
        "nested_real_batpac_summary": nested_summary,
        "evaluator_mode": evaluator_mode,
        "condition": args.scenario,
        "budget": args.budget,
        "num_generated_candidates": nested_summary.get("num_generated_candidates", 0),
        "num_batpac_evaluations": nested_summary.get("num_batpac_evaluations", 0),
        "mean_eval_time_sec": nested_summary.get("mean_eval_time_sec", 0.0),
        "feasible_count": nested_summary.get("feasible_count", 0),
        "failed_count": nested_summary.get("failed_count", 0),
        "best_feasible_candidate": nested_summary.get("best_feasible_candidate"),
        "best_feasible_cost": nested_summary.get("best_feasible_cost"),
        "best_outputs": nested_summary.get("best_outputs"),
        "runtime_workbook": dict(preflight),
        "child_output_dir": str(child_dir),
    }
    summary_path = output_dir / "real_batpac_summary.json"
    save_summary_json(summary_path, summary)
    print(f"real BatPaC summary saved: {summary_path}")
    print_real_batpac_summary(summary)
    return payload, summary


# 목적: runtime workbook preflight 정보를 만든다.
# 입력: persistent workbook path CLI 값.
# 출력: 출력용 workbook path와 존재 여부.
def preflight_runtime_workbook(persistent_workbook_path: str) -> Dict[str, Any]:
    """Return runtime workbook preflight information."""
    path = Path(persistent_workbook_path)
    if not path.is_absolute():
        path = RUNTIME_WORKBOOK_DIR / path
    existed_before = path.exists()
    return {
        "persistent_workbook_path": persistent_workbook_path,
        "runtime_workbook_path": str(path),
        "runtime_workbook_exists": existed_before,
        "runtime_workbook_existed_before_run": existed_before,
    }


def post_run_runtime_workbook_metadata(preflight: Mapping[str, Any]) -> Dict[str, Any]:
    """Refresh final workbook existence without changing runtime behavior."""
    metadata = dict(preflight)
    path = Path(str(metadata["runtime_workbook_path"]))
    existed_before = bool(
        metadata.get("runtime_workbook_existed_before_run", metadata.get("runtime_workbook_exists", False))
    )
    exists_after = path.exists()
    metadata["runtime_workbook_existed_before_run"] = existed_before
    metadata["runtime_workbook_exists_after_run"] = exists_after
    metadata["runtime_workbook_exists"] = exists_after
    return metadata


# 목적: BatPaC wrapper가 runtime directory 기준으로 해석할 workbook path를 정규화한다.
# 입력: CLI에서 받은 persistent workbook path.
# 출력: BatPaC wrapper에 전달할 path 문자열.
def normalize_persistent_workbook_path_for_batpac(persistent_workbook_path: str) -> str:
    """Normalize the persistent workbook argument for the BatPaC wrapper."""
    path = Path(persistent_workbook_path)
    if path.is_absolute():
        return str(path)
    if path.parts and path.parts[0] == RUNTIME_WORKBOOK_DIR.name:
        stripped = Path(*path.parts[1:]) if len(path.parts) > 1 else path
        return str(stripped)
    return str(path)


# 목적: generated candidate가 BatPaC wrapper에 어떻게 매핑되는지 미리 보여준다.
# 입력: candidate와 condition 이름.
# 출력: BatPaC high-level input preview dict.
def candidate_batpac_input_preview(
    candidate: Mapping[str, Any], condition_name: str,
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the BatPaC input preview for a candidate."""
    return candidate_to_batpac_inputs(candidate, make_condition(condition_name), variable_bounds)


# 목적: candidate 리스트를 evaluator로 직접 평가해 record로 저장한다.
# 입력: candidate 리스트, condition dict, evaluator callable.
# 출력: EvaluationRecord 리스트.
def _evaluate_candidates_directly(
    candidates: Any,
    condition: Mapping[str, Any],
    evaluator: Any,
    source: str = "llm_candidate",
) -> list:
    records = []
    for iteration, candidate in enumerate(candidates):
        prepared = repair_candidate(candidate)
        started = time.perf_counter()
        try:
            result = evaluator(copy.deepcopy(prepared), dict(condition))
        except Exception as exc:  # pragma: no cover - defensive external boundary
            result = _direct_failure_result(exc, prepared, condition)
        wall_time_sec = time.perf_counter() - started
        feasible = is_feasible(result, condition)
        record = EvaluationRecord(
            iteration=iteration,
            candidate=copy.deepcopy(prepared),
            condition_name=str(condition["name"]),
            result=copy.deepcopy(result),
            feasible=feasible,
            loss=penalized_loss(result, condition),
            violation=normalized_constraint_violation(result, condition),
            wall_time_sec=wall_time_sec,
            metadata={
                "source": source,
                "batpac_input_preview": candidate_batpac_input_preview(prepared, str(condition["name"])),
            },
        )
        records.append(record)
    return records


# 목적: direct evaluator 예외를 recordable result로 변환한다.
# 입력: exception, candidate, condition.
# 출력: evaluator result dict.
def _direct_failure_result(exc: Exception, candidate: Mapping[str, Any], condition: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "success": False,
        "optimization_valid": False,
        "failure_reason": "simulator_exception",
        "cost": None,
        "total_pack_cost": None,
        "P": None,
        "E": None,
        "V": None,
        "c": None,
        "L_bat": None,
        "W_bat": None,
        "raw": {"error": repr(exc), "candidate": dict(candidate), "condition": dict(condition)},
    }


# 목적: real BatPaC direct candidate records를 summary dict로 변환한다.
# 입력: args, generated candidates, records, evaluator mode, preflight 정보.
# 출력: real BatPaC smoke summary.
def summarize_batpac_candidate_records(
    args: argparse.Namespace,
    candidates: Any,
    records: Any,
    evaluator_mode: str,
    preflight: Mapping[str, Any],
) -> Dict[str, Any]:
    """Summarize direct BatPaC candidate evaluations."""
    feasible_records = [record for record in records if record.feasible]
    failed_records = [
        record
        for record in records
        if not record.result.get("optimization_valid", record.result.get("success", False))
    ]
    best_record = _best_feasible_record(feasible_records)
    eval_times = [float(record.wall_time_sec) for record in records]
    return {
        "run_optimizer": True,
        "real_batpac": True,
        "strategy": args.strategy,
        "selected_strategy": None,
        "optimizer": getattr(args, "optimizer", None),
        "effective_optimizer": "direct_candidate_evaluation",
        "scenario": args.scenario,
        "semantic_level": getattr(args, "semantic_level", None),
        "model_key": getattr(args, "model_key", None),
        "backend": getattr(args, "backend", None),
        "react_engine": _react_engine(args),
        "seed": getattr(args, "seed", None),
        "optimizer_seed": _optimizer_seed(args),
        "evaluator_mode": evaluator_mode,
        "condition": args.scenario,
        "budget": args.budget,
        "num_generated_candidates": len(candidates),
        "num_batpac_evaluations": len(records),
        "mean_eval_time_sec": statistics.mean(eval_times) if eval_times else 0.0,
        "feasible_count": len(feasible_records),
        "evaluations_to_first_feasible": _evaluations_to_first_feasible(records),
        "failed_count": len(failed_records),
        "best_feasible_candidate": copy.deepcopy(best_record.candidate) if best_record else None,
        "best_feasible_cost": _finite_cost(best_record.result) if best_record else None,
        "best_outputs": _best_outputs(best_record.result) if best_record else None,
        "failed_evaluations": [_failure_summary(record) for record in failed_records],
        "runtime_workbook": post_run_runtime_workbook_metadata(preflight),
        "candidate_batpac_input_previews": [
            candidate_batpac_input_preview(candidate, args.scenario) for candidate in candidates
        ],
    }


# 목적: optimizer/ReAct가 만든 real BatPaC records를 공통 summary로 변환한다.
# 입력: args, records, evaluator mode, preflight, runtime, strategy metadata.
# 출력: real BatPaC summary dict.
def summarize_real_optimizer_records(
    args: argparse.Namespace,
    records: List[EvaluationRecord],
    evaluator_mode: str,
    preflight: Mapping[str, Any],
    total_runtime_sec: float,
    variable_bounds: Optional[Mapping[str, Any]] = None,
    fixed_variables: Optional[Mapping[str, Any]] = None,
    strategy_metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarize records produced by a real BatPaC optimizer run."""
    feasible_records = [record for record in records if record.feasible]
    failed_records = [
        record
        for record in records
        if not record.result.get("optimization_valid", record.result.get("success", False))
    ]
    best_record = _best_feasible_record(feasible_records)
    eval_times = [float(record.wall_time_sec) for record in records]
    evaluated_candidates = [copy.deepcopy(record.candidate) for record in records]
    final_workbook_metadata = post_run_runtime_workbook_metadata(preflight)
    summary = {
        "run_optimizer": True,
        "real_batpac": True,
        "strategy": args.strategy,
        "selected_strategy": None,
        "optimizer": getattr(args, "optimizer", None),
        "effective_optimizer": "react_direct" if args.strategy == "react" else getattr(args, "optimizer", None),
        "scenario": args.scenario,
        "semantic_level": getattr(args, "semantic_level", None),
        "model_key": getattr(args, "model_key", None),
        "backend": getattr(args, "backend", None),
        "react_engine": _react_engine(args),
        "seed": getattr(args, "seed", None),
        "optimizer_seed": _optimizer_seed(args),
        "evaluator_mode": evaluator_mode,
        "condition": args.scenario,
        "budget": args.budget,
        "num_generated_candidates": len(evaluated_candidates),
        "num_batpac_evaluations": len(records),
        "mean_eval_time_sec": statistics.mean(eval_times) if eval_times else 0.0,
        "feasible_count": len(feasible_records),
        "evaluations_to_first_feasible": _evaluations_to_first_feasible(records),
        "failed_count": len(failed_records),
        "best_feasible_candidate": copy.deepcopy(best_record.candidate) if best_record else None,
        "best_feasible_cost": _finite_cost(best_record.result) if best_record else None,
        "best_outputs": _best_outputs(best_record.result) if best_record else None,
        "failed_evaluations": [_failure_summary(record) for record in failed_records],
        "runtime_workbook": final_workbook_metadata,
        "total_runtime_sec": total_runtime_sec,
        "optimizer_runtime_sec": total_runtime_sec,
        "batpac_runtime_sec": sum(eval_times),
        "optimizer_overhead_sec": max(0.0, total_runtime_sec - sum(eval_times)),
        "evaluated_candidates": evaluated_candidates,
        "candidate_batpac_input_previews": [
            candidate_batpac_input_preview(candidate, args.scenario, variable_bounds)
            for candidate in evaluated_candidates
        ],
    }
    if fixed_variables is not None:
        summary["fixed_variables"] = dict(fixed_variables)
    if strategy_metadata:
        summary.update(dict(strategy_metadata))
    return summary


def add_direct_reuse_metrics(
    summary: Dict[str, Any], context: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Attach method metrics without comparing cost to an infeasible reuse."""
    reevaluation = (
        context.get("previous_best_reevaluated_under_changed_condition")
        if isinstance(context, Mapping)
        else None
    )
    if not isinstance(reevaluation, Mapping):
        summary.update(
            {
                "direct_reuse_available": False,
                "direct_reuse_feasible": None,
                "direct_reuse_cost": None,
                "improvement_over_direct_reuse": None,
                "feasibility_recovered_from_direct_reuse": None,
            }
        )
        return summary
    raw_result = reevaluation.get("result", reevaluation)
    changed_condition = context.get("changed_condition") if isinstance(context, Mapping) else None
    if not isinstance(raw_result, Mapping) or not isinstance(changed_condition, Mapping):
        raise ValueError("Changed-condition direct-reuse artifact must contain a result object")
    reuse_feasible = is_feasible(raw_result, changed_condition)
    reuse_cost = _finite_cost(raw_result)
    best_cost = summary.get("best_feasible_cost")
    improvement = None
    if reuse_feasible and reuse_cost is not None and best_cost is not None:
        improvement = reuse_cost - float(best_cost)
    summary.update(
        {
            "direct_reuse_available": True,
            "direct_reuse_feasible": reuse_feasible,
            "direct_reuse_cost": reuse_cost,
            "improvement_over_direct_reuse": improvement,
            "feasibility_recovered_from_direct_reuse": (
                (not reuse_feasible) and int(summary.get("feasible_count", 0) or 0) > 0
            ),
        }
    )
    return summary


def _evaluations_to_first_feasible(records: List[EvaluationRecord]) -> Optional[int]:
    for index, record in enumerate(records, start=1):
        if record.feasible:
            return index
    return None


# 목적: ReAct strategy evaluation dict를 optimizer EvaluationRecord로 변환한다.
# 입력: ReAct evaluation list와 condition.
# 출력: EvaluationRecord list.
def react_evaluations_to_records(
    evaluations: List[Mapping[str, Any]],
    condition: Mapping[str, Any],
    variable_bounds: Optional[Mapping[str, Any]] = None,
) -> List[EvaluationRecord]:
    """Convert ReAct evaluations to evaluation records."""
    records: List[EvaluationRecord] = []
    for iteration, evaluation in enumerate(evaluations):
        candidate = repair_candidate(evaluation["candidate"], variable_bounds)
        result = dict(evaluation["result"])
        feasible = bool(evaluation["feasible"])
        record = EvaluationRecord(
            iteration=iteration,
            candidate=copy.deepcopy(candidate),
            condition_name=str(condition["name"]),
            result=copy.deepcopy(result),
            feasible=feasible,
            loss=penalized_loss(result, condition),
            violation=normalized_constraint_violation(result, condition),
            wall_time_sec=float(evaluation.get("wall_time_sec", 0.0)),
            metadata={
                "source": "llm_react",
                "batpac_input_preview": candidate_batpac_input_preview(
                    candidate, str(condition["name"]), variable_bounds
                ),
            },
        )
        records.append(record)
    return records


# 목적: ReActAction dataclass를 summary에 저장 가능한 dict로 변환한다.
# 입력: ReActAction-like object.
# 출력: action summary dict.
def _react_action_summary(action: Any) -> Dict[str, Any]:
    return {
        "action": getattr(action, "action_type", None),
        "candidate": copy.deepcopy(getattr(action, "candidate", None)),
        "final_candidate": copy.deepcopy(getattr(action, "final_candidate", None)),
        "rationale": getattr(action, "reason", None),
    }


def _react_engine(args: argparse.Namespace) -> str:
    return str(getattr(args, "react_engine", "custom"))


# 목적: --real-batpac 결과가 실제 평가 시간 특성을 보이는지 확인한다.
# 입력: real BatPaC summary와 strict flag.
# 출력: 없음, strict이면 RuntimeError 가능.
def apply_real_batpac_guard(summary: Mapping[str, Any], strict: bool = False) -> None:
    """Warn or fail when a real BatPaC summary looks like a fake evaluator."""
    if strict:
        required = ("real_batpac", "num_batpac_evaluations", "mean_eval_time_sec")
        missing = [key for key in required if key not in summary]
        if missing:
            raise RuntimeError(f"Strict real BatPaC guard missing summary fields: {missing}")
        if not bool(summary.get("real_batpac")):
            raise RuntimeError("Strict real BatPaC guard received a non-real BatPaC summary.")

    evaluations = int(summary.get("num_batpac_evaluations", 0) or 0)
    mean_eval_time = float(summary.get("mean_eval_time_sec", 0.0) or 0.0)
    if strict and evaluations <= 0:
        raise RuntimeError("Strict real BatPaC guard received zero BatPaC evaluations.")
    if evaluations <= 0 or mean_eval_time >= 1.0:
        return
    message = (
        "WARNING: --real-batpac produced mean_eval_time_sec < 1.0 with "
        f"{evaluations} evaluations. This may indicate a fast/mock evaluator path."
    )
    if strict:
        raise RuntimeError(message)
    print(message)


# 목적: feasible record 중 cost가 가장 낮은 record를 찾는다.
# 입력: feasible EvaluationRecord 리스트.
# 출력: best record 또는 None.
def _best_feasible_record(records: Any) -> Optional[EvaluationRecord]:
    best = None
    best_cost = math.inf
    for record in records:
        cost = _finite_cost(record.result)
        if cost is not None and cost < best_cost:
            best = record
            best_cost = cost
    return best


# 목적: result에서 finite cost를 추출한다.
# 입력: evaluator result.
# 출력: finite cost 또는 None.
def _finite_cost(result: Mapping[str, Any]) -> Optional[float]:
    try:
        cost = float(result.get("cost"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost):
        return None
    return cost


# 목적: summary에 담을 주요 BatPaC output만 추출한다.
# 입력: evaluator result.
# 출력: 주요 output dict.
def _best_outputs(result: Mapping[str, Any]) -> Dict[str, Any]:
    keys = ("cost", "total_pack_cost", "P", "E", "V", "c", "L_bat", "W_bat")
    return {key: result.get(key) for key in keys}


# 목적: 실패 record를 사람이 읽을 수 있는 summary로 변환한다.
# 입력: EvaluationRecord.
# 출력: failure summary dict.
def _failure_summary(record: EvaluationRecord) -> Dict[str, Any]:
    return {
        "iteration": record.iteration,
        "candidate": copy.deepcopy(record.candidate),
        "failure_reason": record.result.get("failure_reason"),
        "raw": copy.deepcopy(record.result.get("raw")),
    }


# 목적: real BatPaC smoke summary를 stdout에 compact하게 출력한다.
# 입력: summary dict.
# 출력: 없음.
def print_real_batpac_summary(summary: Mapping[str, Any]) -> None:
    """Print a compact real BatPaC summary."""
    print(f"run_optimizer: {summary['run_optimizer']}")
    print(f"real_batpac: {summary['real_batpac']}")
    print(f"num_generated_candidates: {summary.get('num_generated_candidates', 0)}")
    print(f"num_batpac_evaluations: {summary.get('num_batpac_evaluations', 0)}")
    print(f"mean_eval_time_sec: {summary.get('mean_eval_time_sec', 0.0)}")
    print(f"feasible_count: {summary.get('feasible_count', 0)}")
    print(f"failed_count: {summary.get('failed_count', 0)}")
    print(f"best_feasible_candidate: {json.dumps(jsonable(summary.get('best_feasible_candidate')), sort_keys=True)}")
    print(f"best_feasible_cost: {summary.get('best_feasible_cost')}")


def _safe_nonnegative_int(value: Any) -> int:
    """Return a safe nonnegative integer for server-reported counters."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _safe_nonnegative_float(value: Any) -> float:
    """Return a safe finite nonnegative runtime value."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return max(0.0, parsed)


def _resolved_llm_seed(args: argparse.Namespace) -> int:
    """Return the effective LLM generation seed without changing seed routing."""
    explicit = getattr(args, "llm_seed", None)
    if explicit is not None:
        return int(explicit)
    return int(getattr(args, "seed", 0))


def _llm_metrics_from_trace(trace_path: Path) -> Dict[str, Any]:
    """Aggregate actual recorded generation attempts from trace.jsonl."""
    metrics: Dict[str, Any] = {
        "logical_generation_record_count": 0,
        "parse_retry_generation_record_count": 0,
        "recorded_http_request_count": 0,
        "usage_missing_request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
        "reasoning_tokens": 0,
        "request_latency_sec": 0.0,
        "mean_request_latency_sec": 0.0,
        "max_request_latency_sec": 0.0,
        "token_source": "server_reported_vllm_usage",
        "latency_definition": (
            "sum of Qwen backend HTTP request latencies recorded in "
            "generation_metadata.attempts"
        ),
        "trace_status": "trace_not_found",
    }

    if not trace_path.exists():
        return metrics

    request_latencies: List[float] = []

    for raw_line in trace_path.read_text(
        encoding="utf-8"
    ).splitlines():
        if not raw_line.strip():
            continue

        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if not isinstance(row, Mapping):
            continue

        generation_metadata = row.get("generation_metadata")
        if not isinstance(generation_metadata, Mapping):
            continue

        metrics["logical_generation_record_count"] += 1

        if _safe_nonnegative_int(row.get("retry_attempt")) > 0:
            metrics["parse_retry_generation_record_count"] += 1

        raw_attempts = generation_metadata.get("attempts")

        attempts: List[Mapping[str, Any]] = []

        if isinstance(raw_attempts, list) and raw_attempts:
            attempts = [
                attempt
                for attempt in raw_attempts
                if isinstance(attempt, Mapping)
            ]
        else:
            # Non-Qwen backends may expose only top-level usage/latency.
            usage = generation_metadata.get("usage")
            latency = generation_metadata.get("latency_sec")

            has_usage = (
                isinstance(usage, Mapping)
                and bool(usage)
            )
            has_latency = latency is not None

            if has_usage or has_latency:
                attempts = [
                    {
                        "usage": usage,
                        "latency_sec": latency,
                    }
                ]

        for attempt in attempts:
            metrics["recorded_http_request_count"] += 1

            latency = _safe_nonnegative_float(
                attempt.get("latency_sec")
            )
            if latency > 0.0:
                request_latencies.append(latency)

            usage = attempt.get("usage")
            if not isinstance(usage, Mapping) or not usage:
                metrics["usage_missing_request_count"] += 1
                continue

            prompt_tokens = _safe_nonnegative_int(
                usage.get("prompt_tokens")
            )
            completion_tokens = _safe_nonnegative_int(
                usage.get("completion_tokens")
            )
            total_tokens = _safe_nonnegative_int(
                usage.get("total_tokens")
            )

            if total_tokens == 0:
                total_tokens = (
                    prompt_tokens + completion_tokens
                )

            metrics["prompt_tokens"] += prompt_tokens
            metrics["completion_tokens"] += completion_tokens
            metrics["total_tokens"] += total_tokens

            prompt_details = usage.get(
                "prompt_tokens_details"
            )
            if isinstance(prompt_details, Mapping):
                metrics["cached_prompt_tokens"] += (
                    _safe_nonnegative_int(
                        prompt_details.get("cached_tokens")
                    )
                )

            completion_details = usage.get(
                "completion_tokens_details"
            )
            if isinstance(completion_details, Mapping):
                metrics["reasoning_tokens"] += (
                    _safe_nonnegative_int(
                        completion_details.get(
                            "reasoning_tokens"
                        )
                    )
                )

    if request_latencies:
        metrics["request_latency_sec"] = sum(
            request_latencies
        )
        metrics["mean_request_latency_sec"] = (
            statistics.mean(request_latencies)
        )
        metrics["max_request_latency_sec"] = max(
            request_latencies
        )

    metrics["trace_status"] = "parsed"
    return metrics


def _optimizer_compute_metrics(
    optimizer_summary: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Normalize existing optimizer/BatPaC timing evidence."""
    if not isinstance(optimizer_summary, Mapping):
        return {
            "batpac_evaluation_count": 0,
            "evaluations_to_first_feasible": None,
            "numerical_optimizer_runtime_sec": 0.0,
            "simulator_runtime_sec": 0.0,
            "optimizer_overhead_sec": 0.0,
        }

    summary: Mapping[str, Any] = optimizer_summary

    nested = optimizer_summary.get(
        "nested_real_batpac_summary"
    )
    if isinstance(nested, Mapping):
        summary = nested

    evaluation_count = _safe_nonnegative_int(
        summary.get(
            "num_batpac_evaluations",
            summary.get("evaluations"),
        )
    )

    optimizer_runtime = _safe_nonnegative_float(
        summary.get(
            "optimizer_runtime_sec",
            summary.get("total_runtime_sec"),
        )
    )

    simulator_runtime = _safe_nonnegative_float(
        summary.get(
            "batpac_runtime_sec",
            summary.get("simulator_runtime_sec"),
        )
    )

    optimizer_overhead = _safe_nonnegative_float(
        summary.get("optimizer_overhead_sec")
    )

    if (
        optimizer_overhead == 0.0
        and optimizer_runtime >= simulator_runtime
    ):
        optimizer_overhead = (
            optimizer_runtime - simulator_runtime
        )

    first_feasible = summary.get(
        "evaluations_to_first_feasible"
    )

    if first_feasible is not None:
        first_feasible = _safe_nonnegative_int(
            first_feasible
        )

    return {
        "batpac_evaluation_count": evaluation_count,
        "evaluations_to_first_feasible": first_feasible,
        "numerical_optimizer_runtime_sec": optimizer_runtime,
        "simulator_runtime_sec": simulator_runtime,
        "optimizer_overhead_sec": optimizer_overhead,
    }



# 목적: strategy smoke 결과를 JSON summary로 저장한다.
# 입력: output path, args, payload, optional optimizer summary.
# 출력: summary dict.
def save_strategy_summary(
    path: Path,
    args: argparse.Namespace,
    payload: Mapping[str, Any],
    optimizer_summary: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Save strategy summary."""
    output = payload["output"]
    llm_metrics = _llm_metrics_from_trace(
        path.parent / "trace.jsonl"
    )
    optimizer_compute_metrics = _optimizer_compute_metrics(
        optimizer_summary
    )
    summary = {
        "strategy": args.strategy,
        "scenario": args.scenario,
        "semantic_level": args.semantic_level,
        "model_key": args.model_key,
        "backend": args.backend,
        "optimizer": getattr(args, "optimizer", None),
        "effective_optimizer": _effective_optimizer_for_strategy(
            args.strategy,
            getattr(args, "optimizer", None),
            optimizer_summary or {},
        ),
        "seed": getattr(args, "seed", None),
        "llm_seed": _resolved_llm_seed(args),
        "optimizer_seed": _optimizer_seed(args),
        "generation_config": {
            "temperature": float(
                getattr(args, "temperature", 0.0)
            ),
            "top_p": float(
                getattr(args, "top_p", 1.0)
            ),
            "max_completion_tokens": _strategy_max_tokens(args),
            "parse_retries": int(
                getattr(args, "parse_retries", 1)
            ),
        },
        "llm_metrics": llm_metrics,
        "compute_metrics": {
            "total_wall_clock_sec": None,
            "llm_request_latency_sec": (
                llm_metrics["request_latency_sec"]
            ),
            **optimizer_compute_metrics,
            "exact_flops": None,
            "flops_status": (
                "not directly measured; estimate post hoc "
                "from frozen model architecture and recorded "
                "token counts if required"
            ),
        },
        "budget": getattr(args, "budget", None),
        "run_optimizer": bool(args.run_optimizer or getattr(args, "real_batpac", False)),
        "real_batpac": bool(getattr(args, "real_batpac", False)),
        "strategy_output": _output_payload(output),
        "optimizer_summary": optimizer_summary,
        "context_artifact": (payload.get("context") or {}).get("context_artifact"),
        "semantic_artifact": (payload.get("context") or {}).get("semantic_artifact"),
        "prompt_version": getattr(args, "prompt_version", "v0"),
        "react_engine": _react_engine(args),
        "quantization": getattr(args, "quantization", "none"),
        "structured_output_transport": getattr(args, "structured_output_transport", "prompt_only"),
        "cell_runtime_isolation": getattr(args, "cell_runtime_isolation", None),
        "requested_strategy_completion_tokens": _strategy_max_tokens(args),
        "requested_manual_lookup_completion_tokens": int(
            getattr(args, "manual_lookup_completion_tokens", 2048)
        ),
        "model_context_limit": int(getattr(args, "model_context_limit", 16384)),
        "source_history_length": len(
            (payload.get("context") or {}).get("previous_optimization_history") or []
        ),
        "task_input_fingerprint": _task_input_fingerprint(payload.get("context") or {}),
        "llm_proposed_initial_candidates": payload.get("initial_candidates"),
        "llm_proposed_fixed_variables": payload.get("fixed_variables"),
        "actual_optimizer_configuration": {
            "optimizer": getattr(args, "optimizer", None),
            "budget": getattr(args, "budget", None),
            "seed": getattr(args, "seed", None),
            "optimizer_seed": _optimizer_seed(args),
            "initial_candidates": payload.get("initial_candidates"),
            "fixed_variables": payload.get("fixed_variables"),
            "bounds": (payload.get("context") or {}).get("variable_bounds"),
        },
    }
    path.write_text(json.dumps(jsonable(summary), indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _task_input_fingerprint(context: Mapping[str, Any]) -> str:
    """Hash task inputs while intentionally excluding the semantic factor."""
    stable = {
        key: context.get(key)
        for key in (
            "previous_condition",
            "previous_best_design",
            "previous_best_result",
            "previous_optimization_history",
            "changed_condition",
            "structured_condition_changes",
            "previous_best_reevaluated_under_changed_condition",
            "objective",
            "constraints",
            "variable_bounds",
        )
    }
    encoded = json.dumps(jsonable(stable), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# 목적: dataclass strategy output을 JSON-compatible dict로 변환한다.
# 입력: strategy output object.
# 출력: JSON-compatible payload.
def _output_payload(output: Any) -> Dict[str, Any]:
    if is_dataclass(output):
        return asdict(output)
    if hasattr(output, "__dict__"):
        return dict(output.__dict__)
    return {"value": output}


# 목적: strategy별 실제 평가 주체를 summary schema에 명시한다.
# 입력: strategy 이름, CLI optimizer 이름, optional summary.
# 출력: effective optimizer label.
def _effective_optimizer_for_strategy(
    strategy: str,
    optimizer: Optional[str],
    summary: Mapping[str, Any],
) -> Optional[str]:
    if strategy in {"baseline", "source_warm_baseline", "warm_start"}:
        return optimizer
    if strategy == "react":
        return "react_direct"
    if strategy == "hybrid":
        selected = summary.get("selected_strategy")
        nested = summary.get("nested_real_batpac_summary")
        if isinstance(nested, Mapping):
            return _effective_optimizer_for_strategy(
                str(selected or nested.get("strategy") or ""),
                str(nested.get("optimizer") or optimizer or ""),
                nested,
            )
        return str(summary.get("effective_optimizer") or optimizer or "")
    return optimizer


def enforce_runner_safety(args: argparse.Namespace) -> None:
    """Require explicit opt-ins for mock and real production context paths."""
    if getattr(args, "backend", None) == "static" and not getattr(args, "allow_mock", False):
        raise RuntimeError("--allow-mock is required to use --backend static.")
    if (
        not getattr(args, "real_batpac", False)
        and getattr(args, "run_optimizer", False)
        and not getattr(args, "allow_mock", False)
    ):
        raise RuntimeError("--allow-mock is required for non-real optimizer runs.")
    if (
        getattr(args, "real_batpac", False)
        and not getattr(args, "context_artifact", None)
        and not getattr(args, "allow_mock", False)
    ):
        raise RuntimeError("--context-artifact is required for real BatPaC experiments.")


# 목적: smoke script entry point를 실행한다.
# 입력: command line arguments.
# 출력: trace와 summary artifact.
def main() -> None:
    """Run LLM strategy smoke."""
    args = parse_args()
    enforce_runner_safety(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if bool(getattr(args, "real_batpac", False)):
        args.cell_runtime_isolation = build_cell_runtime_isolation(output_dir)
    recorder = TraceRecorder(output_dir)
    recorder.initialize_run(
        {
            "experiment_id": output_dir.name,
            "scenario": args.scenario,
            "model_name": args.model_key,
            "model_size": args.model_key.rsplit("_", 1)[-1],
            "quantization": args.quantization,
            "semantic_context_level": args.semantic_level,
            "reoptimization_method": args.strategy,
            "numerical_optimizer": args.optimizer,
            "prompt_version": args.prompt_version,
            "react_engine": args.react_engine,
            "random_seed": args.seed,
            "llm_seed": _resolved_llm_seed(args),
            "optimizer_seed": _optimizer_seed(args),
            "temperature": float(
                getattr(args, "temperature", 0.0)
            ),
            "top_p": float(
                getattr(args, "top_p", 1.0)
            ),
            "evaluation_budget": args.budget,
            "backend": args.backend,
            "structured_output_transport": args.structured_output_transport,
            "cell_runtime_isolation": getattr(args, "cell_runtime_isolation", None),
            "context_artifact": args.context_artifact,
            "configured_max_tokens": _strategy_max_tokens(args),
            "effective_max_tokens": _strategy_max_tokens(args),
            "requested_strategy_completion_tokens": _strategy_max_tokens(args),
            "requested_manual_lookup_completion_tokens": int(
                args.manual_lookup_completion_tokens
            ),
            "model_context_limit": int(args.model_context_limit),
        }
    )
    started = time.perf_counter()
    try:
        backend = make_backend(
            args.backend,
            args.strategy,
            model_key=args.model_key,
            model_config=args.model_config,
            stop_sequences=args.qwen_stop,
            model_path=args.model_path,
            allow_mock=bool(args.allow_mock),
            structured_output_transport=args.structured_output_transport,
            prompt_version=args.prompt_version,
        )
        llm_seed = (
            int(args.llm_seed)
            if getattr(args, "llm_seed", None) is not None
            else int(args.seed)
        )
        setattr(backend, "seed", llm_seed)
        setattr(backend, "top_p", float(getattr(args, "top_p", 1.0)))
        if hasattr(backend, "debug_output_dir"):
            backend.debug_output_dir = output_dir
        if args.real_batpac:
            real_result = run_real_batpac_strategy(args, backend, recorder, output_dir)
            payload = real_result["payload"]
            optimizer_summary = real_result["optimizer_summary"]
        else:
            payload = run_strategy(args, backend, recorder)
            optimizer_summary = maybe_run_optimizer(args, payload, output_dir)
        summary_path = output_dir / "strategy_summary.json"
        summary = save_strategy_summary(
            summary_path,
            args,
            payload,
            optimizer_summary,
        )
        total_wall_clock_sec = (
            time.perf_counter() - started
        )
        summary["total_wall_clock_sec"] = (
            total_wall_clock_sec
        )
        summary["compute_metrics"][
            "total_wall_clock_sec"
        ] = total_wall_clock_sec
        summary_path.write_text(
            json.dumps(
                jsonable(summary),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        context = payload.get("context") or {}
        recorder.finalize_run(
            "success",
            metadata={
                "total_wall_clock_sec": total_wall_clock_sec,
                "llm_http_request_count": (
                    summary["llm_metrics"][
                        "recorded_http_request_count"
                    ]
                ),
                "llm_prompt_tokens": (
                    summary["llm_metrics"][
                        "prompt_tokens"
                    ]
                ),
                "llm_completion_tokens": (
                    summary["llm_metrics"][
                        "completion_tokens"
                    ]
                ),
                "llm_total_tokens": (
                    summary["llm_metrics"][
                        "total_tokens"
                    ]
                ),
                "llm_request_latency_sec": (
                    summary["llm_metrics"][
                        "request_latency_sec"
                    ]
                ),
                "llm_parse_retry_generation_count": (
                    summary["llm_metrics"][
                        "parse_retry_generation_record_count"
                    ]
                ),
                "batpac_evaluation_count": (
                    summary["compute_metrics"][
                        "batpac_evaluation_count"
                    ]
                ),
                "evaluations_to_first_feasible": (
                    summary["compute_metrics"][
                        "evaluations_to_first_feasible"
                    ]
                ),
                "numerical_optimizer_runtime_sec": (
                    summary["compute_metrics"][
                        "numerical_optimizer_runtime_sec"
                    ]
                ),
                "simulator_runtime_sec": (
                    summary["compute_metrics"][
                        "simulator_runtime_sec"
                    ]
                ),
                "optimizer_overhead_sec": (
                    summary["compute_metrics"][
                        "optimizer_overhead_sec"
                    ]
                ),
                "source_condition": context.get("previous_condition"),
                "target_condition": context.get("changed_condition"),
                "strategy_summary_path": str(summary_path),
            },
        )
    except Exception as exc:
        failure_llm_metrics = _llm_metrics_from_trace(
            recorder.trace_path
        )
        failure = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "total_wall_clock_sec": time.perf_counter() - started,
            "llm_metrics": failure_llm_metrics,
        }
        (output_dir / "failure.json").write_text(
            json.dumps(jsonable(failure), indent=2, sort_keys=True), encoding="utf-8"
        )
        recorder.finalize_run(
            "failed",
            failure_reason=f"{type(exc).__name__}: {exc}",
            metadata={
                "total_wall_clock_sec": (
                    failure["total_wall_clock_sec"]
                ),
                "llm_http_request_count": (
                    failure_llm_metrics[
                        "recorded_http_request_count"
                    ]
                ),
                "llm_prompt_tokens": (
                    failure_llm_metrics[
                        "prompt_tokens"
                    ]
                ),
                "llm_completion_tokens": (
                    failure_llm_metrics[
                        "completion_tokens"
                    ]
                ),
                "llm_total_tokens": (
                    failure_llm_metrics[
                        "total_tokens"
                    ]
                ),
                "llm_request_latency_sec": (
                    failure_llm_metrics[
                        "request_latency_sec"
                    ]
                ),
                "llm_parse_retry_generation_count": (
                    failure_llm_metrics[
                        "parse_retry_generation_record_count"
                    ]
                ),
            },
        )
        raise
    print(f"strategy: {summary['strategy']}")
    print(f"scenario: {summary['scenario']}")
    print(f"semantic level: {summary['semantic_level']}")
    print(f"model key: {summary['model_key']}")
    print(f"backend: {summary['backend']}")
    print(f"trace saved: {recorder.trace_path}")
    print(f"summary saved: {summary_path}")


if __name__ == "__main__":
    main()
