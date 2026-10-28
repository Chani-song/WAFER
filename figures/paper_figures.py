"""Figs. 3 to 5 and 9 to 12 of the paper, from the plotted-value tables in figures/data/ and the result tables."""
from pathlib import Path
import re
import sys
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Ellipse, ConnectionPatch, Rectangle
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import Bbox, TransformedBbox

from . import data
from .style import apply, ticks, panel, save, COL, LS, MK, GREENS, RBLUE, WINE, GOLD
from .jmd_style import STR, disp, FULL_NAME, REASON_LABEL, VAR_TEX, GREY_REF

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"outputs"
RAW=data.result_tables()
SC=("S1","S2","S3","S4")
METHODS=("Cold","Source-warm","VF","WS","ReAct")
PRINT=14.7  # page pt that prints at >=7.5 pt in the 240.7 pt column (TeX sets 14.7 as ~14.64)
FULL=FULL_NAME

def export(name,frame):
    OUT.mkdir(exist_ok=True)
    frame.to_csv(OUT/(name+".csv"),index=False)

# Settings and helpers of the R1 figures.
V8_RC={'text.usetex':True,'font.family':'sans-serif','font.sans-serif':['Helvetica'],'text.latex.preamble':r'\usepackage{helvet}\renewcommand{\familydefault}{\sfdefault}\usepackage{amsmath}\usepackage{amssymb}','font.size':10,'axes.labelsize':11,'axes.titlesize':11,'xtick.labelsize':10,'ytick.labelsize':10,'legend.fontsize':9,'axes.linewidth':.7,'xtick.major.width':.65,'ytick.major.width':.65,'xtick.major.size':3,'ytick.major.size':3,'xtick.direction':'out','ytick.direction':'out','axes.spines.top':False,'axes.spines.right':False,'axes.grid':False,'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white','pdf.fonttype':42,'ps.fonttype':42,'legend.frameon':False,'axes.labelpad':7,'xtick.major.pad':4,'ytick.major.pad':4}

def _v8_panel(ax,letter,s=''):
    ax.set_title(r'\textbf{(%s)}'%letter+(' '+s if s else ''),loc='left',fontweight='bold',pad=7)

def _v8_label_ends(ax,ends,x,gap=.09,ymax=1,fs=8.5,scale=1):
    # Positions are resolved in fraction units, then drawn at scale.
    vals=sorted(ends,key=lambda e:(round(e[0],6),e[1])); ys=[]
    for y,area,st in vals:ys.append(max(y,ys[-1]+gap) if ys else y)
    for i in range(len(ys)-1,-1,-1):ys[i]=min(ys[i],ymax-.01 if i==len(ys)-1 else ys[i+1]-gap)
    for (actual,area,st),y in zip(vals,ys):
        ax.text(x,scale*y,r'\textbf{%s}'%disp(st),color=COL[st],fontsize=fs,fontweight='bold',ha='left',va='center',clip_on=False)

def _v8_scale(fig):
    # Font and line scaling applied before export.
    scale=1.7 if fig.get_figwidth()>4 else 1.25
    minimum=16 if fig.get_figwidth()>4 else 9.5
    for t in fig.findobj(matplotlib.text.Text):
        text=t.get_text()
        text=re.sub(r'(?<!\\)%',r'\\%',text)
        t.set_text(text);t.set_fontfamily('sans-serif');t.set_usetex(True)
        t.set_fontsize(max(minimum,t.get_fontsize()*scale))
    for a in fig.axes:
        for line in a.lines:
            line.set_linewidth(line.get_linewidth()*1.35)
            line.set_markersize(line.get_markersize()*1.4)
    fig.set_dpi(72)

