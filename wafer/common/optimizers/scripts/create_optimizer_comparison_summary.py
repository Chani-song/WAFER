"""Create comparison summaries from existing optimizer benchmark outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

SUMMARY_COLUMNS = [
    "optimizer",
    "condition",
    "budget",
    "evaluations",
    "feasible_count",
    "feasible_rate",
    "failed_count",
    "missing_cost_count",
    "best_feasible_cost",
    "best_feasible_candidate",
    "total_runtime_sec",
    "mean_eval_time_sec",
    "evaluator_mode",
]

DEFAULT_OPTIMIZERS = ("random", "mixed_ga", "pattern", "axbo")
CONDITION_ORDER = ("s0", "s_cost", "s_vehicle", "s_space")
DEFAULT_EVALUATOR_MODE = "subprocess"


# 목적: optimizer별 summary 파일 경로를 명확하게 묶는다.
# 입력: optimizer 이름과 JSON/CSV summary 경로.
# 출력: summary loading에서 사용할 source 객체.
@dataclass(frozen=True)
class SummarySource:
    """Input paths for one optimizer summary."""

    optimizer: str
    json_path: Path
    csv_path: Path


# 목적: command line arguments를 파싱한다.
# 입력: 없음.
# 출력: argparse namespace.
def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-dir", default="outputs_mcp/benchmark_summaries")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--optimizers", nargs="+", default=list(DEFAULT_OPTIMIZERS))
    return parser.parse_args()


# 목적: optimizer 이름에서 expected summary 파일 경로를 만든다.
# 입력: output directory와 optimizer 이름.
# 출력: SummarySource 객체.
def make_summary_source(output_dir: Path, optimizer: str) -> SummarySource:
    """Build expected summary paths."""
    prefix = output_dir / f"condition_smoke_{optimizer}_summary"
    return SummarySource(
        optimizer=optimizer,
        json_path=prefix.with_suffix(".json"),
        csv_path=prefix.with_suffix(".csv"),
    )


# 목적: 여러 optimizer summary를 하나의 normalized row 리스트로 합친다.
# 입력: output directory와 optimizer 이름 목록.
# 출력: comparison CSV/Markdown에 사용할 row 리스트.
def collect_summary_rows(output_dir: Path, optimizers: Sequence[str]) -> List[Dict[str, str]]:
    """Collect normalized summary rows."""
    rows: List[Dict[str, str]] = []
    for optimizer in optimizers:
        source = make_summary_source(output_dir, optimizer)
        rows.extend(load_optimizer_rows(source))
    return sorted(rows, key=_row_sort_key)


# 목적: optimizer 하나의 summary 파일을 읽고 normalized row로 변환한다.
# 입력: SummarySource.
# 출력: normalized row 리스트.
def load_optimizer_rows(source: SummarySource) -> List[Dict[str, str]]:
    """Load one optimizer summary."""
    if source.json_path.exists():
        raw_rows = load_json_rows(source.json_path)
    elif source.csv_path.exists():
        raw_rows = load_csv_rows(source.csv_path)
    else:
        raise FileNotFoundError(f"Missing summary file for optimizer {source.optimizer}: {source.json_path}")

    return [normalize_row(row, default_optimizer=source.optimizer) for row in raw_rows]


# 목적: JSON summary 파일을 읽는다.
# 입력: JSON summary 경로.
# 출력: raw summary row 리스트.
def load_json_rows(path: Path) -> List[Mapping[str, Any]]:
    """Load summary rows from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {path}")
    return [_ensure_mapping(item, path) for item in data]


# 목적: CSV summary 파일을 읽는다.
# 입력: CSV summary 경로.
# 출력: raw summary row 리스트.
def load_csv_rows(path: Path) -> List[Mapping[str, Any]]:
    """Load summary rows from CSV."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# 목적: summary row schema 차이를 comparison용 schema로 정규화한다.
# 입력: raw summary row와 파일명에서 얻은 default optimizer.
# 출력: SUMMARY_COLUMNS 순서를 따르는 문자열 row.
def normalize_row(row: Mapping[str, Any], default_optimizer: str) -> Dict[str, str]:
    """Normalize one summary row."""
    normalized: Dict[str, str] = {}
    for column in SUMMARY_COLUMNS:
        if column == "optimizer":
            normalized[column] = _text_or_default(row.get(column), default_optimizer)
        elif column == "evaluator_mode":
            normalized[column] = _text_or_default(row.get(column), DEFAULT_EVALUATOR_MODE)
        elif column == "best_feasible_candidate":
            normalized[column] = _candidate_to_text(row.get(column))
        else:
            normalized[column] = _value_to_text(row.get(column))
    return normalized


# 목적: normalized rows를 comparison CSV로 저장한다.
# 입력: output path와 row 리스트.
# 출력: 없음.
def write_comparison_csv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    """Write comparison CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


