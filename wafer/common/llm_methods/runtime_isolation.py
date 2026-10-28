"""Absolute, cell-specific runtime paths for real BatPaC execution."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping


def build_cell_runtime_isolation(cell_output_dir: Path) -> Dict[str, str]:
    """Create and return the absolute runtime contract for one production cell."""
    cell_root = Path(cell_output_dir).resolve()
    runtime_root = cell_root / "runtime_isolation"
    paths = {
        "HOME": runtime_root / "home",
        "XDG_CONFIG_HOME": runtime_root / "xdg_config",
        "XDG_CACHE_HOME": runtime_root / "xdg_cache",
        "TMPDIR": runtime_root / "tmp",
        "libreoffice_user_profile": runtime_root / "libreoffice_profile",
        "persistent_workbook_path": runtime_root / "workbook" / "batpac_working.xlsm",
        "batpac_output_dir": runtime_root / "batpac_output",
    }
    for key, path in paths.items():
        (path.parent if key == "persistent_workbook_path" else path).mkdir(parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}


def runtime_environment(runtime: Mapping[str, str]) -> Dict[str, str]:
    """Return only environment variables used by the isolated child process."""
    return {
        "HOME": str(runtime["HOME"]),
        "XDG_CONFIG_HOME": str(runtime["XDG_CONFIG_HOME"]),
        "XDG_CACHE_HOME": str(runtime["XDG_CACHE_HOME"]),
        "TMPDIR": str(runtime["TMPDIR"]),
        "BATPAC_LO_USER_PROFILE": str(runtime["libreoffice_user_profile"]),
    }