def _r1(q,ylab,name,bands=True):
    """R1 layout in percent: median curves (and IQR bands), direct labels, no legend."""
    H0=5.55;CUT=31.6/72;H=H0-CUT
    f=lambda y:(y*H0-CUT)/H
    with plt.rc_context(V8_RC):
        fig,axs=plt.subplots(2,2,figsize=(6.5,H),sharex=True,sharey=True)
        fig.subplots_adjust(left=.137,right=.886,bottom=f(.23),top=f(.927),hspace=.36,wspace=.39)
        for i,(ax,s) in enumerate(zip(axs.flat,SC)):
            ends=[]
            for st in STR:
                g=q[(q.scenario==s)&(q.strategy==st)]
                if bands and st in ['VF','WS','ReAct']:ax.fill_between(g.k,100*g.q1,100*g.q3,color=COL[st],alpha=.13,lw=0,zorder=1)
                ax.plot(g.k,100*g.med,color=COL[st],ls=LS[st],lw=1.65 if st in ['VF','WS','ReAct'] else 1.35,zorder=3)
                ends.append((g.med.iloc[-1],g.med.sum(),st))
            _v8_label_ends(ax,ends,20.65,gap=.15,ymax=1.02,fs=8.5,scale=100)
            ax.set(xlim=(0,20),ylim=(0,102),xticks=[0,10,20],yticks=[0,50,100]);ticks(ax);_v8_panel(ax,'abcd'[i],s)
        fig.supxlabel(r'Accepted simulator evaluations $k$',x=.5115,y=f(.10),fontsize=11.5)
        fig.supylabel(ylab,x=.030,y=f((.23+.927)/2),fontsize=11.5,ha='center',va='center')
        _v8_scale(fig)
        save(fig,OUT/name)

def fig03_04():
    # Values are drawn in percent, with no legend and the bottom trimmed.
    for col,table,ylab,name in (("cum_feas","R1_cum_feas","Feasible discovery (%)","R1a_discovery"),
                                ("cum_gap2","R1_cum_gap2","Runs within 2% of best (%)","R1b_gap2pct")):
        q=pd.read_csv(data.DATA/(table+".csv"))
        _r1(q,ylab,name)
        export(name,q)

