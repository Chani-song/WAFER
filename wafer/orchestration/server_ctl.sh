#!/usr/bin/env bash
set -Eeuo pipefail

V2="${WAFER_RUN_ROOT:?set WAFER_RUN_ROOT to a folder made by python -m wafer.assemble}"

VLLM_PY="${WAFER_VLLM_PYTHON:-python}"
MODELROOT="${WAFER_MODEL_DIR:?set WAFER_MODEL_DIR to the folder holding Qwen3.5-4B, Qwen3.5-9B and Qwen3.5-27B}"

ACTION="$1"
MODEL="$2"

mkdir -p "$V2/server_pids" "$V2/server_logs"

case "$MODEL" in
    qwen35_4b)
        PATH_MODEL="$MODELROOT/Qwen3.5-4B"
        PORT=8000
        GPUS="0"
        TP=1
        MEM=0.90
        ;;
    qwen35_9b)
        PATH_MODEL="$MODELROOT/Qwen3.5-9B"
        PORT=8001
        GPUS="1,2"
        TP=2
        MEM=0.90
        ;;
    qwen35_27b)
        PATH_MODEL="$MODELROOT/Qwen3.5-27B"
        PORT=8003
        GPUS="0,1,2,3"
        TP=4
        MEM=0.95
        ;;
    *)
        echo "UNKNOWN MODEL: $MODEL"
        exit 1
        ;;
esac

PIDFILE="$V2/server_pids/${MODEL}.pid"
LOG="$V2/server_logs/${MODEL}.log"

health () {
    curl -fsS \
      "http://127.0.0.1:${PORT}/v1/models" \
      2>/dev/null \
      | grep -q "\"${MODEL}\""
}

stop_server () {
    if [ ! -f "$PIDFILE" ]; then
        return 0
    fi

    PID="$(cat "$PIDFILE" 2>/dev/null || true)"

    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping $MODEL process group $PID"

        kill -TERM -- "-$PID" 2>/dev/null || true

        for _ in $(seq 1 30); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done

        if kill -0 "$PID" 2>/dev/null; then
            echo "Force killing $MODEL process group $PID"
            kill -KILL -- "-$PID" 2>/dev/null || true
        fi
    fi

    rm -f "$PIDFILE"
}

case "$ACTION" in
    start)
        if [ -f "$PIDFILE" ]; then
            PID="$(cat "$PIDFILE" 2>/dev/null || true)"

            if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null && health; then
                echo "$MODEL already healthy PID=$PID"
                exit 0
            fi

            stop_server
        fi

        # Never silently adopt a server not owned by this production root.
        if health; then
            echo "FATAL: healthy foreign server already occupies model/port:"
            echo "$MODEL port=$PORT"
            exit 73
        fi

        echo "Starting $MODEL"
        echo "GPUS=$GPUS TP=$TP PORT=$PORT MAX_MODEL_LEN=32768"

        setsid env \
          CUDA_VISIBLE_DEVICES="$GPUS" \
          VLLM_USE_FLASHINFER_SAMPLER=0 \
          "$VLLM_PY" \
          -m vllm.entrypoints.openai.api_server \
          --model "$PATH_MODEL" \
          --served-model-name "$MODEL" \
          --host 127.0.0.1 \
          --port "$PORT" \
          --dtype bfloat16 \
          --tensor-parallel-size "$TP" \
          --gpu-memory-utilization "$MEM" \
          --max-model-len 32768 \
          --max-num-seqs 1 \
          --enforce-eager \
          --attention-backend TRITON_ATTN \
          --disable-custom-all-reduce \
          --no-async-scheduling \
          --default-chat-template-kwargs '{"enable_thinking": false}' \
          > "$LOG" \
          2>&1 \
          < /dev/null &

        PID=$!
        echo "$PID" > "$PIDFILE"

        echo "PID=$PID"

        for I in $(seq 1 120); do
            if health; then
                echo "$MODEL HEALTHY after ${I}0 sec max-check interval."
                exit 0
            fi

            if ! kill -0 "$PID" 2>/dev/null; then
                echo "FATAL: $MODEL server exited during startup."
                tail -100 "$LOG"
                rm -f "$PIDFILE"
                exit 74
            fi

            sleep 10
        done

        echo "FATAL: $MODEL health timeout."
        tail -100 "$LOG"
        stop_server
        exit 75
        ;;

    stop)
        stop_server
        ;;

    health)
        if health; then
            echo "HEALTHY $MODEL"
            exit 0
        else
            echo "UNHEALTHY $MODEL"
            exit 1
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|health} MODEL"
        exit 2
        ;;
esac
