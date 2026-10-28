"""Debug one real BatPaC candidate and print mapping details."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from optimizers.core.batpac_adapter import DEFAULT_PERSISTENT_WORKBOOK_PATH
from optimizers.core.domain import make_condition, repair_candidate
from optimizers.core.evaluation import (
    compute_constraint_violations,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)
from optimizers.scripts.run_real_batpac_smoke import (
    _jsonable,
    make_timeout_batpac_evaluator,
    save_available_outputs,
)

DEFAULT_DEBUG_CANDIDATE: Dict[str, Any] = {
    "x_chem": "NMC811",
    "x_T": 100.0,
    "x_S": 20,
    "x_MPR": 4,
    "x_R": 4,
}


# 목적: command line arguments에서 candidate dict를 만든다.
# 입력: argparse namespace.
# 출력: repair된 candidate dict.
def candidate_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Build a candidate from CLI arguments."""
    candidate = {
        "x_chem": args.x_chem,
        "x_T": args.x_T,
        "x_S": args.x_S,
        "x_MPR": args.x_MPR,
        "x_R": args.x_R,
    }
    return repair_candidate(candidate)


# 목적: available BatPaC output 이름 전체를 출력한다.
# 입력: batpac_get_available_outputs() catalog.
# 출력: 없음.
def print_available_output_names(catalog: Dict[str, Any]) -> None:
    """Print all accepted BatPaC output names."""
    print("available BatPaC output names:")
    for name in catalog.get("accepted_output_names", []):
        print(f"  - {name}")


# 목적: raw BatPaC outputs와 normalized evaluator result를 출력한다.
# 입력: candidate, condition, evaluator result.
# 출력: 없음.
def print_debug_result(
    candidate: Dict[str, Any],
    condition: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    """Print raw and normalized debug output."""
    raw = result.get("raw")
    raw_outputs = raw.get("outputs") if isinstance(raw, dict) else None
    violations = compute_constraint_violations(result, condition)
    feasible = is_feasible(result, condition)
    violation = normalized_constraint_violation(result, condition)
    loss = penalized_loss(result, condition)

    print("candidate:")
    print(json.dumps(_jsonable(candidate), indent=2, sort_keys=True))
    print("raw BatPaC outputs:")
    print(json.dumps(_jsonable(raw_outputs), indent=2, sort_keys=True))
    print("normalized evaluator result:")
    print(
        json.dumps(
            _jsonable(
                {
                    "success": result.get("success"),
                    "cost_per_kWh": result.get("cost"),
                    "total_pack_cost": result.get("total_pack_cost"),
                    "P": result.get("P"),
                    "E": result.get("E"),
                    "V": result.get("V"),
                    "c": result.get("c"),
                    "L_bat": result.get("L_bat"),
                    "W_bat": result.get("W_bat"),
                    "feasibility": feasible,
                    "constraint_violations": violations,
                    "normalized_constraint_violation": violation,
                    "penalized_loss": loss,
                }
            ),
            indent=2,
            sort_keys=True,
        )
    )


# 목적: debug 결과 전체를 JSON 파일로 저장한다.
# 입력: 저장 경로, candidate, condition, result.
# 출력: 없음.
def save_debug_json(
    path: Path,
    candidate: Dict[str, Any],
    condition: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    """Save debug output as JSON."""
    payload = {
        "candidate": candidate,
        "condition": condition,
        "result": result,
        "feasible": is_feasible(result, condition),
        "constraint_violations": compute_constraint_violations(result, condition),
        "normalized_constraint_violation": normalized_constraint_violation(result, condition),
        "penalized_loss": penalized_loss(result, condition),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


# 목적: real BatPaC candidate debug command line entry point를 실행한다.
# 입력: command line arguments.
# 출력: available outputs, raw outputs, normalized result를 stdout과 JSON 파일로 남긴다.
def main() -> None:
    """Run one real BatPaC candidate debug evaluation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="s0")
    parser.add_argument("--x-chem", default=DEFAULT_DEBUG_CANDIDATE["x_chem"])
    parser.add_argument("--x-T", type=float, default=DEFAULT_DEBUG_CANDIDATE["x_T"])
    parser.add_argument("--x-S", type=int, default=DEFAULT_DEBUG_CANDIDATE["x_S"])
    parser.add_argument("--x-MPR", type=int, default=DEFAULT_DEBUG_CANDIDATE["x_MPR"])
    parser.add_argument("--x-R", type=int, default=DEFAULT_DEBUG_CANDIDATE["x_R"])
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--persistent-workbook-path", default=DEFAULT_PERSISTENT_WORKBOOK_PATH)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument(
        "--available-outputs-json",
        default="outputs_mcp/batpac_outputs/batpac_available_outputs.json",
    )
    parser.add_argument(
        "--debug-json",
        default="outputs_mcp/diagnostics/real_batpac_debug_candidate.json",
    )
    args = parser.parse_args()

    available_outputs_path = Path(args.available_outputs_json)
    debug_path = Path(args.debug_json)
    catalog = save_available_outputs(available_outputs_path)
    print(f"available output catalog saved: {available_outputs_path}")
    print_available_output_names(catalog)

    candidate = candidate_from_args(args)
    condition = make_condition(args.condition)
    evaluator = make_timeout_batpac_evaluator(
        timeout_sec=args.timeout_sec,
        persistent_workbook_path=args.persistent_workbook_path,
        visible=args.visible,
    )
    result = evaluator(candidate, condition)
    print_debug_result(candidate, condition, result)
    save_debug_json(debug_path, candidate, condition, result)
    print(f"debug result saved: {debug_path}")


if __name__ == "__main__":
    main()