def fig05():
    apply("wide")
    med=pd.read_csv(data.DATA/"R2_medians_iqr.csv")
    cp=pd.read_csv(data.DATA/"R2_configuration_points.csv")
    best=pd.read_csv(RAW/"runs.csv.gz",usecols=["scenario","final_best_feasible_cost"]).groupby(
        "scenario").final_best_feasible_cost.min().to_dict()
    # Costs are drawn as the gap to the best-known cost of the scenario, in percent.
    med=med.assign(best_known_cost=med.scenario.map(best))
    for c in ("y_median","y_q1","y_q3"):med[c+"_gap_pct"]=100*(med[c]/med.best_known_cost-1)
    cp=cp.assign(best_known_cost=cp.scenario.map(best))
    cp["median_final_cost_gap_pct"]=100*(cp.median_final_cost/cp.best_known_cost-1)
    H0=5.65;CUT=24.1/72;H=H0-CUT
    f=lambda y:(y*H0-CUT)/H
    fig,axs=plt.subplots(2,2,figsize=(6.5,H))
    fig.subplots_adjust(left=.145,right=.956,bottom=f(.20),top=f(.93),hspace=.39,wspace=.29)
    for i,(ax,s) in enumerate(zip(axs.flat,SC)):
        m=med[med.scenario.eq(s)].set_index("strategy")
        points=cp[cp.scenario.eq(s)]
        ymin=m.y_q1_gap_pct.min();ymax=m.y_q3_gap_pct.max();span=ymax-ymin
        coincident=[st for st in METHODS if abs(m.loc[st,"x_median"]-100)<2 and
                    abs(m.loc[st,"y_median"]-m.loc["Source-warm","y_median"])<.01] if s=="S1" else []
        for st in METHODS:
            g=points[points.strategy.eq(st)]
            pts=ax.scatter(g.discovery_pct,g.median_final_cost_gap_pct,s=9,color=COL[st],alpha=.18,lw=0,zorder=2)
            # Clipped at the axes except below, so that points at a gap of 0 are drawn whole.
            pts.set_clip_box(TransformedBbox(Bbox([[0,-.05],[1,1]]),ax.transAxes))
            if st in coincident:continue
            z=m.loc[st]
            ax.errorbar(z.x_median,z.y_median_gap_pct,xerr=[[z.x_median-z.x_q1],[z.x_q3-z.x_median]],
                        yerr=[[z.y_median_gap_pct-z.y_q1_gap_pct],[z.y_q3_gap_pct-z.y_median_gap_pct]],fmt=MK[st],
                        color=COL[st],ms=7.8,capsize=0,elinewidth=1,mec="white",mew=.5,zorder=5)
        if coincident:
            z=m.loc[coincident[0]]
            zoom=(z.x_median,z.y_median_gap_pct)
            ins=ax.inset_axes([.49,.53,.48,.45]);ins.set_facecolor("white")
            for sp in ins.spines.values():sp.set_visible(True);sp.set_linewidth(.55)
            rng=np.random.default_rng(1)
            for j,st in enumerate(coincident):
                g=points[points.strategy.eq(st)]
                ins.scatter(np.full(len(g),j)+rng.uniform(-.09,.09,len(g)),g.median_final_cost_gap_pct,
                            s=5,color=COL[st],alpha=.25,lw=0)
                z=m.loc[st]
                ins.errorbar(j,z.y_median_gap_pct,yerr=[[z.y_median_gap_pct-z.y_q1_gap_pct],
                                                        [z.y_q3_gap_pct-z.y_median_gap_pct]],
                             fmt=MK[st],color=COL[st],ms=5.8,elinewidth=.85,capsize=0,
                             mec="#333",mew=.4,zorder=5)
            ins.set(xlim=(-.65,3),ylim=(1.33,12.87),xticks=[],yticks=[])
            # Label centres. The ReAct label has no room inside the inset at tick size,
            # so it sits above the inset frame, flush with its right edge.
            for j,st in enumerate(coincident):
                if st=="ReAct":
                    ins.text(1,1.03,r"\textbf{ReAct}",transform=ins.transAxes,clip_on=False,
                             fontsize=plt.rcParams["xtick.labelsize"],ha="right",va="bottom",color=COL[st])
                    continue
                x,y={"Source-warm":(0.07,6.22),"WS":(1.80,5.65)}[st]
                ins.text(x,y,r"\textbf{%s}"%disp(st),
                         fontsize=plt.rcParams["xtick.labelsize"],
                         ha="center",va="center",color=COL[st])
            ins.tick_params(left=False,bottom=False)
            ins.axhline(z.y_median_gap_pct,color="#777",ls="--",lw=.5)
        if s=="S1":ax.axhline(100*(106.732312784183/best[s]-1),color="#777",lw=.6,ls="--",zorder=1)
        ax.set(xlim=(0,110),xticks=[0,50,100],ylim=(0,ymax+(.75 if s=="S1" else .18)*span))
        ax.yaxis.set_major_locator(MaxNLocator(4));ticks(ax);panel(ax,"abcd"[i],s)
        if coincident:
            # The zoomed region: a rectangle with the aspect of the inset frame, centred on the coincident medians,
            # with the frame's edge colour and width, and one straight arrow to the frame.
            (x0,x1),(y0,y1)=ax.get_xlim(),ax.get_ylim()
            fw=.07;fh=fw*.45/.48
            dx,dy=fw*(x1-x0),fh*(y1-y0)
            frame=ins.spines["bottom"]
            ax.add_patch(Rectangle((zoom[0]-dx/2,zoom[1]-dy/2),dx,dy,fill=False,ec=frame.get_edgecolor(),
                                   lw=frame.get_linewidth(),zorder=6))
            ax.annotate("",xy=((zoom[0]-x0)/(x1-x0),.53),xycoords="axes fraction",
                        xytext=(zoom[0],zoom[1]+dy/2),textcoords="data",
                        arrowprops=dict(arrowstyle="-|>",color=GREY_REF,lw=1.3,mutation_scale=12,shrinkA=0,shrinkB=0),
                        zorder=6)
        # Direct labels use panel-specific positions to leave the inset,
        # markers and reference lines legible at manuscript print width.
        # Label centres at tick size, each the nearest spot beside its own marker
        # that clears markers, whiskers, other labels and the inset frame.
        positions={
            "S1":{"Cold":(14.50,19.49),"VF":(64.35,7.12)},
            "S2":{"Cold":(24.19,6.30),"VF":(19.09,7.70),"Source-warm":(36.68,3.53),
                  "WS":(52.22,1.90),"ReAct":(84,1.30)},
            "S3":{"Cold":(28.37,15.09),"Source-warm":(55.42,11.92),"VF":(46,1.60),
                  "WS":(64,5.50),"ReAct":(96,5.00)},
            "S4":{"Cold":(47.37,14.31),"Source-warm":(86.42,6.31),"VF":(19.08,9.93),
                  "WS":(94.65,3.20),"ReAct":(52.86,1.30)},
        }
        for st in METHODS:
            if st in coincident:continue
            x,y=positions[s][st]
            ax.text(x,y,r"\textbf{%s}"%disp(st),
                    ha="center",va="center",fontsize=plt.rcParams["xtick.labelsize"],color=COL[st],zorder=8)
    fig.supxlabel("Feasible discovery (\\%)",x=.5505,y=f(.08),fontsize=19.5)
    fig.supylabel("Median cost gap to best known (\\%)",x=.022,y=f(.58),fontsize=19.5)
    save(fig,OUT/"R2_strategy_fit_map")
    export("R2_strategy_fit_map",pd.concat([med.assign(layer="median_iqr"),
                                           cp.assign(layer="configuration_point")],ignore_index=True))

