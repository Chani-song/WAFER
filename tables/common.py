"""Shared helpers of the table scripts: data location, loading, rounding, significance tests and output.

WAFER_DATA_DIR points at the folder that contains _pack_staging/. The rounding and test conventions are described
in tables/README.md.
"""
import argparse
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SC = ["S1", "S2", "S3", "S4"]
CANON = {"s_cost": "S1", "s_space": "S2", "s_fastcharge": "S3", "s_energy130_relaxed_pack": "S4"}
STRATEGIES = ["Cold", "SW", "VF", "WS", "ReAct"]
# Printed name of a strategy value of the runs table. SW is the reference-warm baseline RW of the paper.
STRATEGY_LABEL = {"SW": "RW"}
OPTIMIZERS = {"axbo": "BO", "mads": "MADS", "mixed_ga": "GA", "random": "Random"}
SIZES = [4, 9, 27]
LEVELS = ["L1", "L2"]
VARS = ["x_chem", "x_T", "x_S", "x_MPR", "x_R"]
VAR_LABEL = {"x_chem": "x^C", "x_T": "x^T", "x_S": "x^S", "x_MPR": "x^M", "x_R": "x^R"}
SOURCE = {"x_chem": "NMC811", "x_T": 155.0, "x_S": 29, "x_MPR": 2, "x_R": 4}
# Required variables per problem: the variables whose reference-design value appears in no feasible design of the runs
# or the preliminary grid, and the chemistry in S1, where the reference design is feasible
REQUIRED = {"S1": ["x_chem"], "S2": ["x_MPR", "x_R"], "S3": ["x_T"], "S4": ["x_MPR"]}


# ---------------------------------------------------------------- data location
def data_dir():
    d = os.environ.get("WAFER_DATA_DIR")
    if not d:
        sys.exit("error: WAFER_DATA_DIR is not set. Set it to the root of the unpacked data "
                 "archives (the directory that contains _pack_staging/).")
    p = Path(d).expanduser()
    if not (p / "_pack_staging" / "04_results_tables").is_dir():
        sys.exit(f"error: {p}/_pack_staging/04_results_tables not found. Check WAFER_DATA_DIR.")
    return p


def tables_dir():
    return data_dir() / "_pack_staging" / "04_results_tables"


def need(path):
    path = Path(path)
    if not path.exists():
        sys.exit(f"error: required input file is missing: {path}")
    return path


def load(name, **kw):
    """Read a result table, e.g. load('runs') -> 04_results_tables/runs.csv.gz."""
    return pd.read_csv(need(tables_dir() / f"{name}.csv.gz"), low_memory=False, **kw)


def load_runs():
    r = load("runs")
    r["model_size"] = r.model_size.astype("Int64")
    return r


def reference_costs(traj=None, runs=None):
    """Best-known cost of each problem: the lowest cost among the feasible accepted evaluations of all runs, as a
    Series indexed by scenario."""
    traj = load("trajectories", usecols=["run_id", "cost", "feasible"]) if traj is None else traj
    runs = load_runs() if runs is None else runs
    t = traj[traj.feasible == 1].merge(runs[["run_id", "scenario"]], on="run_id")
    return t.groupby("scenario").cost.min()


# ---------------------------------------------------------------- formatting
def fmt(x, d=0):
    """Fixed-point text with d decimals, or '--' for a missing value."""
    if x is None or pd.isna(x):
        return "--"
    return f"{float(x):.{d}f}"


def fmt_sig(x, n=3):
    """Fixed-point text with n significant figures (96.523 -> '96.5', 100.044 -> '100'), or '--' for a missing value."""
    if x is None or pd.isna(x):
        return "--"
    x = float(x)
    if x == 0:
        return fmt(0, n - 1)
    d = max(0, n - 1 - int(math.floor(math.log10(abs(float(f"{x:.{n - 1}e}"))))))
    return f"{x:.{d}f}"


def fmt_comma(x, d=0):
    if x is None or pd.isna(x):
        return "--"
    return f"{float(x):,.{d}f}"


def pct(s):
    """Percentage of true values (100 * mean), the repository convention."""
    s = pd.Series(s).astype(float)
    return 100 * s.mean() if len(s) else np.nan


# ---------------------------------------------------------------- significance tests
def holm(p):
    """Holm-adjusted p-values (step-down, monotone, capped at 1)."""
    p = np.asarray(p, float)
    out = np.empty(len(p))
    running = 0.0
    for rank, idx in enumerate(np.argsort(p)):
        running = max(running, (len(p) - rank) * p[idx])
        out[idx] = min(1.0, running)
    return out


def fisher(a, b):
    """Two-sided Fisher exact test of two 0/1 run outcomes. Returns (p, difference in success rate)."""
    from scipy.stats import fisher_exact
    a, b = pd.Series(a).astype(bool), pd.Series(b).astype(bool)
    table = [[int(a.sum()), int((~a).sum())], [int(b.sum()), int((~b).sum())]]
    return fisher_exact(table).pvalue, a.mean() - b.mean()


METRICS = {"feasible_found": "Disc.", "within2pct": "Gap"}


