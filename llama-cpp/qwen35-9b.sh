#!/bin/bash
# qwen35-9b.sh

# Move to the server's working directory so it can find its internal assets (WebUI)
cd /app

# Use exec so that llama-server becomes PID 1,
# ensuring it receives shutdown signals correctly.
exec /app/llama-server \
    -m /mnt/data/models/llm/Qwen3.5/Qwen3.5-9B-UD-Q4_K_XL.gguf \
    --alias Qwen3.5 \
    --mmproj /mnt/data/models/llm/Qwen3.5/mmproj-BF16.gguf \
    --host 0.0.0.0 \
    --temp 0.6 \
    --top-p 0.95 \
    --top-k 20 \
    --min-p 0.00 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --flash-attn on \
    --parallel 1 \
    --fit on \
    --kv-unified \
    --batch-size 512 \
    --port 9876 \
    --n-gpu-layers 99 \
    --ctx-size 32768 \
    --reasoning off

