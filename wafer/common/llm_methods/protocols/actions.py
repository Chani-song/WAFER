"""Action protocol definitions for ReAct-style search."""

from __future__ import annotations

from typing import Dict, List

ACTION_EVALUATE_CANDIDATE = "evaluate_candidate"
ACTION_FINISH = "finish"
ACTION_STOP = "stop"

AVAILABLE_REACT_TOOLS: List[Dict[str, str]] = [
    {
        "name": ACTION_EVALUATE_CANDIDATE,
        "description": "Evaluate one repaired BatPaC candidate and observe objective/constraint status.",
    },
    {
        "name": ACTION_FINISH,
        "description": "Finish the ReAct loop after enough candidate evaluations have been gathered.",
    },
]
