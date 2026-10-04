# sglang-3090-4070 — homelab config fork

A fork of [sgl-project/sglang](https://github.com/sgl-project/sglang) carrying the
configuration, local patches, and field notes for serving
**`ukisai/Swift-1.5-Qwen3.8-27B`** (AWQ INT4, vision, MTP) on a home server with
**two consumer GPUs of asymmetric VRAM** — RTX 3090 Ti (24 GB, sm_86) + RTX 4070 Ti
SUPER (16 GB, sm_89) — using **pipeline parallelism (PP2)**, not tensor parallelism.

The working stack lives under [`sglang/`](sglang/) (compose file, `.env` template in
the docs, `patches/`, `tests/`, `docs/`). The upstream SGLang README is preserved at
the bottom of this file.

---

## The split-GPU setup

### Why this is hard

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

### What makes it run — the precise steps

The steps are in the order they became load-bearing. Each one is a live setting in
`sglang/docker-compose.yml` / `.env`, and removing any of them breaks startup,
correctness, or throughput.

#### 1. Pin the image to nightly `20261002-67eab570`

```
image: lmsysorg/sglang:nightly-dev-20261002-67eab570
```

First build containing the **PP × speculative hybrid recurrent-state fix** (upstream
sgl#40001). v0.5.21 predates it and cannot run PP + EAGLE on this model. Bump
deliberately; older nightlies crash in warmup.

#### 2. Set the GPU device order — the silent trap

```
CUDA_DEVICE_ORDER: PCI_BUS_ID
CUDA_VISIBLE_DEVICES: 1,0
```

CUDA's default ordering makes **dev0 = 4070 Ti S, dev1 = 3090 Ti**. Without
`PCI_BUS_ID`, PP rank 0 silently lands on the 16 GB card and the 44-layer partition
OOMs. With it, dev0=3090, dev1=4070, so `1,0` puts the **4070 first → PP0 = 4070,
PP1 = 3090** — the larger partition on the faster, bigger card, matching upstream
guidance.

#### 3. Choose the layer partition for VRAM, not balance

```
SGLANG_PP_LAYER_PARTITION=16,48
```

PP0 (4070) carries embed + vision + 16 layers; PP1 (3090) carries lm_head + 48 layers
+ the MTP draft. The KV pool is sized by the **tighter** stage, so layers move off the
4070. `16,48` is the measured optimum after the draft deferral in step 6
(167,296 pool tokens; `20,44` → 155k, `12,52` → 145k).

#### 4. Work around the flashinfer CuTe DSL JIT failure

```
FLASHINFER_USE_CUDA_NORM: "1"
```

The CuTe DSL path dies with `cudaErrorNoKernelImageForDevice ("Target SM ARCH:
unknown")` under heterogeneous PP. This forces flashinfer's CUDA JIT path instead.

#### 5. Set the hybrid-model serving flags

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

#### 6. Mount the three local patches (required while MTP is on)

```
sglang/patches/qwen3_5_mm_relay.py              -> srt/models/qwen3_5.py
sglang/patches/qwen3_5_mtp_pp_spec.py           -> srt/models/qwen3_5_mtp.py
sglang/patches/pp_draft_embedding_lazy_meta.py  -> srt/speculative/pp_draft_embedding.py
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

#### 7. Enable MTP (EAGLE) speculative decoding

```
SGLANG_ENABLE_PP_SPEC=1
--speculative-algorithm EAGLE --speculative-num-steps 3
--speculative-eagle-topk 1 --speculative-num-draft-tokens 4
```

3/1/4 is the community-verified optimum for this family. Result: decode **75–84 tok/s**
(accept len 3.0–3.8) vs ~42 without. **No-spec fallback** (2× slower decode, ~187k pool):
`SGLANG_ENABLE_PP_SPEC=0 SGLANG_SPEC_FLAGS=""`.

#### 8. Serve under the shared contract

```
--served-model-name Qwen3.8-27B      # stable name for the front door, not "default"
--port 8082                          # host port MUST stay 8082 (alias contract)
networks: proxy.aliases: [llm-active]
```

The backend publishes host port 8082 and claims the `llm-active` alias on the `proxy`
network; the LiteLLM front door resolves backends by that name.

### Verified state (nightly 67eab570, 16,48, mem 0.92, MTP on)

| Metric | Value |
|---|---|
| KV pool | 167,296 tokens (usable; >167k prompts 400) |
| Decode | 75–84 tok/s (accept len ~3.0) |
| Prefill | ~1088 tok/s @60k |
| Concurrency | 4 (mamba pool), 0 failures at 8-way in stress |
| Gates / vision / needle | pass |

`sglang/tests/stress_validation.py`: 30/30 mixed multi-image + long-context + concurrent.

### Reproduce / test

```bash
cd sglang
docker compose up -d
cd tests && python3 bench.py --label repro --decode-targets 2000,20000,60000 --conc-levels 1,4,8
python3 needle.py 170000
python3 stress_validation.py
```

### Deep dives

- [`sglang/docs/20261002-224500-sglang-qwen38-migration.md`](sglang/docs/20261002-224500-sglang-qwen38-migration.md)
  — the full migration record: model selection, hardware characterisation, sizing
  sweeps, the MTP blocker journey across four sessions, and the graveyard of
  falsified ideas.
- [`sglang/docs/20261003-061817-fix-sglang-pp-mtp-acceptance.md`](sglang/docs/20261003-061817-fix-sglang-pp-mtp-acceptance.md)
  — open field guide to the PP × MTP acceptance bug (hypotheses with falsification
  tests).

### If you touch this

- **Bumping the nightly:** re-verify the three patches still apply (bind-mounts over an
  editable install will loudly fail on rename), re-run `stress_validation.py`, and check
  `accept len` in the startup log (> ~2.5 is healthy).
- **Changing the partition:** update `SGLANG_PP_LAYER_PARTITION` in `.env` and the
  VRAM-budget comment at the top of `sglang/docker-compose.yml` together.
- **Never** enable `--enable-mixed-chunk`, and keep the 8082/`llm-active` contract
  intact — it is load-bearing for every other consumer of this backend.

---

---
# SGLang: Fast inference for LLMs and multimodal models

<p align="center" id="sglangtop">
<img src="https://raw.githubusercontent.com/sgl-project/sglang/main/assets/logo.png" alt="SGLang" width="400">
</p>

<p align="center">
  <a href="https://pypi.org/project/sglang/"><img src="https://img.shields.io/pypi/v/sglang?style=flat&amp;label=PyPI&amp;labelColor=555555&amp;color=orange" alt="PyPI version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-green?style=flat&amp;labelColor=555555" alt="License: Apache 2.0"></a>
  <a href="https://pypistats.org/packages/sglang"><img src="https://img.shields.io/pypi/dm/sglang?style=flat&amp;label=Downloads&amp;labelColor=555555&amp;color=blue" alt="PyPI downloads per month"></a>
</p>

<p align="center">
  <a href="https://docs.sglang.io/">Docs</a> |
  <a href="https://cookbook.sglang.io/">Cookbook</a> |
  <a href="https://www.sglang.io/">Website</a> |
  <a href="https://lmsys.org/blog/">Blog</a> |
  <a href="https://slack.sglang.io/">Slack</a>
</p>

SGLang is an open-source inference framework for large language, vision-language, and diffusion models, optimized for agentic workloads, RL rollouts, and large-scale serving. [SGLang Diffusion](https://docs.sglang.io/docs/sglang-diffusion) is its built-in image and video generation engine, included in this repository and the `sglang` Python package.

👋 Get started below, or meet the community at [SGLang Events](https://www.sglang.io/events), including meetups, developer meetings, workshops, and office hours.

## Get Started

Pull the Docker image, which includes SGLang and its dependencies:

```bash
docker pull lmsysorg/sglang:latest
```

Alternatively, install SGLang in an activated Python environment with uv:

```bash
uv pip install --prerelease=allow sglang
```

Next, launch your model:

- [Quickstart](https://docs.sglang.io/docs/get-started/quickstart): Run your first model and send a request.
- [Cookbook](https://cookbook.sglang.io/): Choose your model and hardware to get a ready-to-run launch command.

## Supported Hardware

SGLang supports a wide range of GPUs, TPUs, NPUs, CPUs, and Apple Silicon platforms.

| Platform | Representative hardware |
| --- | --- |
| [NVIDIA](https://docs.sglang.io/docs/hardware-platforms/nvidia-gpus) | A100; H100/H200/H800/H20; B200/B300/GB200/GB300; select RTX 30/40/50 series, RTX 6000 Ada / PRO 6000; DGX Spark, Jetson Orin |
| [AMD](https://docs.sglang.io/docs/hardware-platforms/amd_gpu) | Instinct MI300X, MI325X, MI350X, MI355X |
| [Google TPU](https://docs.sglang.io/docs/hardware-platforms/tpu) | v6e, v7; [SGL-JAX](https://github.com/sgl-project/sglang-jax) / [SGL-torchtpu](https://lmsys.org/blog/2026-07-30-sglang-google-tpu/) |
| Intel ([GPU](https://docs.sglang.io/docs/hardware-platforms/xpu) / [CPU](https://docs.sglang.io/docs/hardware-platforms/cpu_server)) | Arc / Arc Pro B-Series GPUs, Xeon CPUs |
| [Apple Silicon](https://docs.sglang.io/docs/hardware-platforms/apple_metal) | Macs via Metal / MLX |
| [Huawei Ascend](https://docs.sglang.io/docs/hardware-platforms/ascend-npus/getting-started/installation) | A2, A3, 950PR/DT NPUs |
| [Moore Threads](https://docs.sglang.io/docs/hardware-platforms/mthreads_gpu) | MTT S5000 GPUs |

Integrations in progress: AWS Trainium, [Alibaba T-Head PPU](https://github.com/sgl-project/sglang/issues/37519), [Cambricon MLU](https://github.com/sgl-project/sglang/pull/26898), Qualcomm QAIC, MetaX, Hygon HCU/DCU, Iluvatar CoreX, and more.

See the [Cookbook](https://cookbook.sglang.io/) and platform guides for model compatibility and setup.

## SGL Ecosystem

| Area | Projects | Purpose |
| --- | --- | --- |
| Education | [Mini-SGLang](https://github.com/sgl-project/mini-sglang), [zero-to-sglang](https://github.com/datawhalechina/zero-to-sglang), [DeepLearning.AI course](https://www.deeplearning.ai/short-courses/efficient-inference-with-sglang-text-and-image-generation/) | Learn inference engine design and efficient text and image generation through code and hands-on courses. |
| Diffusion | [SGLang Diffusion](https://docs.sglang.io/docs/sglang-diffusion/installation) | Built into SGLang for image and video generation with diffusion models. |
| Audio | [SGLang Omni](https://github.com/sgl-project/sglang-omni) | Audio model serving for text-to-speech (TTS) and automatic speech recognition (ASR). |
| RL and Post-Training | [Miles](https://github.com/radixark/miles), [slime](https://github.com/THUDM/slime), [AReaL](https://github.com/inclusionAI/AReaL), [Tunix](https://github.com/google/tunix), [verl](https://github.com/volcengine/verl) | Training frameworks that integrate SGLang for rollout generation. |
| Speculative Decoding | [SpecForge](https://github.com/sgl-project/SpecForge) | Train draft models for speculative decoding and deploy them with SGLang. |
| KV Cache | [HiCache](https://docs.sglang.io/docs/advanced_features/hicache_design), [Mooncake](https://kvcache-ai.github.io/Mooncake/), [LMCache](https://docs.lmcache.ai/developer_guide/integration.html) | Hierarchical KV caching across GPU memory, host memory, and external storage, with cache transfer and reuse for distributed inference. |
| Deployment and Orchestration | [SMG](https://github.com/smg-project/smg), [RBG](https://github.com/sgl-project/rbg), [llm-d](https://llm-d.ai/docs/dev/operations/disaggregation/sglang), [Ray Serve](https://docs.ray.io/en/latest/serve/llm/user-guides/sglang.html), [NVIDIA Dynamo](https://docs.nvidia.com/dynamo/backends/sg-lang/reference-guide) | Deploy and scale SGLang inference services with routing, load balancing, and cluster orchestration. |

## Development and Contributing

Contributions are welcome, from bug fixes and documentation to model support and performance improvements.

### Development setup

Start from the `lmsysorg/sglang:dev` Docker image, which provides development tools and most dependencies. Clone or mount your SGLang checkout inside the container, then install it in editable mode from the repository root so tests use your local Python changes:

```bash
pip install -e "python"
```

In an activated virtual environment, you can use `uv pip install --prerelease=allow -e "python"` instead. See the [development guide](https://docs.sglang.io/docs/developer_guide/development_guide_using_docker) for container setup and testing.

### Contribute

1. Fork the repository and create a branch for your changes. For larger changes, discuss your proposal in a [GitHub issue](https://github.com/sgl-project/sglang/issues) or on [Slack](https://slack.sglang.io/).
2. Make your changes, run the relevant tests, and add regression coverage for fixes or new behavior. Run `pre-commit run --all-files` before submitting.
3. Open a pull request describing the change and how you tested it. Include benchmarks or accuracy evaluations when relevant.

See the [contributor guide](https://docs.sglang.io/docs/developer_guide/contribution_guide) for formatting, testing, and pull request instructions. Documentation contributors can start with the [docs guide](docs/README.md).

## Community and Sponsorship

SGLang is hosted by [LMSYS](https://lmsys.org/about/), a non-profit open-source organization.

- **Community discussions:** Join [Slack](https://slack.sglang.io/) for technical questions and development discussions.
- **Events:** Find meetups, workshops, and office hours on [SGLang Events](https://www.sglang.io/events).
- **Updates:** Follow [X](https://x.com/lmsysorg) and [LinkedIn](https://www.linkedin.com/company/sgl-project/) for project updates, and the [LMSYS Blog](https://lmsys.org/blog/) for release announcements and technical articles.
- **Project resources:** Explore the [documentation](https://docs.sglang.io/), [Cookbook](https://cookbook.sglang.io/), [roadmap](https://roadmap.sglang.io/), [release notes](https://github.com/sgl-project/sglang/releases), [issue tracker](https://github.com/sgl-project/sglang/issues), and [contributor guide](https://docs.sglang.io/docs/developer_guide/contribution_guide).
- **Contact Us:** For enterprise adoption and deployment, technical consulting, sponsorship, or partnership inquiries, please contact [sglang@lmsys.org](mailto:sglang@lmsys.org).
- **Contributor sponsorship:** Long-term active SGLang contributors are eligible for coding agent sponsorship, including Cursor, Claude Code, or OpenAI Codex. To apply, email [sglang@lmsys.org](mailto:sglang@lmsys.org) with links to your key commits or pull requests.

## Trusted by Industry and Research

SGLang serves production workloads across AI labs, cloud platforms, enterprises, and universities.

<img src="https://raw.githubusercontent.com/sgl-project/sgl-learning-materials/refs/heads/main/slides/adoption.png" alt="Organizations adopting SGLang" width="800">

## Acknowledgment
We learned the design and reused code from the following projects: [Guidance](https://github.com/guidance-ai/guidance), [vLLM](https://github.com/vllm-project/vllm), [LightLLM](https://github.com/ModelTC/lightllm), [FlashInfer](https://github.com/flashinfer-ai/flashinfer), [Outlines](https://github.com/outlines-dev/outlines), and [LMQL](https://github.com/eth-sri/lmql).
