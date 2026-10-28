"""Table 9, in four panels, from runs.csv.gz, trajectories.csv.gz and the valid VF partitions and WS candidates.

Panel (a) gives VF outcomes by mask in S2 and S4, (b) VF feasible discovery by optimizer for FFFRR in S2 and FRRRR in
S4, (c) WS runs with and without the exact reference design among their starting points, and (d) the conversion after
the first feasible design (supp_postfeasibility.py). In panel (d), Within 10 counts the runs with at least 10
evaluations left that reach a gap of 2% or less within the 10 accepted evaluations after the first feasible design.
The script also prints the range of runs per optimizer in panel (b).
"""
import pandas as pd

import supp_postfeasibility
from common import OPTIMIZERS, SC, SIZES, args, fmt, fmt_comma, load, load_runs, pct, write

SUBSPACE = {"S2": "FFFRR", "S4": "FRRRR"}


def vf_masks(vf, runs):
    """VF masks chosen by at least 20 valid partitions in a problem, with the share of their runs that found a
    feasible design and that ended within 2% of the best-known cost."""
    v = vf[vf.partition_valid == 1].merge(runs[["run_id", "feasible_found", "within2pct"]], on="run_id", validate="one_to_one")
    rows = []
    for sc in SC:
        g = v[v.scenario == sc]
        stats = g.groupby("mask").agg(n=("run_id", "size")).reset_index()
        stats = stats[stats.n >= 20].assign(nR=lambda d: d["mask"].str.count("R"))
        stats = stats.sort_values(["nR", "n", "mask"], ascending=[False, False, True])
        for mask in stats["mask"]:
            gm = g[g["mask"] == mask]
            row = {"Sc.": sc, "Mask": mask, "n runs": str(len(gm)), "Discovery (%)": fmt(pct(gm.feasible_found), 0),
                   "Gap <= 2% (%)": fmt(pct(gm.within2pct), 0)}
            for m in SIZES:
                k = int((gm.model_size == m).sum())
                row[f"{m}B"] = str(k) if k else "--"
            rows.append(row)
    return pd.DataFrame(rows)


def panel_a(masks):
    cols = ["Mask", "n", "Disc. (%)", "Gap <= 2% (%)"]
    side = {}
    for sc in ("S2", "S4"):
        g = masks[masks["Sc."] == sc]
        side[sc] = pd.DataFrame({"Mask": g["Mask"].values, "n": g["n runs"].values,
                                 "Disc. (%)": g["Discovery (%)"].values, "Gap <= 2% (%)": g["Gap <= 2% (%)"].values})
    n = max(len(d) for d in side.values())
    parts = [side[sc].reindex(range(n)).fillna("").add_prefix(f"{sc} ") for sc in ("S2", "S4")]
    return pd.concat(parts, axis=1)


def panel_b(vf, runs):
    v = vf[vf.partition_valid == 1].merge(runs[["run_id", "feasible_found"]], on="run_id", validate="one_to_one")
    rows, marks, sizes = [], [], []
    for sc, mask in SUBSPACE.items():
        g = v[(v.scenario == sc) & (v["mask"] == mask)]
        rates = {lab: pct(g[g.optimizer == opt].feasible_found) for opt, lab in OPTIMIZERS.items()}
        sizes += [int((g.optimizer == opt).sum()) for opt in OPTIMIZERS]
        best = max(rates.values())
        row = {"Sc.": sc, "Mask": mask}
        mark = {"Sc.": "", "Mask": ""}
        for lab in ("BO", "MADS", "GA", "Random"):
            row[lab] = fmt(rates[lab], 0)
            mark[lab] = "bold" if rates[lab] == best else ""
        rows.append(row)
        marks.append(mark)
    return pd.DataFrame(rows), pd.DataFrame(marks), (min(sizes), max(sizes))


def panel_c(runs, ws):
    ex = ws[ws.candidate_valid == 1].groupby("run_id").exact_source.max()
    r = runs[runs.strategy == "WS"].join(ex, on="run_id")
    r = r[r.exact_source.notna()]
    rows, marks = [], []
    for sc in SC:
        gaps = {}
        for flag, lab in [(0, "No"), (1, "Yes")]:
            g = r[(r.scenario == sc) & (r.exact_source == flag)]
            gaps[lab] = pct(g.within2pct)
            rows.append({"Sc.": sc, "Ref.": lab, "n": fmt_comma(len(g), 0), "Disc. (%)": fmt(pct(g.feasible_found), 1),
                         "1st feas.": fmt(g[g.feasible_found == 1].first_feasible_eval.median(), 0),
                         "Gap <= 2% (%)": fmt(gaps[lab], 1)})
        hi = max(gaps, key=gaps.get)
        marks += [{"Gap <= 2% (%)": "bold" if lab == hi else ""} for lab in ("No", "Yes")]
    return pd.DataFrame(rows), pd.DataFrame(marks)


def panel_d(post):
    p = post.copy()
    p["conv"] = p.conv_unrounded.astype(float)
    rows = []
    for sc in ("S2", "S3", "S4"):
        g = p[p["Sc."] == sc]
        pick = [g[(g.Strategy == "ReAct") & (g.Size == "27B")].iloc[0]]
        for st in ("VF", "WS"):
            h = g[g.Strategy == st]
            pick.append(h.loc[h.conv.idxmax()])
        for r in pick:
            rows.append({"Sc.": sc, "Configuration": f"{r.Strategy} {r.Size}", "n_f": r.n_f, "Conv. (%)": r["Conv. (%)"],
                         "Within 10 (%)": r["Within 10 (%)"], "n_o": r.n_o, "Conv. n_o (%)": r["Conv. n_o (%)"]})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    runs, vf, ws = load_runs(), load("vf_decisions"), load("ws_candidates")
    traj = load("trajectories", usecols=["run_id", "k", "cost", "feasible"])
    write(panel_a(vf_masks(vf, runs)), out, "tab09a_vf_masks")
    b, bm, (lo, hi) = panel_b(vf, runs)
    write(b, out, "tab09b_vf_optimizer", marks=bm)
    print(f"panel (b): {lo} to {hi} runs per optimizer")
    c, cm = panel_c(runs, ws)
    write(c, out, "tab09c_ws_source", marks=cm)
    write(panel_d(supp_postfeasibility.build(runs, traj)), out, "tab09d_postfeasibility")
