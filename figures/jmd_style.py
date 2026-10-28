"""Figure style of the paper.

Text is Helvetica and math Computer Modern through usetex, with a full axes box, outward ticks, bold panel labels,
the Paul Tol colour-blind-safe palette and baselines in black and grey. Figures are drawn 11.2 in wide with 16 to
20 pt fonts, so that the text is 10 to 12.5 pt at 7.0 in.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.lines import Line2D

COL = {"Cold": "#000000", "Source-warm": "#ADADAD", "VF": "#117733", "WS": "#CC6677", "ReAct": "#4477AA"}
LS = {"Cold": (0, (4, 2)), "Source-warm": (0, (1.2, 1.4)), "VF": "-", "WS": "-", "ReAct": "-"}
MK = {"Cold": "o", "Source-warm": "s", "VF": "^", "WS": "D", "ReAct": "P"}
STR = ["Cold", "Source-warm", "VF", "WS", "ReAct"]
# Display names in figures (data label -> figure label). The data labels stay unchanged.
DISP = {"Source-warm": "RW"}
disp = lambda m: DISP.get(m, m)
FULL_NAME = {"Cold": "Cold start", "Source-warm": "Reference-warm start", "VF": "Variable fixing",
             "WS": "Warm starting", "ReAct": "ReAct"}
REASON_LABEL = {"reasoning_engineering": "Engineering mechanism", "reasoning_constraint": "Constraint targeted",
                "reasoning_objective": "Objective targeted", "reasoning_opt_history": "Optimization history",
                "reasoning_source_ref": "Reference design"}
VAR_TEX = {"x_chem": r"$x^{C}$", "x_T": r"$x^{T}$", "x_S": r"$x^{S}$", "x_MPR": r"$x^{M}$", "x_R": r"$x^{R}$"}
SC = ["S1", "S2", "S3", "S4"]
GREY_REF = "#555555"
SEQ = "#4477AA"          # sequential heatmap hue (dark end)
import os
_K = float(os.environ.get("JMD_FONT_SCALE", "1.7"))   # scale of the in-figure text
FS = dict(tick=round(16*_K), label=round(20*_K), panel=round(20*_K), legend=round(17*_K), annot=round(15*_K), small=round(13*_K))

plt.rcParams.update({
    "text.usetex": True,
    "font.family": "sans-serif",
    "text.latex.preamble": r"\usepackage{helvet}\renewcommand{\familydefault}{\sfdefault}\usepackage{amsmath}\usepackage{amssymb}",
    "font.size": FS["tick"], "axes.labelsize": FS["label"], "axes.titlesize": FS["panel"],
    "xtick.labelsize": FS["tick"], "ytick.labelsize": FS["tick"], "legend.fontsize": FS["legend"],
    "axes.linewidth": 1.4, "xtick.major.width": 1.3, "ytick.major.width": 1.3,
    "xtick.major.size": 7, "ytick.major.size": 7, "xtick.direction": "out", "ytick.direction": "out",
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "pdf.fonttype": 42, "ps.fonttype": 42, "legend.frameon": True, "legend.edgecolor": "black",
    "legend.fancybox": False, "legend.framealpha": 1.0,
})
_plain = FuncFormatter(lambda v, _: f"{v:g}")

def plain_ticks(ax, x=True, y=True):
    """Helvetica digits on numeric axes (usetex would otherwise typeset tick numbers in CM math)."""
    if x: ax.xaxis.set_major_formatter(_plain)
    if y: ax.yaxis.set_major_formatter(_plain)

def panel_label(ax, letter, text="", pad=8):
    ax.set_title(r"\textbf{(%s)} %s" % (letter, text) if text else r"\textbf{(%s)}" % letter, loc="left", pad=pad, fontsize=FS["panel"])

def shared_legend(fig, methods=STR, labels=None, y=0.01, ncol=None, linestyle=True):
    labels = labels or [disp(m) for m in methods]
    handles = [Line2D([0], [0], color=COL[m], lw=2.6, ls=LS[m] if linestyle else "-",
                      marker=None if linestyle else MK[m], ms=9, mec="white", mew=0.8) for m in methods]
    return fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, y), ncol=ncol or len(methods),
                      fontsize=FS["legend"], columnspacing=1.4, handlelength=2.8, handletextpad=0.7, borderpad=0.55)

def direct_labels(ax, ends, x, min_gap, fontsize=None, bold=True, ymax=None, order=None):
    """ends: list of (y, name) or (y, tiebreak, name). Labels keep their curve height where possible. Overlaps are
    resolved by pushing up, and if the stack exceeds the top limit only the upper labels are pushed down."""
    ends = [(e[0], e[1] if len(e) == 3 else 0, e[-1]) for e in ends]
    ends = sorted(ends, key=lambda e: (round(e[0], 6), e[1])); ends = [(y, name) for y, _, name in ends]
    ys = []; last = -1e9
    for y, name in ends:
        y = max(y, last + min_gap); last = y; ys.append(y)
    top = ymax if ymax is not None else ax.get_ylim()[1]
    lim = top - 0.4 * min_gap
    for i in range(len(ys) - 1, -1, -1):              # descending pass: push down only what exceeds the limit
        cap = lim if i == len(ys) - 1 else ys[i + 1] - min_gap
        if ys[i] > cap: ys[i] = cap
    for (y0, name), y in zip(ends, ys):
        ax.text(x, y, (r"\textbf{%s}" % disp(name)) if bold else disp(name), color=COL[name], fontsize=fontsize or FS["annot"], va="center", ha="left", clip_on=False)

def full_box(ax):
    """Heatmaps / images keep the full frame."""
    for k in ("top", "right"): ax.spines[k].set_visible(True)

def save(fig, path_base, dpi=300):
    fig.savefig(path_base + ".pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(path_base + ".png", dpi=dpi, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print("saved", path_base)
