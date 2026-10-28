"""Table 8: the effect of L2 against L1.

Panel (a) gives feasible discovery, a gap of at most 2% and the median first feasible evaluation from runs.csv.gz,
pooled over sizes and optimizers. Panel (b) gives the redesign decisions of the valid VF partitions and valid WS
candidates. The 216 optimizer- and size-matched L1 against L2 tests are written to tab08_semantic_tests.csv.
"""
import pandas as pd

from common import LEVELS, SC, VARS, VAR_LABEL, args, fmt, load, load_runs, pct, tests_l1_l2, write


def build_a(runs):
    rows = []
    for st in ["VF", "WS", "ReAct"]:
        for sc in SC:
            row = {"Strategy": st, "Sc.": sc}
            g = {lev: runs[(runs.strategy == st) & (runs.scenario == sc) & (runs.semantic_level == lev)] for lev in LEVELS}
            for lev in LEVELS:
                row[f"Discovery {lev}"] = fmt(pct(g[lev].feasible_found), 0)
            for lev in LEVELS:
                row[f"Gap {lev}"] = fmt(pct(g[lev].within2pct), 0)
            for lev in LEVELS:
                row[f"1st feas. {lev}"] = fmt(g[lev][g[lev].feasible_found == 1].first_feasible_eval.median(), 0)
            rows.append(row)
    return pd.DataFrame(rows)


def build_b(vf, ws):
    vf = vf[vf.partition_valid == 1]
    ws = ws[ws.candidate_valid == 1]
    by = lambda df, f: [f(df[df.semantic_level == lev]) for lev in LEVELS]
    rows = [(f"VF: release {VAR_LABEL[v]} (%)", by(vf, lambda g, v=v: fmt(100 * (1 - g[f"fixed_{v}"].mean()), 1))) for v in VARS]
    rows += [("VF: required variable released (%)", by(vf, lambda g: fmt(pct(g.required_released), 1))),
             ("WS: reference-design candidate (%)", by(ws, lambda g: fmt(pct(g.exact_source), 1))),
             ("WS: variables changed", by(ws, lambda g: fmt(g.n_vars_changed_from_source.mean(), 1))),
             ("WS: feasible candidate (%)", by(ws, lambda g: fmt(pct(g.initial_feasible), 1))),
             ("WS: distance from reference", by(ws, lambda g: fmt(g.source_distance.mean(), 1)))]
    df = pd.DataFrame([[name] + vals for name, vals in rows], columns=["Decision"] + LEVELS)
    # coarse_flag: the S2 flag x_MPR OR x_R released, from the column required_released_original_coarse
    df["coarse_flag"] = ""
    df.loc[df.Decision.str.startswith("VF: required"), "coarse_flag"] = "/".join(
        by(vf, lambda g: fmt(pct(g.required_released_original_coarse), 1)))
    return df


if __name__ == "__main__":
    out = args(__doc__)
    runs = load_runs()
    write(build_a(runs), out, "tab08_semantic_a")
    write(build_b(load("vf_decisions"), load("ws_candidates")), out, "tab08_semantic_b")
    t = tests_l1_l2(runs)
    t.to_csv(out / "tab08_semantic_tests.csv", index=False)
    worse = t[t.worse]
    print(f"L1 against L2: {len(t)} tests, L2 significantly better in {int(t.better.sum())}, worse in {len(worse)} "
          f"({int(((worse.strategy == 'WS') & (worse.metric == 'Gap') & (worse.scenario == 'S4')).sum())} of them WS Gap S4)")
