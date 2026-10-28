"""JSONL trace recorder for LLM method experiments."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


class TraceRecorder:
    """Record prompts, raw responses, and parsed strategy steps."""

    # 목적: trace output directory와 JSONL path를 준비한다.
    # 입력: output directory와 trace filename.
    # 출력: TraceRecorder instance.
    def __init__(self, output_dir: Path, trace_filename: str = "trace.jsonl") -> None:
        self.output_dir = Path(output_dir)
        self.prompt_dir = self.output_dir / "prompts"
        self.raw_dir = self.output_dir / "raw_responses"
        self.trace_path = self.output_dir / trace_filename
        self.events_path = self.output_dir / "experiment_events.jsonl"
        self.manifest_path = self.output_dir / "experiment_manifest.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def initialize_run(self, metadata: Mapping[str, Any]) -> Dict[str, Any]:
        """Create the immutable run header used by all experiment methods."""
        payload = {
            "schema_version": "jmd_experiment_v1",
            "experiment_id": metadata.get("experiment_id") or self.output_dir.name,
            "started_at": _utc_now(),
            "code_version": _code_version(),
            "python": platform.python_version(),
            "hostname": platform.node(),
            "pid": os.getpid(),
            **dict(metadata),
            "final_status": "running",
            "failure_reason": None,
        }
        self.manifest_path.write_text(
            json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8"
        )
        self.record_event("run_started", payload)
        return payload

    def record_event(self, event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Append an event without mutating or deleting earlier evidence."""
        row = {"timestamp": _utc_now(), "event_type": event_type, **dict(payload)}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")
        return row

    def finalize_run(
        self,
        status: str,
        failure_reason: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Finalize the manifest while retaining the append-only terminal event."""
        current: Dict[str, Any] = {}
        if self.manifest_path.exists():
            current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        current.update(dict(metadata or {}))
        current.update(
            {
                "finished_at": _utc_now(),
                "final_status": status,
                "failure_reason": failure_reason,
            }
        )
        self.manifest_path.write_text(
            json.dumps(_jsonable(current), indent=2, sort_keys=True), encoding="utf-8"
        )
        self.record_event(
            "run_finished",
            {"final_status": status, "failure_reason": failure_reason, **dict(metadata or {})},
        )
        summary_path = self.output_dir / "experiment_summary.csv"
        columns = (
            "experiment_id",
            "started_at",
            "finished_at",
            "final_status",
            "failure_reason",
            "scenario",
            "model_name",
            "semantic_context_level",
            "reoptimization_method",
            "numerical_optimizer",
            "random_seed",
            "llm_seed",
            "optimizer_seed",
            "temperature",
            "top_p",
            "evaluation_budget",
            "llm_http_request_count",
            "llm_prompt_tokens",
            "llm_completion_tokens",
            "llm_total_tokens",
            "llm_request_latency_sec",
            "llm_parse_retry_generation_count",
            "batpac_evaluation_count",
            "evaluations_to_first_feasible",
            "numerical_optimizer_runtime_sec",
            "simulator_runtime_sec",
            "optimizer_overhead_sec",
            "total_wall_clock_sec",
        )
        with summary_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerow({key: _csv_value(current.get(key)) for key in columns})
        return current

    # 목적: 한 LLM strategy step의 prompt/raw/parsed/eval 정보를 저장한다.
    # 입력: trace metadata, messages, raw response, parsed output, optional candidate/result.
    # 출력: JSONL row dict.
    def record_step(
        self,
        run_id: str,
        scenario: str,
        strategy: str,
        optimizer: Optional[str],
        semantic_level: str,
        model_key: str,
        iteration: int,
        messages: Sequence[Mapping[str, str]],
        raw_response: Any,
        parsed_output: Any,
        candidate: Optional[Mapping[str, Any]] = None,
        result: Optional[Mapping[str, Any]] = None,
        feasible: Optional[bool] = None,
        cost: Optional[float] = None,
        llm_time_sec: float = 0.0,
        sim_time_sec: float = 0.0,
        optimizer_overhead_sec: float = 0.0,
        parse_error: Optional[str] = None,
        retry_attempt: int = 0,
        raw_reasoning: Optional[str] = None,
        generation_metadata: Optional[Mapping[str, Any]] = None,
        generation_failure_kind: Optional[str] = None,
        event_type: str = "llm_step",
        tool_call: Optional[Mapping[str, Any]] = None,
        tool_result: Optional[Mapping[str, Any]] = None,
        observations_used: Optional[Sequence[Any]] = None,
        reported_observations_used: Optional[Sequence[Any]] = None,
        resolved_observations_used: Optional[Sequence[Any]] = None,
        forbidden_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
        matching_forbidden_candidate_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record one strategy step."""
        artifact_stem = f"{run_id}_{strategy}_{iteration}"
        if retry_attempt:
            artifact_stem += f"_retry_{retry_attempt}"
        prompt_path = self._write_json(
            self.prompt_dir / f"{artifact_stem}_prompt.json",
            [dict(message) for message in messages],
        )
        raw_path = self._write_json(
            self.raw_dir / f"{artifact_stem}_raw.json",
            raw_response,
        )
        row = {
            "run_id": run_id,
            "scenario": scenario,
            "strategy": strategy,
            "optimizer": optimizer,
            "semantic_level": semantic_level,
            "model_key": model_key,
            "iteration": iteration,
            "prompt_path": str(prompt_path),
            "raw_response_path": str(raw_path),
            "parsed_output": _jsonable(parsed_output),
            "candidate": _jsonable(candidate),
            "result": _jsonable(result),
            "feasible": feasible,
            "cost": cost,
            "llm_time_sec": llm_time_sec,
            "sim_time_sec": sim_time_sec,
            "optimizer_overhead_sec": optimizer_overhead_sec,
            "parse_error": parse_error,
            "retry_attempt": retry_attempt,
            "raw_reasoning": raw_reasoning,
            "generation_metadata": _jsonable(generation_metadata),
            "generation_failure_kind": generation_failure_kind,
            "event_type": event_type,
            "tool_call": _jsonable(tool_call),
            "tool_result": _jsonable(tool_result),
            "observations_used": _jsonable(observations_used),
            "reported_observations_used": _jsonable(reported_observations_used),
            "resolved_observations_used": _jsonable(resolved_observations_used),
            "forbidden_candidates": _jsonable(forbidden_candidates),
            "matching_forbidden_candidate_index": matching_forbidden_candidate_index,
            "timestamp": _utc_now(),
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")
        return row

    # 목적: JSON artifact를 저장한다.
    def _write_json(self, path: Path, value: Any) -> Path:
        path = _unique_path(path)
        path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True), encoding="utf-8")
        return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_version() -> Dict[str, Any]:
    """Return a useful identifier for both Git and copied server trees."""
    try:
        root = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return {"kind": "git", "commit": commit, "dirty": dirty}
    except Exception:
        stat = Path(__file__).stat()
        return {
            "kind": "filesystem_snapshot",
            "recorder_mtime_ns": stat.st_mtime_ns,
            "note": "No readable Git metadata; archive/hash provenance is required.",
        }


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_jsonable(value), sort_keys=True)
    return value

def _unique_path(path: Path) -> Path:
    """Avoid overwriting earlier retry artifacts for the same logical step."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


# 목적: trace에 저장 가능한 JSON-compatible 값으로 변환한다.
# 입력: 임의 value.
# 출력: JSON-compatible value.
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
