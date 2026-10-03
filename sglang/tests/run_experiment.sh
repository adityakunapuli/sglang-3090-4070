#!/usr/bin/env bash
# Experiment launcher for the SGLang PP2 stack.
#
# Step-0 principle (see docs/20261003-061817-fix-sglang-pp-mtp-acceptance.md):
# the scarce resource is a 27B boot (~10min). This script makes boots cheap
# and comparable:
#   * mounts the host's compose cache volume -> flashinfer/triton JIT persists
#   * mamba/context/graph sizes are SIZED FOR DEBUG by default (not production)
#   * SPEC=1 turns MTP on; MTPDBG=1 turns the instrumented MTP patch logging on
#   * always names the container sgl-lab and publishes on the proxy network only
#     (no host port collisions; never disturbs the :8082 production slot)
#
# Usage:
#   tests/run_experiment.sh                    # no-spec quick boot
#   SPEC=1 MTPDBG=1 tests/run_experiment.sh    # instrumented MTP boot
#   CTX=16384 MEMFRAC=0.70 tests/run_experiment.sh
#
set -u
cd "$(dirname "$0")/.."

CTX="${CTX:-32768}"
MEMFRAC="${MEMFRAC:-0.80}"
MAMBA="${MAMBA:-12}"
PARTITION="${PARTITION:-20,44}"
IMAGE="${SGLANG_IMAGE:-$(grep -E '^SGLANG_IMAGE=' .env | cut -d= -f2)}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

SPEC_FLAGS=""
if [ "${SPEC:-0}" = "1" ]; then
  SPEC_FLAGS="--speculative-algorithm EAGLE --speculative-num-steps ${SPEC_STEPS:-3} --speculative-eagle-topk 1 --speculative-num-draft-tokens ${SPEC_DRAFT:-4}"
fi

MOUNTS=(
  -v /mnt/data/models/llm/Qwen3.8-SGLang:/model:ro
  -v sglang_sglang-cache:/root/.cache
  -v "$PWD/patches/qwen3_5_mm_relay.py:/sgl-workspace/sglang/python/sglang/srt/models/qwen3_5.py:ro"
  -v "$PWD/patches/qwen3_5_mtp_pp_spec.py:/sgl-workspace/sglang/python/sglang/srt/models/qwen3_5_mtp.py:ro"
)

docker rm -f sglang-lab >/dev/null 2>&1
docker run -d --name sglang-lab \
  --gpus '"device=GPU-49b45ca5-302d-9e6a-0bd6-54548fb53674"' \
  --gpus '"device=GPU-29fcc8f1-92c0-0b66-1573-9046c133efbf"' \
  --ipc=host --network proxy \
  -e HF_HUB_OFFLINE=1 -e FLASHINFER_USE_CUDA_NORM=1 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -e SGLANG_DISABLE_TQDM=1 \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID -e CUDA_VISIBLE_DEVICES=1,0 \
  -e SGLANG_PP_LAYER_PARTITION="$PARTITION" \
  -e SGLANG_ENABLE_PP_SPEC="${SPEC:-0}" -e SGLANG_VLM_CACHE_SIZE_MB="${VLM_MB:-1536}" \
  -e SGLANG_DEBUG_MTP="${MTPDBG:-0}" \
  "${MOUNTS[@]}" \
  "$IMAGE" \
  python3 -m sglang.launch_server \
    --model-path /model --served-model-name default \
    --host 0.0.0.0 --port 8082 --trust-remote-code \
    --enable-multimodal --dtype bfloat16 \
    --pp-size 2 --dist-timeout 1800 \
    --context-length "$CTX" \
    --kv-cache-dtype fp8_e4m3 \
    --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer \
    --max-mamba-cache-size "$MAMBA" \
    --mem-fraction-static "$MEMFRAC" \
    --max-running-requests 8 \
    --chunked-prefill-size 8192 --max-prefill-tokens 8192 \
    --page-size 64 --disable-prefill-cuda-graph \
    --mm-feature-transport cpu \
    --cuda-graph-bs-decode 1 2 4 \
    --skip-server-warmup \
    $SPEC_FLAGS \
    --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
    $EXTRA_ARGS >/dev/null

IP=$(docker inspect sglang-lab --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' | head -1)
echo "sglang-lab starting: http://$IP:8082  (SPEC=${SPEC:-0} MTPDBG=${MTPDBG:-0} CTX=$CTX)"
t0=$SECONDS
for i in $(seq 1 60); do
  sleep 15
  if ! docker ps --format '{{.Names}}' | grep -q '^sglang-lab$'; then
    echo "DIED after $((SECONDS-t0))s; last logs:"; docker logs --tail 15 sglang-lab 2>&1; exit 1
  fi
  if curl -sf "http://$IP:8082/health" >/dev/null 2>&1; then
    echo "HEALTHY after $((SECONDS-t0))s"
    curl -s "http://$IP:8082/get_server_info" | python3 -c "import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ('max_total_num_tokens','max_mamba_cache_size')}, 'spec:', d.get('speculative_algorithm'))"
    exit 0
  fi
done
echo "TIMEOUT waiting for health"; exit 2
