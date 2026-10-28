"""Builds the plotted-value tables in figures/data/ from the result tables under $WAFER_DATA_DIR.

VF tables use the valid partitions, WS tables the valid candidates, and near-best means a feasible cost of at most
1.02 times the reference cost. The module imports the figure code, so it needs LaTeX as figures.build does.
"""
import argparse
import io
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import prepare_fig08u, prepare_fig10
from .data import result_tables

HERE = Path(__file__).resolve().parent
SC = ("S1", "S2", "S3", "S4")
SUBSPACE = (("S2", "FFFRR"), ("S4", "FRRRR"))


def cell_curves(raw):
    """Cumulative feasible discovery and attainment of a gap of at most 2% per configuration at k = 0 to 20."""
    runs = pd.read_csv(raw / "runs.csv.gz")
    runs["strategy"] = runs.strategy.replace({"SW": "Source-warm"})
    runs["cell"] = runs[["scenario", "strategy", "optimizer", "model_size", "semantic_level"]].fillna("NA").astype(str) \
        .agg("|".join, axis=1)
    best = runs.groupby("scenario").final_best_feasible_cost.min()
    t = pd.read_csv(raw / "trajectories.csv.gz", usecols=["run_id", "k", "cost", "feasible"])
    t = t.merge(runs[["run_id", "scenario"]], on="run_id").sort_values(["run_id", "k"])
    t["feasible"] = t.feasible.astype(bool)
    t["cum_feas"] = t.groupby("run_id").feasible.cummax().astype(float)
    t["cum_best"] = t.assign(c=np.where(t.feasible, t.cost, np.inf)).groupby("run_id").c.cummin()
    t["cum_gap2"] = (t.cum_best <= t.scenario.map(best) * 1.02).astype(float)
    cols = ["cum_feas", "cum_gap2"]
    idx = pd.MultiIndex.from_product([runs.run_id, range(1, 21)], names=["run_id", "k"])
    cur = t.set_index(["run_id", "k"])[cols].reindex(idx).groupby(level=0).ffill().fillna(0.0).reset_index()
    cur = cur.merge(runs[["run_id", "scenario", "strategy", "cell"]], on="run_id")
    cc = cur.groupby(["scenario", "strategy", "cell", "k"])[cols].mean().reset_index()
    zero = cc[cc.k == 1].copy()
    zero["k"] = 0
    zero[cols] = 0.0
    return pd.concat([zero, cc], ignore_index=True)


