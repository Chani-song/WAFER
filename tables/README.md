# tables/

Each script writes its table as CSV and as a LaTeX tabular body into `--out` (default `tables/out/`) from the result tables under `$WAFER_DATA_DIR`. `python tables/build_all.py --out DIR` runs all of them.

| Table | Script |
|---|---|
| Table 1, design domains | `tab01_design_domains.py` |
| Table 2, redesign scenarios | `tab02_redesign_scenarios.py` |
| Table 3, best designs | `tab03_bestdesign.py` |
| Table 4, comparison with RW | `tab04_strategy.py` |
| Table 5, inference cost | `tab05_compute.py` |
| Table 6, preliminary grid | `tab06_source_grid.py` |
| Table 7, feasible grid designs | `tab07_grid_feasible.py` |
| Table 8, L1 against L2 | `tab08_semantic.py` |
| Table 9, panels (a) to (d) | `tab09_supplementary_results.py` |
| Table 10, reasoning coding rules | `tab10_reasoning_coding.py` |
| Table 11, all 248 configurations | `tab11_full_results.py` |
| Reasoning categories by semantic level | `supp_reasoning_by_level.py` |
| Conversion rates of every configuration | `supp_postfeasibility.py` |

Numbers are rounded half to even.
