"""Table 5: median tokens and inference time per run by strategy and model size, from runs.csv.gz.

The medians pool scenarios, semantic levels and optimizers over 1,600 VF, 1,600 WS and 400 ReAct runs per size.
Time/run is the measured language model inference time without simulator time.
"""
import pandas as pd

from common import SIZES, args, fmt, fmt_comma, load_runs, write


def build(runs):
    rows = []
    for st in ["VF", "WS", "ReAct"]:
        g = runs[runs.strategy == st]
        row = {"Strategy": st}
        for m in SIZES:
            row[f"Tokens/run {m}B"] = fmt_comma(g[g.model_size == m].tokens_total.median(), 0)
        for m in SIZES:
            row[f"Time/run (s) {m}B"] = fmt(g[g.model_size == m].inference_time_total.median(), 0)
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    write(build(load_runs()), out, "tab05_compute")
