"""NOMAD 4/PyNomad MADS adapter for the BatPaC mixed-variable domain."""

from __future__ import annotations

import copy
import math
import multiprocessing
import random
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from optimizers.core.base import BaseOptimizer, EvaluationRecord, OptimizationResult
from optimizers.core.domain import (
    active_integer_bounds,
    apply_fixed_variables,
    make_condition,
    repair_candidate,
    sample_random_candidate,
    thickness_from_unit_interval,
    unit_interval_from_thickness,
    validate_candidate,
)
from optimizers.core.evaluation import (
    decompose_evaluator_status,
    is_feasible,
    normalized_constraint_violation,
    penalized_loss,
)

VARIABLE_ORDER = ("x_chem", "u_T", "x_S", "x_MPR", "x_R")


@dataclass
class NomadMadsOptimizer(BaseOptimizer):
    """Run official PyNomad MADS with a scalar penalized BatPaC objective."""

    method_name: str = "nomad_mads"

    def optimize(
        self,
        condition_name: str,
        budget: int,
        seed: int,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        fixed_variables: Optional[Dict[str, Any]] = None,
    ) -> OptimizationResult:
        if budget < 0:
            raise ValueError("budget must be non-negative")
        condition = make_condition(condition_name)
        fixed = dict(fixed_variables or {})
        _validate_fixed_variables(fixed)
        records: List[EvaluationRecord] = []
        tracker = _Tracker()
        evaluated: Dict[Tuple[Any, ...], EvaluationRecord] = {}

        if budget == 0:
            return tracker.result(self.method_name, condition_name, records, seed, budget, {})

        starts = [_prepare_candidate(item, fixed) for item in (initial_candidates or [])]
        if not starts:
            starts = [sample_random_candidate(random.Random(seed), fixed_variables=fixed)]

        # Evaluate every informed start once under the shared simulator budget.
        for candidate in starts:
            if len(records) >= budget:
                break
            record = self._evaluate(len(records), candidate, condition, "initial")
            records.append(record)
            evaluated[_candidate_key(candidate)] = record
            tracker.update(record)

        if len(records) >= budget or not _free_variables(fixed):
            return tracker.result(self.method_name, condition_name, records, seed, budget, {})

        _import_nomad()
        incumbent = min(records, key=lambda item: item.loss).candidate
        free = _free_variables(fixed)
        x0, lower, upper, input_types = _nomad_vectors(incumbent, fixed, free)

        def blackbox(point: Any) -> int:
            candidate = _candidate_from_point(point, fixed, free)
            key = _candidate_key(candidate)
            cached = evaluated.get(key)
            if cached is not None:
                point.setBBO(str(cached.loss).encode("UTF-8"))
                return 1
            if len(records) >= budget:
                point.setBBO(str(math.inf).encode("UTF-8"))
                return 0
            record = self._evaluate(len(records), candidate, condition, "nomad_mads")
            records.append(record)
            evaluated[key] = record
            tracker.update(record)
            point.setBBO(str(record.loss).encode("UTF-8"))
            return 1

        # Extra NOMAD evaluations cover cached x0/duplicate requests; the callback
        # remains the authoritative hard stop for expensive simulator calls.
        params = [
            "BB_OUTPUT_TYPE OBJ",
            f"BB_INPUT_TYPE ( {' '.join(input_types)} )",
            f"MAX_BB_EVAL {budget + len(evaluated) + 10}",
            f"SEED {int(seed)}",
            "DISPLAY_DEGREE 0",
            "DISPLAY_ALL_EVAL false",
        ]
        nomad_result = _optimize_in_fresh_process(blackbox, x0, lower, upper, params)
        metadata = {
            "nomad_result": nomad_result,
            "nomad_free_variables": free,
            "nomad_input_types": input_types,
            "penalized_objective": True,
        }
        return tracker.result(self.method_name, condition_name, records, seed, budget, metadata)

    def _evaluate(
        self,
        iteration: int,
        candidate: Dict[str, Any],
        condition: Dict[str, Any],
        source: str,
    ) -> EvaluationRecord:
        started = time.perf_counter()
        try:
            result = self.evaluator(copy.deepcopy(candidate), dict(condition))
        except Exception as exc:  # pragma: no cover - defensive simulator boundary
            result = _failure_result(exc)
        elapsed = time.perf_counter() - started
        return EvaluationRecord(
            iteration=iteration,
            candidate=copy.deepcopy(candidate),
            condition_name=str(condition["name"]),
            result=copy.deepcopy(result),
            feasible=is_feasible(result, condition),
            loss=penalized_loss(result, condition),
            violation=normalized_constraint_violation(result, condition),
            wall_time_sec=elapsed,
            metadata={"source": source, "optimizer": "PyNomad"},
        )


