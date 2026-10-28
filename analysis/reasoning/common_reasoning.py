from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "additional_reasoning_analysis"
TAB = OUT / "tables"
FIG = OUT / "figures"
CODED = OUT / "coded"
QA = OUT / "QA"
for directory in [TAB, FIG, CODED, QA]:
    directory.mkdir(parents=True, exist_ok=True)

SCENARIO = {
    "s_cost": "S1",
    "s_space": "S2",
    "s_fastcharge": "S3",
    "s_energy130_relaxed_pack": "S4",
}
VARS = ["x_chem", "x_T", "x_S", "x_MPR", "x_R"]


def norm(text: object) -> str:
    return re.sub(r"\s+", " ", "" if pd.isna(text) else str(text)).strip()


def sentences(text: object) -> list[str]:
    value = norm(text)
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", value) if len(s.strip()) > 8]


MECHANISM = [
    r"\bthick(?:ness)?\b", r"\belectrode\b", r"\bcathode\b", r"\bx_t\b",
    r"\btransport\b", r"\bdiffus", r"\bimpedan", r"\bfast.?charg",
    r"\bseries\b", r"\bx_s\b", r"\bvoltage\b", r"\bcell count\b",
    r"\bcapacit", r"\benergy\b", r"\bkwh\b", r"\bc_max\b", r"\be_min\b",
    r"\bmodules? per row\b", r"\bmpr\b", r"\bx_mpr\b", r"\brows?\b", r"\bx_r\b",
    r"\btopolog", r"\bpack width\b", r"\bfootprint\b", r"\bgeometr",
    r"\bchemistr", r"\blfp\b", r"\bnmc", r"\bx_chem\b", r"\bmaterial cost\b",
    r"\bpower\b", r"\bcurrent\b", r"\bthermal\b", r"\bcell area\b",
]
RELATION = [
    r"\btherefore\b", r"\bthus\b", r"\bhence\b", r"\bbecause\b", r"\bdue to\b",
    r"\bleads? to\b", r"\bresults? in\b", r"\bso that\b", r"\bin order to\b",
    r"\bto (?:meet|satisfy|achieve|reduce|increase|maintain|preserve)\b",
    r"\bmust\b", r"\bneed to\b", r"\brequires?\b", r"\bwill (?:increase|decrease|reduce|raise|lower|improve|preserve)\b",
]


