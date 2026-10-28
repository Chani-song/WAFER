"""Builds Figs. 3 to 12 of the paper into figures/outputs/ from the plotted-value tables and the result tables.

`python -m figures.build` builds all figures and `--figure KEY` builds one. figures/README.md maps the keys to the
figures.
"""
import argparse

from . import paper_figures, render

FIGURES = {"03_04": paper_figures.fig03_04, "05": paper_figures.fig05, "07": render.fig07, "09nb": render.fig09,
           "10": render.fig10, "11": paper_figures.fig11_split, "12": paper_figures.fig12, "6b": paper_figures.fig_new_s3,
           "08u": lambda: paper_figures.fig08u(gray=True)}


def main():
    p = argparse.ArgumentParser(description="Build the figures of the paper.")
    p.add_argument("--figure", choices=FIGURES)
    x = p.parse_args()
    for key, draw in FIGURES.items():
        if x.figure is None or x.figure == key:
            print("render", key, flush=True)
            draw()


if __name__ == "__main__":
    main()
