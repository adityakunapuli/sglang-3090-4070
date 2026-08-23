import subprocess
import time
import requests

GPU_SLOT_1 = "GPU-49b45ca5-302d-9e6a-0bd6-54548fb53674"
GPU_SLOT_3 = "GPU-29fcc8f1-92c0-0b66-1573-9046c133efbf"
docker_image = "ghcr.io/ggml-org/llama.cpp:server-cuda"

code_prompt = """
Below is a database connection pool manager class in Python. Please implement logging wrappers, get/release methods, and basic CRUD boilerplate for all fields, matching the coding style, naming conventions, and docstrings of the existing code. Start outputting the Python code directly without any introductory text or explanations.

```python
import logging
import sqlite3
from typing import Dict, Any, List, Optional

class DatabaseConnectionManager:
    \"\"\"
    Manages active database connection pools, local caching, query execution,
    and automatic rollback on transaction failures.
    \"\"\"
    def __init__(self, db_path: str, pool_size: int = 5, timeout: float = 10.0):
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self._connection_pool: List[sqlite3.Connection] = []
        self._allocated_connections: Dict[str, sqlite3.Connection] = {}
        self._is_initialized = False
        self.logger = logging.getLogger("DatabaseConnectionManager")
        
    def initialize_pool(self) -> None:
        \"\"\"Initializes the database connection pool up to pool_size.\"\"\"
        self.logger.info(f"Initializing connection pool of size {self.pool_size} at {self.db_path}")
        for _ in range(self.pool_size):
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            conn.row_factory = sqlite3.Row
            self._connection_pool.append(conn)
        self._is_initialized = True
        self.logger.debug("Database connection pool initialized successfully.")
```
"""

configs = {
    "1. MTP(2) + Ngram(24, 48, 64)": [
        "--no-mmap",
        "--model", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
        "--mmproj", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/mmproj-BF16.gguf",
        "--alias", "Qwen3.6-35B-A3B",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--temp", "1.0",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.0",
        "--flash-attn", "on",
        "--reasoning", "off",
        "--split-mode", "layer",
        "--fit", "on",
        "--ctx-size", "8192",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--spec-type", "draft-mtp,ngram-mod",
        "--spec-draft-n-max", "2",
        "--spec-ngram-mod-n-match", "24",
        "--spec-ngram-mod-n-min", "48",
        "--spec-ngram-mod-n-max", "64"
    ],
    "2. MTP(2) + Ngram(40, 0, 16)": [
        "--no-mmap",
        "--model", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
        "--mmproj", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/mmproj-BF16.gguf",
        "--alias", "Qwen3.6-35B-A3B",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--temp", "1.0",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.0",
        "--flash-attn", "on",
        "--reasoning", "off",
        "--split-mode", "layer",
        "--fit", "on",
        "--ctx-size", "8192",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--spec-type", "draft-mtp,ngram-mod",
        "--spec-draft-n-max", "2",
        "--spec-ngram-mod-n-match", "40",
        "--spec-ngram-mod-n-min", "0",
        "--spec-ngram-mod-n-max", "16"
    ],
    "3. MTP(3) + Ngram(24, 48, 64)": [
        "--no-mmap",
        "--model", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
        "--mmproj", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/mmproj-BF16.gguf",
        "--alias", "Qwen3.6-35B-A3B",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--temp", "1.0",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.0",
        "--flash-attn", "on",
        "--reasoning", "off",
        "--split-mode", "layer",
        "--fit", "on",
        "--ctx-size", "8192",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--spec-type", "draft-mtp,ngram-mod",
        "--spec-draft-n-max", "3",
        "--spec-ngram-mod-n-match", "24",
        "--spec-ngram-mod-n-min", "48",
        "--spec-ngram-mod-n-max", "64"
    ],
    "4. MTP(3) + Ngram(40, 0, 16)": [
        "--no-mmap",
        "--model", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
        "--mmproj", "/mnt/data/models/llm/Qwen3.6/35B-A3B-MTP/mmproj-BF16.gguf",
        "--alias", "Qwen3.6-35B-A3B",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--temp", "1.0",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.0",
        "--flash-attn", "on",
        "--reasoning", "off",
        "--split-mode", "layer",
        "--fit", "on",
        "--ctx-size", "8192",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--spec-type", "draft-mtp,ngram-mod",
        "--spec-draft-n-max", "3",
        "--spec-ngram-mod-n-match", "40",
        "--spec-ngram-mod-n-min", "0",
        "--spec-ngram-mod-n-max", "16"
    ]
}

def run_cmd(args):
    return subprocess.run(args, capture_output=True, text=True)

def test_inference(prompt):
    payload = {
        "model": "Qwen3.6-35B-A3B",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 128,
        "temperature": 0.2
    }
    r = requests.post("http://localhost:8085/v1/chat/completions", json=payload, timeout=90)
    if r.status_code == 200:
        data = r.json()
        timings = data.get("timings", {})
        return timings.get("prompt_per_second", 0.0), timings.get("predicted_per_second", 0.0)
    return 0.0, 0.0

def main():
    print("Stopping llama-swap...")
    run_cmd(["docker", "stop", "llama-swap"])
    time.sleep(2)
    
    results = {}
    
    try:
        for name, args in configs.items():
            print(f"\n>>> Loading model config: {name}")
            run_cmd(["docker", "rm", "-f", "llama-bench-temp"])
            
            docker_cmd = [
                "docker", "run", "--rm", "-d",
                "--name", "llama-bench-temp",
                "--gpus", "all",
                "--ipc", "host",
                "-p", "8085:8080",
                "-v", "/mnt/data/models/llm:/mnt/data/models/llm",
                "-e", f"CUDA_VISIBLE_DEVICES={GPU_SLOT_1},{GPU_SLOT_3}",
                "-e", "NVIDIA_VISIBLE_DEVICES=all",
                "-e", "NVIDIA_DRIVER_CAPABILITIES=all",
                docker_image
            ] + args
            
            proc = run_cmd(docker_cmd)
            if proc.returncode != 0:
                print(f"Error starting container: {proc.stderr}")
                continue
                
            ready = False
            for _ in range(90):
                time.sleep(2)
                try:
                    r = requests.get("http://localhost:8085/health", timeout=2)
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass
                    
            if not ready:
                print("Server failed to load.")
                logs = run_cmd(["docker", "logs", "llama-bench-temp"])
                print(logs.stdout)
                print(logs.stderr)
                run_cmd(["docker", "rm", "-f", "llama-bench-temp"])
                continue
                
            print("Server ready. Running code chat test...")
            pp_speed, tg_speed = test_inference(code_prompt)
            
            results[name] = {
                "prefill": pp_speed,
                "generation": tg_speed
            }
            
            print(f"Results: Prefill = {pp_speed:.2f} t/s | Generation = {tg_speed:.2f} t/s")
            run_cmd(["docker", "rm", "-f", "llama-bench-temp"])
            
    finally:
        print("\nStarting llama-swap service back up...")
        run_cmd(["docker", "start", "llama-swap"])
        
    print("\n\n=======================================================")
    print("        QWEN3.6-35B-A3B SPEC PARAMETER SWEEP SUMMARY")
    print("=======================================================")
    print("| Configuration | Prefill (t/s) | Generation (t/s) |")
    print("|---|---|---|")
    for name, data in results.items():
        print(f"| {name} | {data['prefill']:.2f} | {data['generation']:.2f} |")

if __name__ == "__main__":
    main()
