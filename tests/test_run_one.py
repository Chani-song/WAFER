"""scripts/run_one.sh stops with a clear message, before any evaluation, when BatPaC or the model server is missing."""
import os
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

RUN_ONE = REPO / "scripts/run_one.sh"


def _run(args, tmp_path, path=None):
    env = dict(os.environ, WAFER_PYTHON=sys.executable)
    env.pop("WAFER_BATPAC_WORKBOOK", None)  # the workbook is passed explicitly where a test needs it
    if path is not None:
        env["PATH"] = path
    return subprocess.run(["bash", str(RUN_ONE)] + args, cwd=REPO, env=env, capture_output=True, text=True)


def test_without_workbook(tmp_path):
    r = _run(["--strategy", "react", "--scenario", "s_cost", "--out", str(tmp_path)], tmp_path)
    assert r.returncode == 2 and "BatPaC workbook not given" in r.stderr
    assert not any(tmp_path.iterdir()), "nothing may run without BatPaC"


def test_with_a_file_that_is_not_batpac_5(tmp_path):
    fake = tmp_path / "BatPaC 5.0 2022-02-09.xlsm"
    fake.write_text("not a workbook")
    r = _run(["--strategy", "warm_start", "--scenario", "s_cost", "--out", str(tmp_path / "o"),
              "--batpac-workbook", str(fake)], tmp_path)
    assert r.returncode == 2 and "is not an .xlsm workbook" in r.stderr


@pytest.mark.skipif(not os.environ.get("WAFER_BATPAC_WORKBOOK"), reason="no BatPaC workbook (WAFER_BATPAC_WORKBOOK)")
def test_without_libreoffice_or_model_server(tmp_path):
    workbook = os.environ["WAFER_BATPAC_WORKBOOK"]
    bare = "/usr/bin:/bin"  # no libreoffice
    r = _run(["--strategy", "react", "--scenario", "s_cost", "--out", str(tmp_path / "a"),
              "--batpac-workbook", workbook], tmp_path, path=bare)
    assert r.returncode == 3 and "LibreOffice not found" in r.stderr
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if soffice is None:
        pytest.skip("LibreOffice not installed")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/libreoffice").symlink_to(soffice)
    r = _run(["--strategy", "react", "--scenario", "s_cost", "--out", str(tmp_path / "b"), "--port", "65531",
              "--batpac-workbook", workbook], tmp_path, path=f"{tmp_path / 'bin'}:{bare}")
    assert r.returncode == 4 and "model server not reachable" in r.stderr
