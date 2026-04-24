#!/bin/bash
# gemma-4-26b.sh
# Refactored for Gemma 4 26B MoE with Unsloth recommendations and dual-GPU support.

set -euo pipefail

cd /app

MODEL_PATH="/mnt/data/models/llm/Gemma/26B-A4B/gemma-4-26B-A4B-it-UD-IQ4_NL.gguf"
MM_PROJ_PATH="/mnt/data/models/llm/Gemma/26B-A4B/mmproj-BF16.gguf"

LLAMA_ARGS=(
  --model "$MODEL_PATH"
  --alias "Gemma-4-26B"
  --host "0.0.0.0"
  --port "9876"

  # Unsloth recommended inference parameters for Gemma 4
  --temp "1.0"
  --top-p "0.95"
  --top-k "64"
  
  # Gemma 4 explicit thinking control
  --chat-template-kwargs '{"enable_thinking":true}'

  # Resource Management
  --flash-attn "on"
  --parallel "1"
  --fit "on"
  --kv-unified
  
  # KV Cache Quantization to save VRAM and avoid OOM
  --cache-type-k "q4_0"
  --cache-type-v "q4_0"

  # Performance & Multi-GPU
  --batch-size "512"
  --n-gpu-layers "999"      # Offload all layers
  --tensor-split "1,1"      # Split layers evenly across the two RTX 3060s
  --ctx-size "32768"        # Unsloth recommended initial context window

  # Multimodal Support
   --mmproj "$MM_PROJ_PATH"
)

# Note: Ensure both GPUs are visible to the container via docker-compose.
exec /app/llama-server "${LLAMA_ARGS[@]}"