# 목적: normalized rows를 condition별 Markdown table로 변환한다.
# 입력: row 리스트.
# 출력: Markdown 문자열.
def render_markdown(rows: Sequence[Mapping[str, str]]) -> str:
    """Render grouped Markdown summary."""
    lines = ["# Optimizer Comparison Summary", ""]
    for condition in _ordered_conditions(rows):
        condition_rows = [row for row in rows if row.get("condition") == condition]
        if not condition_rows:
            continue
        lines.extend(
            [
                f"## {condition}",
                "",
                "| optimizer | budget | evaluations | feasible_count | feasible_rate | failed_count | missing_cost_count | best_feasible_cost | mean_eval_time_sec | total_runtime_sec | evaluator_mode | best_feasible_candidate |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for row in condition_rows:
            lines.append(_markdown_row(row))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# 목적: Markdown summary를 파일로 저장한다.
# 입력: output path와 row 리스트.
# 출력: 없음.
def write_markdown(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    """Write comparison Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(rows), encoding="utf-8")


# 목적: comparison summary 생성 entry point를 실행한다.
# 입력: command line arguments.
# 출력: comparison CSV와 Markdown 파일.
def main() -> None:
    """Create optimizer comparison summaries."""
    args = parse_args()
    summary_dir = Path(args.summary_dir)
    output_dir = Path(args.output_dir) if args.output_dir else summary_dir
    rows = collect_summary_rows(summary_dir, args.optimizers)

    csv_path = output_dir / "optimizer_comparison_summary.csv"
    markdown_path = output_dir / "optimizer_comparison_summary.md"
    write_comparison_csv(csv_path, rows)
    write_markdown(markdown_path, rows)

    print(f"comparison CSV saved: {csv_path}")
    print(f"comparison Markdown saved: {markdown_path}")
    print(f"rows: {len(rows)}")


# 목적: JSON item이 mapping인지 검증한다.
# 입력: JSON item과 source path.
# 출력: mapping item.
def _ensure_mapping(item: Any, path: Path) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"Expected object rows in {path}")
    return item


# 목적: condition과 optimizer의 stable sorting key를 만든다.
# 입력: normalized row.
# 출력: sort key tuple.
def _row_sort_key(row: Mapping[str, str]) -> tuple[int, str, int, str]:
    condition = row.get("condition", "")
    optimizer = row.get("optimizer", "")
    return (
        _ordered_index(CONDITION_ORDER, condition),
        condition,
        _ordered_index(DEFAULT_OPTIMIZERS, optimizer),
        optimizer,
    )


# 목적: Markdown에 표시할 condition 순서를 정한다.
# 입력: normalized rows.
# 출력: ordered condition 리스트.
def _ordered_conditions(rows: Sequence[Mapping[str, str]]) -> List[str]:
    conditions = {row.get("condition", "") for row in rows if row.get("condition")}
    return sorted(conditions, key=lambda condition: (_ordered_index(CONDITION_ORDER, condition), condition))


# 목적: known order에 없는 값도 안정적으로 뒤에 정렬한다.
# 입력: order tuple과 value.
# 출력: 정렬 index.
def _ordered_index(order: Sequence[str], value: str) -> int:
    try:
        return order.index(value)
    except ValueError:
        return len(order)


# 목적: Markdown table row 하나를 만든다.
# 입력: normalized row.
# 출력: Markdown table row 문자열.
def _markdown_row(row: Mapping[str, str]) -> str:
    cells = [
        row.get("optimizer", ""),
        row.get("budget", ""),
        row.get("evaluations", ""),
        row.get("feasible_count", ""),
        _format_float(row.get("feasible_rate", "")),
        row.get("failed_count", ""),
        row.get("missing_cost_count", ""),
        _format_float(row.get("best_feasible_cost", "")),
        _format_float(row.get("mean_eval_time_sec", "")),
        _format_float(row.get("total_runtime_sec", "")),
        row.get("evaluator_mode", ""),
        row.get("best_feasible_candidate", ""),
    ]
    return "| " + " | ".join(_escape_markdown_cell(cell) for cell in cells) + " |"


# 목적: table cell 안의 pipe 문자를 escape한다.
# 입력: cell 문자열.
# 출력: Markdown-safe cell 문자열.
def _escape_markdown_cell(value: str) -> str:
    return str(value).replace("|", "\\|")


# 목적: best feasible candidate를 compact JSON 문자열로 정규화한다.
# 입력: dict, list, JSON string, None 중 하나.
# 출력: CSV/Markdown에 넣을 candidate 문자열.
def _candidate_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "null"}:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# 목적: scalar summary 값을 CSV용 문자열로 정규화한다.
# 입력: 임의 값.
# 출력: 빈 문자열 또는 문자열.
def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


# 목적: 빈 값이면 default 문자열을 사용한다.
# 입력: 임의 값과 default 문자열.
# 출력: normalized 문자열.
def _text_or_default(value: Any, default: str) -> str:
    text = _value_to_text(value).strip()
    return text if text else default


# 목적: Markdown용 숫자를 compact하게 포맷한다.
# 입력: 숫자 문자열.
# 출력: 소수점 여섯 자리 이하의 compact 문자열.
def _format_float(value: str) -> str:
    if not value:
        return ""
    try:
        numeric = float(value)
    except ValueError:
        return value
    if not math.isfinite(numeric):
        return value
    return f"{numeric:.6f}".rstrip("0").rstrip(".")


if __name__ == "__main__":
    main()
