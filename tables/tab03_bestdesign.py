"""Table 3: the best feasible design of each redesign problem and the reference design.

Each redesign row is the lowest-cost feasible evaluation among all 239,645 primary evaluations of its problem, and its
cost is the best-known cost, against which the optimality gap is measured. The row labelled Source is the reference
design and its result under the source condition, from scenario_specs.json. Costs and thickness are given to three
significant figures.
"""
import json
import sys

import pandas as pd

from common import SC, args, data_dir, fmt_sig, load, load_runs, need, write


def build(traj, runs, specs):
    src = {json.dumps(v["context"]["source_reference_design"], sort_keys=True) for v in specs.values()}
    if len(src) != 1:
        sys.exit("error: reference design differs between problems")
    ctx = specs["s_cost"]["context"]
    s = ctx["source_reference_design"]
    rows = [{"Sc.": "Reference", "x^C": s["x_chem"], "x^T (µm)": fmt_sig(s["x_T"]), "x^S": str(s["x_S"]),
             "x^M": str(s["x_MPR"]), "x^R": str(s["x_R"]), "Cost (USD/kWh)": fmt_sig(ctx["evaluated_source_result"]["cost"])}]
    t = traj[traj.feasible == 1].merge(runs[["run_id", "scenario"]], on="run_id")
    for sc in SC:
        g = t[t.scenario == sc]
        b = g.loc[g.cost.idxmin()]
        rows.append({"Sc.": sc, "x^C": b.x_chem, "x^T (µm)": fmt_sig(b.x_T), "x^S": str(int(b.x_S)),
                     "x^M": str(int(b.x_MPR)), "x^R": str(int(b.x_R)), "Cost (USD/kWh)": fmt_sig(b.cost)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    specs = json.loads(need(data_dir() / "_pack_staging/02_experiment_def/scenario_specs.json").read_text())
    write(build(load("trajectories"), load_runs(), specs), out, "tab03_bestdesign")
