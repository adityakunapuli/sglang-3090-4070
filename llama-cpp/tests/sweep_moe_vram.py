import subprocess
import time
import requests

GPU_SLOT_1 = "GPU-49b45ca5-302d-9e6a-0bd6-54548fb53674"
GPU_SLOT_3 = "GPU-29fcc8f1-92c0-0b66-1573-9046c133efbf"
docker_image = "ghcr.io/ggml-org/llama.cpp:server-cuda"

splits_to_test = [
    "70,30",
    "75,25",
    "80,20",
    "85,15"
]

def run_cmd(args):
    return subprocess.run(args, capture_output=True, text=True)

def get_gpu_usage():
    proc = run_cmd(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"])
    if proc.returncode == 0:
        lines = proc.stdout.strip().split("\n")
        total, used = map(int, lines[0].split(","))
        return total, used
    return 0, 0

def main():
    print("Stopping llama-swap...")
    run_cmd(["docker", "stop", "llama-swap"])
    time.sleep(2)
    
    results = []
    
    try:
        for split in splits_to_test:
            print(f"\n>>> Testing MoE split: {split}")
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
                docker_image,
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
                "--tensor-split", split,
                "--fit", "on",
                "--ctx-size", "262144",
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "--spec-type", "draft-mtp,ngram-mod",
                "--spec-draft-n-max", "3",
                "--spec-ngram-mod-n-match", "24",
                "--spec-ngram-mod-n-min", "48",
                "--spec-ngram-mod-n-max", "64"
            ]
            
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
                print("Server failed to load (likely OOM).")
                results.append({
                    "split": split,
                    "status": "OOM / Failed"
                })
                run_cmd(["docker", "rm", "-f", "llama-bench-temp"])
                continue
                
            total_vram, used_vram = get_gpu_usage()
            free_vram = total_vram - used_vram
            
            print(f"Server loaded! GPU 0 VRAM: Used = {used_vram} MiB | Free = {free_vram} MiB")
            results.append({
                "split": split,
                "status": "Success",
                "used": used_vram,
                "free": free_vram
            })
            
            run_cmd(["docker", "rm", "-f", "llama-bench-temp"])
            
    finally:
        print("\nStarting llama-swap service back up...")
        run_cmd(["docker", "start", "llama-swap"])
        
    print("\n\n=======================================================")
    print("             MoE VRAM LAYER SPLIT SWEEP RESULTS")
    print("=======================================================")
    print("| Split Ratio (GPU 0,1) | GPU 0 Used VRAM (MiB) | GPU 0 Free VRAM (MiB) | Status |")
    print("|---|---|---|---|")
    for r in results:
        if r["status"] == "Success":
            print(f"| {r['split']} | {r['used']} MiB | {r['free']} MiB | Success |")
        else:
            print(f"| {r['split']} | N/A | N/A | {r['status']} |")

if __name__ == "__main__":
    main()
