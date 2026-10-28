"""CLI for source-history leakage diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from llm_methods.context import load_context_artifact
from llm_methods.leakage import diagnose_source_history_leakage
from llm_methods.runtime import fake_evaluator, make_real_batpac_evaluator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-artifact", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-grid-optimum")
    parser.add_argument("--real-batpac", action="store_true")
    parser.add_argument("--direct-eval", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--persistent-workbook-path")
    args = parser.parse_args()
    context = load_context_artifact(args.context_artifact)
    grid = (
        json.loads(Path(args.target_grid_optimum).read_text(encoding="utf-8"))
        if args.target_grid_optimum
        else None
    )
    if args.real_batpac:
        kwargs = {
            "real_batpac": True,
            "direct_eval": args.direct_eval,
            "timeout_sec": args.timeout_sec,
        }
        if args.persistent_workbook_path:
            kwargs["persistent_workbook_path"] = args.persistent_workbook_path
        evaluator, _ = make_real_batpac_evaluator(**kwargs)
    else:
        evaluator = fake_evaluator
    report = diagnose_source_history_leakage(context, evaluator, grid)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
