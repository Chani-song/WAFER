"""Builds the risk sets of Fig. 8, figures/data/fig10_full_risk_sets.csv, from the result tables.

A run is at risk at offset k after its first feasible evaluation while it has at least k accepted evaluations left.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent

def make(raw,output):
    raw=Path(raw)
    runs=pd.read_csv(raw/"runs.csv.gz",usecols=[
        "run_id","scenario","strategy","model_size","first_feasible_eval",
        "best_observed_feasible_cost"])
    traj=pd.read_csv(raw/"trajectories.csv.gz",usecols=["run_id","k","feasible","cost"])
    assert runs.run_id.is_unique
    assert traj.k.between(1,20).all()
    runs["strategy"]=runs.strategy.replace({"SW":"Source-warm"})
    best=runs.groupby("scenario").best_observed_feasible_cost.first()
    assert runs.groupby("scenario").best_observed_feasible_cost.nunique().eq(1).all()
    traj=traj.merge(runs[["run_id","scenario"]],on="run_id",validate="many_to_one")
    traj["near_best_eval"]=traj.feasible.eq(1)&traj.cost.le(
        traj.scenario.map(best)*1.02)
    first=traj[traj.near_best_eval].groupby("run_id").k.min().rename("first_near_best")
    joined=runs.join(first,on="run_id")
    rows=[]
    for (scenario,strategy,model),g in joined.groupby(
            ["scenario","strategy","model_size"],dropna=False):
        for offset in range(18):
            at_risk=g[g.first_feasible_eval.notna() &
                      (g.first_feasible_eval+offset<=20)]
            n=len(at_risk)
            near=int((at_risk.first_near_best<=
                      at_risk.first_feasible_eval+offset).sum())
            rows.append(dict(
                scenario=scenario,strategy=strategy,model_size=model,
                offset=offset,n_at_risk=n,near_best=near,
                conditional_pct=100*near/n if n else np.nan,
                all_scheduled_n=len(g)))
    result=pd.DataFrame(rows)
    assert len(result)==792
    assert (result.n_at_risk>=result.near_best).all()
    assert result.groupby(["scenario","strategy","model_size"],
                          dropna=False).n_at_risk.apply(
                              lambda x:x.is_monotonic_decreasing).all()
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    result.to_csv(output,index=False)
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--raw-tables",type=Path,required=True)
    p.add_argument("--output",type=Path,
                   default=HERE/"data/fig10_full_risk_sets.csv")
    a=p.parse_args()
    make(a.raw_tables,a.output)
