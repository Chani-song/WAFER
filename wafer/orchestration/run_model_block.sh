#!/usr/bin/env bash
set -Eeuo pipefail

V2="${WAFER_RUN_ROOT:?set WAFER_RUN_ROOT to a folder made by python -m wafer.assemble}"
PY="${WAFER_PYTHON:-python}"

MODEL="$1"
PORT="$2"
SEED_START="$3"
SEED_END="$4"

export LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
export LLM_API_KEY="EMPTY"
export LLM_MODEL="$MODEL"
export BATPAC_BACKEND="libreoffice_cli"

MODELROOT="$V2/code/$MODEL"
MAX_INFRA_RETRIES=2
MIN_FREE_GB=25

aggregate_count () {
    local OUT="$1"

    "$PY" - "$OUT" <<'PY'
import json, sys
from pathlib import Path

p = Path(sys.argv[1]) / "aggregate_real_batpac_matrix.json"

if not p.exists():
    print(0)
    raise SystemExit

obj = json.loads(p.read_text())

if isinstance(obj, list):
    print(len(obj))
    raise SystemExit

if isinstance(obj, dict):
    for k in ("rows", "results", "runs", "records"):
        v = obj.get(k)
        if isinstance(v, list):
            print(len(v))
            raise SystemExit

print(0)
PY
}

control_guard () {
    if [ -f "$V2/PROTOCOL_REJECTED" ]; then
        echo "CONTROL STOP: PROTOCOL_REJECTED"
        exit 88
    fi

    if [ -f "$V2/STOP_NOW" ]; then
        echo "CONTROL STOP: STOP_NOW"
        exit 88
    fi
}

disk_guard () {
    local FREE_KB
    FREE_KB="$(df -Pk "$V2" | awk 'NR==2 {print $4}')"

    if [ "$FREE_KB" -lt $((MIN_FREE_GB * 1024 * 1024)) ]; then
        echo "LOW DISK: less than ${MIN_FREE_GB} GB free."
        touch "$V2/LOW_DISK_STOP"
        exit 90
    fi
}

server_guard () {
    curl -fsS \
      "http://127.0.0.1:${PORT}/v1/models" \
      | grep -q "\"${MODEL}\"" \
      || {
          echo "SERVER UNAVAILABLE model=$MODEL port=$PORT"
          return 1
      }
}

mapping () {
    local SCEN="$1"

    case "$SCEN" in
        s_cost|s_space)
            WCODE="$MODELROOT/wsvf_general"
            RCODE="$MODELROOT/react_general"
            ;;
        s_fastcharge)
            WCODE="$MODELROOT/wsvf_fastcharge"
            RCODE="$MODELROOT/react_fastcharge"
            ;;
        s_energy130_relaxed_pack)
            WCODE="$MODELROOT/wsvf_general"
            RCODE="$MODELROOT/react_general"
            ;;
        *)
            echo "UNKNOWN SCENARIO: $SCEN"
            exit 1
            ;;
    esac

    WCTX="$V2/contexts/wsvf_${SCEN}.json"
    RCTX="$V2/contexts/react_${SCEN}.json"
}

quarantine () {
    local OUT="$1"
    local SCEN="$2"
    local SEED="$3"
    local KIND="$4"
    local ATTEMPT="$5"

    if [ ! -d "$OUT" ]; then
        return
    fi

    local TS
    TS="$(date +%Y%m%d_%H%M%S)"

    local Q="$V2/quarantine/$MODEL/$SCEN/seed${SEED}/${KIND}_${TS}_attempt${ATTEMPT}"

    mkdir -p "$(dirname "$Q")"
    mv "$OUT" "$Q"

    echo "QUARANTINED: $Q"
}

