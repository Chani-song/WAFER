import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def run_root(tmp_path_factory):
    """Role trees for one model size (the trees are identical across model sizes)."""
    out = tmp_path_factory.mktemp("run_root")
    subprocess.run([sys.executable, "-m", "wafer.assemble", "--out", str(out), "--models", "qwen35_4b"],
                   cwd=REPO, check=True, capture_output=True, text=True)
    return out
