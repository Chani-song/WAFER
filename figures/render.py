"""Figs. 6, 7 and 8 of the paper, from the plotted-value tables in figures/data/."""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.colors import LinearSegmentedColormap
from .data import wilson
from . import data
from .style import apply, ticks, panel, save, COL, LS, MK, GREENS, RBLUE, WINE, GOLD
from .jmd_style import disp

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"outputs"
SC=("S1","S2","S3","S4")
OPT=("axbo","mads","mixed_ga","random")
OPTL=("BO","MADS","GA","Rand.")
PRINT=14.7  # page pt that prints at >=7.5 pt in the 240.7 pt column (TeX sets 14.7 as ~14.64)

def label_ends(ax,ends,x,gap,ymax,fs):
    """Direct labels at the curve ends in the style of Fig. 4. ends: (y, tiebreak, text, color). Overlapping labels
    are pushed up, and if the stack exceeds ymax only the upper labels are pushed down."""
    vals=sorted(ends,key=lambda e:(round(e[0],6),e[1]));ys=[]
    for y,*_ in vals:ys.append(max(y,ys[-1]+gap) if ys else y)
    for i in range(len(ys)-1,-1,-1):ys[i]=min(ys[i],ymax if i==len(ys)-1 else ys[i+1]-gap)
    for (_,_,text,color),y in zip(vals,ys):
        ax.text(x,y,r"\textbf{%s}"%text,color=color,fontsize=fs,ha="left",va="center",clip_on=False)

def fig09():
    """Fig. 7: WS runs within 2% of the best design against the number of starts that BatPaC evaluates as feasible."""
    apply("wide")
    d=data.read("fig09_initial_feasible_bins")
    # Row gap and bottom margin reduced by the space the run counts used (pt).
    H0=5.2*72;ROW=15.5;BOT=8.4;LEG=29.6;H=H0-ROW-BOT+LEG;h=(.924-.25)*H0/2.55
    fig,axs=plt.subplots(2,2,figsize=(6.5,H/72),sharex=True,sharey=True)
    fig.subplots_adjust(left=.135,right=.962,bottom=(.25*H0-BOT+LEG)/H,top=1-.076*H0/H,
                        wspace=.17,hspace=(.55*h-ROW)/h)
    for i,(ax,s) in enumerate(zip(axs.flat,SC)):
        g=d[d.scenario.eq(s)].set_index("n_initial_feasible").reindex(range(4),fill_value=0)
        den=g.n.replace(0,np.nan)
        series=(("feasible",COL["WS"],"-","o"),("final",WINE,"--","s"))
        for key,color,ls,mk in series[1:]:
            vals=100*g[key]/den
            ax.plot(range(4),vals,ls=ls,color=color,lw=1.6,zorder=3)
            for k in range(4):
                if g.loc[k,"n"]==0:continue
                lo,hi=wilson(int(g.loc[k,key]),int(g.loc[k,"n"]))
                ax.errorbar(k,vals.loc[k],yerr=[[max(0,vals.loc[k]-100*lo)],
                                                 [max(0,100*hi-vals.loc[k])]],
                            fmt="none",ecolor=color,elinewidth=.8,capsize=2,zorder=2)
                ax.plot(k,vals.loc[k],mk,ms=5.8,color=color,
                        mfc="white" if g.loc[k,"n"]<10 else color,
                        mec="#333",mew=.55,zorder=4)
        ax.set(xticks=range(4),xlim=(-.35,3.35),ylim=(0,105),yticks=[0,50,100])
        ticks(ax);panel(ax,"abcd"[i],s)
    fig.supylabel("Runs (\\%)",x=.02,y=(.587*H0-BOT-ROW/2+LEG)/H,fontsize=19.5)
    fig.supxlabel("Feasible warm-start candidates (of 3)",x=.5485,y=(.105*H0+LEG)/H,fontsize=19.5)
    handles=[Line2D([0],[0],color=COL["WS"],lw=1.5,marker="o",ms=5),
             Line2D([0],[0],color=WINE,lw=1.5,ls="--",marker="s",ms=5),
             Line2D([0],[0],ls="none",marker="s",ms=5.8,mfc="white",mec="#333",mew=.55)]
    labels=["Feasible discovery","Within 2\\% of best","Fewer than 10 runs"]
    handles,labels=handles[1:],labels[1:]
    fig.legend(handles,labels,
               loc="lower center",bbox_to_anchor=(.5,.022*H0/H),ncol=2,
               fontsize=plt.rcParams["xtick.labelsize"],handlelength=1.2,handletextpad=.4,columnspacing=.65)
    save(fig,OUT/"R4_WS_feasible_starts_nearbest")
    plot=d.copy()
    for count,stem in (("feasible","discovery"),("final","near_best")):
        ci=np.array([wilson(int(k),int(n)) for k,n in zip(plot[count],plot.n)])
        plot[stem+"_pct"]=100*plot[count]/plot.n.replace(0,np.nan)
        plot[stem+"_lo_pct"]=100*ci[:,0]
        plot[stem+"_hi_pct"]=100*ci[:,1]
    plot.to_csv(OUT/"R4_WS_feasible_starts_nearbest.csv",index=False)

