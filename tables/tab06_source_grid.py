"""Table 6: the preliminary grid used to select the reference design, from the 1,374 designs of raw_vehicle_grid.csv
evaluated under the source condition.

The value sets and their spacing are read from these designs. The script also prints the Cartesian size of the value
sets, the number of evaluated designs and the lowest-cost design that meets the source constraints.
"""
import numpy as np
import pandas as pd

from common import args, data_dir, need, write


def spacing(v):
    d = np.unique(np.diff(np.sort(v)))
    return f"{d[0]:g}" if len(d) == 1 else "nonuniform"


def build(g):
    rows = [{"Variable": "x^C", "Search range or values": ", ".join(sorted(g.chemistry.unique())), "Spacing": "categorical"}]
    for chem in ["NMC811", "LFP"]:
        v = np.sort(g[g.chemistry == chem].x_T.unique())
        rows.append({"Variable": f"x^T ({chem})", "Search range or values": "{" + ",".join(f"{x:g}" for x in v) + "} µm",
                     "Spacing": spacing(v)})
    for c, lab in [("x_S", "x^S"), ("x_MPR", "x^M"), ("x_R", "x^R")]:
        v = np.sort(g[c].unique())
        rows.append({"Variable": lab, "Search range or values": f"{v.min()}–{v.max()}", "Spacing": spacing(v)})
    return pd.DataFrame(rows)


def source_design(g):
    f = ((g.energy_kwh >= 84) & (g.power_kw >= 239) & g.nominal_voltage_v.between(200, 1000)
         & g.cell_capacity_ah.between(40, 120) & (g.pack_length_mm <= 2200) & (g.pack_width_mm <= 1500))
    b = g[f].sort_values("cost_per_kwh").iloc[0]
    n_cart = sum(g[g.chemistry == c].x_T.nunique() for c in g.chemistry.unique()) * g.x_S.nunique() * g.x_MPR.nunique() * g.x_R.nunique()
    print(f"grid: {len(g)} evaluated designs, Cartesian size of the value sets {n_cart}. {int(f.sum())} satisfy the "
          f"source constraints. Cheapest ({b.chemistry}, {b.x_T:g}, {b.x_S}, {b.x_MPR}, {b.x_R}) at {b.cost_per_kwh:.3f} USD/kWh")


if __name__ == "__main__":
    out = args(__doc__)
    g = pd.read_csv(need(data_dir() / "JMD_grid_envelope_20260922/grid/raw_vehicle_grid.csv"))
    g = g[g.condition_name == "cost_84_239_base_long"]
    source_design(g)
    write(build(g), out, "tab06_source_grid")
