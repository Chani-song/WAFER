# WAFER

This repository holds the code and prompts of "LLM-Assisted Optimization for Simulation-Based Redesign" by Chan-Eui Song, Jeremy J. Michalek and Christopher McComb, submitted to the ASME *Journal of Mechanical Design* (2026). WAFER uses a language model in a simulator-driven redesign loop in three ways: warm starting (WS), variable fixing (VF) and ReAct. The paper applies them to the redesign of an electric vehicle battery pack evaluated with BatPaC 5.0.

## Installation

```sh
micromamba create -f environment.yml      # or conda / mamba
micromamba activate wafer
pip install --no-deps -e .
```

The figures need LaTeX with `helvet`, `type1cm`, `type1ec`, `underscore` and `cm-super`. Set `JMD_TEX_BIN` to the folder of `latex` if it is not on `PATH`. New experiment runs also need the BatPaC 5.0 workbook, LibreOffice and an OpenAI-compatible server for each language model.

## Running one experiment

`scripts/run_one.sh` runs one configuration with one seed. This command runs warm starting with Bayesian optimization in S3 with the 4B model:

```sh
scripts/run_one.sh --strategy warm_start --scenario s_fastcharge --level L2 --model qwen35_4b \
    --optimizer axbo --seed 0 --batpac-workbook "/path/to/BatPaC 5.0 2022-02-09.xlsm" --out runs/
```

The strategies are `warm_start`, `variable_fixing`, `react`, `baseline` (cold start) and `source_warm_baseline`. The scenarios are `s_cost` (S1), `s_space` (S2), `s_fastcharge` (S3) and `s_energy130_relaxed_pack` (S4). The optimizers are `axbo` (Bayesian optimization), `mixed_ga` (genetic algorithm), `mads` (mesh adaptive direct search) and `random`. `--budget N` sets the evaluation budget, which is 20 in the paper. `LLM_BASE_URL` sets the model server, which serves the model under the name given by `--model`. The script exits before the first evaluation with code 2, 3 or 4 when the workbook, LibreOffice or the model server is missing.

A run writes `real_batpac_records.jsonl` with one line per BatPaC evaluation, `trace.jsonl` with every model request and reply, and summary files.

## Rebuilding the tables and figures

```sh
scripts/build_paper_outputs.sh
```

The script downloads the result tables and the preliminary grid from the dataset, writes the tables to `paper_outputs/tables/` and the figures to `figures/outputs/`. It needs neither BatPaC nor a GPU. [`tables/README.md`](tables/README.md) and [`figures/README.md`](figures/README.md) give the script of each table and figure.

## Layout

- `wafer/`: the experiment code, the scenario context files, the launch scripts and the model settings.
- `prompts/`: the system prompts, strategy and optimizer instructions, ReAct action instructions, JSON output schemas and the L1 and L2 texts.
- `tables/`, `figures/`: the scripts of the tables and figures.
- `analysis/reasoning/`: the coder of the reasoning categories.
- `supplementary/l2_comprehension/`: the L2 comprehension check of Appendix C.
- `scripts/`: `run_one.sh` and `build_paper_outputs.sh`.
- `tests/`: assembly and BatPaC interface tests, and a dry run of each strategy with a mock simulator and a mock language model.

## Data

The value `SW` in the `strategy` column of the result tables, written `Source-warm` in the plotted-value tables of `figures/data/`, is the reference-warm baseline (RW) of the paper.

The result tables and the raw run records are in the Hugging Face dataset [`Chani-Song/wafer-jmd-release-data`](https://huggingface.co/datasets/Chani-Song/wafer-jmd-release-data). Its card lists the tables and their columns.

## BatPaC

BatPaC is developed by Argonne National Laboratory and distributed under a BSD license. The workbook is not included. Obtain BatPaC 5.0 (2022-02-09) from https://www.anl.gov/cse/batpac-model-software and cite its manual:

> K. W. Knehr, J. J. Kubal, P. A. Nelson, and S. Ahmed, "Battery Performance and Cost Modeling for Electric-Drive Vehicles: A Manual for BatPaC v5.0," Argonne National Laboratory, ANL/CSE-22/1, July 2022. doi:10.2172/1877590.

## Citation

```bibtex
@unpublished{song_wafer,
  author = {Song, Chan-Eui and Michalek, Jeremy J. and McComb, Christopher},
  title  = {{LLM}-Assisted Optimization for Simulation-Based Redesign},
  note   = {Submitted to the ASME Journal of Mechanical Design (2026)},
  year   = {2026}
}
```

## License

The code is released under the MIT license. BatPaC and the Qwen3.5 models are not included and have their own terms.
