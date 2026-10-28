"""Deterministic mock LLM for dry runs: schema-valid responses, no network.

It answers the three structured outputs the strategies request (WarmStartOutput,
VariableFixingOutput, ReActAction) with in-bounds candidates drawn from a seeded
generator. ReAct candidates are never repeated, so a run can spend its whole budget.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Sequence

VARIABLES = ("x_chem", "x_T", "x_S", "x_MPR", "x_R")


def make_mock_llm(base_class, context: Mapping[str, Any], seed: int = 0):
    """Build the mock as a subclass of the role tree's own LLMBackend (imported by the caller)."""

    class MockLLM(base_class):
        def __init__(self) -> None:
            self.bounds = context["variable_bounds"]
            self.source = dict(context["previous_best_design"])
            self.rng = random.Random(seed)
            self.seen: set = set()
            self.calls: List[str] = []
            self.seed = seed

        def _candidate(self) -> Dict[str, Any]:
            b = self.bounds
            for _ in range(10000):
                chem = self.rng.choice(list(b["x_chem"]["choices"]))
                tb = b["x_T"]["bounds_by_chemistry"][chem]
                cand = {
                    "x_chem": chem,
                    "x_T": round(self.rng.uniform(float(tb["lower"]), float(tb["upper"])), 1),
                    "x_S": self.rng.randint(int(b["x_S"]["lower"]), int(b["x_S"]["upper"])),
                    "x_MPR": self.rng.randint(int(b["x_MPR"]["lower"]), int(b["x_MPR"]["upper"])),
                    "x_R": self.rng.randint(int(b["x_R"]["lower"]), int(b["x_R"]["upper"])),
                }
                key = tuple(cand[v] for v in VARIABLES)
                if key not in self.seen:
                    self.seen.add(key)
                    return cand
            raise RuntimeError("mock LLM ran out of distinct candidates")

        def generate_json(self, messages: Sequence[Mapping[str, str]], schema_name: str,
                          temperature: float = 0.2, max_tokens: int = 1024) -> Dict[str, Any]:
            self.calls.append(schema_name)
            if schema_name == "WarmStartOutput":
                cands = []
                for i in range(3):
                    c = self._candidate()
                    c.update(rationale=f"Mock start {i + 1}.", evidence_references=["source design"],
                             search_role="exploration", expected_effects=["mock"], tradeoffs=["mock"],
                             uncertainty=["mock"])
                    cands.append(c)
                return {"warm_start_count": 3, "allocation_rationale": "Three mock starts.", "candidates": cands}
            if schema_name == "VariableFixingOutput":
                free = ["x_MPR", "x_R"]
                fixed = {v: self.source[v] for v in VARIABLES if v not in free}
                return {
                    "free_variables": free,
                    "fixed_variables": fixed,
                    "variable_assessments": [
                        {"variable": v, "source_value": self.source[v], "decision": "free" if v in free else "fix",
                         "justification": "Mock decision.", "confidence": "medium"} for v in VARIABLES],
                    "rationale": "Mock partition: release the pack layout.",
                    "warnings": [],
                }
            if schema_name == "ReActAction":
                return {"thought": "Mock step.", "action": "evaluate_candidate", "candidate": self._candidate()}
            raise ValueError(f"mock LLM has no response for schema {schema_name!r}")

    return MockLLM()
