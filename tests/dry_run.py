"""Runs one configuration of the production controller with a mock simulator and a mock LLM.

Run inside an assembled role tree (PYTHONPATH = that tree), one process per run:

    python tests/dry_run.py --role-tree RUN_ROOT/code/qwen35_4b/wsvf_general \
        --context RUN_ROOT/contexts/wsvf_s_cost.json --strategy warm_start --optimizer random \
        --scenario s_cost --level level2_engineering --seed 0 --out OUT

The command line passed to the controller is the one in wafer/orchestration/run_model_block.sh
(budget 20, max tokens 4096, temperature 0.7, top-p 0.8, strict real-BatPaC mode). Two parts are
mocks: the BatPaC evaluator is the code's own synthetic `runtime.fake_evaluator`, and the model
server is tests/mock_llm.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--role-tree", required=True, type=Path)
    p.add_argument("--context", required=True, type=Path)
    p.add_argument("--strategy", required=True)
    p.add_argument("--optimizer", default="random")
    p.add_argument("--scenario", required=True)
    p.add_argument("--level", default="level2_engineering")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()

    sys.path.insert(0, str(a.role_tree))
    sys.path.insert(1, str(HERE))
    import llm_methods.scripts.run_llm_strategy_smoke as smoke
    import llm_methods.scripts.run_real_batpac_strategy_matrix as matrix
    from llm_methods.backends.base import LLMBackend
    from llm_methods.runtime import fake_evaluator
    from mock_llm import make_mock_llm

    context = json.loads(a.context.read_text())["context"]
    smoke.make_real_batpac_evaluator = lambda **kw: (fake_evaluator, "mock_simulator")
    smoke.preflight_runtime_workbook = lambda path: {
        "persistent_workbook_path": path, "runtime_workbook_path": path, "runtime_workbook_exists": False}
    matrix.make_backend = lambda *args, **kw: make_mock_llm(LLMBackend, context, a.seed)
    # The strict guard rejects evaluators faster than 1 s, because it exists to catch mock evaluators.
    # Keep its other checks and let it only warn here.
    guard = smoke.apply_real_batpac_guard
    smoke.apply_real_batpac_guard = lambda summary, strict=False: guard(summary, strict=False)

    common = ["--scenarios", a.scenario, "--llm-seeds", str(a.seed), "--optimizer-seeds", str(2000 + a.seed),
              "--semantic-levels", a.level, "--model-key", "qwen35_4b", "--backend", "qwen_server",
              "--structured-output-transport", "json_schema", "--timeout-sec", "600",
              "--context-artifact-template", str(a.context), "--budget", "20", "--max-tokens", "4096",
              "--temperature", "0.7", "--top-p", "0.8", "--strict-real-batpac", "--parse-retries", "1",
              "--output-dir", str(a.out)]
    if a.strategy in ("warm_start", "variable_fixing"):
        extra = ["--strategies", a.strategy, "--optimizers", a.optimizer, "--prompt-version", "v0",
                 "--warm-start-count-mode", "fixed", "--fixed-warm-start-count", "3",
                 "--require-variable-assessments"]
    elif a.strategy == "react":
        extra = ["--strategies", "react", "--max-steps", "60", "--prompt-style", "standard",
                 "--react-engine", "custom", "--prompt-version", "v3_3_6_evaluate_only_react"]
        if a.scenario == "s_fastcharge":
            extra += ["--react-feedback-mode", "quantitative"]
    else:  # numerical baselines (cold, source-warm): the command of run_numerical_baselines.sh
        common = ["--scenarios", a.scenario, "--llm-seeds", str(a.seed), "--optimizer-seeds", str(2000 + a.seed),
                  "--semantic-levels", "minimal", "--model-key", "qwen35_4b", "--backend", "qwen_server",
                  "--context-artifact", str(a.context), "--budget", "20", "--timeout-sec", "600",
                  "--strict-real-batpac", "--output-dir", str(a.out)]
        extra = ["--strategies", a.strategy, "--optimizers", a.optimizer]
    sys.argv = ["run_real_batpac_strategy_matrix"] + extra + common
    return matrix.main()


if __name__ == "__main__":
    raise SystemExit(main())
