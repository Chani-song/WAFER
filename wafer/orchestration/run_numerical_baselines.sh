#!/usr/bin/env bash
set -u

MAIN="${WAFER_RUN_ROOT:?set WAFER_RUN_ROOT to a folder made by python -m wafer.assemble}"
PY="${WAFER_PYTHON:-python}"
OUT="${WAFER_BASELINE_OUT:-$MAIN/numerical_baselines}"

SCEN="$1"

if [ "$SCEN" = "s_fastcharge" ]; then
    CODE="$MAIN/code/qwen35_4b/wsvf_fastcharge"
else
    CODE="$MAIN/code/qwen35_4b/wsvf_general"
fi

CTX="$MAIN/contexts/wsvf_${SCEN}.json"

# Required only because the shared runner constructs QwenServerBackend.
# Numerical baseline strategies never call this endpoint.
export LLM_BASE_URL="http://127.0.0.1:65534/v1"
export LLM_MODEL="qwen35_4b"

export JMD_AX_TORCH_DEVICE=cpu
export CUDA_VISIBLE_DEVICES=""

run_condition () {
    STRAT="$1"

    if [ "$STRAT" = "baseline" ]; then
        GROUP="control_cold"
    else
        GROUP="control_source_warm"
    fi

    for SEED in $(seq 0 49); do
        OPTSEED=$((2000 + SEED))
        DEST="$OUT/results/$GROUP/$SCEN/seed$SEED"

        mkdir -p "$DEST"

        echo "$(date -Is) START scenario=$SCEN strategy=$STRAT seed=$SEED optseed=$OPTSEED"

        (
            cd "$CODE" || exit 1

            PYTHONPATH="$CODE" "$PY" \
              -m llm_methods.scripts.run_real_batpac_strategy_matrix \
              --strategies "$STRAT" \
              --scenarios "$SCEN" \
              --llm-seeds "$SEED" \
              --optimizer-seeds "$OPTSEED" \
              --semantic-levels minimal \
              --model-key qwen35_4b \
              --backend qwen_server \
              --context-artifact "$CTX" \
              --optimizers random mixed_ga axbo mads \
              --budget 20 \
              --timeout-sec 600 \
              --strict-real-batpac \
              --output-dir "$DEST"
        )

        RC=$?
        echo "$(date -Is) END scenario=$SCEN strategy=$STRAT seed=$SEED rc=$RC"

        if [ "$RC" -ne 0 ]; then
            echo "$(date -Is) ERROR scenario=$SCEN strategy=$STRAT seed=$SEED rc=$RC"
        fi
    done
}

run_condition baseline
run_condition source_warm_baseline
