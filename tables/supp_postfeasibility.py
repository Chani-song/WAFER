"""Conversion to a near-best design after the first feasible design for every configuration, from runs.csv.gz and
trajectories.csv.gz.

It writes one row per scenario, strategy and model size, and one per configuration in
supp_postfeasibility_by_configuration.csv. Among the runs that reached feasibility, Conv. is the share that ends
within 2% of the best-known cost, and Conv. n_o that share for the n_o runs whose first feasible design was not yet
within 2%. Within 10 is the share of the runs with at least 10 evaluations left that reach a gap of 2% or less within
the 10 accepted evaluations after the first feasible design, counting the first feasible design itself.
"""
import pandas as pd

from common import (LEVELS, OPTIMIZERS, SC, SIZES, STRATEGY_LABEL, args, fmt, load, load_runs, pct, reference_costs,
                    write)

HORIZON = 10


def prepare(runs, traj):
    ref = reference_costs(traj, runs)
    first = (traj[traj.feasible == 1].sort_values(["run_id", "k"]).groupby("run_id").first()[["k", "cost"]]
             .rename(columns={"k": "k_first", "cost": "c_first"}))
    t = traj[traj.feasible == 1].merge(runs[["run_id", "scenario"]], on="run_id")
    k_nb = t[t.cost <= 1.02 * t.scenario.map(ref)].groupby("run_id").k.min().rename("k_nb")
    r = runs.join(first, on="run_id").join(k_nb, on="run_id")
    r["left"] = 20 - r.k_first
    r["first_nb"] = r.c_first <= 1.02 * r.scenario.map(ref)
    r["nb_in_horizon"] = r.k_nb <= r.k_first + HORIZON
    return r


def stats(g):
    f = g[g.feasible_found == 1]
    ten = f[f.left >= 10]
    notyet = f[~f.first_nb.astype(bool)]
    return {"n_f": str(len(f)), "Conv. (%)": fmt(pct(f.within2pct), 0), "Within 10 (%)": fmt(pct(ten.nb_in_horizon), 0),
            "n_o": str(len(notyet)), "Conv. n_o (%)": fmt(pct(notyet.within2pct), 0),
            "conv_unrounded": pct(f.within2pct)}


def build(runs, traj):
    r = prepare(runs, traj)
    rows = []
    for sc in SC:
        for st in ["Cold", "SW", "VF", "WS", "ReAct"]:
            for m in ([None] if st in ("Cold", "SW") else SIZES):
                g = r[(r.scenario == sc) & (r.strategy == st)]
                if m is not None:
                    g = g[g.model_size == m]
                rows.append({"Sc.": sc, "Strategy": STRATEGY_LABEL.get(st, st), "Size": "--" if m is None else f"{m}B",
                             **stats(g)})
    return pd.DataFrame(rows)


def build_by_configuration(runs, traj):
    r = prepare(runs, traj)
    rows = []
    for sc in SC:
        for st in ["Cold", "SW", "VF", "WS", "ReAct"]:
            for m in ([None] if st in ("Cold", "SW") else SIZES):
                for lev in ([None] if st in ("Cold", "SW") else LEVELS):
                    for opt in ([None] if st == "ReAct" else list(OPTIMIZERS)):
                        g = r[(r.scenario == sc) & (r.strategy == st)]
                        if m is not None:
                            g = g[(g.model_size == m) & (g.semantic_level == lev)]
                        if opt is not None:
                            g = g[g.optimizer == opt]
                        rows.append({"Sc.": sc, "Strategy": STRATEGY_LABEL.get(st, st),
                                     "Size": "--" if m is None else f"{m}B", "Level": lev or "--",
                                     "Optimizer": OPTIMIZERS.get(opt, "--"), "n": str(len(g)), **stats(g)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    runs, traj = load_runs(), load("trajectories", usecols=["run_id", "k", "cost", "feasible"])
    write(build(runs, traj), out, "supp_postfeasibility")
    write(build_by_configuration(runs, traj), out, "supp_postfeasibility_by_configuration", tex=False)
