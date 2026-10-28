"""Retry failed real BatPaC evaluations from optimizer JSONL records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.core.domain import make_condition
from optimizers.core.evaluation import (
    compute_constraint_violations,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)
from optimizers.core.record_utils import (
    CLASS_INTERMITTENT_MISSING_COST,
    CLASS_RECOVERED_SUCCESSFULLY,
    CLASS_REPRODUCIBLE_MISSING_COST,
    CLASS_SIMULATOR_EXCEPTION,
    FailedEvaluationRecord,
    classify_retry_attempts,
    extract_failed_records,
    missing_required_outputs,
)
from optimizers.scripts.run_real_batpac_smoke import (
    _jsonable,
    make_timeout_batpac_evaluator,
)


# 목적: failed record 하나를 지정된 횟수만큼 real BatPaC로 재평가한다.
# 입력: failed record, retry 횟수, evaluator callable.
# 출력: retry attempt payload 리스트.
def retry_failed_record(
    failed_record: FailedEvaluationRecord,
    retries: int,
    evaluator: Any,
) -> List[Dict[str, Any]]:
    """Retry one failed record."""
    condition = make_condition(failed_record.condition_name)
    attempts = []
    for retry_index in range(retries):
        result = evaluator(dict(failed_record.candidate), dict(condition))
        feasible = is_feasible(result, condition)
        attempt = {
            "original_record_index": failed_record.record_index,
            "original_iteration": failed_record.iteration,
            "retry_index": retry_index,
            "candidate": dict(failed_record.candidate),
            "condition_name": failed_record.condition_name,
            "result": result,
            "missing_outputs": missing_required_outputs(result),
            "feasible": feasible,
            "constraint_violations": compute_constraint_violations(result, condition),
            "normalized_constraint_violation": normalized_constraint_violation(result, condition),
            "penalized_loss": penalized_loss(result, condition),
        }
        attempts.append(attempt)
    return attempts


# 목적: retry attempts를 JSONL 파일에 저장한다.
# 입력: output path와 retry summary 리스트.
# 출력: 없음.
def save_retry_results(path: Path, retry_summaries: List[Dict[str, Any]]) -> None:
    """Save retry summaries as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for summary in retry_summaries:
            handle.write(json.dumps(_jsonable(summary), sort_keys=True) + "\n")


# 목적: classification별 retry 결과 개수를 계산한다.
# 입력: retry summary 리스트.
# 출력: classification 이름별 count dict.
def count_classifications(retry_summaries: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count retry classifications."""
    counts = {
        CLASS_REPRODUCIBLE_MISSING_COST: 0,
        CLASS_INTERMITTENT_MISSING_COST: 0,
        CLASS_RECOVERED_SUCCESSFULLY: 0,
        CLASS_SIMULATOR_EXCEPTION: 0,
    }
    for summary in retry_summaries:
        classification = summary["classification"]
        counts[classification] = counts.get(classification, 0) + 1
    return counts


# 목적: retry summary를 stdout에 자세히 출력한다.
# 입력: retry summary dict.
# 출력: 없음.
def print_retry_summary(summary: Dict[str, Any]) -> None:
    """Print one retry summary."""
    print(
        "failed record "
        f"index={summary['original_record_index']} "
        f"iteration={summary['original_iteration']} "
        f"classification={summary['classification']}"
    )
    print(f"candidate: {json.dumps(summary['candidate'], sort_keys=True)}")
    for attempt in summary["attempts"]:
        result = attempt["result"]
        raw = result.get("raw")
        raw_outputs = None
        if isinstance(raw, dict):
            raw_outputs = raw.get("outputs")
            if raw_outputs is None and isinstance(raw.get("batpac"), dict):
                raw_outputs = raw["batpac"].get("outputs")
        print(
            f"  retry={attempt['retry_index']} "
            f"success={result.get('success')} "
            f"feasible={attempt['feasible']} "
            f"missing={attempt['missing_outputs']} "
            f"loss={attempt['penalized_loss']}"
        )
        print(f"  raw_outputs={json.dumps(_jsonable(raw_outputs), sort_keys=True)}")


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("records_jsonl")
    parser.add_argument("--output-jsonl", default=None)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--output-dir", default="outputs_mcp/diagnostics")
    return parser.parse_args()


# 목적: retry output path 기본값을 만든다.
# 입력: input JSONL path와 optional output path.
# 출력: output JSONL path.
def resolve_output_path(input_path: Path, raw_output_path: Any, output_dir: Any = "outputs_mcp/diagnostics") -> Path:
    """Resolve retry output path."""
    if raw_output_path:
        return Path(raw_output_path)
    return Path(output_dir) / f"{input_path.stem}_retries.jsonl"


# 목적: retry script command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: retry JSONL 파일과 stdout summary.
def main() -> None:
    """Retry failed BatPaC records."""
    args = parse_args()
    if args.retries <= 0:
        raise ValueError("--retries must be positive")

    input_path = Path(args.records_jsonl)
    output_path = resolve_output_path(input_path, args.output_jsonl, args.output_dir)
    failed_records = extract_failed_records(input_path)
    evaluator = make_timeout_batpac_evaluator(
        timeout_sec=args.timeout_sec,
        persistent_workbook_path=args.persistent_workbook_path,
        visible=args.visible,
    )

    retry_summaries = []
    for failed_record in failed_records:
        attempts = retry_failed_record(failed_record, args.retries, evaluator)
        classification = classify_retry_attempts(attempts)
        summary = {
            "original_record_index": failed_record.record_index,
            "original_iteration": failed_record.iteration,
            "candidate": failed_record.candidate,
            "condition_name": failed_record.condition_name,
            "original_missing_outputs": failed_record.missing_outputs,
            "classification": classification,
            "attempts": attempts,
        }
        retry_summaries.append(summary)

    save_retry_results(output_path, retry_summaries)
    counts = count_classifications(retry_summaries)
    print(f"input records: {input_path}")
    print(f"retry results saved: {output_path}")
    print(f"failed records found: {len(failed_records)}")
    print(f"reproducible: {counts[CLASS_REPRODUCIBLE_MISSING_COST]}")
    print(f"intermittent: {counts[CLASS_INTERMITTENT_MISSING_COST]}")
    print(f"recovered: {counts[CLASS_RECOVERED_SUCCESSFULLY]}")
    print(f"simulator_exception: {counts[CLASS_SIMULATOR_EXCEPTION]}")
    for summary in retry_summaries:
        print_retry_summary(summary)


if __name__ == "__main__":
    main()
