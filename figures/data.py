"""Reads the plotted-value tables in figures/data/ and the result tables, without changing them."""
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

def result_tables():
    """Result tables: $WAFER_DATA_DIR/_pack_staging/04_results_tables."""
    import os
    root=os.environ.get("WAFER_DATA_DIR")
    if not root:
        raise SystemExit("set WAFER_DATA_DIR to the root of the unpacked data archives (it contains _pack_staging/)")
    return Path(root).expanduser()/"_pack_staging"/"04_results_tables"
def read(name):
    path=DATA/(name+".csv")
    if not path.exists():raise FileNotFoundError(f"plotted-value table not found: {path}")
    return pd.read_csv(path)

def mask_order(g):
    return sorted(g["mask"],key=lambda m:(-m.count("R"),-int(g.loc[g["mask"].eq(m),"n"].iloc[0])))

def wilson(k,n,z=1.96):
    if n==0:return (float("nan"),float("nan"))
    p=k/n
    den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*((p*(1-p)/n+z*z/(4*n*n))**.5)/den
    return center-half,center+half

def risk_curves():
    """One risk-set definition for every method, with model sizes pooled by counts."""
    risk=read("fig10_full_risk_sets")
    rows=[]
    for (scenario,model),g in risk[risk.strategy.eq("ReAct")].groupby(
            ["scenario","model_size"]):
        rows.append(pd.DataFrame(dict(
            scenario=scenario,strategy=f"ReAct{int(model)}",
            k_after_first_feasible=g.offset.to_numpy(),
            n_at_risk=g.n_at_risk.to_numpy(),near_best=g.near_best.to_numpy(),
            gap2_pct=g.conditional_pct.to_numpy())))
    for (scenario,strategy),g in risk[~risk.strategy.eq("ReAct")].groupby(
            ["scenario","strategy"]):
        pooled=g.groupby("offset",as_index=False)[["n_at_risk","near_best"]].sum()
        rows.append(pd.DataFrame(dict(
            scenario=scenario,strategy=strategy,
            k_after_first_feasible=pooled.offset.to_numpy(),
            n_at_risk=pooled.n_at_risk.to_numpy(),
            near_best=pooled.near_best.to_numpy(),
            gap2_pct=(100*pooled.near_best/
                      pooled.n_at_risk.replace(0,float("nan"))).to_numpy())))
    return pd.concat(rows,ignore_index=True)
