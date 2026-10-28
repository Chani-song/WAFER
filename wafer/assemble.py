"""Assembles runnable role trees from wafer/common and wafer/roles.

The experiments ran each role (wsvf_general, wsvf_fastcharge, react_general, react_fastcharge)
from its own copy of the code with its own PYTHONPATH. The copies were identical across the
three model sizes. This script rebuilds that layout:

    <out>/code/<model>/<role>/      common files + the role's own files
    <out>/contexts/*.json           context artifacts
    <out>/scripts/*.sh              orchestration scripts

Usage:
    python -m wafer.assemble --out RUN_ROOT [--batpac-workbook PATH] [--models qwen35_4b ...]
"""
from __future__ import annotations

import argparse
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLES = ("wsvf_general", "wsvf_fastcharge", "react_general", "react_fastcharge")
MODELS = ("qwen35_4b", "qwen35_9b", "qwen35_27b")
WORKBOOK_NAME = "BatPaC 5.0 2022-02-09.xlsm"


def build_role_tree(role: str, dest: Path) -> Path:
    """Copy common/ and then roles/<role>/ into dest (role files take precedence)."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    dest.mkdir(parents=True, exist_ok=True)
    for src_root in (HERE / "common", HERE / "roles" / role):
        for src in sorted(src_root.rglob("*")):
            if src.is_file() and "__pycache__" not in src.parts:
                dst = dest / src.relative_to(src_root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
    return dest


def install_workbook(workbook: Path, role_tree: Path) -> None:
    """Check the user's workbook (BatPaC 5.0) and place it where the code expects it."""
    sys.path.insert(0, str(HERE / "common"))
    try:
        from batpac_workbook import require_batpac_5
    finally:
        sys.path.pop(0)
    require_batpac_5(workbook)
    shutil.copyfile(workbook, role_tree / WORKBOOK_NAME)


def assemble(out: Path, models=MODELS, workbook: Path | None = None) -> None:
    for model in models:
        for role in ROLES:
            tree = build_role_tree(role, out / "code" / model / role)
            if workbook is not None:
                install_workbook(workbook, tree)
    (out / "contexts").mkdir(parents=True, exist_ok=True)
    for src in sorted((HERE / "contexts").glob("*.json")):
        shutil.copyfile(src, out / "contexts" / src.name)
    (out / "scripts").mkdir(parents=True, exist_ok=True)
    for src in sorted((HERE / "orchestration").glob("*.sh")):
        dst = out / "scripts" / src.name
        shutil.copyfile(src, dst)
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    p.add_argument("--batpac-workbook", type=Path, default=None,
                   help=f"your copy of BatPaC 5.0 (2022-02-09), installed as '{WORKBOOK_NAME}' in each role tree")
    a = p.parse_args(argv)
    assemble(a.out, a.models, a.batpac_workbook)
    print(f"assembled {len(a.models)} model(s) x {len(ROLES)} roles in {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
