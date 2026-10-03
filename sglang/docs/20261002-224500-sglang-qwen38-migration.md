# SGLang migration: Swift-1.5-Qwen3.8-27B on 2×consumer GPU

**Date:** 2026-10-02
**Author:** opencode (autonomous session)
**Status:** Requirements 1, 2, 4 MET and measured. Requirement 3 (MTP) BLOCKED by upstream bugs; one patched locally, second one deeper.
**Rev:** 2026-10-03 — added section 5.5 (PP+spec patch attempt), section 4.6 (partition rebalance), section 10 (status log).

---

## 1. Original requirements and verdict

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Max context (262,144) | **PARTIAL** — 176,896 usable (69%) | §4.4 |
| 2 | Vision tower enabled | **MET** | §4.2 |
| 3 | MTP included | **BLOCKED** | §5.1, §5.2 |
| 4 | Trained to reduce reasoning tokens | **MET** | §2.1 |

Requirement 3 is blocked by an upstream defect, not by this hardware or configuration. The MTP weights are present, correct, and loaded — SGLang simply cannot run PP together with speculative decoding on this stack today. §5 documents the two dead ends and the recommended workarounds.

---

## 2. Model selection

### 2.1 Choice: `ukisai/Swift-1.5-Qwen3.8-27b-W4A16-AWQ`

Chosen over the originally-proposed `...-W4A16-AutoRound`.

| Candidate | Format | Size | Verdict |
|---|---|---|---|
| **`ukisai/Swift-1.5-Qwen3.8-27b-W4A16-AWQ`** | compressed-tensors | **19.55 GB** | **Selected** |
| `cyankiwi/Swift-1.5-Qwen3.8-27B-AWQ-INT4` | compressed-tensors | 21.02 GB | Larger; same format |
| `ukisai/...-W4A16-AutoRound` | auto-round, g128 | 18.70 GB | MTP shipped inline |

Rationale:
- **Token efficiency is requirement 4**, and Swift 1.5 is the only broadly-distributed derivative explicitly trained for it: 58.5% fewer *median* thinking tokens on GPQA-Diamond while scoring 0.35% higher, and 1.95× on some tasks. Verified against base at every `reasoning_effort` level (xhigh/medium/low).
- **AWQ/compressed-tensors over AutoRound**: Marlin is the best-trodden W4A16 kernel path on Ampere/Ada in both SGLang and vLLM.
- **Smallest of the compressed-tensors options** at 19.55 GB, which matters because VRAM is the binding constraint (§4).
- **MTP shipped as a separate BF16 file** (`model-mtp-bf16.safetensors`, 849 MB), so it is bit-exact and never quantised.

### 2.2 Verification of the checkpoint

Downloaded and inspected directly:

- `total_size` 19,547,553,504 — matches the index exactly.
- **15 MTP tensors**, all BF16, correct shapes (`mtp.fc.weight [5120, 10240]`, `mtp.layers.0.*`).
- **333 vision tensors** present (`model.visual.blocks.0..32.*`).
- 67 transformer layers detected (64 language + embedding/head/norm accounting).
- Licence: Swift Open License v1.0 — free to **US$1M gross annual revenue**, so fine for a home server. Base model Qwen3.8-27B remains Apache 2.0. The repo is marked `gated: true` and needs a one-time browser licence acceptance plus `HF_TOKEN` (inherited from root `.env`).

### 2.3 Per-layer weight measurements (used for all sizing)

| Component | Size |
|---|---|
| Per transformer layer | **198 MB** |
| Vision tower | 921 MB |
| MTP head | 849 MB |
| embed + lm_head + norms | 5.09 GB |
| **Total** | **19.55 GB** |

---

## 3. Hardware characterisation

### 3.1 Measured GPU properties

Empirical, not spec-sheet. Run with the SGLang container, both GPUs visible:

| GPU | CUDA arch | Memory | **Memory BW** | BF16 TFLOPS |
|---|---|---|---|---|
| RTX 3090 Ti | sm_86 | 24,564 MiB | **903 GB/s** | 77.7 |
| RTX 4070 Ti SUPER | sm_89 | 16,376 MiB | **613 GB/s** | 85.6 |

