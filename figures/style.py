"""Shared figure style: Helvetica text through LaTeX, the method colors and the print widths."""
import os
import shutil
import sys
from pathlib import Path

# LaTeX: `latex` on PATH, or its bin directory in JMD_TEX_BIN. WAFER_TEXMFHOME can add a texmf tree
# with helvet, type1cm, type1ec, underscore and cm-super when the distribution lacks them.
tex_bin = os.environ.get("JMD_TEX_BIN")
if tex_bin: os.environ["PATH"] = tex_bin + os.pathsep + os.environ["PATH"]
tex_home = os.environ.get("WAFER_TEXMFHOME")
if tex_home: os.environ["TEXMFHOME"] = tex_home
if not shutil.which("latex"):
    raise RuntimeError("LaTeX with helvet is required for the Nimbus Sans appearance. Set JMD_TEX_BIN.")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from . import jmd_style

COL, LS, MK = jmd_style.COL, jmd_style.LS, jmd_style.MK
GREENS = {4:"#BFDCC6",9:"#5FA97A",27:"#0E5C2A"}
RBLUE = {4:"#A9C4E0",9:"#4477AA",27:"#1B3A5C"}
WINE, GOLD = "#882255", "#DDAA33"
PREAMBLE = r"\usepackage{helvet}\renewcommand{\familydefault}{\sfdefault}\usepackage{amsmath}\usepackage{amssymb}"

def apply(kind="wide"):
    # Text sizes in points, as measured in the 6.5-inch PDFs.
    if kind == "dense": tick,label,panel,legend,small = 12.0,16.0,17.0,12.0,10.0
    elif kind == "heat": tick,label,panel,legend,small = 10.6,15.9,16.5,12.4,9.4
    elif kind == "si": tick,label,panel,legend,small = 8.7,10.6,11.0,8.5,8.2
    else: tick,label,panel,legend,small = 16.9,19.5,18.6,15.9,12.0
    plt.rcParams.update({
        "text.usetex":True,"font.family":"sans-serif","font.sans-serif":["Helvetica","Nimbus Sans"],
        "text.latex.preamble":PREAMBLE,"font.size":tick,"axes.labelsize":label,
        "axes.titlesize":panel,"xtick.labelsize":tick,"ytick.labelsize":tick,
        "legend.fontsize":legend,"axes.linewidth":.7,"xtick.major.width":.65,
        "ytick.major.width":.65,"xtick.major.size":3,"ytick.major.size":3,
        "xtick.direction":"out","ytick.direction":"out","axes.spines.top":False,
        "axes.spines.right":False,"axes.grid":False,"figure.facecolor":"white",
        "axes.facecolor":"white","savefig.facecolor":"white","pdf.fonttype":42,
        "ps.fonttype":42,"legend.frameon":False,"axes.labelpad":7,
        "xtick.major.pad":4,"ytick.major.pad":4,
    })
    return dict(tick=tick,label=label,panel=panel,legend=legend,small=small)

def ticks(ax,x=True,y=True):
    fmt=FuncFormatter(lambda v,_:f"{v:g}")
    if x:ax.xaxis.set_major_formatter(fmt)
    if y:ax.yaxis.set_major_formatter(fmt)

def panel(ax,letter,scenario="",size=18.6):
    ax.set_title(r"\textbf{(%s)}"%letter+(" "+scenario if scenario else ""),loc="left",pad=7,fontsize=size)

def save(fig,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    # Fixed physical page width, without tight crop or automatic scaling.
    fig.savefig(output.with_suffix(".pdf"),bbox_inches=None)
    try: import fitz  # PyMuPDF, only for the PNG preview
    except ImportError:
        plt.close(fig); return
    with fitz.open(output.with_suffix(".pdf")) as doc:
        pix=doc[0].get_pixmap(matrix=fitz.Matrix(300/72,300/72),alpha=False)  # 300 dpi
        pix.set_dpi(300,300);pix.save(output.with_suffix(".png"))
    plt.close(fig)
