"""Build a versioned production redesign-context artifact from JSON inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from llm_methods.context import build_redesign_context, save_context_artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-condition", default="s0")
    parser.add_argument("--changed-condition", required=True)
    parser.add_argument("--previous-best-design", required=True)
    parser.add_argument("--previous-best-result", required=True)
    parser.add_argument("--previous-history", default=None)
    parser.add_argument("--changed-reevaluation", default=None)
    parser.add_argument("--compressed-manual-path", default=None)
    parser.add_argument("--full-manual-path", default=None)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def build_artifact_from_files(
    previous_condition: str,
    changed_condition: str,
    previous_best_design_path: Path,
    previous_best_result_path: Path,
    output_path: Path,
    previous_history_path: Optional[Path] = None,
    changed_reevaluation_path: Optional[Path] = None,
    compressed_manual_path: Optional[Path] = None,
    full_manual_path: Optional[Path] = None,
) -> Path:
    design = _load_object(previous_best_design_path)
    result = _load_object(previous_best_result_path)
    history = _load_history(previous_history_path) if previous_history_path else []
    reevaluation = _load_object(changed_reevaluation_path) if changed_reevaluation_path else None
    context = build_redesign_context(
        previous_condition_name=previous_condition,
        changed_condition_name=changed_condition,
        previous_best_design=design,
        previous_best_result=result,
        previous_optimization_history=history,
        previous_best_reevaluated_under_changed_condition=reevaluation,
    )
    semantic_sources = {}
    if compressed_manual_path is not None:
        semantic_sources["compressed_manual"] = {"path": str(compressed_manual_path.resolve())}
    if full_manual_path is not None:
        semantic_sources["full_manual"] = {"path": str(full_manual_path.resolve())}
    if semantic_sources:
        context["semantic_sources"] = semantic_sources
    return save_context_artifact(context, output_path)


def _load_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _load_history(path: Path) -> list[Mapping[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"History line {line_number} in {path} is not an object")
            rows.append(row)
        return rows
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        raise ValueError(f"Expected a JSON object list in {path}")
    return payload


def main() -> None:
    args = parse_args()
    path = build_artifact_from_files(
        previous_condition=args.previous_condition,
        changed_condition=args.changed_condition,
        previous_best_design_path=Path(args.previous_best_design),
        previous_best_result_path=Path(args.previous_best_result),
        output_path=Path(args.output),
        previous_history_path=Path(args.previous_history) if args.previous_history else None,
        changed_reevaluation_path=Path(args.changed_reevaluation) if args.changed_reevaluation else None,
        compressed_manual_path=Path(args.compressed_manual_path) if args.compressed_manual_path else None,
        full_manual_path=Path(args.full_manual_path) if args.full_manual_path else None,
    )
    print(path)


if __name__ == "__main__":
    main()
