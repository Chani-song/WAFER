"""Run a minimal real BatPaC random-search smoke test."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from optimizers.core.batpac_adapter import (
    DEFAULT_PERSISTENT_WORKBOOK_PATH,
    _load_batpac_functions,
    make_batpac_evaluator,
)
from optimizers.core.evaluation import decompose_evaluator_status
from optimizers.random_search import RandomSearchOptimizer

DEFAULT_INITIAL_CANDIDATE: Dict[str, Any] = {
    "x_chem": "NMC811",
    "x_T": 100.0,
    "x_S": 20,
    "x_MPR": 4,
    "x_R": 4,
}


# 목적: BatPaC public output catalog를 파일로 저장한다.
# 입력: 저장할 JSON 경로.
# 출력: batpac_get_available_outputs()가 반환한 catalog dict.
def save_available_outputs(path: Path) -> Dict[str, Any]:
    """Save the BatPaC output catalog."""
    batpac_functions = _load_batpac_functions()
    catalog = batpac_functions["batpac_get_available_outputs"]()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(catalog), indent=2, sort_keys=True), encoding="utf-8")
    return catalog


# 목적: optimization records를 JSONL로 저장한다.
# 입력: 저장 경로와 EvaluationRecord iterable.
# 출력: 없음.
def save_records_jsonl(path: Path, records: Iterable[Any]) -> None:
    """Save evaluation records as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = {
                "iteration": record.iteration,
                "candidate": record.candidate,
                "condition_name": record.condition_name,
                "result": record.result,
                "feasible": record.feasible,
                "loss": record.loss,
                "violation": record.violation,
                "wall_time_sec": record.wall_time_sec,
                "metadata": record.metadata,
            }
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


# 목적: real BatPaC evaluator에 evaluation별 timeout을 적용한다.
# 입력: timeout 초, stable workbook 경로, Excel 표시 여부, subprocess 사용 여부.
# 출력: timeout 실패 또는 direct real BatPaC evaluator callable.
def make_timeout_batpac_evaluator(
    timeout_sec: float,
    persistent_workbook_path: str,
    visible: bool,
    use_subprocess_timeout: bool = True,
    runtime_environment: Dict[str, str] | None = None,
    output_dir: str | None = None,
):
    """Create a timeout-protected BatPaC evaluator."""
    if not use_subprocess_timeout:
        if runtime_environment:
            os.environ.update({str(key): str(value) for key, value in runtime_environment.items()})
        options = {
            "persistent_workbook_path": persistent_workbook_path,
            "visible": visible,
        }
        if output_dir is not None:
            options["output_dir"] = output_dir
        return make_batpac_evaluator(**options)

    def evaluator(candidate: Dict[str, Any], condition: Dict[str, Any]) -> Dict[str, Any]:
        queue = _multiprocessing_context().Queue()
        process = _multiprocessing_context().Process(
            target=_evaluate_batpac_in_child,
            args=(candidate, condition, queue, persistent_workbook_path, visible, runtime_environment, output_dir),
        )
        process.start()
        process.join(timeout_sec)
        if process.is_alive():
            process.terminate()
            process.join(5)
            return _failure_result(
                f"BatPaC evaluation timed out after {timeout_sec} seconds",
                candidate,
                condition,
            )
        if queue.empty():
            return _failure_result(
                f"BatPaC evaluation child exited without returning a result; exitcode={process.exitcode}",
                candidate,
                condition,
            )

        status, payload = queue.get()
        if status == "success":
            return payload
        return _failure_result(str(payload), candidate, condition)

    return evaluator


# 목적: real BatPaC evaluator 실행 mode 이름을 일관되게 만든다.
# 입력: subprocess timeout 사용 여부.
# 출력: "subprocess" 또는 "direct" 문자열.
def batpac_evaluator_mode(use_subprocess_timeout: bool) -> str:
    """Return the real BatPaC evaluator mode name."""
    return "subprocess" if use_subprocess_timeout else "direct"


# 목적: child process 안에서 real BatPaC evaluator를 실행한다.
# 입력: candidate, condition, multiprocessing queue, runtime options.
# 출력: queue에 success/result 또는 error message를 넣는다.
def _evaluate_batpac_in_child(
    candidate: Dict[str, Any],
    condition: Dict[str, Any],
    queue: Any,
    persistent_workbook_path: str,
    visible: bool,
    runtime_environment: Dict[str, str] | None = None,
    output_dir: str | None = None,
) -> None:
    try:
        if runtime_environment:
            os.environ.update({str(key): str(value) for key, value in runtime_environment.items()})
        options = {
            "persistent_workbook_path": persistent_workbook_path,
            "visible": visible,
        }
        if output_dir is not None:
            options["output_dir"] = output_dir
        evaluator = make_batpac_evaluator(**options)
        queue.put(("success", evaluator(candidate, condition)))
    except Exception as exc:
        queue.put(("error", repr(exc)))