**This corrects an assumption made earlier in the session** (that the 4070 Ti S was the slower card by a wide margin). It is ~32% slower in *memory bandwidth*, which is what matters for W4A16 decode, and ~10% *faster* in dense BF16 compute, which matters for prefill.

### 3.2 Device ordering is a trap

```
CUDA default order:            dev0 = 4070 Ti S,  dev1 = 3090 Ti
CUDA_DEVICE_ORDER=PCI_BUS_ID:  dev0 = 3090 Ti,    dev1 = 4070 Ti S
nvidia-smi index:              0 = 3090 Ti,       1 = 4070 Ti S
```

Without `CUDA_DEVICE_ORDER=PCI_BUS_ID`, **PP rank 0 is assigned the 16 GB card**. This was the direct cause of several early failures (§5.3). The compose sets it explicitly.

### 3.3 Partition rationale

PP throughput is `1/max(t_stage0, t_stage1)`, so the goal is to **balance stage time**, not to fill the larger card.

```
t0 = L0 / 613     t1 = L1 / 903     balance:  L0 / 613 = L1 / 903
L0 + L1 = 64  =>  L0 = 26,  L1 = 38
```

Upstream independently recommends this direction — `docs/advanced_features/pipeline_parallelism.mdx:52`: *"Put the larger partition in the higher PP rank."* PP1 is the 3090 Ti, so the larger partition belongs there. Our `26,38` matches.

This is also good for VRAM: PP0 (4070) carries `embed_tokens + vision + 26 layers` = 8.28 GB measured; PP1 (3090) carries `lm_head + 38 layers` = 10.52 GB measured.

---

## 4. Test results

All figures from `tests/bench.py`, unique (non-cacheable) prompts. Raw JSON in `tests/results/`.

### 4.1 Correctness gates — PASS

| Gate | Expected | Result |
|---|---|---|
| Arithmetic (`2718 × 4319`) | `11739042` | PASS |
| Syllogism | `yes` | PASS |
| Code execution | `45` | PASS |

> Harness correction: an earlier revision asserted `11735042` and produced a false FAIL. The model was correct; the test was wrong.

### 4.2 Vision — PASS

Synthetic PNG with a red border; model correctly answered `" Red"`. The vision tower is live under PP, which the docs do not document as supported (§6).

### 4.3 Throughput (PP2, `26,38`, 176,896-token pool)

| Prompt | TTFT | Decode tok/s | Prefill tok/s |
|---|---|---|---|
| ~2k | 3.38 s | **42.4** | 592 |
| ~20k | 16.68 s | **40.6** | 1,199 |
| ~60k | 51.79 s | **37.1** | 1,158 |

Decode degrades gracefully (~12% from 2k to 60k) — no long-context collapse. Prefill is measured client-side as `prompt_tokens / TTFT` with salted prompts so the radix cache cannot flatter it.

### 4.4 Memory sizing — the central constraint

SGLang splits post-weight VRAM between the **KV pool** and the **mamba state pool**, and they compete directly:

| `--max-mamba-cache-size` | Concurrency | KV pool tokens |
|---|---|---|
| unset (auto), ratio 0.3 | 5 | 164,544 |
| 24, ratio 0.5 | 6 | **176,896** |
| auto, ratio 0.05 | **0 (unusable)** | — |

**This is why 262,144 is not reached.** The KV pool must hold the *whole* context for a single sequence to use it. At ~11.7 KB/token on PP0, 176,896 tokens consumes the entire remaining budget. Closing the gap to 262k needs ~3 GB more on the 4070 Ti S, which does not exist after 8.28 GB of weights.

Secondary constraint, from upstream guidance (`hyperparameter_tuning.mdx:40-45`): reserve 5–8 GB for activations. PP0 ended with **2.44 GB** available — below the safe band, so this configuration is closer to OOM than it looks.

### 4.6 Partition rebalance (supersedes the earlier 26,38)

