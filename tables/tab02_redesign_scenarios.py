"""Table 2 (tab:redesign_scenarios): source-to-target changes of the four redesign problems.

Source: _pack_staging/02_experiment_def/scenario_specs.json,
`context.target_condition_change` (field, previous, changed) of each problem, as recorded in
the prompts that were sent. The problem conditions themselves are defined in the experiment code
(optimizers/core/domain.py make_condition) and in contexts/*.json.
The quantity names and units below are presentation text keyed by condition field. The numbers
come from the file, except the source recharge time (see SOURCE_VALUE).
"""
import json

import pandas as pd

from common import CANON, args, data_dir, need, write

LABEL = {"cam_cost_nmc811": ("NMC811 CAM cost", "USD/kg"), "W_max": ("Maximum pack width", "mm"),
         "charge_time_min": ("Recharge time", "min"), "E_req": ("Required pack energy", "kWh"),
         "L_max": ("Maximum pack length", "mm")}
# Source values that BatPaC used. The source condition does not write the charging cells, so BatPaC uses
# its defaults: Dashboard!D34 "Calculate Charging Requirements?" = Yes and D57 recharge time = 60 min.
SOURCE_VALUE = {"charge_time_min": 60.0}


def g(x):
    return f"{x:g}"


def build(specs):
    rows = []
    for canon, sc in CANON.items():
        for ch in specs[canon]["context"]["target_condition_change"]:
            name, unit = LABEL[ch["field"]]
            rows.append({"Scenario": sc, "Changed quantity": name,
                         "Reference → Redesign": f"{g(SOURCE_VALUE.get(ch['field'], ch['previous']))} → {g(ch['changed'])} {unit}"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    specs = json.loads(need(data_dir() / "_pack_staging/02_experiment_def/scenario_specs.json").read_text())
    write(build(specs), out, "tab02_redesign_scenarios")