def fig07(scen=("S1","S3"),name="R3a_VF_mask_outcomes",selection_pass=False,stacked=False):
    """Fig. 6: feasible discovery and attainment of a gap of at most 2% of the VF masks in S1 and S3."""
    apply("dense")
    d=data.read("fig07_mask_rates")
    exact=data.read("fig07_exact_counts")
    for selection in ((False,True) if selection_pass else (False,)):
        H0=3.45*72;LEG=12.9;H=H0+LEG  # LEG: extra bottom room for the larger labels
        if stacked:  # one full-width panel per scenario, for scenarios with many masks
            Hs=6.6*72+19  # 19 pt: legend clear of the rotated mask labels
            fig,axs=plt.subplots(2,1,figsize=(6.5,Hs/72))
            fig.subplots_adjust(left=.145,right=.985,bottom=(.15*6.6*72+19)/Hs,top=1-.055*6.6*72/Hs,hspace=.72)
        else:
            fig,axs=plt.subplots(1,2,figsize=(6.5,H/72))
            fig.subplots_adjust(left=.145,right=.985,bottom=(.31*H0+LEG)/H,top=1-.11*H0/H,wspace=.23)
        for i,(ax,s) in enumerate(zip(axs,scen)):
            g=d[(d.scenario==s)&(d.n>=20)].copy()
            masks=data.mask_order(g)
            g=g.set_index("mask").loc[masks]
            x=np.arange(len(g));w=.34
            if selection:
                bottom=np.zeros(len(g))
                for m in (4,9,27):
                    counts=exact[(exact.scenario==s)&(exact.model_size==m)].groupby("mask").n.sum().reindex(masks,fill_value=0).to_numpy()
                    ax.bar(x,counts,bottom=bottom,width=.76,color=GREENS[m],ec="#333",lw=.45,label=f"{m}B")
                    bottom+=counts
                ax.set_ylim(0,max(550,float(bottom.max())*1.08))
            else:
                ax.bar(x-w/2,100*g.feasible/g.n,w,color=GREENS[27],ec="#333",lw=.45,label="Feasible")
                ax.bar(x+w/2,100*g.near_best/g.n,w,color=GREENS[9],ec="#333",lw=.45,label="Within 2\\%")
                for k,counts in ((-w/2,g.feasible),(w/2,g.near_best)):
                    ci=np.array([wilson(int(v),int(n)) for v,n in zip(counts,g.n)])
                    rate=100*counts/g.n
                    ax.errorbar(x+k,rate,yerr=[np.maximum(0,rate-100*ci[:,0]),
                                                np.maximum(0,100*ci[:,1]-rate)],
                                fmt="none",ecolor="#222",elinewidth=.8,capsize=1.8,zorder=4)
                ax.set_ylim(0,105);ax.set_yticks([0,25,50,75,100])
            ax.set_xticks(x);ax.set_xticklabels(masks,rotation=35,ha="right",fontsize=PRINT,
                fontfamily="sans-serif");ax.tick_params(axis="y",labelsize=PRINT)
            ax.set_xlim(-.6,len(g)-.4);ticks(ax,x=False);panel(ax,"ab"[i],s,size=17)
            if i==0 or stacked:ax.set_ylabel("VF runs" if selection else "Runs (\\%)",fontsize=PRINT)
        handles=[Patch(facecolor=GREENS[m],ec="#333") for m in (4,9,27)] if selection else [
            Patch(facecolor=GREENS[27],ec="#333"),Patch(facecolor=GREENS[9],ec="#333")]
        labs=["4B","9B","27B"] if selection else ["Feasible discovery","Within 2\\% of best"]
        fig.legend(handles,labs,ncol=len(labs),loc="lower center",bbox_to_anchor=(.53,2.4/Hs if stacked else .01*H0/H),
            fontsize=PRINT,frameon=False,columnspacing=.8,handlelength=1)
        save(fig,OUT/("SI_R3a_mask_selection" if selection else name))
        if not selection:
            plot=d[d.scenario.isin(scen)&d.n.ge(20)].copy()
            for count,stem in (("feasible","discovery"),("near_best","near_best")):
                ci=np.array([wilson(int(k),int(n)) for k,n in zip(plot[count],plot.n)])
                plot[stem+"_lo_pct"]=100*ci[:,0]
                plot[stem+"_hi_pct"]=100*ci[:,1]
            plot.to_csv(OUT/(name+".csv"),index=False)