The initial `26,38` split was optimised for **stage-time balance**, which is the wrong
objective for this box and left the 3090 Ti underused (67% vs the 4070's 89%). The
correct objective is **VRAM utilisation**: the KV pool size is set by the *tighter*
stage, and PP0 (the 4070 Ti S) is always the tighter one, so layers must move **off**
PP0 and onto PP1.

| Split | KV pool tokens | 3090 Ti used | 4070 Ti S used |
|---|---|---|---|
| `26,38` | 179,328 | 16,455 / 24,564 (67%) | 14,504 / 16,376 (89%) |
| **`20,44`** | **205,888** | 18,733 / 24,564 (76%) | 12,862 / 16,376 (79%) |
| `8,56` | not measured (aborted) | - | - |

+15% context and memory moved to the 3090 Ti as intended. `20,44` is the current
setting. Cost: stage times become `20/613 = 0.0326` vs `44/903 = 0.0487`, so PP1
becomes the bottleneck and single-stream decode throughput is expected to drop ~13%
versus the balanced split. That trade was accepted deliberately: context and
concurrency matter more than single-stream latency for a queue-serving agentic load.

**Not yet tuned:** further lopsided splits, and a sweep of `--max-mamba-cache-size`
to map the concurrency-vs-context frontier.

### 4.5 Concurrency — works, but capped at 6

| Concurrent | Succeeded | Failed | Aggregate tok/s |
|---|---|---|---|
| 1 | 1 | 0 | 11.9 |
| 4 | 4 | 0 | 38.2 |
| 8 | 8 | 0 | 73.2 |

Zero failures at 8-way. But aggregate tok/s is lower than a pure-decode figure because each request carries a unique ~1.2k-token prompt, so prefill dominates the wall time. **Concurrency is hard-capped by the mamba pool at 6**, not by `--max-running-requests`. For the real workload (Frigate, Paperless ×2, Open WebUI, Hermes, Agent Zero) this is the number that matters most, and 6 is thin.

> On concurrency: the mamba state pool is a *fixed-size recurrent state per sequence*, not per-token. It is a completely separate budget from KV, and on this architecture it — not KV — is what limits parallelism.

---

## 5. Blockers found

### 5.1 PP + speculative decoding is broken (blocks requirement 3)

**v0.5.20** hard-refuses:
```
AssertionError: Pipeline parallelism is not compatible with overlap schedule, speculative decoding
```
(`srt/arg_groups/validation_hook.py:82`)

**v0.5.21** adds an undocumented escape hatch `SGLANG_ENABLE_PP_SPEC=1`, which clears validation. But it then fails during warmup:
```
PP1: AssertionError: assert input_embeds is not None
```

`SGLANG_ENABLE_PP_SPEC` appears **nowhere in the sgl-docs repository** — not in the 76 documented `SGLANG_*` variables. It is an in-flight feature. Two attempts were made to reach it (GPU ordering corrected, flashinfer fallback applied, partition rebalanced) and both ended at the same assertion.

### 5.2 TP is not a viable alternative

| Attempt | Result |
|---|---|
| `--tp-size 2` | `RuntimeError: The memory capacity is unbalanced` — 13.53 GB vs 23.09 GB |
| + `SGLANG_ENABLE_TP_MEMORY_INBALANCE_CHECK=0` | Passes balance check, then `Not enough GPU memory for hybrid state cache` (`total_rest_memory=0.41 GB`) |

TP requires roughly equal VRAM. The env bypass gets past the check but the 16 GB card still cannot hold its half of the weights plus a usable KV and mamba pool.

### 5.3 Two kernel-level bugs that needed workarounds

**(a) flashinfer CuTe DSL JIT fails under heterogeneous PP.**
```
cudaErrorNoKernelImageForDevice (error code 209)
= note: Target SM ARCH: unknown (unspecified)
```
in `flashinfer/norm/fused_add_rmsnorm_cute`. Also undocumented. Workaround: `FLASHINFER_USE_CUDA_NORM=1` (the library's own documented "CUDA JIT instead of CuTe DSL" fallback). Documented alternative that was not needed: `--attention-backend triton --sampling-backend pytorch`.

**(b) GPU ordering.** See §3.2 — `CUDA_DEVICE_ORDER=PCI_BUS_ID` is mandatory.

**(c) Early false alarm.** A Marlin "no kernel image" error during the first PP2 attempt looked like a missing `sm_89` binary. It was not: both GPUs pass PP1 alone. It was misdiagnosed because the run lacked `CUDA_DEVICE_ORDER=PCI_BUS_ID`, so the 44-layer partition landed on the 16 GB card. Recorded because the arch list genuinely omits `sm_89` (`torch.cuda.get_arch_list()` = `sm_75, sm_80, sm_86, sm_90, sm_100, sm_120`) and that coincidence invited the wrong conclusion.

### 5.4 Upstream hazards to keep disabled

- **`--enable-mixed-chunk` — never enable.** [sgl#39342](https://github.com/sgl-project/sglang/issues/39342), **open on main**: `merge_batch()` nulls `mamba_track_*`, the GDN backend skips the state write, yet the slot is still donated to the radix tree. Later requests sharing that prefix restore an SSM state that was never written. Measured upstream: exact match **0.908 → 0.836**, p50 **200 ms → 3–7 s**, only when prefills co-batch with decodes — i.e. precisely this multi-consumer workload. The docs give **no warning** about this flag on hybrid GDN models.
- [sgl#29633](https://github.com/sgl-project/sglang/pull/29633) — wrong silent first token on a radix cache hit when prompt length ≡ 1 (mod 64).
- [sgl#29034](https://github.com/sgl-project/sglang/issues/29034) — `--hicache-size` is per-rank GB and double-allocates for hybrid mamba. HiCache not enabled.

### 5.5 PP + spec: local patch attempt (partial success)

`SGLANG_ENABLE_PP_SPEC=1` on **today's nightly** (`nightly-dev-20261002-67eab570`,
commit `65c7425`) still fails warmup with the same assertion as v0.5.21. Since the
precedent for patching the serving engine already exists in this repo
(`vllm/patches/` monkey-patches SGLang internals for the Nemotron path), the repo was
cloned to `/mnt/data/docker/sglang/sglang-src` and the failure traced.

**Root cause** — `srt/models/qwen3_5_mtp.py:217`:

```python
input_embeds = forward_batch.mm_input_embeds
if (forward_batch.forward_mode.is_extend()
        and forward_batch.contains_mm_inputs()
        and not forward_batch.forward_mode.is_draft_extend_v2()):
    assert input_embeds is not None          # <-- fires
    ...
if input_embeds is None:                     # <-- already handles None correctly
    input_embeds = self.model.embed_tokens(input_ids)
```

The assert is gated on `contains_mm_inputs()` ("this batch carries vision inputs"),
but `input_embeds` comes from `forward_batch.mm_input_embeds`, which is threaded
through the EAGLE draft worker (`srt/speculative/eagle_worker_v2.py`). Under
`SGLANG_ENABLE_PP_SPEC` the draft worker never has it populated —
`model_runner.py:477` explicitly special-cases `is_draft_worker` — yet
`contains_mm_inputs()` is still True because the batch was built with multimodal
inputs upstream. The fallback that handles `None` correctly sat *below* the assert,
so it could never run.

This only reproduces for **multimodal** models. Text-only models take the
`contains_mm_inputs() == False` path, which is why every published PP + spec config
works and this one does not.

**Patch** (`patches/qwen3_5_mtp_pp_spec.py.md`): hoist the `None` fallback above the
multimodal block and gate that block on the tensor existing rather than on the batch
merely claiming vision inputs. Behaviour is unchanged for every path that already
worked.

**Result: the assertion is fixed.** The server proceeds past warmup into the PP-spec
relay and then fails differently:

```
vectorized_gather_kernel: Assertion `ind >=0 && ind < ind_dim_size
&& "vectorized gather kernel index out of bounds"` failed
```

This is a hidden-state shape/extent mismatch in the PP spec relay path — a deeper
structural gap, not a one-line guard. **Conclusion: PP + speculative decoding is
unfinished upstream for multimodal models**, in the same release and on today's main.
The patch is retained (it is strictly more permissive and correct) but does not
unblock the feature.

**Remaining route to MTP + vision: `--tp-size 2` with spec decode.** TP was rejected
earlier (§5.2) for refusing asymmetric VRAM, but that test predates the mamba-pool
sizing work in §4.4 and has not been retried with a tuned `--max-mamba-cache-size`.

---

## 6. What upstream documents (negative results)

A subagent cloned `sgl-project/sgl-docs` (328 files) and searched exhaustively. Four of our five core needs are **not documented anywhere**:

| Topic | Status |
|---|---|
| PP + vision/multimodal | **Not documented.** `grep -rn "pp-size" cookbook/vlm/` → 0 hits. No PP page mentions vision. |
| `SGLANG_ENABLE_PP_SPEC` | **Not documented.** 0 occurrences repo-wide. |
| Qwen3.8-27B cookbook | **Does not exist.** Closest is Qwen3.5-397B-A17B, H100/H200/B200 only. |
| Heterogeneous / unbalanced VRAM | **Not documented.** All PP examples are homogeneous (TP8+PP2, PP4, PP8). |
| `SGLANG_PP_LAYER_PARTITION` | **Documented**, `pipeline_parallelism.mdx:52` — larger partition on the higher rank. |

Also undocumented: the mamba-pool ↔ KV-pool sizing relationship (no formula, no bytes/token), and any `--enable-mixed-chunk` GDN warning.

**Conclusion: this configuration is genuinely uncharted.** The 4-of-5 negative result is itself the most important finding — it means the numbers in §4 cannot be checked against a reference implementation, only against physics.

---

## 7. Recommended next steps

### 7.1 Requirement 3 (MTP) — three options

| Option | Trade-off |
|---|---|
| **A. Wait for upstream** — file a minimal repro on sgl#39342-style PP+spec | Cleanest. `SGLANG_ENABLE_PP_SPEC` is clearly in flight. |
| **B. Drop PP, single 3090 Ti, keep MTP** | MTP works (verified — MTP draft loads clean, 5.07 GB). But measured `max_mamba_cache_size=3` → **concurrency of ~0–1**. Unusable for 6 consumers. |
| **C. Keep PP2, ship without MTP** | 176,896 tokens, 6-way concurrency, ~40 tok/s. Works today. |

**Recommendation: C now, A in parallel.** MTP is a throughput multiplier; the mamba-pool concurrency ceiling is a harder constraint and PP2 is the only way past it.

### 7.2 To close the context gap (164k → 262k)

1. Move `embed_tokens` + vision off PP0, or shard PP0's weights — not possible without upstream work.
2. `--mamba-ssm-dtype bfloat16` is already on; FP8 mamba checkpoints (`--enable-int8-mamba-checkpoint`) would cut the pool further and buy KV — untested here.
3. Accept ~176k. Note the 4070 Ti S also hosts jellyfin + audiocpp + whisper (~1.7 GB currently resident), so PP0's real budget is already reduced.

### 7.3 To raise concurrency above 6

This is the highest-value remaining work for an agentic workload. The knob is `--max-mamba-cache-size`, traded directly against KV tokens (§4.4). A sweep of 16 / 24 / 32 / 48 would map the frontier; 24 was measured, others are not.

### 7.4 Engineering hygiene

- Bind-mount Python files for fast iteration (pattern already used by `vllm/patches/`). Persist `~/.cache/flashinfer` — JIT compiles once per kernel, not per experiment.
- Model load is ~6 min. A long-lived container with a restart-only-on-flag-change loop will dominate iteration time.

---

## 10. Status log

- **2026-10-02 22:45** — Session start. Requirements 1-4.
- **2026-10-03 01:20** — Model downloaded and verified (19.55 GB, 15 MTP tensors, 333 vision tensors). Req 4 satisfied by selection.
- **2026-10-03 01:30** — PP2 serving. Found the `CUDA_DEVICE_ORDER` / `CUDA_VISIBLE_DEVICES` trap (§3.2) and the flashinfer CuTe DSL failure (§5.3a). Req 2 verified.
- **2026-10-03 01:50** — Req 1 measured: 179,328 tokens at `26,38`.
- **2026-10-03 01:59** — Docs moved here from `/mnt/data/scripts/docs/` per user request.
- **2026-10-03 02:03** — `20,44` rebalance: 205,888 tokens (+15%), memory shifted to the 3090 Ti.
- **2026-10-03 02:10** — PP+spec patched locally. Assert fixed; deeper gather-kernel failure found. Req 3 still blocked.

## 8. Reproduction

```bash
cd /mnt/data/docker/sglang
docker compose up -d
cd tests && python3 bench.py --label repro --decode-targets 2000,20000,60000 --conc-levels 1,4,8
python3 needle.py 170000
```

Files:
- `tests/bench.py` — gates, vision, decode, prefill, concurrency. Documents two measurement traps in its own docstring (SGLang's decode log line charges prefill time to decode; unsalted prompt prefixes get radix-cached and inflate prefill throughput).
- `tests/run_test.sh` — single-experiment launch/bench/teardown.
- `tests/needle.py` — needle-in-haystack at arbitrary depth.
- `tests/results/` — raw JSON per run.

---

## 9. Corrections to earlier claims in this session

Recorded so they are not repeated:

1. **"The 4070 Ti S has 60% less memory bandwidth"** — wrong direction. Measured 613 vs 903 GB/s: the 4070 Ti S has **~32% less**, and **more** BF16 compute (85.6 vs 77.7 TFLOPS).
2. **"The existing `vllm/` PP2 profile proves this works here"** — false. That compose file records a *failed* attempt; the `26,14` split and 64k window were workarounds, not a tuned solution. A file existing is evidence someone tried, not that it succeeded.
3. **"Pin `v0.5.20`"** — superseded twice. First by `v0.5.21` (for `SGLANG_ENABLE_PP_SPEC`), then by **nightly 67eab570** (20261002), which carries the PP+spec hybrid recurrent-state fix (#40001).
4. **"Split `44,20`"** — wrong; assumed nvidia-smi ordering. With `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=1,0` (4070 Ti S becomes PP0), the current production split is `20,44`.
---

## 11. Second session (2026-10-03): MTP under PP — unblocked, but acceptance is 0

### What was established

1. **Both startup crashes are fixable and were fixed.**
   - `assert input_embeds is not None` at `qwen3_5_mtp.py:217`: root cause is that the
     EAGLE draft worker under `SGLANG_ENABLE_PP_SPEC=1` never receives
     `forward_batch.mm_input_embeds` (skipped for `is_draft_worker` in
     model_runner.py) while `contains_mm_inputs()` is still True. Multimodal-only
     defect — text-only PP+spec configs pass this branch untouched.
   - The follow-on `vectorized_gather_kernel` assert was the hybrid-GDN
     recurrent-state commit bug fixed upstream by **sgl#40001** ("Fix hybrid
     recurrent-state commit and micro-batch pairing under PP x speculative
     decoding", YAMY1234). **Already merged in nightly 67eab570** — the image now
     pinned in this stack. (The first session's "structural relay bug" verdict was
     premature; the clone I checked out was shallow, which masked that the fix had
     already landed.)

2. **Two local patches are required and live in `../patches/`:**
   - `qwen3_5_mm_relay.py` — ports upstream #40501's qwen4-exp mechanism to
     qwen3_5.py: the first PP stage packs `forward_batch.mm_input_embeds` into the
     PPProxyTensors dict next to `hidden_states` (in `Qwen3_5ForCausalLM.forward`)
     and the last stage restores it, so the MTP draft's vision-token embeddings
     cross the stage boundary. The PP transport is a generic tensor dict, so no
     other wiring is needed.
   - `qwen3_5_mtp_pp_spec.py` — degrades gracefully (token-embed fallback) when the
     draft sees `contains_mm_inputs()` without the tensor, instead of asserting.
     Correctness-neutral: spec verify is done by the target, so a degraded draft
     only costs acceptance, never output distribution.

3. **PP+EAGLE+multimodal boots and serves correctly.** All correctness gates pass;
   vision answers correctly with MTP enabled; verify/draft CUDA graphs capture.

4. **BUT draft acceptance is 0.00** (accept len 1.00, i.e. no bonus token ever
   accepted) on plain text, with both the default verify kernel and
   `--enable-linear-replayssm-spec`. Rejected proposals ⇒ spec is pure overhead:
   ~25 tok/s vs ~42 with spec off. This is an upstream gap, not a config issue:
   - Same symptom class documented in the #40001 e2e test rationale
     (`test_pp_spec.py`: "a mis-sized proxy buffer or a mis-rebuilt tree shows up
     as... speculation that never gets accepted") for a *separate-draft* model;
     our in-checkpoint MTP + multimodal path predates the Qwen4-Exp work in
     #40501 / tracker #39634, which is still landing piecemeal.
   - **`--enable-linear-replayssm-spec` made no difference** (ruled out the
     mamba-state hypothesis for the acceptance).
   - **DFLASH is not an escape hatch**: `_handle_dflash` requires `pp_size == 1`.
   - **PP1+MTP is out on this hardware**: draft weights + pools push the minimum
     viable mem-fraction to 0.983 on the 3090 Ti — no room for KV/mamba pools.

### Decision shipped in compose

MTP disabled by default (`SGLANG_SPEC_FLAGS` empty, `SGLANG_ENABLE_PP_SPEC=0`,
patch mounts commented). PP2 no-spec config restored: `20,44` partition,
`--context-length 262144`, mem 0.95. Re-test MTP on each new nightly: uncomment
mounts + flags, set `SGLANG_ENABLE_PP_SPEC=1 SGLANG_MEM_FRACTION=0.80
SGLANG_CONTEXT_LENGTH=32768`, then check `accept len` in the logs — fixed means
> ~2.5 for 3/1/4 (healthy Qwen3.5 MTP measures accept len ~3.0).

### Candidates for the acceptance fix (when upstreaming / next session)

- Verify the draft's hidden-state relay contents under PP (pp_spec_relay.py
  `PPSpecRelayInput`) against a single-GPU run — byte-compare first-token draft
  logits. The all-zero acceptance on plain text suggests mis-indexed hidden
  states or token positions (mrope channels) rather than numeric noise.
- Watch tracker sgl#39634 for the qwen3_5-specific halve of #40501; when it
  lands, drop `patches/qwen3_5_mm_relay.py`.
- #42094 (tied-draft-lm_head token-map bug) is NOT applicable (no
  `--speculative-token-map`, untied head) — documented here so nobody re-chases it.

### Validation of the shipped config (nightly 67eab570, 262k, 20,44, mem 0.92, mamba 24)

| Test | Result |
|---|---|
| Correctness gates (arith/logic/code) | PASS (3/3) |
| Vision | PASS |
| Usable KV pool (`max_total_num_tokens`) | 187,008 tokens |
| Decode @2k / @60k | 42.6 / 37.9 tok/s single-stream |
| Prefill @20k / @60k / @148k | ~880 / ~1013 / ~1100 tok/s |
| Concurrency 8-way | 8/8 ok, 45.6 tok/s aggregate |
| Needle at 148,268 tokens | CORRECT (135.5 s wall) |

Notes:
- `SGLANG_MEM_FRACTION` must be 0.92, not 0.95: triton GDN kernels lazy-load
  AFTER pool sizing and the extra ~200 MB OOMs the 4070 stage mid-prefill.
- The server still forwards `--context-length 262144` to clients; the KV pool
  holds 187k, so >187k prompts return 400. Clients should treat 187k as the
  real window.

### Status log (continued)

- **2026-10-03 04:40** — nightly 67eab570 + PP_SPEC=1 + both patches: boots, vision OK.
- **2026-10-03 05:05** — acceptance measured 0.00 (default verify AND
  --enable-linear-replayssm-spec). MTP disabled by default; toggle documented.
- **2026-10-03 05:20** — production config validated end-to-end (see table above).
- **2026-10-03 04:55** — acceptance measured 0.00 @ 3/1/4 on text; replayssm-spec variant: same.
- **2026-10-03 05:10** — PP1+MTP confirmed OOM-infeasible (min mem-fraction 0.983).
- **2026-10-03 05:20** — shipped no-spec config as default; MTP documented as gated toggle.