def fig08u(gray=False):
    """Fig. 12: feasible fraction of the evaluations in S2 and S4 by x^R and x^MPR (one column per value 1 to 10 and >=11).

    The cells come from data/fig08u_{S2,S4}_evaluated_grid.csv. gray=True, the version in the paper, draws a cell
    without any evaluation in light gray.
    """
    cm=LinearSegmentedColormap.from_list("green",["#FFFFFF","#CFE5D6","#5FA97A","#117733"])
    if gray:cm.set_bad("#D9D9D9")
    apply("heat")
    H0=2.95*72;EXTRA=10.1;H=H0+EXTRA  # EXTRA: bottom room for the larger labels
    fig,axs=plt.subplots(1,2,figsize=(6.5,H/72))
    # Narrower gap and right margin: wider cells for the tick labels, room for the colorbar label.
    fig.subplots_adjust(left=.105,right=.848,top=1-.166*H0/H,bottom=(.217*H0+EXTRA)/H,wspace=.146)
    bins=[str(v) for v in range(1,11)]+["11+"]
    full={k:(b if b!="11+" else "≥11") for k,b in enumerate(bins)}
    all_cells=[]
    for j,(ax,s,mask) in enumerate(zip(axs,("S2","S4"),("FFFRR","FRRRR"))):
        z=data.read("fig08u_"+s+"_evaluated_grid").copy();z["mpr_bin"]=z.mpr_bin.astype(str)
        all_cells.append(z.assign(scenario=s))
        F=z.pivot(index="x_R",columns="mpr_bin",values="fraction").reindex(index=[1,2,3,4],columns=bins)
        im=ax.imshow(np.ma.masked_invalid(F.to_numpy(dtype=float)),origin="lower",cmap=cm,vmin=0,vmax=1,aspect="auto")
        for side in ("top","right"):ax.spines[side].set_visible(True)
        ax.set_xticks(range(11));ax.set_xticklabels([full[k] for k in range(11)],
                                                    fontsize=PRINT,usetex=False,fontfamily="Helvetica")
        ax.set_yticks(range(4));ax.set_yticklabels([1,2,3,4],fontsize=PRINT)
        # Superscripts set at 0.7 of the base size print at 7.5 pt with a 21 pt base.
        ax.set_xlabel(VAR_TEX["x_MPR"],fontsize=21,loc="center")
        if j==0:ax.set_ylabel(VAR_TEX["x_R"],fontsize=21)
        ax.set_title(r"\textbf{(%s)} "%"ab"[j]+s+", "+mask,loc="left",fontsize=16.5)
        ax.add_patch(Rectangle((.5,2.5),1,1,fill=False,ec=GOLD,lw=1.7))  # reference design: x^R = 4, x^MPR = 2
    # If neighbouring x^MPR labels overlap at the printed size, label 1, 3, 5, 7, 9 and >=11 only (all 11 cells stay).
    # Centred labels collide when half their widths together exceed the column spacing.
    rend=fig.canvas.get_renderer()
    w=[rend.get_text_width_height_descent(l.get_text(),l.get_fontproperties(),ismath=False)[0]
       for l in axs[0].get_xticklabels()]
    col=axs[0].get_window_extent(rend).width/11
    if any((a+b)/2>col for a,b in zip(w,w[1:])):
        for ax in axs:
            ax.set_xticks([0,2,4,6,8,10]);ax.set_xticklabels([full[k] for k in (0,2,4,6,8,10)],
                                                            fontsize=PRINT,usetex=False,fontfamily="Helvetica")
    pos=axs[1].get_position()
    cb=fig.colorbar(im,cax=fig.add_axes([pos.x1+.018,pos.y0,.018,pos.height]),orientation="vertical")
    cb.set_ticks([0,.5,1]);cb.set_ticklabels(["0","0.5","1"])
    cb.ax.tick_params(labelsize=PRINT)
    cb.set_label("Feasible fraction",fontsize=PRINT)
    name="R3b_VF_feasible_subspace_mpr1"+("_gray" if gray else "")
    save(fig,OUT/name)
    export(name,pd.concat(all_cells,ignore_index=True))