def fig10():
    """R5 3x2 geometry: all-run strip, then conditional curves."""
    apply("wide")
    cells=data.read("R5_cell_strip")
    curves=data.risk_curves()
    # Heights in pt: top margin, panel (a), gap below (a), panels (b) to (e), gap between their rows, bottom margin.
    TOP,A,GAP_A,P,GAP,BOT=52.6,89.5,56.3,114.7,34,52;H=TOP+A+GAP_A+2*P+GAP+BOT
    LAB=16  # direct labels, as in Fig. 4
    fig=plt.figure(figsize=(6.5,H/72))
    outer=fig.add_gridspec(2,1,height_ratios=[A,2*P+GAP],left=.145,right=.90,bottom=BOT/H,top=1-TOP/H,
        hspace=GAP_A/((A+2*P+GAP)/2))
    gs=outer[1].subgridspec(2,2,wspace=.345,hspace=GAP/P)
    ax=fig.add_subplot(outer[0]);rng=np.random.default_rng(3)
    for i,s in enumerate(SC):
        g=cells[cells.scenario==s]
        for st in ("Cold","Source-warm","VF","WS"):
            h=g[g.strategy==st]
            ax.scatter(i-.20+rng.uniform(-.16,.16,len(h)),h.gap,
                s=14,color=COL[st],alpha=.75,lw=.25,edgecolor="white",zorder=2)
        for m in (4,9,27):
            h=g[(g.strategy=="ReAct")&(g.model_size==m)]
            ax.scatter([i+.24+(.08 if z=="L2" else -.08) for z in h.semantic_level],
                h.gap,s=55,marker="P",color=RBLUE[m],edgecolor="#222",lw=.45,zorder=4)
    ax.set(xticks=range(4),xlim=(-.6,3.6),ylim=(-3,104),yticks=[0,50,100]);ax.set_xticklabels(SC)
    ticks(ax,x=False);ax.set_ylabel("Within 2\\% of best (\\%)",fontsize=17,labelpad=8);panel(ax,"a")
    for j,s in enumerate(SC):
        ax=fig.add_subplot(gs[j//2,j%2]);ends=[]
        for st in ("Cold","Source-warm","VF","WS"):
            g=curves[(curves.scenario==s)&(curves.strategy==st)&
                     (curves.n_at_risk>=20)]
            good=g[np.isfinite(g.gap2_pct)]
            if good.empty:continue
            ax.plot(g.k_after_first_feasible,g.gap2_pct,color=COL[st],ls=LS[st],
                lw=1.6 if st in ("VF","WS") else 1.25,zorder=3)
            z=good.iloc[-1]
            ax.plot(z.k_after_first_feasible,z.gap2_pct,MK[st],color=COL[st],ms=5.5,
                mec="#333",mew=.4,zorder=5,clip_on=False)
            ends.append((z.gap2_pct,good.gap2_pct.sum(),disp(st),COL[st]))
        for m in (4,9,27):
            g=curves[(curves.scenario==s)&(curves.strategy==f"ReAct{m}")&
                     (curves.n_at_risk>=20)]
            good=g[np.isfinite(g.gap2_pct)]
            if good.empty:continue
            ax.plot(g.k_after_first_feasible,g.gap2_pct,color=RBLUE[m],
                lw=2.05 if m==27 else 1.4,zorder=4 if m==27 else 3)
            z=good.iloc[-1]
            ax.plot(z.k_after_first_feasible,z.gap2_pct,"P",color=RBLUE[m],
                ms=6 if m==27 else 5.3,mec="#333",mew=.4,zorder=6,clip_on=False)
            ends.append((z.gap2_pct,good.gap2_pct.sum(),f"{m}B",RBLUE[m]))
        ax.set(xlim=(0,17),ylim=(0,104),xticks=[0,5,10,15],yticks=[0,50,100]);ticks(ax);panel(ax,"bcde"[j],s)
        # Tick labels on the outer panels only, as in Fig. 4.
        ax.tick_params(labelbottom=j>=2,labelleft=j%2==0)
        label_ends(ax,ends,17.9,gap=1.1*LAB*104/P,ymax=101,fs=LAB)
    b,e=fig.axes[1].get_position(),fig.axes[4].get_position()
    fig.text((b.x0+e.x1)/2,12/H,"Evaluations after first feasible design",ha="center",fontsize=17)
    fig.text(.018,(b.y1+e.y0)/2,
        "Feasible runs within 2\\% of best (\\%)",rotation=90,va="center",fontsize=17)
    save(fig,OUT/"R5_ReAct_postfeasibility")
    plotted=curves[curves.n_at_risk.ge(20)].copy()
    plotted["panel"]="risk_set"
    intervals=np.array([wilson(int(k),int(n)) for k,n in zip(
        plotted.near_best,plotted.n_at_risk)])
    plotted["ci_lo_pct"]=100*intervals[:,0]
    plotted["ci_hi_pct"]=100*intervals[:,1]
    top=cells.copy();top["panel"]="configuration"
    import pandas as pd
    pd.concat([top,plotted],ignore_index=True).to_csv(
        OUT/"R5_ReAct_postfeasibility.csv",index=False)
