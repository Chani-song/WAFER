"""Mock-simulator, mock-LLM dry run: one 20-evaluation run per strategy through the production controller."""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

CASES = [  # strategy, role tree, context, scenario, optimizer
    ("warm_start", "wsvf_general", "wsvf_s_cost.json", "s_cost", "mads"),
    ("variable_fixing", "wsvf_general", "wsvf_s_space.json", "s_space", "random"),
    ("variable_fixing", "wsvf_fastcharge", "wsvf_s_fastcharge.json", "s_fastcharge", "mixed_ga"),
    ("react", "react_general", "react_s_energy130_relaxed_pack.json", "s_energy130_relaxed_pack", "random"),
    ("react", "react_fastcharge", "react_s_fastcharge.json", "s_fastcharge", "random"),
    ("baseline", "wsvf_general", "wsvf_s_cost.json", "s_cost", "axbo"),
    ("source_warm_baseline", "wsvf_general", "wsvf_s_cost.json", "s_cost", "random"),
]


@pytest.mark.parametrize("strategy,role,context,scenario,optimizer", CASES)
def test_dry_run_spends_twenty_evaluations(run_root, tmp_path, strategy, role, context, scenario, optimizer):
    out = tmp_path / "out"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", JMD_AX_TORCH_DEVICE="cpu", CUDA_VISIBLE_DEVICES="")
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/dry_run.py"), "--role-tree", str(run_root / "code/qwen35_4b" / role),
         "--context", str(run_root / "contexts" / context), "--strategy", strategy, "--optimizer", optimizer,
         "--scenario", scenario, "--seed", "3", "--out", str(out)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    rows = json.loads((out / "aggregate_real_batpac_matrix.json").read_text())
    rows = rows["rows"] if isinstance(rows, dict) else rows
    assert len(rows) == 1 and rows[0]["status"] == "success", rows
    records = list(out.glob("*/real_batpac_records.jsonl"))
    assert len(records) == 1
    assert sum(1 for line in records[0].read_text().splitlines() if line.strip()) == 20