class _Point:
    """The coordinates of a NOMAD evaluation point, received from the NOMAD process."""

    def __init__(self, coords: Sequence[float]) -> None:
        self.coords = list(coords)
        self.bbo = b""

    def get_coord(self, index: int) -> float:
        return self.coords[index]

    def setBBO(self, value: bytes) -> None:
        self.bbo = value


def _optimize_in_fresh_process(
    blackbox: Any, x0: List[float], lower: List[float], upper: List[float], params: List[str]
) -> Any:
    """Run PyNomad.optimize in a new process, so every MADS run starts from a fresh NOMAD state.

    The NOMAD process sends each point's coordinates here, where `blackbox` evaluates it, and receives the objective
    and the evaluation flag. Returns the NOMAD result as JSON-compatible data.
    """
    context = multiprocessing.get_context("spawn")
    here, there = context.Pipe()
    process = context.Process(target=_nomad_process, args=(there, x0, lower, upper, params), daemon=True)
    process.start()
    there.close()
    try:
        while True:
            try:
                kind, payload = here.recv()
            except EOFError as exc:
                raise RuntimeError("NOMAD process ended without a result") from exc
            if kind == "done":
                return payload
            if kind == "error":
                raise RuntimeError(f"NOMAD process failed: {payload}")
            point = _Point(payload)
            flag = blackbox(point)
            here.send((point.bbo, flag))
    finally:
        process.join(timeout=60)
        if process.is_alive():
            process.terminate()
        here.close()


def _nomad_process(conn: Any, x0: List[float], lower: List[float], upper: List[float], params: List[str]) -> None:
    try:
        PyNomad = _import_nomad()

        def blackbox(point: Any) -> int:
            conn.send(("eval", [point.get_coord(index) for index in range(len(x0))]))
            bbo, flag = conn.recv()
            point.setBBO(bbo)
            return flag

        result = PyNomad.optimize(blackbox, x0, lower, upper, params)
        conn.send(("done", _jsonable_nomad_result(result)))
    except BaseException:
        conn.send(("error", traceback.format_exc()[-2000:]))
    finally:
        conn.close()


def _import_nomad() -> Any:
    try:
        import PyNomad  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "NOMAD/PyNomad is required for optimizer=mads. Install the official "
            "PyNomadBBO package (`python3 -m pip install PyNomadBBO`) or exclude MADS."
        ) from exc
    return PyNomad


def _validate_fixed_variables(fixed: Mapping[str, Any]) -> None:
    if not fixed:
        return
    integer_bounds = active_integer_bounds()
    probe = {"x_chem": "LFP", "x_T": 100.0, **{name: value[0] for name, value in integer_bounds.items()}}
    probe.update(fixed)
    validate_candidate(repair_candidate(probe))


def _free_variables(fixed: Mapping[str, Any]) -> List[str]:
    free: List[str] = []
    if "x_chem" not in fixed:
        free.append("x_chem")
    if "x_T" not in fixed:
        free.append("u_T")
    for name in ("x_S", "x_MPR", "x_R"):
        if name not in fixed:
            free.append(name)
    return free


def _nomad_vectors(
    candidate: Mapping[str, Any],
    fixed: Mapping[str, Any],
    free: Sequence[str],
) -> Tuple[List[float], List[float], List[float], List[str]]:
    prepared = _prepare_candidate(candidate, fixed)
    integer_bounds = active_integer_bounds()
    x0: List[float] = []
    lower: List[float] = []
    upper: List[float] = []
    input_types: List[str] = []
    for name in free:
        if name == "x_chem":
            x0.append(0.0 if prepared["x_chem"] == "LFP" else 1.0)
            lower.append(0.0)
            upper.append(1.0)
            input_types.append("I")
        elif name == "u_T":
            x0.append(unit_interval_from_thickness(prepared["x_chem"], prepared["x_T"]))
            lower.append(0.0)
            upper.append(1.0)
            input_types.append("R")
        else:
            bounds = integer_bounds[name]
            x0.append(float(prepared[name]))
            lower.append(float(bounds[0]))
            upper.append(float(bounds[1]))
            input_types.append("I")
    return x0, lower, upper, input_types


