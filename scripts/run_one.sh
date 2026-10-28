#!/usr/bin/env bash
# Runs one experiment configuration with the command line of wafer/orchestration/run_model_block.sh and
# run_numerical_baselines.sh.
#
#   scripts/run_one.sh --strategy warm_start --scenario s_cost --level L2 --model qwen35_4b \
#       --optimizer mads --seed 0 --batpac-workbook "/path/to/BatPaC 5.0 2022-02-09.xlsm" --out runs/
#
# strategy:  warm_start | variable_fixing | react | baseline (cold start) | source_warm_baseline
# scenario:  s_cost (S1) | s_space (S2) | s_fastcharge (S3) | s_energy130_relaxed_pack (S4)
# level:     L1 | L2 (ignored by the numerical baselines)
# optimizer: random | mixed_ga | axbo | mads (ignored by react)
# Optional:  --run-root DIR (assembled code, default <out>/run_root), --port N,
#            --budget N (default 20, the budget of the paper).
#            LLM_BASE_URL overrides the model server URL (default http://127.0.0.1:<port>/v1).
# Requires:  your own BatPaC 5.0 workbook, LibreOffice on PATH (`libreoffice`), and for the
#            LLM-assisted strategies a vLLM server for the model (wafer/orchestration/server_ctl.sh).
# Exit codes: 2 bad arguments or workbook, 3 no LibreOffice, 4 no model server, 5 run failed.
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${WAFER_PYTHON:-python}"
STRATEGY= SCENARIO= LEVEL=L2 MODEL=qwen35_4b OPTIMIZER=random SEED=0 OUT= WORKBOOK="${WAFER_BATPAC_WORKBOOK:-}"
RUN_ROOT= PORT= BUDGET=20

die () { echo "run_one.sh: $2" >&2; exit "$1"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --strategy) STRATEGY="$2"; shift 2 ;;
        --scenario) SCENARIO="$2"; shift 2 ;;
        --level) LEVEL="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --optimizer) OPTIMIZER="$2"; shift 2 ;;
        --seed) SEED="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --batpac-workbook) WORKBOOK="$2"; shift 2 ;;
        --run-root) RUN_ROOT="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --budget) BUDGET="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^# Exit codes/p' "$0"; exit 0 ;;
        *) die 2 "unknown argument: $1" ;;
    esac
done

case "$STRATEGY" in warm_start|variable_fixing|react|baseline|source_warm_baseline) ;; *) die 2 "--strategy is required (warm_start, variable_fixing, react, baseline, source_warm_baseline)" ;; esac
case "$SCENARIO" in s_cost|s_space|s_fastcharge|s_energy130_relaxed_pack) ;; *) die 2 "--scenario is required (s_cost, s_space, s_fastcharge, s_energy130_relaxed_pack)" ;; esac
case "$LEVEL" in L1) SEMANTIC=level1_definitions ;; L2) SEMANTIC=level2_engineering ;; *) die 2 "--level must be L1 or L2" ;; esac
case "$MODEL" in qwen35_4b) DEFPORT=8000 ;; qwen35_9b) DEFPORT=8001 ;; qwen35_27b) DEFPORT=8003 ;; *) die 2 "--model must be qwen35_4b, qwen35_9b or qwen35_27b" ;; esac
case "$OPTIMIZER" in random|mixed_ga|axbo|mads) ;; *) die 2 "--optimizer must be random, mixed_ga, axbo or mads" ;; esac
[ -n "$OUT" ] || die 2 "--out is required"
case "$BUDGET" in ''|*[!0-9]*) die 2 "--budget must be a positive integer" ;; esac
PORT="${PORT:-$DEFPORT}"
RUN_ROOT="${RUN_ROOT:-$OUT/run_root}"

# 1. BatPaC: the workbook is not distributed. It must be BatPaC 5.0 (2022-02-09).
[ -n "$WORKBOOK" ] || die 2 "BatPaC workbook not given. BatPaC is not distributed with this code: obtain BatPaC 5.0 (2022-02-09) from Argonne National Laboratory and pass it with --batpac-workbook (see README, 'BatPaC')."
"$PY" - "$WORKBOOK" "$REPO/wafer/common" <<'PY' || exit 2
import sys
sys.path.insert(0, sys.argv[2])
from batpac_workbook import require_batpac_5
try:
    require_batpac_5(sys.argv[1])
except Exception as exc:
    sys.exit(f"run_one.sh: {exc}")
PY

