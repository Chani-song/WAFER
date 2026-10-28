"""Table 10: the coding rules of the seven reasoning categories as printed in the paper, checked against code_react in
analysis/reasoning/common_reasoning.py.

Every listed term must match the regular expressions of its set, every window must equal the window of its pattern,
and the sentence split and the seven categories must be those of the coder. The coder is read with ast and not
imported, because importing it creates folders.
"""
import ast
import re
import sys
from pathlib import Path

import pandas as pd

from common import args, write

CODER = Path(__file__).resolve().parents[1] / "analysis/reasoning/common_reasoning.py"

RULES = [  # (printed category, printed rule as in the TeX, code_react output key)
    ("Engineering mechanism", r"$\exists s:\ M(s) \wedge R(s)$", "engineering_mechanism"),
    ("Optimization history", r"$\exists s:\ H(s) \wedge N(s)$", "optimization_history_numeric_feedback"),
    ("Constraint targeted", r"$K \vee (C \rightarrow_{60} A) \vee (E \rightarrow_{35} L) \vee T$", "constraint_targeted"),
    ("Objective targeted", r"$(V \rightarrow_{40} O) \vee (\{\text{cost, objective}\} \rightarrow_{50} V') \vee "
                           r"(\text{best feasible} \rightarrow_{50} \text{cost})$", "objective_targeted"),
    ("Reference design", r"$S \vee (\text{restore} \rightarrow_{40} \text{source})$", "source_reference"),
    ("Exploration and uncertainty", r"$X$", "exploration_uncertainty"),
    ("Generic", r"$G \wedge \neg(\text{any of the six categories above})$", "generic_unspecific"),
]
TERM_TEXT = [  # (printed term set, printed terms as in the TeX)
    ("$M$", r"thickness, electrode, cathode, transport, diffusion, impedance, fast charging, series, voltage, cell "
            r"count, capacity, energy, kWh, modules per row, row(s), topology, pack width, footprint, geometry, "
            r"chemistry, LFP, NMC, material cost, power, current, thermal, cell area, c\_max, e\_min, mpr, and the "
            r"five variable names"),
    ("$R$", r"therefore, thus, hence, because, due to, lead(s) to, result(s) in, so that, in order to, to meet, "
            r"satisfy, achieve, reduce, increase, maintain, or preserve, must, need to, require(s), will increase, "
            r"decrease, reduce, raise, lower, improve, or preserve"),
    ("$H$", r"previous, prior, last, recent, earlier, so far, trajectory, iteration, previously evaluated"),
    ("$N$", r"a numeral, higher, lower, increase, decrease, improve, worsen, trend"),
    ("$K$", r"active, violated, binding, failed, or failing, followed by constraint or limit"),
    ("$C$, $A$", r"$C$: constraint, violation, limit. $A$: increase, decrease, reduce, raise, lower, change, keep, "
                 r"try, propose, must, need"),
    ("$E$, $L$", r"$E$: exceed(s), below, above. $L$: limit, minimum, maximum, constraint"),
    ("$T$", r"to meet, to satisfy, to restore"),
    ("$V$, $O$", r"$V$: reduce, lower, minimize, improve, with $V'$ adding better. $O$: cost, objective, \$/kWh, "
                 r"cost\_per\_kWh"),
    ("$S$", r"source followed by design, reference, point, configuration, topology, or architecture, revert, return "
            r"to (the) source or baseline"),
    ("$X$", r"uncertain, unknown, hypothesis, explore, test whether, if, a, or the, try a, an, the, changing, "
            r"increasing, decreasing, or reducing, alternative, different design, configuration, chemistry, or topology"),
    ("$G$", r"next step, next candidate, next design, optimizer, continue, adjust"),
]

# The terms of each set written out as test strings, and the patterns of code_react that implement the set.
# A pattern reference is (variable in code_react or module list, index or None for the whole list).
M_TERMS = ["thickness", "electrode", "cathode", "transport", "diffusion", "impedance", "fast charging", "series",
           "voltage", "cell count", "capacity", "energy", "kWh", "modules per row", "row", "rows", "topology",
           "pack width", "footprint", "geometry", "chemistry", "LFP", "NMC", "material cost", "power", "current",
           "thermal", "cell area", "c_max", "e_min", "mpr", "x_chem", "x_T", "x_S", "x_MPR", "x_R"]
R_TERMS = ["therefore", "thus", "hence", "because", "due to", "lead to", "leads to", "result in", "results in",
           "so that", "in order to"] + [f"to {w}" for w in ("meet", "satisfy", "achieve", "reduce", "increase",
                                                            "maintain", "preserve")] + \
          ["must", "need to", "require", "requires"] + [f"will {w}" for w in ("increase", "decrease", "reduce", "raise",
                                                                               "lower", "improve", "preserve")]
C, A = ["constraint", "violation", "limit"], ["increase", "decrease", "reduce", "raise", "lower", "change", "keep", "try",
                                              "propose", "must", "need"]
