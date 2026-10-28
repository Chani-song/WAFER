"""BatPaC interface: cell map only (no workbook values), version check, and reading a user's workbook.

The last test needs WAFER_BATPAC_WORKBOOK, the path to your BatPaC 5.0 (2022-02-09) workbook, and is skipped without it.
"""
import ast
import os
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import REPO

COMMON = REPO / "wafer/common"
sys.path.insert(0, str(COMMON))
from batpac_workbook import batpac_workbook_version, read_workbook_defaults, require_batpac_5  # noqa: E402

def _module_parts(path):
    tree = ast.parse(Path(path).read_text())
    cell_map, defaults = None, {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if getattr(target, "id", "") == "CELL_ADDRESS_MAP":
                cell_map = ast.literal_eval(node.value)
        if isinstance(node, ast.ClassDef) and node.name == "BatPaCInputs":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign):
                    defaults[stmt.target.id] = ast.literal_eval(stmt.value) if stmt.value else None
    return cell_map, defaults


def test_codegen_has_cell_map_and_no_values():
    cell_map, defaults = _module_parts(COMMON / "batpac_codegen2.py")
    assert len(cell_map) == 159
    assert defaults == {}, "batpac_codegen2.py must not contain baked-in workbook values"


def test_version_check_rejects_other_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        require_batpac_5(tmp_path / "missing.xlsm")
    bad = tmp_path / "not_a_workbook.xlsm"
    bad.write_text("x")
    with pytest.raises(RuntimeError):
        require_batpac_5(bad)
    other = tmp_path / "other_version.xlsm"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("xl/drawings/drawing1.xml", "<a:t>Software Name: BatPaC-Version 5.1</a:t>")
    assert batpac_workbook_version(other) == "5.1"
    with pytest.raises(RuntimeError, match="requires BatPaC 5.0"):
        require_batpac_5(other)


@pytest.mark.skipif(not os.environ.get("WAFER_BATPAC_WORKBOOK"), reason="no BatPaC workbook (WAFER_BATPAC_WORKBOOK)")
def test_user_workbook_is_batpac_5_and_every_mapped_cell_reads():
    workbook = os.environ["WAFER_BATPAC_WORKBOOK"]
    assert require_batpac_5(workbook) == "5.0"
    cell_map, _ = _module_parts(COMMON / "batpac_codegen2.py")
    assert len(read_workbook_defaults(workbook, cell_map)) == 159
