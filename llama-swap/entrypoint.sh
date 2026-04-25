#!/bin/bash
set -e

# Install wget and tar if missing
if ! command -v wget &> /dev/null || ! command -v tar &> /dev/null; then
    apt-get update && apt-get install -y wget tar
fi

# Download and extract llama-swap if missing
if [ ! -f /app/llama-swap ]; then
    wget -qO /tmp/llama-swap.tar.gz https://github.com/mostlygeek/llama-swap/releases/download/v202/llama-swap_206_linux_amd64.tar.gz
    tar -xzf /tmp/llama-swap.tar.gz -C /app
    chmod +x /app/llama-swap
    rm /tmp/llama-swap.tar.gz
fi

exec /app/llama-swap --config /app/llama-swap-config.yaml --listen 0.0.0.0:8080