def fig11_split():
    """Fig. 9: shift of the ReAct reasoning categories after the first feasible design, by model size."""
    apply("wide")
    cats=["reasoning_engineering","reasoning_constraint","reasoning_objective",
          "reasoning_opt_history","reasoning_source_ref"]
    steps=pd.read_csv(RAW/"react_steps.csv.gz",usecols=["run_id","model_size",
        "pre_or_post_feasibility"]+cats)
    steps=steps[steps.pre_or_post_feasibility.isin(["pre-feasible","post-feasible"])]
    rows=[]
    for m in (4,9,27):
        sub=steps[steps.model_size.eq(m)]
        for phase in ("pre-feasible","post-feasible"):
            ph=sub[sub.pre_or_post_feasibility.eq(phase)]
            run=ph.groupby("run_id")[cats].agg(["sum","count"])
            nrun=len(run)
            rng=np.random.default_rng(20260930+int(m)+(0 if phase=="pre-feasible" else 100))
            picks=rng.integers(0,nrun,size=(2000,nrun))
            for c in cats:
                success=run[(c,"sum")].to_numpy();total=run[(c,"count")].to_numpy()
                point=100*success.sum()/total.sum()
                boot=100*success[picks].sum(axis=1)/total[picks].sum(axis=1)
                rows.append(dict(model=m,category=c,phase=phase,prevalence_pct=point,
                                 ci_lo=np.quantile(boot,.025),ci_hi=np.quantile(boot,.975),
                                 n_steps=int(total.sum()),n_runs=nrun))
    values=pd.DataFrame(rows)
    # Horizontal bars, so that the category names fit on one line. Heights in pt.
    TOP,AX,BOT=12,200,92;H=TOP+AX+BOT
    fig,ax=plt.subplots(figsize=(6.5,H/72))
    fig.subplots_adjust(left=.405,right=.965,bottom=BOT/H,top=1-TOP/H)
    y=np.arange(5);w=.27;sizec={4:"#BBBBBB",9:"#6699CC",27:"#1B3A5C"}
    for i,m in enumerate((4,9,27)):
        for j,c in enumerate(cats):
            g=values[(values.model.eq(m))&(values.category.eq(c))].set_index("phase")
            b=g.loc["pre-feasible"];a=g.loc["post-feasible"];yi=j+(i-1)*w
            ax.barh(yi,b.prevalence_pct,w,color=sizec[m],edgecolor="#333",lw=.4,alpha=.45,zorder=2)
            ax.plot([b.prevalence_pct,a.prevalence_pct],[yi,yi],color="#333",lw=.85,zorder=4)
            ax.plot(a.prevalence_pct,yi,"<" if a.prevalence_pct<b.prevalence_pct else ">",
                    color=sizec[m],mec="#333",mew=.4,ms=5,zorder=5)
    # First category and the 4B bar at the top.
    ax.set(yticks=y,xlim=(0,100),xticks=[0,50,100],ylim=(4.6,-.6))
    ax.set_yticklabels([REASON_LABEL[c] for c in cats],
                       fontsize=plt.rcParams["ytick.labelsize"])
    ticks(ax,y=False);ax.set_xlabel("Steps with category (\\%)",fontsize=17)
    fig.legend([Patch(facecolor=sizec[m],edgecolor="#333",alpha=.45) for m in (4,9,27)],
               ["4B","9B","27B"],ncol=3,loc="lower center",bbox_to_anchor=((.405+.965)/2,2/H),
               fontsize=plt.rcParams["ytick.labelsize"])
    save(fig,OUT/"R5b_ReAct_mechanism_b")
    export("R5b_ReAct_mechanism_b",values)

