# figures/

`python -m figures.build` writes every figure of the paper as PDF, with its plotted values as CSV, into `figures/outputs/`. `--figure KEY` builds one figure. The figures read the plotted-value tables in `data/` and the result tables under `$WAFER_DATA_DIR`. `python -m figures.prepare_aggregates` rebuilds the tables in `data/` from the result tables. The figures need LaTeX (see the root README).

| Figure | Key | Script | Output |
|---|---|---|---|
| Figs. 3 and 4 | `03_04` | `paper_figures.py` | `R1a_discovery.pdf`, `R1b_gap2pct.pdf` |
| Fig. 5 | `05` | `paper_figures.py` | `R2_strategy_fit_map.pdf` |
| Fig. 6 | `07` | `render.py` | `R3a_VF_mask_outcomes.pdf` |
| Fig. 7 | `09nb` | `render.py` | `R4_WS_feasible_starts_nearbest.pdf` |
| Fig. 8 | `10` | `render.py` | `R5_ReAct_postfeasibility.pdf` |
| Fig. 9 | `11` | `paper_figures.py` | `R5b_ReAct_mechanism_b.pdf` |
| Fig. 10 | `12` | `paper_figures.py` | `R6_model_size.pdf` |
| Fig. 11 | `6b` | `paper_figures.py` | `R6b_VF_mask_selection_S3.pdf` |
| Fig. 12 | `08u` | `paper_figures.py` | `R3b_VF_feasible_subspace_mpr1_gray.pdf` |

Figs. 1 and 2 are drawn by hand and are not included. `jmd_style.py` and `style.py` hold the colors, line styles, markers and fonts.
