"""The role trees assemble and import cleanly, and the repository carries no machine paths."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

ROLES = ("wsvf_general", "wsvf_fastcharge", "react_general", "react_fastcharge")


def test_assemble_builds_every_role_tree(run_root):
    for role in ROLES:
        tree = run_root / "code/qwen35_4b" / role
        assert (tree / "llm_methods/scripts/run_real_batpac_strategy_matrix.py").is_file(), role
    assert sorted(p.name for p in (run_root / "contexts").glob("*.json"))


@pytest.mark.parametrize("role", ROLES)
def test_role_tree_imports_in_its_own_process(run_root, role):
    tree = run_root / "code/qwen35_4b" / role
    code = ("import llm_methods.scripts.run_real_batpac_strategy_matrix, "
            "llm_methods.scripts.run_llm_strategy_smoke, optimizers.core.batpac_adapter, src.tools.batpac")
    r = subprocess.run([sys.executable, "-c", code], cwd=tree, env={"PYTHONPATH": str(tree), "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_machine_paths_in_release():
    pattern = re.compile("|".join([r"/home/[a-z]", r"/Users/[A-Za-z]", "/private/" + "var/"]))
    hits = []
    for path in REPO.rglob("*"):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts and path.suffix in {
                ".py", ".sh", ".json", ".md", ".txt", ".csv", ".yml", ".cff"}:
            for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(REPO)}:{i}")
    assert not hits, hits
