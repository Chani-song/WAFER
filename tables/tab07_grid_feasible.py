"""Table 7: feasible designs of the preliminary grid under each target condition.

S2 feasibility is recomputed from the source-condition outputs of raw_vehicle_grid.csv with a width limit of
1,100 mm, and S3 and S4 feasibility come from the evaluations in S3_results.jsonl and S4_results.jsonl. Column 3 is a
property that every feasible design shares, and the extra column all_shared lists every such property for information.
"""
import json

import pandas as pd

from common import VARS, args, data_dir, need, write

CLAIMS = {  # stated property -> predicate on the feasible designs
    "S2": ("x^R=3 and x^M≥3", lambda f, g: (f.x_R == 3).all() and (f.x_MPR >= 3).all()),
    "S3": ("x^T=100 µm, the lowest thickness in the grid", lambda f, g: (f.x_T == 100).all() and g.x_T.min() == 100),
    "S4": ("x^M≥3", lambda f, g: (f.x_MPR >= 3).all()),
}


def shared(f, g):
    out = []
    for v in VARS:
        u = sorted(f[v].unique())
        if len(u) == 1:
            out.append(f"{v}={u[0]:g}" if v != "x_chem" else f"{v}={u[0]}")
        elif v != "x_chem" and min(u) > g[v].min():
            out.append(f"{v}>={min(u):g}")
    return "; ".join(out)


def load_grid():
    root = data_dir() / "JMD_grid_envelope_20260922"
    g = pd.read_csv(need(root / "grid/raw_vehicle_grid.csv"))
    g = g[g.condition_name == "cost_84_239_base_long"].rename(columns={"chemistry": "x_chem"})
    feas = {"S2": g[(g.energy_kwh >= 84) & (g.power_kw >= 239) & g.nominal_voltage_v.between(200, 1000)
                    & g.cell_capacity_ah.between(40, 120) & (g.pack_length_mm <= 2200) & (g.pack_width_mm <= 1100)]}
    for sc in ["S3", "S4"]:
        recs = [json.loads(line) for line in need(root / f"audit/{sc}_results.jsonl").open()]
        d = pd.DataFrame([dict(r["candidate"], feasible=bool(r["feasible"]), design_id=r["design_id"]) for r in recs])
        if set(d.design_id) != set(g.design_id):
            raise SystemExit(f"error: {sc} re-evaluation does not cover the same grid designs")
        feas[sc] = d[d.feasible]
    return g, feas


def build(g, feas):
    rows = []
    for sc in ["S2", "S3", "S4"]:
        f = feas[sc]
        text, pred = CLAIMS[sc]
        rows.append({"Sc.": sc, "Feasible": str(len(f)),
                     "Shared by every feasible grid design": text if pred(f, g) else "not shared",
                     "all_shared": shared(f, g)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    g, feas = load_grid()
    write(build(g, feas), out, "tab07_grid_feasible")