def fig12():
    apply("heat")
    d=pd.read_csv(data.DATA/"R6_model_size.csv")
    fig,axs=plt.subplots(2,4,figsize=(6.5,4.85),sharey="row",sharex=True)
    # Wider right margin for the larger direct labels. The panel gaps shrink so the
    # panels keep their width.
    W=(.923-.193)/(4+3*.2);RIGHT=.899
    fig.subplots_adjust(left=.193,right=RIGHT,bottom=.165,top=.907,
                        wspace=((RIGHT-.193)/W-4)/3,hspace=.45)
    for r,(metric,label,letters) in enumerate((("feasible_found","Feasible\ndiscovery (\\%)","abcd"),
        ("within2pct","Runs within 2\\%\nof best (\\%)","efgh"))):
        for j,s in enumerate(SC):
            ax=axs[r,j]
            for st in ("VF","WS","ReAct"):
                g=d[(d.metric.eq(metric))&(d.scenario.eq(s))&(d.strategy.eq(st))].sort_values("model")
                ax.plot([0,1,2],g.med,color=COL[st],lw=1.5,marker=MK[st],ms=4.6,
                        mec="white",mew=.35,zorder=3)
                if st!="ReAct":ax.errorbar([0,1,2],g.med,yerr=[g.med-g.q1,g.q3-g.med],
                    fmt="none",ecolor=COL[st],elinewidth=.75,alpha=.65,capsize=1.7,zorder=2)
            ax.set(xticks=[0,1,2],xlim=(-.23,2.23),ylim=(0,104),yticks=[0,50,100])
            ax.set_xticklabels(["4B","9B","27B"],fontsize=PRINT);ax.tick_params(axis="y",labelsize=PRINT)
            ticks(ax,x=False);panel(ax,letters[j],s,size=PRINT)
        axs[r,0].set_ylabel(label,fontsize=PRINT)
    fig.supxlabel("Model size",x=.55,y=.0144,fontsize=15)
    for st in ("VF","WS","ReAct"):
        g=d[(d.metric.eq("within2pct"))&(d.scenario.eq("S4"))&(d.strategy.eq(st))].sort_values("model")
        axs[1,3].text(2.3,g.med.iloc[-1],r"\textbf{%s}"%st,color=COL[st],fontsize=PRINT,
                      ha="left",va="center",clip_on=False)
    save(fig,OUT/"R6_model_size")
    export("R6_model_size",d)

def fig_new_s3():
    apply("si")
    exact=data.read("fig07_exact_counts")
    g=exact[exact.scenario.eq("S3")].groupby(["mask","model_size"],as_index=False).n.sum()
    allmask=exact[exact.scenario.eq("S3")].groupby("mask",as_index=False).n.sum()
    masks=data.mask_order(allmask[allmask.n.ge(20)])
    fig,ax=plt.subplots(figsize=(3.25,2.70))
    fig.subplots_adjust(left=.22,right=.98,bottom=.204,top=.870)
    bottom=np.zeros(len(masks));rows=[]
    for m in (4,9,27):
        h=g[g.model_size.eq(m)].set_index("mask").n.reindex(masks,fill_value=0)
        ax.bar(np.arange(len(masks)),h,bottom=bottom,width=.76,color=GREENS[m],ec="#333",lw=.45,label=f"{m}B")
        for mask,n in h.items():rows.append(dict(scenario="S3",mask=mask,model_size=m,n=int(n)))
        bottom+=h.to_numpy()
    ax.set_xticks(range(len(masks)));ax.set_xticklabels(masks,rotation=35,ha="right",fontsize=9,
                                                    fontfamily="sans-serif")
    ax.set_xlim(-.6,len(masks)-.4);ticks(ax,x=False)
    ax.set_ylabel("VF runs",fontsize=11)
    ax.legend(loc="upper right",fontsize=8,ncol=1,handlelength=1)
    save(fig,OUT/"R6b_VF_mask_selection_S3")
    export("R6b_VF_mask_selection_S3",pd.DataFrame(rows))
