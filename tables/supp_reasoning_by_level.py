"""Reasoning categories by semantic level: the share of rationales that mention each category under L1 and L2.

The units are the VF partition rationales, the WS candidate rationales without the set-level text and the accepted
ReAct steps, each with text, and the labels are those stored in the result tables. Appendix F of the paper refers
to this table.
"""
import pandas as pd

from common import LEVELS, args, fmt, load, write

CATEGORIES = [("reasoning_engineering", "Engineering relation"), ("reasoning_constraint", "Constraint"),
              ("reasoning_objective", "Objective / cost"), ("reasoning_opt_history", "Optimization history"),
              ("reasoning_source_ref", "Reference design"), ("reasoning_exploration_uncertainty", "Exploration / uncertainty"),
              ("reasoning_generic_unspecific", "Generic / unspecific")]


def units():
    vf = load("vf_rationale")
    ws = load("ws_rationale")
    rs = load("react_steps", usecols=["run_id", "semantic_level", "text_available"] + [c for c, _ in CATEGORIES])
    return {"VF": vf[vf.text_available == 1], "WS": ws[(ws.candidate_idx > 0) & (ws.text_available == 1)],
            "ReAct": rs[rs.text_available == 1]}


def build(u):
    rows = []
    for col, lab in CATEGORIES:
        row = {"Category (%)": lab}
        for st in ["VF", "WS", "ReAct"]:
            for lev in LEVELS:
                g = u[st][u[st].semantic_level == lev]
                row[f"{st} {lev}"] = fmt(100 * g[col].mean(), 0)
        rows.append(row)
    print("units with text:", {f"{st} {lev}": int((u[st].semantic_level == lev).sum()) for st in u for lev in LEVELS})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    write(build(units()), out, "supp_reasoning_by_level")
