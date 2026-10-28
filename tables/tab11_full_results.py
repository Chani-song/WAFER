"""Table 11: feasible discovery and attainment of a gap of at most 2% for each of the 248 configurations, from
runs.csv.gz.

Each entry is a share of the 50 runs of the configuration, and runs that ended without a usable decision count as
failures.
"""
import pandas as pd

from common import LEVELS, METRICS, OPTIMIZERS, SC, SIZES, STRATEGY_LABEL, args, fmt, load_runs, pct, tests_vs_sw, write

LABEL = {"axbo": "BO", "mixed_ga": "GA", "mads": "MADS", "random": "Random"}
ORDER = ["axbo", "mixed_ga", "mads", "random"]  # BO, GA, MADS, Random as printed


def shaded(tests, st, m, lev, opt, sc, metric):
    t = tests[(tests.scenario == sc) & (tests.strategy == st) & (tests.metric == metric)
              & (tests.model_size == m) & (tests.semantic_level == lev)]
    if st == "ReAct":
        assert len(t) == 4
        return bool(t.better.all())
    t = t[t.optimizer == opt]
    assert len(t) == 1
    return bool(t.better.iloc[0])


def build(runs, tests):
    rows, marks = [], []
    configs = [("Cold", None, None, o) for o in ORDER] + [("SW", None, None, o) for o in ORDER]
    configs += [(st, m, lev, o) for st in ("VF", "WS") for m in SIZES for lev in LEVELS for o in ORDER]
    configs += [("ReAct", m, lev, None) for m in SIZES for lev in LEVELS]
    for st, m, lev, opt in configs:
        g = runs[runs.strategy == st]
        if m is not None:
            g = g[(g.model_size == m) & (g.semantic_level == lev)]
        if opt is not None:
            g = g[g.optimizer == opt]
        row = {"Method": STRATEGY_LABEL.get(st, st), "Size": f"{m}B" if m else "", "Level": lev or "",
               "Optimizer": LABEL.get(opt, "--")}
        mark = {}
        for sc in SC:
            gs = g[g.scenario == sc]
            assert len(gs) == 50, (st, m, lev, opt, sc, len(gs))
            for col, metric in METRICS.items():
                key = f"{sc} {metric}"
                row[key] = fmt(pct(gs[col]), 0)
                mark[key] = "shaded" if st in ("VF", "WS", "ReAct") and shaded(tests, st, m, lev, opt, sc, metric) else ""
        rows.append(row)
        marks.append(mark)
    return pd.DataFrame(rows), pd.DataFrame(marks)


if __name__ == "__main__":
    out = args(__doc__)
    runs = load_runs()
    table, marks = build(runs, tests_vs_sw(runs))
    write(table, out, "tab11_full_results", marks=marks)
    print(f"{int((marks == 'shaded').to_numpy().sum())} shaded cells")
