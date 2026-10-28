"""Builds Tables 1 to 11 of the paper and the two tables that the paper places in the code repository into --out.

The tabNN_*.py scripts write Tables 1 to 11, supp_reasoning_by_level.py the reasoning categories by semantic level
and supp_postfeasibility.py the conversion rates of every configuration, from the data under $WAFER_DATA_DIR.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = sorted(p.name for p in HERE.glob("tab[0-9][0-9]_*.py")) + ["supp_reasoning_by_level.py", "supp_postfeasibility.py"]


def main():
    ap = argparse.ArgumentParser(description="Build all tables.")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    for script in SCRIPTS:
        print(f"\n== {script}", flush=True)
        r = subprocess.run([sys.executable, str(HERE / script), "--out", a.out], cwd=HERE)
        if r.returncode:
            sys.exit(f"error: {script} failed with exit code {r.returncode}")


if __name__ == "__main__":
    main()
