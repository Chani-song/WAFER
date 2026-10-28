"""Utilities for optimizer JSONL records."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .evaluation import REQUIRED_RESULT_KEYS

CLASS_REPRODUCIBLE_MISSING_COST = "reproducible_missing_cost"
CLASS_INTERMITTENT_MISSING_COST = "intermittent_missing_cost"
CLASS_SIMULATOR_EXCEPTION = "simulator_exception"
CLASS_RECOVERED_SUCCESSFULLY = "recovered_successfully"


# 목적: 실패한 원본 evaluation record의 핵심 정보를 담는다.
# 입력: JSONL record에서 추출한 candidate, condition, result metadata.
# 출력: retry script가 사용할 failed record 객체.
@dataclass
class FailedEvaluationRecord:
    """Failed record extracted from JSONL."""

    record_index: int
    iteration: Optional[int]
    candidate: Dict[str, Any]
    condition_name: str
    result: Dict[str, Any]
    missing_outputs: List[str]
    record: Dict[str, Any]


# 목적: JSONL record 파일을 읽어 dict 리스트로 변환한다.
# 입력: JSONL 파일 경로.
# 출력: record dict 리스트.
def read_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    """Read optimizer records from JSONL."""
    records: List[Dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return records


# 목적: JSONL records에서 retry 대상 실패 record만 추출한다.
# 입력: JSONL 파일 경로.
# 출력: FailedEvaluationRecord 리스트.
def extract_failed_records(path: Path) -> List[FailedEvaluationRecord]:
    """Extract failed optimizer records from JSONL."""
    failed: List[FailedEvaluationRecord] = []
    for index, record in enumerate(read_jsonl_records(path)):
        result = _as_dict(record.get("result"))
        missing_outputs = missing_required_outputs(result)
        if result.get("success", False) and not missing_outputs:
            continue

        candidate = _as_dict(record.get("candidate"))
        condition_name = str(record.get("condition_name") or "s0")
        failed.append(
            FailedEvaluationRecord(
                record_index=index,
                iteration=_optional_int(record.get("iteration")),
                candidate=dict(candidate),
                condition_name=condition_name,
                result=dict(result),
                missing_outputs=missing_outputs,
                record=record,
            )
        )
    return failed


# 목적: evaluator result에서 required output 누락 여부를 확인한다.
# 입력: evaluator result mapping.
# 출력: 누락되었거나 finite 숫자가 아닌 required output 이름 리스트.
def missing_required_outputs(result: Mapping[str, Any]) -> List[str]:
    """Return missing required output names."""
    missing = []
    for key in REQUIRED_RESULT_KEYS:
        if _finite_float_or_none(result.get(key)) is None:
            missing.append(key)
    return missing


# 목적: retry attempts의 결과를 재현성 기준으로 분류한다.
# 입력: retry attempt dict 리스트.
# 출력: classification 문자열.
def classify_retry_attempts(attempts: List[Dict[str, Any]]) -> str:
    """Classify retry results."""
    statuses = [classify_attempt_status(_as_dict(attempt.get("result"))) for attempt in attempts]
    if not statuses:
        return CLASS_SIMULATOR_EXCEPTION
    if all(status == "success" for status in statuses):
        return CLASS_RECOVERED_SUCCESSFULLY
    if all(status == "missing_cost" for status in statuses):
        return CLASS_REPRODUCIBLE_MISSING_COST
    if "missing_cost" in statuses and "success" in statuses:
        return CLASS_INTERMITTENT_MISSING_COST
    return CLASS_SIMULATOR_EXCEPTION


# 목적: 단일 retry result의 상태를 분류한다.
# 입력: evaluator result mapping.
# 출력: success, missing_cost, simulator_exception, other_incomplete 중 하나.
def classify_attempt_status(result: Mapping[str, Any]) -> str:
    """Classify one retry attempt."""
    if result_has_simulator_exception(result):
        return "simulator_exception"
    if _finite_float_or_none(result.get("cost")) is None:
        return "missing_cost"
    if result.get("success", False) and not missing_required_outputs(result):
        return "success"
    return "other_incomplete"


# 목적: evaluator result가 Excel/BatPaC 실행 예외를 담는지 확인한다.
# 입력: evaluator result mapping.
# 출력: simulator exception 여부.
def result_has_simulator_exception(result: Mapping[str, Any]) -> bool:
    """Return whether result contains a simulator exception."""
    raw = result.get("raw")
    if isinstance(raw, Mapping) and "error" in raw:
        return True
    return False


# 목적: 임의 값을 finite float 또는 None으로 정규화한다.
# 입력: 변환할 값.
# 출력: finite float 또는 None.
def _finite_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


# 목적: dict가 아닌 값을 안전하게 빈 dict로 처리한다.
# 입력: 임의 객체.
# 출력: dict 객체.
def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


# 목적: JSON에서 읽은 iteration 값을 int 또는 None으로 정규화한다.
# 입력: 임의 객체.
# 출력: int 또는 None.
def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