def _candidate_from_point(
    point: Any,
    fixed: Mapping[str, Any],
    free: Sequence[str],
) -> Dict[str, Any]:
    values = {name: point.get_coord(index) for index, name in enumerate(free)}
    candidate = dict(fixed)
    if "x_chem" in values:
        candidate["x_chem"] = "LFP" if int(round(float(values["x_chem"]))) <= 0 else "NMC811"
    chemistry = candidate.get("x_chem")
    if chemistry is None:
        raise ValueError("x_chem is required to decode NOMAD u_T")
    if "u_T" in values:
        unit = min(1.0, max(0.0, float(values["u_T"])))
        candidate["x_T"] = thickness_from_unit_interval(chemistry, unit)
    for name in ("x_S", "x_MPR", "x_R"):
        if name in values:
            candidate[name] = int(round(float(values[name])))
    return _prepare_candidate(candidate, fixed)


def _prepare_candidate(
    candidate: Mapping[str, Any], fixed: Mapping[str, Any]
) -> Dict[str, Any]:
    return repair_candidate(apply_fixed_variables(candidate, fixed))


def _candidate_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    prepared = repair_candidate(candidate)
    return (
        prepared["x_chem"],
        round(float(prepared["x_T"]), 10),
        prepared["x_S"],
        prepared["x_MPR"],
        prepared["x_R"],
    )


def _failure_result(exc: Exception) -> Dict[str, Any]:
    result = {
        "success": False,
        "cost": None,
        "P": None,
        "E": None,
        "V": None,
        "c": None,
        "L_bat": None,
        "W_bat": None,
        "raw": {"error": repr(exc)},
    }
    result.update(decompose_evaluator_status(result, simulator_call_success=False))
    return result


def _jsonable_nomad_result(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable_nomad_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_nomad_result(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass
class _Tracker:
    best_candidate: Optional[Dict[str, Any]] = None
    best_result: Optional[Dict[str, Any]] = None
    best_loss: float = math.inf
    best_feasible_candidate: Optional[Dict[str, Any]] = None
    best_feasible_result: Optional[Dict[str, Any]] = None
    best_feasible_cost: Optional[float] = None

    def update(self, record: EvaluationRecord) -> None:
        if record.loss < self.best_loss:
            self.best_loss = record.loss
            self.best_candidate = copy.deepcopy(record.candidate)
            self.best_result = copy.deepcopy(record.result)
        try:
            cost = float(record.result.get("cost"))
        except (TypeError, ValueError):
            cost = math.inf
        if record.feasible and math.isfinite(cost):
            if self.best_feasible_cost is None or cost < self.best_feasible_cost:
                self.best_feasible_cost = cost
                self.best_feasible_candidate = copy.deepcopy(record.candidate)
                self.best_feasible_result = copy.deepcopy(record.result)

    def result(
        self,
        method_name: str,
        condition_name: str,
        records: List[EvaluationRecord],
        seed: int,
        budget: int,
        extra_metadata: Mapping[str, Any],
    ) -> OptimizationResult:
        metadata = {
            "total_evaluations": len(records),
            "best_feasible_candidate": copy.deepcopy(self.best_feasible_candidate),
            "best_feasible_result": copy.deepcopy(self.best_feasible_result),
            **dict(extra_metadata),
        }
        return OptimizationResult(
            method_name=method_name,
            condition_name=condition_name,
            best_candidate=copy.deepcopy(self.best_candidate),
            best_result=copy.deepcopy(self.best_result),
            best_feasible_cost=self.best_feasible_cost,
            best_loss=self.best_loss,
            records=records,
            seed=seed,
            budget=budget,
            metadata=metadata,
        )
