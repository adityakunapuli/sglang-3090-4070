#!/usr/bin/env bash
# Launch SGLang PP2 for a single experiment, run the bench, then tear down.
# Usage: ./run_test.sh <label> <partition:38,26> <ctx> "<extra flags>"
set -uo pipefail

LABEL="$1"; PARTITION="$2"; CTX="$3"; EXTRA="${4:-}"
RESULTS_DIR=/mnt/data/docker/sglang/tests/results
mkdir -p "$RESULTS_DIR"
LOG="/tmp/sglang-${LABEL}.log"

export CUDA_VISIBLE_DEVICES=0,1
export SGLANG_PP_LAYER_PARTITION="$PARTITION"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export SGLANG_DISABLE_TQDM=1
export HF_HUB_OFFLINE=1

echo "### [${LABEL}] partition=${PARTITION} ctx=${CTX} extra=[${EXTRA}]"

docker rm -f sglang-test >/dev/null 2>&1
docker run -d --name sglang-test --gpus all --ipc=host \
  --network host --shm-size=32g \
  -v /mnt/data/models/llm/Qwen3.8-SGLang:/model:ro \
  --entrypoint python3 lmsysorg/sglang:v0.5.20 \
  -m sglang.launch_server \
  --model-path /model --served-model-name default \
  --host 127.0.0.1 --port 8082 --trust-remote-code \
  --enable-multimodal --dtype bfloat16 \
  --pp-size 2 --dist-timeout 1800 \
  --context-length "${CTX}" \
  --kv-cache-dtype fp8_e4m3 \
  --mamba-ssm-dtype bfloat16 --mamba-full-memory-ratio 0.95 \
  --mamba-radix-cache-strategy extra_buffer \
  --mem-fraction-static 0.80 \
  --max-running-requests 64 \
  --chunked-prefill-size 8192 --max-prefill-tokens 8192 \
  --page-size 64 \
  --disable-prefill-cuda-graph --mm-feature-transport cpu \
  --speculative-algorithm EAGLE --speculative-num-steps 3 \
  --speculative-eagle-topk 1 --speculative-num-draft-tokens 4 \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
  --watchdog-timeout 1200 \
  ${EXTRA} > "${LOG}" 2>&1

# Wait for boot; bail fast if it died.
for i in $(seq 1 240); do
  if ! docker ps --format '{{.Names}}' | grep -q sglang-test; then
    echo "### [${LABEL}] CONTAINER DIED during startup"
    tail -40 "${LOG}"
    exit 2
  fi
  if curl -sf http://127.0.0.1:8082/health >/dev/null 2>&1; then
    echo "### [${LABEL}] healthy after $((i*5))s"
    break
  fi
  sleep 5
done

if ! curl -sf http://127.0.0.1:8082/health >/dev/null 2>&1; then
  echo "### [${LABEL}] TIMEOUT waiting for health"
  tail -40 "${LOG}"
  exit 3
fi

# Capture SGLang's own computed pool sizes + any VRAM warnings.
{
  echo "===== SERVER LOG (allocation lines) ====="
  grep -iE "max_total_num_tokens|max_mamba_cache_size|memory pool|KV cache|available|out of memory|Load weight" "${LOG}" | head -30
  echo "===== GPU STATE ====="
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
} >> "${RESULTS_DIR}/${LABEL}.meta.txt"

python3 /mnt/data/docker/sglang/tests/bench.py \
  --label "${LABEL}" --out "${RESULTS_DIR}/${LABEL}.json"
RC=$?

cp "${LOG}" "${RESULTS_DIR}/${LABEL}.serverlog"
docker rm -f sglang-test >/dev/null 2>&1
sleep 10
echo "### [${LABEL}] done rc=${RC}"
exit ${RC}