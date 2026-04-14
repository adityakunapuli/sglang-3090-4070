#!/bin/bash
# copaw-flash-9b.sh
# Uses the Agentscope-supplied CoPaw Flash 9B IQ4 model: https://huggingface.co/bartowski/agentscope-ai_CoPaw-Flash-9B-GGUF
# Alternate HF entry: https://huggingface.co/agentscope-ai/CoPaw-Flash-9B


set -euo pipefail

cd /app

MODEL_PATH="/mnt/data/models/llm/CoPaw/agentscope-ai_CoPaw-Flash-9B-IQ4_NL.gguf"
# shellcheck disable=SC2034
MM_PROJ_PATH="/mnt/data/models/llm/CoPaw/mmproj-agentscope-ai_CoPaw-Flash-9B-bf16.gguf"

LLAMA_ARGS=(
  --model "$MODEL_PATH"
  --alias "CoPaw-Flash-9B"
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
  --n-gpu-layers "60"
  --ctx-size "32768"

  # Enable for multimodal inference only.
   --mmproj "$MM_PROJ_PATH"
)

exec /app/llama-server "${LLAMA_ARGS[@]}"
