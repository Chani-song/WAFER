"""Builds the grids of Fig. 12, figures/data/fig08u_{S2,S4}_evaluated_grid.csv, from the result tables.

The cells count every accepted evaluation of the VF runs with a valid partition and the mask FFFRR in S2 or FRRRR in S4
by x^R and x^MPR (one column per value 1 to 10 and 11+). Columns: x_R, mpr_bin, n, feasible, fraction (empty when
n = 0).
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
PANELS=(("S2","FFFRR"),("S4","FRRRR"))
MPR=[str(v) for v in range(1,11)]+["11+"]

def cells(raw,scenario,mask):
    raw=Path(raw)
    vf=pd.read_csv(raw/"vf_decisions.csv.gz",usecols=["run_id","scenario","mask","partition_valid"])
    ids=vf[vf.partition_valid.astype(bool)&vf.scenario.eq(scenario)&vf["mask"].eq(mask)].run_id
    t=pd.read_csv(raw/"trajectories.csv.gz",usecols=["run_id","x_R","x_MPR","feasible"])
    t=t[t.run_id.isin(ids)]
    assert t.x_R.between(1,4).all() and t.x_MPR.ge(1).all() and t.x_MPR.notna().all()
    t=t.assign(x_R=t.x_R.astype(int),mpr_bin=np.where(t.x_MPR.ge(11),"11+",t.x_MPR.astype(int).astype(str)))
    g=t.groupby(["x_R","mpr_bin"]).feasible.agg(n="size",feasible="sum")
    g=g.reindex(pd.MultiIndex.from_product([[1,2,3,4],MPR],names=["x_R","mpr_bin"]),fill_value=0).reset_index()
    g["feasible"]=g.feasible.astype(int)
    g["fraction"]=(g.feasible/g.n).where(g.n.gt(0))
    return g

def make(raw,outdir=HERE/"data"):
    out={}
    for scenario,mask in PANELS:
        g=cells(raw,scenario,mask)
        g.to_csv(Path(outdir)/f"fig08u_{scenario}_evaluated_grid.csv",index=False)
        out[scenario]=g
    return out

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--raw-tables",type=Path,required=True)
    p.add_argument("--outdir",type=Path,default=HERE/"data")
    x=p.parse_args()
    make(x.raw_tables,x.outdir)