after_chunk () {
    local SCEN="$1"
    local SEED="$2"
    local KIND="$3"

    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$(date -Is)" "$MODEL" "$SCEN" "$SEED" "$KIND" \
      > "$V2/state/heartbeat_${MODEL}.tsv"

    if [ -f "$V2/PROTOCOL_REJECTED" ] || [ -f "$V2/STOP_NOW" ]; then
        exit 88
    fi

    if [ -f "$V2/STOP_AFTER_CURRENT_CHUNK" ]; then
        echo "CONTROL STOP after current chunk."
        exit 89
    fi
}

run_wsvf () {
    local SCEN="$1"
    local SEED="$2"
    local OPTSEED=$((2000 + SEED))

    mapping "$SCEN"

    local OUT="$V2/results/$MODEL/$SCEN/seed${SEED}/wsvf"
    local EXPECTED=16
    local N

    mkdir -p "$(dirname "$OUT")"

    N="$(aggregate_count "$OUT")"

    if [ "$N" -eq "$EXPECTED" ]; then
        echo "SKIP_COMPLETE WSVF $MODEL $SCEN seed=$SEED"
        return
    fi

    if [ -d "$OUT" ] && find "$OUT" -mindepth 1 -print -quit | grep -q .; then
        quarantine "$OUT" "$SCEN" "$SEED" "wsvf_preexisting_partial" 0
    fi

    for ATTEMPT in $(seq 1 "$MAX_INFRA_RETRIES"); do
        control_guard
        disk_guard

        server_guard || {
            echo "Server guard failed before WSVF attempt=$ATTEMPT"
            sleep 10
            continue
        }

        mkdir -p "$OUT"

        echo
        echo "===== WSVF ====="
        echo "model=$MODEL scenario=$SCEN seed=$SEED attempt=$ATTEMPT"
        echo "max_tokens=4096 GA_pop=5 Ax_init=5"
        echo "time=$(date -Is)"

        set +e
        (
            cd "$WCODE"

            PYTHONPATH="$WCODE" "$PY" \
              -m llm_methods.scripts.run_real_batpac_strategy_matrix \
              --strategies warm_start variable_fixing \
              --scenarios "$SCEN" \
              --llm-seeds "$SEED" \
              --optimizer-seeds "$OPTSEED" \
              --semantic-levels \
                level1_definitions \
                level2_engineering \
              --model-key "$MODEL" \
              --backend qwen_server \
              --structured-output-transport json_schema \
              --timeout-sec 600 \
              --context-artifact-template "$WCTX" \
              --optimizers random mixed_ga axbo mads \
              --budget 20 \
              --max-tokens 4096 \
              --temperature 0.7 \
              --top-p 0.8 \
              --strict-real-batpac \
              --prompt-version v0 \
              --parse-retries 1 \
              --warm-start-count-mode fixed \
              --fixed-warm-start-count 3 \
              --require-variable-assessments \
              --output-dir "$OUT"
        )
        RC=$?
        set -e

        N="$(aggregate_count "$OUT")"

        echo "WSVF rc=$RC rows=$N/$EXPECTED"

        # All scheduled rows terminalized: accept even if some rows are
        # scientific/model failures.
        if [ "$N" -eq "$EXPECTED" ]; then
            after_chunk "$SCEN" "$SEED" "wsvf"
            return
        fi

        echo "Infrastructure-incomplete WSVF chunk."
        quarantine "$OUT" "$SCEN" "$SEED" "wsvf_incomplete" "$ATTEMPT"

        sleep 15
    done

    echo "FATAL: WSVF failed to terminalize after retries."
    exit 71
}

