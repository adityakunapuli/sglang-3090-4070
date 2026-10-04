# sglang — Swift-1.5-Qwen3.8-27B on 3090 Ti + 4070 Ti S (PP2)

This stack serves `ukisai/Swift-1.5-Qwen3.8-27B` (AWQ INT4, vision, MTP) across the
**two consumer GPUs with asymmetric VRAM** — RTX 3090 Ti (24 GB, sm_86) and RTX 4070 Ti
SUPER (16 GB, sm_89) — using **pipeline parallelism (PP2)**, not tensor parallelism.

It is the primary `llm-active` backend in this repo's LLM topology: it publishes host
port **8082** and claims the `llm-active` alias on the `proxy` network, which the
LiteLLM front door resolves by name.

## Why this is hard

- The two cards are on **separate PCIe root complexes** (`nvidia-smi topo -m` → `PHB`)
  with **P2P blocked at the driver level** (consumer GeForce). Every inter-stage
  activation crosses the host bridge over a PCIe 4.0 x4 link (~7.87 GB/s). PP2 here is
  **interconnect-bound, not compute-bound**.
- TP was tried and rejected: it refuses unbalanced VRAM, and the 16 GB card cannot hold
  its half of the weights plus usable KV/mamba pools.
- The model is a **hybrid Gated-DeltaNet** (mamba-style recurrent state **per
  sequence**, not per-token KV) + multimodal. PP + multimodal + hybrid linear attention
  + speculative decoding is a combination SGLang does not document on *any* hardware.
  Everything below was worked out empirically.

## What makes it run — the precise steps

The steps are in the order they became load-bearing. Each one is a live setting in
`docker-compose.yml` / `.env`, and removing any of them breaks startup, correctness,
or throughput.

### 1. Pin the image to nightly `20261002-67eab570`

```
image: lmsysorg/sglang:nightly-dev-20261002-67eab570
```

