"""Table 4: optimizer-matched comparisons with the reference-warm baseline (RW), from runs.csv.gz.

Each entry b/w counts the comparisons in which the method is significantly better or worse than RW in feasible
discovery or in a gap of at most 2% at the end of the budget, with the tests described in tables/README.md. The pooled
rates per method and scenario that Sec. 4.1 quotes are written to tab04_strategy_rates.csv.
"""
import pandas as pd

from common import SC, STRATEGY_LABEL, args, fmt, load_runs, pct, tests_vs_sw, write

METHODS = ["VF", "WS", "ReAct"]


def build(tests):
    rows, marks = [], []
    for sc in SC:
        row, mark = {"Sc.": sc}, {"Sc.": ""}
        for st in METHODS:
            for metric in ("Disc.", "Gap"):
                t = tests[(tests.scenario == sc) & (tests.strategy == st) & (tests.metric == metric)]
                assert len(t) == 24, (sc, st, metric, len(t))
                b, w = int(t.better.sum()), int(t.worse.sum())
                row[f"{st} {metric}"] = f"{b}/{w}"
                mark[f"{st} {metric}"] = "bold" if b else ""
        rows.append(row)
        marks.append(mark)
    return pd.DataFrame(rows), pd.DataFrame(marks)


def rates(runs):
    """Pooled rates per scenario and method (all scheduled runs in the denominator)."""
    rows = []
    for sc in SC:
        for st in ["Cold", "SW", "VF", "WS", "ReAct"]:
            g = runs[(runs.scenario == sc) & (runs.strategy == st)]
            f = g[g.feasible_found == 1]
            rows.append({"Sc.": sc, "Method": STRATEGY_LABEL.get(st, st), "n runs": str(len(g)),
                         "Disc. (%)": fmt(pct(g.feasible_found), 1),
                         "1st feas.": fmt(f.first_feasible_eval.median(), 0),
                         "Cost (USD/kWh)": fmt(f.final_best_feasible_cost.median(), 2),
                         "Gap (%)": fmt(pct(g.within2pct), 1)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    runs = load_runs()
    tests = tests_vs_sw(runs)
    table, marks = build(tests)
    write(table, out, "tab04_strategy", marks=marks)
    tests.to_csv(out / "tab04_strategy_tests.csv", index=False)
    write(rates(runs), out, "tab04_strategy_rates", tex=False)