def tests_vs_sw(runs):
    """Every optimizer-matched comparison of a WAFER configuration with the reference-warm baseline (RW).

    One row per test. A VF or WS configuration is compared with the RW configuration of the same
    scenario and downstream optimizer. Each ReAct configuration has no optimizer and is compared
    with all four RW configurations of its scenario. A family is one scenario, strategy and metric
    (24 tests). The p-values are Holm-adjusted within the family, alpha 0.05. Used by Table 4
    (counts) and Table 11 (shading).
    """
    rows = []
    for sc in SC:
        r = runs[runs.scenario == sc]
        for st in ("VF", "WS", "ReAct"):
            for col, metric in METRICS.items():
                fam = []
                for opt in OPTIMIZERS:
                    sw = r[(r.strategy == "SW") & (r.optimizer == opt)][col]
                    for m in SIZES:
                        for lev in LEVELS:
                            a = r[(r.strategy == st) & (r.model_size == m) & (r.semantic_level == lev)]
                            if st != "ReAct":
                                a = a[a.optimizer == opt]
                            p, d = fisher(a[col], sw)
                            fam.append(dict(scenario=sc, strategy=st, metric=metric, model_size=m, semantic_level=lev,
                                            optimizer=None if st == "ReAct" else opt, sw_optimizer=opt,
                                            rate=a[col].mean(), sw_rate=sw.mean(), p=p, diff=d))
                adj = holm([t["p"] for t in fam])
                for t, q in zip(fam, adj):
                    t.update(p_holm=q, better=bool(q < .05 and t["diff"] > 0), worse=bool(q < .05 and t["diff"] < 0))
                rows += fam
    return pd.DataFrame(rows)


def tests_l1_l2(runs):
    """Optimizer- and size-matched L1 against L2 comparisons (216 tests).

    VF and WS: one test per scenario, optimizer, model size and metric. ReAct: one per scenario,
    model size and metric. Two-sided Fisher exact test, Holm adjustment within each scenario,
    strategy and metric, alpha 0.05. `better` and `worse` describe L2 relative to L1.
    """
    rows = []
    for sc in SC:
        for st in ("VF", "WS", "ReAct"):
            for col, metric in METRICS.items():
                fam = []
                for opt in ([None] if st == "ReAct" else list(OPTIMIZERS)):
                    for m in SIZES:
                        g = runs[(runs.scenario == sc) & (runs.strategy == st) & (runs.model_size == m)]
                        if opt is not None:
                            g = g[g.optimizer == opt]
                        p, d = fisher(g[g.semantic_level == "L2"][col], g[g.semantic_level == "L1"][col])
                        fam.append(dict(scenario=sc, strategy=st, metric=metric, optimizer=opt, model_size=m, p=p, diff=d))
                adj = holm([t["p"] for t in fam])
                for t, q in zip(fam, adj):
                    t.update(p_holm=q, better=bool(q < .05 and t["diff"] > 0), worse=bool(q < .05 and t["diff"] < 0))
                rows += fam
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- output
def args(doc):
    ap = argparse.ArgumentParser(description=(doc or "").strip().splitlines()[0])
    ap.add_argument("--out", default=str(HERE / "out"), help="output directory (default tables/out)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    return out


def tex_cell(x):
    """Plain cell text -> LaTeX (escapes, x^Name -> $x^{\\mathrm{Name}}$, unicode symbols -> math)."""
    s = str(x)
    for a, b in [("%", r"\%"), ("&", r"\&"), ("_", r"\_"), ("#", r"\#"), ("$", r"\$"), ("{", r"\{"), ("}", r"\}")]:
        s = s.replace(a, b)
    s = re.sub(r"x\^(\w+)", lambda m: "$x^{\\mathrm{%s}}$" % m.group(1), s)
    for a, b in [("µ", r"$\mu$"), ("≥", r"$\geq$"), ("→", r"$\rightarrow$"), ("<=", r"$\leq$"), ("<", r"$<$"), ("–", "--")]:
        s = s.replace(a, b)
    return s


def tex_marked(value, mark):
    """LaTeX cell with the printed emphasis: bold (the count before a slash, if any) or gray shading."""
    s = tex_cell(value)
    if mark == "bold":
        head, sep, tail = s.partition("/")
        return r"\textbf{%s}%s%s" % (head, sep, tail)
    if mark == "shaded":
        return r"\cellcolor[gray]{0.88}" + s
    return s


def write(df, out, stem, tex=True, marks=None):
    """Write the table as CSV (display strings, manuscript column order) and a .tex tabular body.

    marks: optional frame of the same shape with "", "bold" or "shaded" per cell, used in the .tex body.
    """
    df.to_csv(Path(out) / f"{stem}.csv", index=False)
    if marks is not None:
        marks = marks.reindex(index=df.index, columns=df.columns).fillna("")
    if tex:
        m = marks.astype(str).values.tolist() if marks is not None else [[""] * len(df.columns)] * len(df)
        lines = [" & ".join(tex_cell(c) for c in df.columns) + r" \\", r"\midrule"]
        lines += [" & ".join(tex_marked(v, k) for v, k in zip(row, mk)) + r" \\"
                  for row, mk in zip(df.astype(str).itertuples(index=False), m)]
        (Path(out) / f"{stem}.tex").write_text("\n".join(lines) + "\n")
    print(f"wrote {Path(out) / (stem + '.csv')} ({len(df)} rows)")