def build(raw):
    """Return {file stem: table} for every plotted-value table that the figures read."""
    runs = pd.read_csv(raw / "runs.csv.gz", low_memory=False)
    runs["strategy_fig"] = runs.strategy.replace({"SW": "Source-warm"})
    ws = pd.read_csv(raw / "ws_candidates.csv.gz")
    vf = pd.read_csv(raw / "vf_decisions.csv.gz")
    cells = pd.read_csv(raw / "cells.csv.gz")
    vfv = vf[vf.partition_valid.astype(bool)]
    out = {}

    # Figs. 3 and 4: configuration median and quartiles of the cumulative curves
    curves = cell_curves(raw)
    for col, stem in (("cum_feas", "R1_cum_feas"), ("cum_gap2", "R1_cum_gap2")):
        g = curves.groupby(["scenario", "strategy", "k"])[col]
        out[f"plotted/{stem}"] = pd.DataFrame({"med": g.median(), "q1": g.quantile(.25), "q3": g.quantile(.75)}).reset_index()

    # Fig. 5: the plotted configurations of cells.csv, and their medians and quartiles
    cp = cells[cells.plotted.astype(str).eq("True")].reset_index(drop=True)
    cp["strategy"] = cp.strategy.replace({"SW": "Source-warm"})
    out["plotted/R2_configuration_points"] = cp
    rows = []
    for (sc, st), g in cp.groupby(["scenario", "strategy"]):
        x, y = g.discovery_pct, g.median_final_cost
        rows.append(dict(scenario=sc, strategy=st, n=len(g), x_median=x.median(), x_q1=x.quantile(.25),
                         x_q3=x.quantile(.75), y_median=y.median(), y_q1=y.quantile(.25), y_q3=y.quantile(.75)))
    out["plotted/R2_medians_iqr"] = pd.DataFrame(rows)

    # Figs. 6 and 11: valid VF partitions by exact mask
    V = vfv.merge(runs[["run_id", "feasible_found", "within2pct", "budget", "n_accepted_evals"]], on="run_id",
                  validate="one_to_one")
    exact = V.groupby(["scenario", "mask", "model_size", "optimizer", "semantic_level"], as_index=False).agg(
        n=("run_id", "size"), feasible=("feasible_found", "sum"), near_best=("within2pct", "sum"))
    out["data/fig07_exact_counts"] = exact
    rates = exact.groupby(["scenario", "mask"], as_index=False)[["n", "feasible", "near_best"]].sum()
    rates["discovery_pct"] = 100 * rates.feasible / rates.n
    rates["near_best_pct"] = 100 * rates.near_best / rates.n
    out["data/fig07_mask_rates"] = rates
    # Fig. 12: accepted evaluations of the runs that selected the mask, by x^R and x^MPR
    for sc, mask in SUBSPACE:
        out[f"data/fig08u_{sc}_evaluated_grid"] = prepare_fig08u.cells(raw, sc, mask)

    # Fig. 7: WS runs with three valid evaluated starts, by the number of feasible starts
    best = runs.groupby("scenario").best_observed_feasible_cost.first()
    w = ws[ws.candidate_valid.eq(1) & ws.initial_evaluated.eq(1)]
    w = w.assign(init_near=w.initial_feasible.eq(1) & w.initial_cost.le(w.scenario.map(best) * 1.02))
    c = w.groupby("run_id").agg(nc=("candidate_idx", "size"), nif=("initial_feasible", "sum"),
                                init_near=("init_near", "max"))
    c = c[c.nc.eq(3)].reset_index().merge(
        runs[runs.strategy.eq("WS")][["run_id", "scenario", "feasible_found", "within2pct", "final_best_feasible_cost",
                                      "source_design_cost"]], on="run_id", validate="one_to_one")
    c["initial"] = c.init_near & c.within2pct.eq(1)
    c["later"] = ~c.init_near & c.within2pct.eq(1)
    b = c.groupby(["scenario", "nif"]).agg(n=("run_id", "size"), feasible=("feasible_found", "sum"),
                                           initial=("initial", "sum"), later=("later", "sum"),
                                           final=("within2pct", "sum"))
    b = b.reindex(pd.MultiIndex.from_product([SC, range(4)], names=["scenario", "nif"]), fill_value=0).reset_index()
    b = b.rename(columns={"nif": "n_initial_feasible"}).astype({"n_initial_feasible": int})
    out["data/fig09_initial_feasible_bins"] = b[["n_initial_feasible", "n", "feasible", "initial", "later", "final",
                                                 "scenario"]]
    # Fig. 8 (a): discovery and near-best attainment per configuration
    runs["cell"] = runs[["scenario", "strategy_fig", "optimizer", "model_size", "semantic_level"]].astype(object) \
        .fillna("NA").astype(str).agg("|".join, axis=1)
    out["plotted/R5_cell_strip"] = runs.groupby(
        ["scenario", "strategy_fig", "optimizer", "model_size", "semantic_level", "cell"], dropna=False).agg(
        disc=("feasible_found", "mean"), gap=("within2pct", "mean")).mul(100).reset_index().rename(
        columns={"strategy_fig": "strategy"})
    # Fig. 8 (b) to (e): risk sets after the first feasible evaluation
    with tempfile.TemporaryDirectory() as tmp:
        risk = prepare_fig10.make(raw, Path(tmp) / "risk.csv")
    out["data/fig10_full_risk_sets"] = risk

    # Fig. 10: configuration median and quartiles per model size
    llm = runs[runs.strategy.isin(["VF", "WS", "ReAct"])]
    cs = llm.groupby(["scenario", "strategy", "model_size", "cell"])[["feasible_found", "within2pct"]].mean().mul(100)
    parts = []
    for metric in ("feasible_found", "within2pct"):
        g = cs[metric].groupby(level=[0, 1, 2])
        parts.append(pd.DataFrame({"med": g.median(), "q1": g.quantile(.25), "q3": g.quantile(.75)}).reset_index()
                     .assign(metric=metric))
    size = pd.concat(parts).rename(columns={"model_size": "model"})
    order = {"VF": 0, "WS": 1, "ReAct": 2}
    size = size.assign(_m=size.metric.ne("feasible_found"), _s=size.strategy.map(order)).sort_values(
        ["_m", "scenario", "_s", "model"], kind="stable")
    out["plotted/R6_model_size"] = size[["scenario", "strategy", "model", "med", "q1", "q3", "metric"]]

    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=HERE / "data", help="output folder (default: figures/data/)")
    x = p.parse_args()
    x.out.mkdir(parents=True, exist_ok=True)
    for key, frame in build(result_tables()).items():
        target = x.out / f"{key.split('/')[1]}.csv"
        text = frame.to_csv(index=False)
        # A stored file with the same values is kept, so the last printed digit of a float does not depend on the
        # numpy version.
        if target.exists() and pd.read_csv(target).equals(pd.read_csv(io.StringIO(text))):
            print(f"same     {target.name} ({len(frame)} rows)")
            continue
        target.write_text(text)
        print(f"written  {target.name} ({len(frame)} rows)")


if __name__ == "__main__":
    main()
