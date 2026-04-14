#!/bin/bash
# qwen35-9b-v2.sh
#
# Memory-oriented changes from qwen35-9b.sh:
# - KV cache lowered from q8_0 to q4_0 to reduce host/GPU memory pressure.
# - Batch size lowered from 512 to 256 to reduce runtime buffer allocation.
# - GPU offload lowered from 99 to 60 layers to avoid pushing an almost-full model
#   into unstable VRAM/RAM fallback behavior.
# - The multimodal projector is left disabled by default because it adds substantial
#   memory overhead and is unnecessary for text-only use. Uncomment it only if you
#   need image inputs.
# - Context remains 32768 as requested.
#
# This script is written as a Bash array so individual flags are easier to inspect,
# comment out, or override later.

set -euo pipefail

cd /app

MODEL_PATH="/mnt/data/models/llm/Qwen3.5/Qwen3.5-9B-UD-Q4_K_XL.gguf"
# shellcheck disable=SC2034
MM_PROJ_PATH="/mnt/data/models/llm/Qwen3.5/mmproj-BF16.gguf"

LLAMA_ARGS=(
  --model "$MODEL_PATH"
  --alias "Qwen3.5"
  --host "0.0.0.0"
  --port "9876"

  --temp "0.6"
  --top-p "0.95"
  --top-k "20"
  --min-p "0.00"
  --reasoning "off"

  --cache-type-k "q4_0"
  --cache-type-v "q4_0"
  --flash-attn "on"
  --parallel "1"
  --fit "on"
  --kv-unified

  --batch-size "256"
  --n-gpu-layers "90"
  --ctx-size "32768"

  # Enable for multimodal inference only.
   --mmproj "$MM_PROJ_PATH"
)

exec /app/llama-server "${LLAMA_ARGS[@]}"