run_react () {
    local SCEN="$1"
    local SEED="$2"

    # REACT_ABLATION_PATCH_V1
    # Primary ReAct uses KIND=react.
    # Fast-charge binary-feedback ablation uses KIND=react_binary.
    local FEEDBACK_MODE="${3:-quantitative}"
    local KIND="${4:-react}"
    local OPTSEED=$((2000 + SEED))

    mapping "$SCEN"

    local EXTRA_REACT_ARGS=()

    if [ "$SCEN" = "s_fastcharge" ]; then
        EXTRA_REACT_ARGS=(
            --react-feedback-mode "$FEEDBACK_MODE"
        )
    fi

    local OUT="$V2/results/$MODEL/$SCEN/seed${SEED}/${KIND}"
    local EXPECTED=2
    local N

    mkdir -p "$(dirname "$OUT")"

    N="$(aggregate_count "$OUT")"

    if [ "$N" -eq "$EXPECTED" ]; then
        echo "SKIP_COMPLETE REACT $MODEL $SCEN seed=$SEED"
        return
    fi

    if [ -d "$OUT" ] && find "$OUT" -mindepth 1 -print -quit | grep -q .; then
        quarantine "$OUT" "$SCEN" "$SEED" "${KIND}_preexisting_partial" 0
    fi

    for ATTEMPT in $(seq 1 "$MAX_INFRA_RETRIES"); do
        control_guard
        disk_guard

        server_guard || {
            echo "Server guard failed before ReAct attempt=$ATTEMPT"
            sleep 10
            continue
        }

        mkdir -p "$OUT"

        echo
        echo "===== REACT ====="
        echo "model=$MODEL scenario=$SCEN seed=$SEED kind=$KIND feedback=$FEEDBACK_MODE attempt=$ATTEMPT"
        echo "max_tokens=4096"
        echo "time=$(date -Is)"

        set +e
        (
            cd "$RCODE"

            PYTHONPATH="$RCODE" "$PY" \
              -m llm_methods.scripts.run_real_batpac_strategy_matrix \
              --strategies react \
              --scenarios "$SCEN" \
              --llm-seeds "$SEED" \
              --optimizer-seeds "$OPTSEED" \
              --semantic-levels \
                level1_definitions \
                level2_engineering \
              --model-key "$MODEL" \
              --backend qwen_server \
              --structured-output-transport json_schema \
              --timeout-sec 600 \
              --context-artifact-template "$RCTX" \
              --budget 20 \
              --max-steps 60 \
              --max-tokens 4096 \
              --temperature 0.7 \
              --top-p 0.8 \
              --strict-real-batpac \
              --prompt-style standard \
              --react-engine custom \
              --prompt-version v3_3_6_evaluate_only_react \
              --parse-retries 1 \
              "${EXTRA_REACT_ARGS[@]}" \
              --output-dir "$OUT"
        )
        RC=$?
        set -e

        N="$(aggregate_count "$OUT")"

        echo "REACT rc=$RC rows=$N/$EXPECTED"

        if [ "$N" -eq "$EXPECTED" ]; then
            after_chunk "$SCEN" "$SEED" "$KIND"
            return
        fi

        echo "Infrastructure-incomplete ReAct chunk."
        quarantine "$OUT" "$SCEN" "$SEED" "${KIND}_incomplete" "$ATTEMPT"

        sleep 15
    done

    echo "FATAL: ReAct failed to terminalize after retries."
    exit 72
}

control_guard
disk_guard
server_guard

echo "================================================================"
echo "MODEL BLOCK"
echo "model=$MODEL"
echo "seeds=$SEED_START-$SEED_END"
echo "started=$(date -Is)"
echo "================================================================"

for SCEN in \
    s_cost \
    s_space \
    s_fastcharge \
    s_energy130_relaxed_pack
do
    for SEED in $(seq "$SEED_START" "$SEED_END"); do
        run_wsvf "$SCEN" "$SEED"
        # Main ReAct condition.
        # For fast-charge this explicitly uses quantitative violation feedback.
        run_react "$SCEN" "$SEED" quantitative react

        # Paired feedback ablation exists only for fast-charge.
        if [ "$SCEN" = "s_fastcharge" ]; then
            run_react "$SCEN" "$SEED" binary react_binary
        fi
    done
done

MARK="$V2/state/${MODEL}_block_${SEED_START}_${SEED_END}.COMPLETE"
touch "$MARK"

echo "MODEL BLOCK COMPLETE model=$MODEL seeds=$SEED_START-$SEED_END"