# 목적: timeout, import, child crash를 expected evaluator schema로 변환한다.
# 입력: error message, candidate, condition.
# 출력: failed evaluator result dict.
def _failure_result(message: str, candidate: Dict[str, Any], condition: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "success": False,
        "cost": None,
        "total_pack_cost": None,
        "P": None,
        "E": None,
        "V": None,
        "c": None,
        "L_bat": None,
        "W_bat": None,
        "raw": {
            "error": message,
            "candidate": candidate,
            "condition": condition,
        },
    }
    result.update(decompose_evaluator_status(result, simulator_call_success=False))
    return result


# 목적: 실패한 evaluation의 error message를 사람이 읽기 쉽게 추출한다.
# 입력: evaluator result dict.
# 출력: error message 문자열.
def extract_failure_message(result: Dict[str, Any]) -> str:
    """Extract a failure message from an evaluator result."""
    raw = result.get("raw")
    if isinstance(raw, dict):
        if "error" in raw:
            return str(raw["error"])
        missing = raw.get("missing_output_keys")
        if missing:
            return f"missing output keys: {missing}"
        batpac = raw.get("batpac")
        if isinstance(batpac, dict):
            return json.dumps(_jsonable(batpac), sort_keys=True)
    return json.dumps(_jsonable(result), sort_keys=True)


# 목적: available output catalog의 핵심 이름들을 stdout에 출력한다.
# 입력: catalog dict와 저장 경로.
# 출력: 없음.
def print_available_output_summary(catalog: Dict[str, Any], path: Path) -> None:
    """Print a compact output catalog summary."""
    print(f"available output catalog saved: {path}")
    print(f"available output count: {catalog.get('count')}")
    print("friendly aliases:")
    for item in catalog.get("friendly_aliases", []):
        print(f"  - {item.get('name')} -> {item.get('attribute')}")
    print("legacy names:")
    for item in catalog.get("legacy_names", []):
        print(f"  - {item.get('name')} -> {item.get('attribute')}")


# 목적: real BatPaC smoke 결과와 실패 내역을 stdout에 출력한다.
# 입력: OptimizationResult.
# 출력: 없음.
def print_smoke_summary(result: Any) -> None:
    """Print the real smoke result summary."""
    failed_records = [record for record in result.records if not record.result.get("success", False)]
    feasible_count = sum(1 for record in result.records if record.feasible)

    print(f"number of evaluations: {len(result.records)}")
    print(f"number of feasible evaluations: {feasible_count}")
    print(f"best feasible candidate: {json.dumps(result.best_feasible_candidate, sort_keys=True)}")
    print(f"best feasible cost: {result.best_feasible_cost}")

    if not failed_records:
        print("failed evaluations: none")
        return

    print("failed evaluations:")
    for record in failed_records:
        print(f"  - iteration {record.iteration}: {extract_failure_message(record.result)}")


# 목적: 임의 객체를 JSON 저장 가능한 형태로 변환한다.
# 입력: JSON으로 저장할 객체.
# 출력: JSON-compatible 객체.
def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# 목적: macOS에서 Excel child process를 parent와 분리해 timeout 제어를 가능하게 한다.
# 입력: 없음.
# 출력: multiprocessing context.
def _multiprocessing_context() -> Any:
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return multiprocessing.get_context()


# 목적: real BatPaC smoke command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: output catalog, JSONL path, optimization summary를 stdout에 출력한다.
def main() -> None:
    """Run the real BatPaC smoke script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="s0")
    parser.add_argument("--budget", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--no-initial-candidate", action="store_true")
    parser.add_argument(
        "--available-outputs-json",
        default="outputs_mcp/batpac_outputs/batpac_available_outputs.json",
    )
    parser.add_argument(
        "--records-jsonl",
        default="outputs_mcp/smoke_records/real_batpac_random_search_records.jsonl",
    )
    args = parser.parse_args()

    available_outputs_path = Path(args.available_outputs_json)
    records_path = Path(args.records_jsonl)

    catalog = save_available_outputs(available_outputs_path)
    print_available_output_summary(catalog, available_outputs_path)

    optimizer = RandomSearchOptimizer(
        evaluator=make_timeout_batpac_evaluator(
            timeout_sec=args.timeout_sec,
            persistent_workbook_path=args.persistent_workbook_path,
            visible=args.visible,
        )
    )
    result = optimizer.optimize(
        condition_name=args.condition,
        budget=args.budget,
        seed=args.seed,
        initial_candidates=None if args.no_initial_candidate else [DEFAULT_INITIAL_CANDIDATE],
    )
    save_records_jsonl(records_path, result.records)

    print(f"evaluation records saved: {records_path}")
    print_smoke_summary(result)


if __name__ == "__main__":
    main()
