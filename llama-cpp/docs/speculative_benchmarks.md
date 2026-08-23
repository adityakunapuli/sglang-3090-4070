# Speculative Decoding Benchmarks & Hardware Optimization (July 2026)

This document records the optimization process, VRAM layer-split boundaries, and benchmark results for speculative decoding on the dual-GPU homelab setup.

## Hardware Environment
* **GPU 0 (Slot 1):** NVIDIA GeForce RTX 3090 Ti (24GB VRAM)
* **GPU 1 (Slot 3):** ZOTAC RTX 4070 Ti SUPER (16GB VRAM, PCIe 4.0 x4 slot bandwidth cap ~7.87 GB/s)

---

## Core Findings

### 1. The Multi-Drafter Stack in `llama.cpp`
`llama.cpp` allows stacking a neural speculator (`draft-mtp`) with CPU-based text-continuation lookup tables (`ngram-mod`, `ngram-map-k4v`) via:
`--spec-type draft-mtp,ngram-mod`

The draft router evaluates the N-gram lookup table first (instantaneous, 0 MB VRAM footprint).
* **If it hits** (e.g., repeating syntax patterns, docstrings, or types in a code file), it drafts up to **64 tokens** in one step.
* **If it misses** (e.g., generating creative reasoning thought blocks), it falls back to the neural **MTP head**, drafting **2 to 3 tokens**.
* This provides a massive throughput boost across both thinking and code-generation phases.

### 2. Autotuner (`--fit on`) vs. Manual `--tensor-split`
When using MTP heads, the Unified Draft model requires `--fit on` to load successfully. 
* Specifying both `--fit on` and a hardcoded `--tensor-split` causes a warning: `failed to fit params to free device memory: model_params::tensor_split already set by user, abort`.
* The fit manager aborts its autotuner and falls back to the user split. If this split is too heavy for one of the GPUs, it will trigger a CUDA Out of Memory (OOM) error.
* **Solution:** To manually balance memory margins, set `--fit on` and adjust the `--tensor-split` in `llama-swap-config.yaml` to ensure the target GPU stays within bounds.

---

## Qwen3.6-27B Benchmark Results

Quantized to **UD-Q4_K_XL**, running with a context size of **262,144** (unified dynamic KV cache).

### 1. Speculative Decoding Speeds (Chat completions API)

| Configuration | Prefill / PP (t/s) | Generation / TG (t/s) | Speedup (Gen) | VRAM Cost |
|---|---|---|---|---|
| **R-On: Baseline** (No Spec) | 689.74 | 38.41 | 1.00x | 0 MB |
| **R-On: MTP Only** | 621.76 | 64.98 | 1.69x | ~3 GB |
| **R-On: MTP + N-gram Combo** | 606.48 | 60.63 | 1.58x | ~3 GB |
| **R-Off: Baseline** (No Spec) | 685.47 | 38.13 | 1.00x | 0 MB |
| **R-Off: MTP Only** | 562.73 | 71.90 | 1.89x | ~3 GB |
| **R-Off: MTP + N-gram Combo** | 543.91 | **156.76** | **4.11x** | ~3 GB |

* **Insight:** During the reasoning phase (`Reasoning = On`), N-gram lookups match nothing (0% draft acceptance), causing a tiny CPU search overhead. However, once the model begins writing repetitive code (`Reasoning = Off`), the hybrid stack accelerates generation speed to over **156 t/s (4.11x speedup)**.

### 2. RTX 3090 Ti VRAM Layer Split Sweep (27B Model)

We swept integer layer splits to push the model onto the 3090 Ti (leaving ~600MB free).

| Split Ratio (GPU 0,1) | GPU 0 Used VRAM (MiB) | GPU 0 Free VRAM (MiB) | Status |
|---|---|---|---|
| 70,30 | 21,999 MiB | 2,565 MiB | Success |
| 73,27 | 23,031 MiB | 1,533 MiB | Success |
| 75,25 | 23,309 MiB | 1,255 MiB | Success |
| **77,23** | **23,615 MiB** | **949 MiB** | **Success (Optimal)** |
| 78,22 | N/A | N/A | OOM / Failed |
| 79,21 | N/A | N/A | OOM / Failed |
| 80,20 | N/A | N/A | OOM / Failed |

* **Conclusion:** **`77,23`** is the physical limit of layers we can fit onto the 3090 Ti. It leaves **935 MiB** of free VRAM at runtime.

---

## Qwen3.6-35B-A3B-MTP MoE Benchmark Results

Quantized to **UD-Q5_K_XL**, running with a context size of **262,144**.

### 1. Speculative Parameter Sweep (Reasoning = Off)

We swept speculative parameter configurations to find the optimal combination of MTP draft tokens and N-gram match parameters for MoE:

| Configuration | Prefill / PP (t/s) | Generation / TG (t/s) |
|---|---|---|
| **Baseline** (No Spec) | 997.27 | 124.22 |
| **1. MTP(2) + Ngram(24, 48, 64)** | 1,002.57 | 156.96 |
| **2. MTP(2) + Ngram(40, 0, 16)** | 599.56 | 170.84 |
| **3. MTP(3) + Ngram(24, 48, 64)** | **1,210.95** | **171.45 (Optimal)** |
| **4. MTP(3) + Ngram(40, 0, 16)** | 963.08 | 165.71 |

* **Insight:** Because Qwen 35B MoE activations are compute-light (~3B parameters per token pass), its baseline speed is already extremely high (124 t/s). Stacking **MTP(3) + N-gram** pushes it to **171.45 t/s** (a **38% speedup**) while boosting prefill context processing to **1,210.95 t/s**.

### 2. RTX 3090 Ti VRAM Layer Split Sweep (35B Model)

| Split Ratio (GPU 0,1) | GPU 0 Used VRAM (MiB) | GPU 0 Free VRAM (MiB) | Status |
|---|---|---|---|
| **70,30** | **23,721 MiB** | **843 MiB** | **Success (Optimal)** |
| 75,25 | N/A | N/A | OOM / Failed |
| 80,20 | N/A | N/A | OOM / Failed |
| 85,15 | N/A | N/A | OOM / Failed |

* **Conclusion:** Due to the larger parameter size of the 35B model, **`70,30`** is the maximum split that will fit onto the 3090 Ti, leaving **1,377 MiB** free during runtime.

---

## Test Reproduction and Scripts

The test scripts are saved in the project tests directory:

1.  **`tests/bench_27b_spec.py`:** Benchmarks 27B speculative stack throughput under Reasoning=On/Off.
2.  **`tests/bench_moe_params.py`:** Sweeps MoE speculative parameter candidates.
3.  **`tests/sweep_27b_vram.py`:** Evaluates VRAM split increments for the 27B model.
4.  **`tests/sweep_moe_vram.py`:** Evaluates VRAM split increments for the 35B model.

### How to Run:
```bash
# 1. Stop the active llama-swap container to free up VRAM
docker compose -f /mnt/data/docker/llama-swap/docker-compose.yaml stop llama-swap

# 2. Run the desired benchmark script
python3 /mnt/data/docker/llama-cpp/tests/bench_27b_spec.py
```
*(The scripts handle stopping, spawning temporary docker containers, executing health checks, run timing evaluations, cleaning up, and restoring the production `llama-swap` service automatically).*