E, L = ["exceed", "exceeds", "below", "above"], ["limit", "minimum", "maximum", "constraint"]
# The coder writes the O terms as \b(?:cost|objective|\$/kwh|cost_per_kwh). The leading \b means that $/kWh
# matches only directly after a letter or digit ("97.1$/kWh") and not after a space ("97.1 $/kWh").
# The test string uses the attached form.
V, O = ["reduce", "lower", "minimize", "improve"], ["cost", "objective", "97.1$/kWh", "cost_per_kWh"]
CHECKS = [  # (term set, test strings, patterns)
    ("M", M_TERMS, [("MECHANISM", None)]),
    ("R", R_TERMS, [("RELATION", None)]),
    ("H", ["previous", "prior", "last", "recent", "earlier", "so far", "trajectory", "iteration", "previously evaluated"],
     [("history", None)]),
    ("N", ["12", "3.5", "higher", "lower", "increase", "decrease", "improve", "worsen", "trend"], [("numeric", None)]),
    ("K", [f"{a} {b}" for a in ("active", "violated", "binding", "failed", "failing") for b in ("constraint", "limit")],
     [("constraint", 0)]),
    ("C->A", [f"{c} {a}" for c in C for a in A], [("constraint", 1)]),
    ("E->L", [f"{e} {lim}" for e in E for lim in L], [("constraint", 2)]),
    ("T", ["to meet", "to satisfy", "to restore"], [("constraint", 3)]),
    ("V->O", [f"{v} {o}" for v in V for o in O], [("objective", 0)]),
    ("{cost, objective}->V'", [f"{o} {v}" for o in ("cost", "objective") for v in V + ["better"]], [("objective", 1)]),
    ("best feasible->cost", ["best feasible cost"], [("objective", 2)]),
    ("S", [f"source {w}" for w in ("design", "reference", "point", "configuration", "topology", "architecture")]
     + ["revert", "return to source", "return to the source", "return to baseline", "return to the baseline"],
     [("source", 0), ("source", 2), ("source", 3)]),
    ("restore->source", ["restore source", "restore the source"], [("source", 1)]),
    ("X", ["uncertain", "unknown", "hypothesis", "explore", "test whether", "test if", "test a", "test the", "try a",
           "try an", "try the", "try changing", "try increasing", "try decreasing", "try reducing", "alternative",
           "different design", "different configuration", "different chemistry", "different topology"],
     [("exploration", None)]),
    ("G", ["next step", "next candidate", "next design", "optimizer", "continue", "adjust"], [("generic_signal", None)]),
]
WINDOWS = [("constraint", 1, 60), ("constraint", 2, 35), ("objective", 0, 40), ("objective", 1, 50),
           ("objective", 2, 50), ("source", 1, 40)]


def strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def read_coder(path=CODER):
    """Pattern lists of the coder: module lists MECHANISM, RELATION and the top-level assignments of code_react."""
    tree = ast.parse(path.read_text())
    found, keys, split = {}, [], None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("MECHANISM", "RELATION"):
            found[node.targets[0].id] = strings(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "sentences":
            split = [s for s in strings(node) if "?" in s][0]
        if isinstance(node, ast.FunctionDef) and node.name == "code_react":
            for stmt in node.body:  # top-level statements only, which skips the `if conservative:` block
                if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                    found[stmt.targets[0].id] = [s for s in strings(stmt.value) if s.strip()]
                if isinstance(stmt, ast.Return):
                    keys = [k.value for k in stmt.value.keys]
    return found, keys, split


def patterns_for(found, refs):
    out = []
    for var, i in refs:
        out += found[var] if i is None else [found[var][i]]
    return out


def check(found, keys, split):
    errors = []
    if sorted(keys) != sorted(k for _, _, k in RULES):
        errors.append(f"code_react returns {keys}, not the seven categories of Table 10")
    if split != r"(?<=[.!?;])\s+|\n+":
        errors.append(f"sentence split in the coder is {split!r}")
    for name, terms, refs in CHECKS:
        pats = patterns_for(found, refs)
        for t in terms:
            if not any(re.search(p, t, flags=re.I) for p in pats):
                errors.append(f"term set {name}: '{t}' is not matched by {pats}")
    for var, i, n in WINDOWS:
        got = re.findall(r"\.\{0,(\d+)\}", found[var][i])
        if got != [str(n)]:
            errors.append(f"{var}[{i}] window {got}, Table 10 states {n}")
    return errors


def build(found):
    def pats(refs):
        return " | ".join(patterns_for(found, refs))
    code = {"engineering_mechanism": "MECHANISM and RELATION in one sentence", "optimization_history_numeric_feedback":
            pats([("history", None)]) + " and " + found["numeric"][0],
            "constraint_targeted": pats([("constraint", None)]), "objective_targeted": pats([("objective", None)]),
            "source_reference": pats([("source", None)]), "exploration_uncertainty": pats([("exploration", None)]),
            "generic_unspecific": pats([("generic_signal", None)]) + " and none of the six"}
    rows = [{"Category": n, "Rule": r, "code_patterns": code[k]} for n, r, k in RULES]
    rows.append({"Category": "Term set", "Rule": "Terms", "code_patterns": ""})
    rows += [{"Category": s, "Rule": t, "code_patterns": ""} for s, t in TERM_TEXT]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = args(__doc__)
    found, keys, split = read_coder()
    errors = check(found, keys, split)
    if errors:
        sys.exit("error: Table 10 does not describe the coder:\n  " + "\n  ".join(errors))
    n_terms = sum(len(t) for _, t, _ in CHECKS)
    print(f"Table 10 against the coder: {n_terms} terms in {len(CHECKS)} term sets and {len(WINDOWS)} windows match")
    write(build(found), out, "tab10_reasoning_coding", tex=False)