First build containing the **PP × speculative hybrid recurrent-state fix** (upstream
sgl#40001). v0.5.21 predates it and cannot run PP + EAGLE on this model. Bump
deliberately; older nightlies crash in warmup.

### 2. Set the GPU device order — the silent trap

```
CUDA_DEVICE_ORDER: PCI_BUS_ID
CUDA_VISIBLE_DEVICES: 1,0
```

CUDA's default ordering makes **dev0 = 4070 Ti S, dev1 = 3090 Ti**. Without
`PCI_BUS_ID`, PP rank 0 silently lands on the 16 GB card and the 44-layer partition
OOMs. With it, dev0=3090, dev1=4070, so `1,0` puts the **4070 first → PP0 = 4070,
PP1 = 3090** — the larger partition on the faster, bigger card, matching upstream
guidance.

### 3. Choose the layer partition for VRAM, not balance

```
SGLANG_PP_LAYER_PARTITION=16,48
```

PP0 (4070) carries embed + vision + 16 layers; PP1 (3090) carries lm_head + 48 layers
+ the MTP draft. The KV pool is sized by the **tighter** stage, so layers move off the
4070. `16,48` is the measured optimum after the draft deferral in step 6
(167,296 pool tokens; `20,44` → 155k, `12,52` → 145k).

### 4. Work around the flashinfer CuTe DSL JIT failure

```
FLASHINFER_USE_CUDA_NORM: "1"
```

The CuTe DSL path dies with `cudaErrorNoKernelImageForDevice ("Target SM ARCH:
unknown")` under heterogeneous PP. This forces flashinfer's CUDA JIT path instead.

### 5. Set the hybrid-model serving flags

```
--kv-cache-dtype fp8_e4m3        # fp8 KV storage is fine; FP8 *tensor cores* are not (Ampere)
--mamba-ssm-dtype bfloat16       # LOAD-BEARING: halves the per-sequence mamba state
--mamba-full-memory-ratio 0.5
--mamba-radix-cache-strategy extra_buffer
--max-mamba-cache-size 16        # the REAL concurrency ceiling (4 in production)
--mem-fraction-static 0.92       # 0.94+ flaps when foreign procs spike on the 4070
--context-length 262144          # KV pool is the binding limit (~167k), see step 6
--chunked-prefill-size 8192 --max-prefill-tokens 8192
--cuda-graph-bs-decode 1 2 4     # 16k graphs cost ~1GB of PP0 slack → OOM; keep small
--disable-prefill-cuda-graph
--mm-feature-transport cpu
SGLANG_VLM_CACHE_SIZE_MB=1024    # upstream ~100MB default OOMs on multi-MP screenshots
```

`--mamba-ssm-dtype bfloat16` is not cosmetic: the checkpoint ships
`mamba_ssm_dtype: float32`, and the mamba state pool — not KV — caps concurrency.

**Do NOT enable `--enable-mixed-chunk`.** Upstream sgl#39342 (open): it corrupts the
mamba radix cache on hybrid GDN models exactly when prefills co-batch with decodes —
this multi-consumer workload. Measured degradation: exact match 0.908 → 0.836,
p50 200 ms → 3–7 s.

### 6. Mount the three local patches (required while MTP is on)

```
./patches/qwen3_5_mm_relay.py              -> srt/models/qwen3_5.py
./patches/qwen3_5_mtp_pp_spec.py           -> srt/models/qwen3_5_mtp.py
./patches/pp_draft_embedding_lazy_meta.py  -> srt/speculative/pp_draft_embedding.py
```

- **`qwen3_5_mm_relay.py`** — relays `mm_input_embeds` across the PP boundary (port of
  upstream #40501's qwen4-exp mechanism) so the last-stage MTP draft can merge vision
  embeddings. Drops `qwen3_5.py` when the upstream qwen3_5 half of #40501/#39634 lands.
- **`qwen3_5_mtp_pp_spec.py`** — the acceptance fix. The checkpoint ships the MTP head
  in raw bf16 while the compressed-tensors config lists **no `mtp.*` quant targets**, so
  the draft was built as Marlin W4A16 with **uninitialized scale buffers → NaN logits →
  acceptance 0.00**. The patch forces bf16 for that case. Note the quant name is
  `"compressed_tensors"` (underscore), not `"compressed-tensors"`.
- **`pp_draft_embedding_lazy_meta.py`** — defers the draft's 2.5 GB lm_head to post-
  sizing (meta placeholder) and stores the 2.4 GB draft embedding as fp8 rows **at
  construction**. Recovered ~4–5 GB of pool budget: **83.5k → 167k pool tokens**.

### 7. Enable MTP (EAGLE) speculative decoding

```
SGLANG_ENABLE_PP_SPEC=1
--speculative-algorithm EAGLE --speculative-num-steps 3
--speculative-eagle-topk 1 --speculative-num-draft-tokens 4
```

3/1/4 is the community-verified optimum for this family. Result: decode **75–84 tok/s**
(accept len 3.0–3.8) vs ~42 without. **No-spec fallback** (2× slower decode, ~187k pool):
`SGLANG_ENABLE_PP_SPEC=0 SGLANG_SPEC_FLAGS=""`.

### 8. Serve under the shared contract

```
--served-model-name Qwen3.8-27B      # stable name for the front door, not "default"
--port 8082                          # host port MUST stay 8082 (alias contract)
networks: proxy.aliases: [llm-active]
```

## Verified state (nightly 67eab570, 16,48, mem 0.92, MTP on)

| Metric | Value |
|---|---|
| KV pool | 167,296 tokens (usable; >167k prompts 400) |
| Decode | 75–84 tok/s (accept len ~3.0) |
| Prefill | ~1088 tok/s @60k |
| Concurrency | 4 (mamba pool), 0 failures at 8-way in stress |
| Gates / vision / needle | pass |

`tests/stress_validation.py`: 30/30 mixed multi-image + long-context + concurrent.

## Reproduce / test

```bash
docker compose up -d
cd tests && python3 bench.py --label repro --decode-targets 2000,20000,60000 --conc-levels 1,4,8
python3 needle.py 170000
python3 stress_validation.py
```

## Deep dives

- `docs/20261002-224500-sglang-qwen38-migration.md` — the full migration record:
  model selection, hardware characterisation, sizing sweeps, the MTP blocker journey
  across four sessions, and the graveyard of falsified ideas.
- `docs/20261003-061817-fix-sglang-pp-mtp-acceptance.md` — open field guide to the
  PP × MTP acceptance bug (hypotheses with falsification tests).

## If you touch this

- **Bumping the nightly:** re-verify the three patches still apply (bind-mounts over an
  editable install will loudly fail on rename), re-run `stress_validation.py`, and check
  `accept len` in the startup log (> ~2.5 is healthy).
- **Changing the partition:** update `SGLANG_PP_LAYER_PARTITION` in `.env` and the
  VRAM-budget comment at the top of `docker-compose.yml` together.
- **Never** enable `--enable-mixed-chunk`, and keep the 8082/`llm-active` contract
  intact — it is load-bearing for every other stack in this repo.