# 2. LibreOffice evaluates the workbook headless.
command -v libreoffice >/dev/null 2>&1 || die 3 "LibreOffice not found: the BatPaC backend calls 'libreoffice --headless'. Install LibreOffice and put 'libreoffice' on PATH."

# 3. Model server (LLM-assisted strategies only).
export BATPAC_BACKEND=libreoffice_cli
if [ "$STRATEGY" = baseline ] || [ "$STRATEGY" = source_warm_baseline ]; then
    # The shared runner constructs a server backend that the numerical baselines never call.
    export LLM_BASE_URL="http://127.0.0.1:65534/v1" LLM_MODEL=qwen35_4b JMD_AX_TORCH_DEVICE=cpu
else
    export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:${PORT}/v1}" LLM_API_KEY="${LLM_API_KEY:-EMPTY}" LLM_MODEL="$MODEL"
    curl -fsS --max-time 10 "$LLM_BASE_URL/models" 2>/dev/null | grep -q "\"$MODEL\"" \
        || die 4 "model server not reachable or not serving '$MODEL' at $LLM_BASE_URL. Start one with wafer/orchestration/server_ctl.sh (see wafer/orchestration/VLLM_SERVER.md) or set LLM_BASE_URL."
fi

# 4. Assemble the role trees (once) and pick the role the full matrix used for this scenario.
if [ ! -d "$RUN_ROOT/code/$MODEL" ]; then
    (cd "$REPO" && "$PY" -m wafer.assemble --out "$RUN_ROOT" --models "$MODEL" --batpac-workbook "$WORKBOOK") >/dev/null \
        || die 2 "assembling the code into $RUN_ROOT failed"
fi
KIND=wsvf; [ "$STRATEGY" = react ] && KIND=react
ROLE="${KIND}_general"; [ "$SCENARIO" = s_fastcharge ] && ROLE="${KIND}_fastcharge"
CODE="$RUN_ROOT/code/$MODEL/$ROLE"
CTX="$RUN_ROOT/contexts/wsvf_${SCENARIO}.json"; [ "$KIND" = react ] && CTX="$RUN_ROOT/contexts/react_${SCENARIO}.json"
LABEL="$LEVEL"; case "$STRATEGY" in baseline|source_warm_baseline) LABEL=minimal ;; esac
DEST="$OUT/$MODEL/$SCENARIO/seed$SEED/${STRATEGY}_${OPTIMIZER}_${LABEL}"
mkdir -p "$DEST"

COMMON=(--scenarios "$SCENARIO" --llm-seeds "$SEED" --optimizer-seeds "$((2000 + SEED))"
        --model-key "$MODEL" --backend qwen_server --budget "$BUDGET" --timeout-sec 600 --strict-real-batpac)
case "$STRATEGY" in
    warm_start|variable_fixing)
        ARGS=(--strategies "$STRATEGY" --semantic-levels "$SEMANTIC" --structured-output-transport json_schema
              --context-artifact-template "$CTX" --optimizers "$OPTIMIZER" --max-tokens 4096 --temperature 0.7
              --top-p 0.8 --prompt-version v0 --parse-retries 1 --warm-start-count-mode fixed
              --fixed-warm-start-count 3 --require-variable-assessments) ;;
    react)
        ARGS=(--strategies react --semantic-levels "$SEMANTIC" --structured-output-transport json_schema
              --context-artifact-template "$CTX" --max-steps 60 --max-tokens 4096 --temperature 0.7 --top-p 0.8
              --prompt-style standard --react-engine custom --prompt-version v3_3_6_evaluate_only_react
              --parse-retries 1)
        [ "$SCENARIO" = s_fastcharge ] && ARGS+=(--react-feedback-mode quantitative) ;;
    *)
        ARGS=(--strategies "$STRATEGY" --semantic-levels minimal --context-artifact "$CTX" --optimizers "$OPTIMIZER") ;;
esac

set +e
(cd "$CODE" && PYTHONPATH="$CODE" "$PY" -m llm_methods.scripts.run_real_batpac_strategy_matrix \
    "${ARGS[@]}" "${COMMON[@]}" --output-dir "$DEST")
RC=$?
set -e
OK="$("$PY" - "$DEST/aggregate_real_batpac_matrix.json" <<'PY'
import json, sys
try:
    rows = json.load(open(sys.argv[1]))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    print(sum(1 for r in rows if r.get("status") == "success"))
except Exception:
    print(0)
PY
)"
[ "$RC" -eq 0 ] && [ "$OK" -ge 1 ] || die 5 "run failed (exit $RC, successful rows $OK); see $DEST"
echo "run_one.sh: done, results in $DEST"
