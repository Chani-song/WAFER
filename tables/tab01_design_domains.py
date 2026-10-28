"""Table 1: the design variables and their domains, from the variable bounds in scenario_specs.json.

The bounds are the same in all four problems.
"""
import json
import sys

import pandas as pd

from common import args, data_dir, need, write

TYPE = {"categorical": "categorical", "real": "continuous", "integer": "integer"}


def g(x):
    return f"{x:g}"


def bounds():
    specs = json.loads(need(data_dir() / "_pack_staging/02_experiment_def/scenario_specs.json").read_text())
    all_b = [v["problem"]["variable_bounds"] for v in specs.values()]
    if any(b != all_b[0] for b in all_b):
        sys.exit("error: variable bounds differ between problems in scenario_specs.json")
    return all_b[0]


def build(b):
    rows = [{"Variable": "x^C", "Type": TYPE[b["x_chem"]["type"]], "Domain": "{" + ",".join(b["x_chem"]["choices"]) + "}"}]
    for chem in ["LFP", "NMC811"]:
        t = b["x_T"]["bounds_by_chemistry"][chem]
        rows.append({"Variable": f"x^T ({chem})", "Type": TYPE[b["x_T"]["type"]], "Domain": f"[{g(t['lower'])},{g(t['upper'])}] µm"})
    for k, lab in [("x_S", "x^S"), ("x_MPR", "x^M"), ("x_R", "x^R")]:
        rows.append({"Variable": lab, "Type": TYPE[b[k]["type"]], "Domain": f"[{b[k]['lower']},{b[k]['upper']}]"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    write(build(bounds()), out, "tab01_design_domains")
