#!/usr/bin/env bash
# Builds every table and figure of the paper from the dataset.
#
#   scripts/build_paper_outputs.sh [--data DIR] [--out DIR]
#
# --data DIR  a folder that holds the unpacked archives result_tables and preliminary_grid. Without it the script
#             downloads the two archives from the Hugging Face dataset with the `hf` tool and unpacks them into
#             <out>/data.
# --out DIR   output folder, default paper_outputs/. The tables go to <out>/tables and the figures to
#             figures/outputs/. The figures need LaTeX (see figures/README.md).
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${WAFER_PYTHON:-python}"
DATASET="${WAFER_HF_DATASET:-Chani-Song/wafer-jmd-release-data}"
DATA="" OUT="$REPO/paper_outputs"

while [ $# -gt 0 ]; do
    case "$1" in
        --data) DATA="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$REPO"

if [ -z "$DATA" ]; then
    DATA="$OUT/data"
    hf download "$DATASET" archives/result_tables.tar.zst archives/preliminary_grid.tar.zst \
        --repo-type dataset --local-dir "$OUT/download" > /dev/null
    mkdir -p "$DATA"
    for archive in result_tables preliminary_grid; do
        zstd -dc "$OUT/download/archives/$archive.tar.zst" | tar -xf - -C "$DATA"
    done
fi
export WAFER_DATA_DIR="$DATA"

"$PY" tables/build_all.py --out "$OUT/tables"
"$PY" -m figures.build
echo "tables in $OUT/tables, figures in figures/outputs"