def any_re(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def mechanism_reasoning(text: object) -> bool:
    return any(any_re(MECHANISM, s) and any_re(RELATION, s) for s in sentences(text))


def code_vf(text: object, conservative: bool = False) -> dict[str, bool]:
    t = norm(text).lower()
    source = any_re([
        r"\b(?:source|incumbent|validated|known feasible|baseline)\b.{0,80}\b(?:preserv|retain|maintain|fix|unchanged|reuse)",
        r"\b(?:preserv|retain|maintain|fix|reuse)\b.{0,80}\b(?:source|incumbent|validated|known feasible|baseline)",
    ], t)
    changed = any_re([
        r"\b(?:changed|new|target|tightened|reduced|increased)\b.{0,80}\b(?:constraint|condition|requirement|limit|driver)",
        r"\b(?:primary|dominant|required|necessary)\b.{0,50}\b(?:lever|variable|constraint|driver)",
    ], t)
    irrelevant = any_re([
        r"\b(?:irrelevant|weakly coupled|weak coupling|secondary|low impact|minimal impact|limited impact)\b",
        r"\bnot directly (?:related|relevant|affected|impact)", r"\bno clear (?:benefit|need|impact)",
    ], t)
    reduction = any_re([
        r"\b(?:reduce|reducing|reduced|minimi[sz]e|limit)\b.{0,35}\b(?:search|dimension|dimensionality|space)",
        r"\bfocus(?:es|ed|ing)?\b.{0,45}\b(?:budget|optimizer|search|high-impact|critical)",
        r"\bsimplif(?:y|ies|ying)\b.{0,40}\b(?:search|optimization|optimizer)",
    ], t)
    feasibility = any_re([r"\bfeasib", r"\bconstraint", r"\bviolation", r"\bto (?:meet|satisfy)\b"], t)
    objective = any_re([r"\bobjective\b", r"\bcost(?:_per_kwh| per kwh)?\b", r"\bcheaper\b", r"\bprice\b", r"\bminimi[sz]e\b"], t)
    uncertainty = any_re([
        r"\buncertain", r"\bunknown\b", r"\bunclear\b", r"\bnot (?:known|sure)\b",
        r"\bhedg", r"\bkeep\b.{0,25}\bfree\b.{0,50}\b(?:uncertain|unknown|flexib)",
    ], t)
    engineering = mechanism_reasoning(t)
    if conservative:
        source = any_re([r"\bsource (?:design|topology|architecture|configuration)\b.{0,60}\b(?:preserv|retain|maintain|fix|reuse)", r"\bknown feasible\b.{0,50}\b(?:preserv|retain|fix)"], t)
        changed = any_re([r"\b(?:target|new|changed|tightened|reduced|increased)\b.{0,55}\b(?:constraint|condition|requirement|limit)\b", r"\b(?:required|dominant) (?:physical )?(?:lever|variable)\b"], t)
        irrelevant = any_re([r"\birrelevant\b", r"\bweakly coupled\b", r"\bminimal impact\b", r"\bnot directly related\b"], t)
        reduction = any_re([r"\breduc(?:e|es|ing) (?:the )?search (?:space|dimension|dimensionality)\b", r"\breduce dimensionality\b"], t)
        uncertainty = any_re([r"\buncertain(?:ty)?\b", r"\bunknown\b", r"\bnot known\b"], t)
    generic_signal = any_re([r"\boptimizer\b", r"\boptimi[sz]", r"\bsearch\b", r"\bdesign variable"], t)
    generic = generic_signal and not any([engineering, changed, feasibility, objective, source])
    return {
        "source_preservation": source,
        "changed_constraint_or_required_lever": changed,
        "irrelevant_or_weakly_coupled": irrelevant,
        "search_space_reduction": reduction,
        "feasibility_recovery": feasibility,
        "objective_cost_improvement": objective,
        "uncertainty_hedging": uncertainty,
        "engineering_mechanism": engineering,
        "generic_optimizer_language": generic,
    }


def code_ws(text: object, conservative: bool = False) -> dict[str, bool]:
    t = norm(text).lower()
    source = any_re([
        r"\b(?:exact |validated |known feasible |baseline |incumbent )?(?:source|baseline|incumbent)\b.{0,90}\b(?:anchor|retain|preserv|keep|include|reuse|unchanged)",
        r"\b(?:retain|preserv|keep|include|reuse)\b.{0,70}\b(?:source|baseline|incumbent)",
    ], t)
    directional = any_re([
        r"\b(?:increase|decrease|reduce|raise|lower|thin|expand)\w*\b.{0,60}\b(?:from|relative to|source|baseline)",
        r"\bone[- ]sided\b", r"\bsame direction\b",
    ], t)
    diversity = any_re([
        r"\b(?:divers|distinct|space.?fill|bracket|both sides|range of|multiple hypotheses|complementary)\w*\b",
        r"\bthree (?:different|distinct|candidate|design)", r"\bexplor(?:e|es|ing)\b.{0,60}\b(?:trade.?off|range|alternative|strateg)",
    ], t)
    constraint = any_re([r"\bconstraint", r"\bviolation", r"\brecover feasibility", r"\bto (?:meet|satisfy|restore)\b", r"\bfeasib"], t)
    objective = any_re([r"\bobjective\b", r"\bcost(?:_per_kwh| per kwh)?\b", r"\bcheaper\b", r"\bprice\b", r"\bminimi[sz]e\b"], t)
    engineering = mechanism_reasoning(t)
    coupled = any_re([
        r"\bcoupl", r"\btopolog", r"\btrade.?off\b.{0,70}\b(?:energy|power|voltage|width|length|cost|capacity)",
        r"\b(?:x_mpr|mpr|modules per row|x_r|rows)\b.{0,90}\b(?:x_s|series|width|length|energy|voltage)",
    ], t)
    uncertainty = any_re([r"\buncertain", r"\bunknown\b", r"\bhedg", r"\bmultiple hypotheses\b", r"\btest (?:whether|if)\b"], t)
    if conservative:
        source = any_re([r"\bexact source\b", r"\bsource (?:design|point|configuration)\b.{0,50}\b(?:retain|preserv|include|anchor|reuse)", r"\bretain(?:s|ing)? the source\b"], t)
        directional = any_re([r"\bone[- ]sided\b", r"\b(?:increase|decrease|reduce|lower)\w*\b.{0,30}\bfrom (?:the )?source\b", r"\bcandidate \d\b.{0,80}\b(?:increase|decrease|reduce|lower|thin)\w*\b"], t) and any_re([r"\bsource\b", r"\bbaseline\b", r"\bincumbent\b"], t)
        diversity = any_re([r"\bdeliberate diversity\b", r"\bspace.?fill", r"\bbracket both sides\b", r"\bthree distinct candidates\b", r"\bmultiple hypotheses\b"], t)
        coupled = any_re([r"\bcoupled[- ]variable", r"\btopology\b.{0,60}\b(?:width|length|energy|voltage)", r"\b(?:mpr|modules per row)\b.{0,60}\brows\b"], t)
        uncertainty = any_re([r"\buncertain(?:ty)?\b", r"\bunknown\b", r"\bmultiple hypotheses\b"], t)
    generic_signal = any_re([r"\boptimizer\b", r"\boptimi[sz]", r"\bwarm start\b", r"\bsearch\b"], t)
    generic = generic_signal and not any([engineering, constraint, objective, source, diversity])
    return {
        "exact_source_anchor": source,
        "one_sided_directional_intervention": directional,
        "deliberate_diversity": diversity,
        "constraint_recovery": constraint,
        "objective_cost_reduction": objective,
        "engineering_mechanism": engineering,
        "coupled_variable_topology": coupled,
        "uncertainty_multiple_hypotheses": uncertainty,
        "generic_optimizer_language": generic,
    }


def code_react(text: object, conservative: bool = False) -> dict[str, bool]:
    t = norm(text).lower()
    numeric = r"(?:[-+]?\d+(?:\.\d+)?|\d+\s*[%$]|\b(?:higher|lower|increase|decrease|improv|worsen|trend))"
    history = any(
        any_re([r"\b(?:previous|prior|last|recent|earlier|so far|trajectory|iteration)\b", r"\bpreviously evaluated\b"], s)
        and re.search(numeric, s)
        for s in sentences(t)
    )
    engineering = mechanism_reasoning(t)
    constraint = any_re([
        r"\b(?:active|violated|binding|failed|failing) (?:the )?(?:constraint|limit)",
        r"\b(?:constraint|violation|limit)\b.{0,60}\b(?:increase|decrease|reduce|raise|lower|change|keep|try|propose|must|need)",
        r"\b(?:exceeds?|below|above)\b.{0,35}\b(?:limit|minimum|maximum|constraint)",
        r"\bto (?:meet|satisfy|restore)\b",
    ], t)
    objective = any_re([
        r"\b(?:reduce|lower|minimi[sz]e|improve)\b.{0,40}\b(?:cost|objective|\$/kwh|cost_per_kwh)",
        r"\b(?:cost|objective)\b.{0,50}\b(?:reduce|lower|minimi[sz]e|improve|better)",
        r"\bbest feasible\b.{0,50}\bcost\b",
    ], t)
    source = any_re([r"\bsource (?:design|reference|point|configuration|topology|architecture)\b", r"\brestore\b.{0,40}\bsource\b", r"\brevert\b", r"\breturn to (?:the )?(?:source|baseline)\b"], t)
    exploration = any_re([r"\buncertain", r"\bunknown\b", r"\bhypothes", r"\bexplor", r"\btest (?:whether|if|a|the)\b", r"\btry (?:a|an|the|changing|increasing|decreasing|reducing)", r"\balternative\b", r"\bdifferent (?:design|configuration|chemistry|topology)"], t)
    if conservative:
        history = any(
            any_re([r"\b(?:previous|prior|last|recent) (?:candidate|design|evaluation|attempt|result)s?\b", r"\btrajectory\b"], s)
            and re.search(r"\d+(?:\.\d+)?|\b(?:improved|worsened|increased|decreased)\b", s)
            for s in sentences(t)
        )
        constraint = any_re([r"\bviolat(?:ed|es|ion)\b.{0,90}\b(?:so|therefore|must|need|increase|decrease|reduce|raise|lower|try|propose)", r"\b(?:failed|failing) (?:due to|because of)\b.{0,70}\b(?:constraint|width|length|voltage|energy|power|capacity)", r"\bto (?:meet|satisfy) (?:the )?.{0,30}(?:constraint|limit|minimum|maximum)"], t)
        objective = any_re([r"\b(?:reduce|lower|minimi[sz]e) (?:the )?(?:cost|objective|cost_per_kwh)\b", r"\bcost reduction\b"], t)
        source = any_re([r"\bsource (?:design|reference|point|configuration|topology|architecture)\b", r"\brevert to\b"] ,t)
        exploration = any_re([r"\buncertain(?:ty)?\b", r"\bhypothesis\b", r"\bexplor(?:e|ing) (?:a|an|whether|alternative|different|if|the effect)", r"\btest (?:whether|if|the effect|a hypothesis)\b", r"\bqualitatively different\b"], t)
    generic_signal = any_re([r"\bnext (?:step|candidate|design)\b", r"\boptimizer\b", r"\bcontinue\b", r"\badjust(?:ment)?\b"], t)
    generic = generic_signal and not any([history, engineering, constraint, objective, source, exploration])
    return {
        "optimization_history_numeric_feedback": history,
        "engineering_mechanism": engineering,
        "constraint_targeted": constraint,
        "objective_targeted": objective,
        "source_reference": source,
        "exploration_uncertainty": exploration,
        "generic_unspecific": generic,
    }


def apply_coder(df: pd.DataFrame, text_col: str, coder, prefix: str = "") -> tuple[pd.DataFrame, list[str]]:
    records = [coder(value) for value in df[text_col]]
    labels = list(records[0]) if records else []
    coded = pd.DataFrame(records, index=df.index).astype(bool)
    if prefix:
        coded = coded.add_prefix(prefix)
    return pd.concat([df.reset_index(drop=True), coded.reset_index(drop=True)], axis=1), labels


def holm(pvalues: pd.Series) -> pd.Series:
    values = pvalues.to_numpy(float)
    result = np.full(len(values), np.nan)
    valid = np.flatnonzero(np.isfinite(values))
    if not len(valid):
        return pd.Series(result, index=pvalues.index)
    order = valid[np.argsort(values[valid])]
    adjusted = np.maximum.accumulate([(len(order) - i) * values[idx] for i, idx in enumerate(order)])
    result[order] = np.minimum(adjusted, 1.0)
    return pd.Series(result, index=pvalues.index)


def bootstrap_ci(values: list[float], seed: int = 20260915, n_boot: int = 4000) -> tuple[float, float]:
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    if not len(a):
        return np.nan, np.nan
    rng = np.random.default_rng(seed + len(a))
    means = np.mean(a[rng.integers(0, len(a), size=(n_boot, len(a)))], axis=1)
    return float(np.quantile(means, .025)), float(np.quantile(means, .975))


def signed_rank_p(values: list[float]) -> float:
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    if len(a) < 3 or np.allclose(a, 0):
        return np.nan
    try:
        return float(wilcoxon(a, zero_method="wilcox", alternative="two-sided").pvalue)
    except ValueError:
        return np.nan


def write_table(df: pd.DataFrame, name: str, caption: str, note: str, provenance: str) -> None:
    df.to_csv(TAB / f"{name}.csv", index=False)
    fmt = df.copy()
    for col in fmt:
        if pd.api.types.is_float_dtype(fmt[col]):
            fmt[col] = fmt[col].map(lambda x: "N/A" if pd.isna(x) else f"{x:.3f}")
    (TAB / f"{name}.tex").write_text(fmt.to_latex(index=False, escape=True, longtable=len(fmt) > 35, caption=caption, label="tab:" + name.replace("_", "-")))
    (TAB / f"{name}_note.md").write_text(f"# {name}\n\n**Caption.** {caption}\n\n**Interpretation.** {note}\n\n**Provenance.** {provenance}\n")


def short_quote(text: object, patterns: list[str] | None = None, max_words: int = 24) -> str:
    ss = sentences(text)
    chosen = ss[0] if ss else norm(text)
    if patterns:
        for sentence in ss:
            if any_re(patterns, sentence.lower()):
                chosen = sentence
                break
    words = chosen.split()
    return " ".join(words[:max_words]) + (" …" if len(words) > max_words else "")
