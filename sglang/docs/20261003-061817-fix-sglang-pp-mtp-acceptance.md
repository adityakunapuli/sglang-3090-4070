# Fixing PP × MTP acceptance on Qwen3.8-27B (SGLang) — an open field guide

**Status:** open problem · **Written:** 2026-10-03 · **Related:** the migration doc in this
directory (`20261002-224500-sglang-qwen38-migration.md`) covers how the stack got here.

---

## 0. A note to whoever picks this up

This document is deliberately **not** a step list. It is a map of what is known, what is
suspected, and what has already been falsified. Everything in §3–§5 is a *hypothesis with
a falsification test attached*, not a claim. If your evidence contradicts something written
here, the evidence wins — correct the doc and mention it in the "graveyard" (§6) so the
next person doesn't re-walk the dead end.

The goal state is also open: "MTP works" is the headline, but if you find a better answer
(a different engine, a different partitioning, a different speculation family, a structural
workaround nobody considered), take it. The constraints that are NOT negotiable are in §1.4;
almost everything else is negotiable, including the choice of SGLang itself.

## 1. Ground truth (measured, do not re-derive)

### 1.1 The symptom

On this box (3090 Ti + 4070 Ti S, PP2, nightly `67eab570`, `SGLANG_ENABLE_PP_SPEC=1`,
EAGLE 3/1/4), the server boots, serves, passes correctness gates, and answers vision
questions correctly — with **speculative acceptance stuck at 0**:

```
Decode batch ... accept len: 1.00, accept rate: 0.00
```

Healthy Qwen3.5-family MTP reports `accept len ≈ 3.0` at these steps
(`ikawrakow/ik_llama.cpp#1394`, observed in April 2026 against SGLang single-node; LMSYS's
Qwen3.8-Flash-Next blog reports 3.3 at TP4/B200). `1.00` means the *mandatory bonus token*
is the only token ever surviving verify — none of the drafted tokens match, ever.

Consequence measured: decode 25 tps with spec vs 42.6 without. Spec is pure overhead
until acceptance rises above ~1.8 (rough breakeven at our measured draft/verify cost).

### 1.2 What proves the failure is in the draft input, not verify

- Both verify implementations produce identical `accept len 1.00`: default
  (`intermediate_ssm`) and `--enable-linear-replayssm-spec`. Same symptom across two
  independent verify kernels → the verify mechanics are probably fine; the tensors the
  draft consumes are probably not.
- Output *content* is correct (gates pass, vision answers correctly). Spec verify is
  done by the target, so this correctness is guaranteed regardless of draft sanity —
  it tells us nothing is wrong with the target path, only that we can't yet see what
  the draft is chewing on.

### 1.3 Walls already confirmed (don't re-test these)

| Attempt | Result |
|---|---|
| v0.5.20 image | refuses PP+spec outright |
| v0.5.21 image | PP+spec behind `SGLANG_ENABLE_PP_SPEC=1`, crashes on the mm assert |
| PP1 (single 3090) + MTP | weights + draft + pools → min viable `--mem-fraction-static` 0.983 → no KV room. Physically infeasible for MTP at any useful context |
| `--speculative-algorithm DFLASH` | hard-capped to `pp_size == 1` in `_handle_dflash` |
| TP2 + spec | TP refuses asymmetric VRAM without a bypass; and TP = 64 all-reduces/token over a PCIe 4.0 x4 PHB link — slow even if it worked |
| `--speculative-token-map` | not applicable (untied head); #42094 is a different bug |
| `--enable-linear-replayssm-spec` | boots, accept still 0.00 |
| Waiting on KV/page flags | mem layout isn't the trigger |

### 1.4 Hard constraints (negotiable only by the human)

- GPUs are exactly: 3090 Ti (24 GB, sm_86) + 4070 Ti S (16 GB, sm_89, NVLink-less,
  PCIe 4.0 x4 uplink, P2P blocked at driver, both on separate root complexes).
  No hardware purchases are on the table.
- The 4070 Ti S also hosts jellyfin + audio-cpp + whisper (~1.7–2.6 GB resident).
- The model must stay vision-capable and MTP-equipped in its checkpoint. Currently
  `ukisai/Swift-1.5-Qwen3.8-27B` (INT4 AWQ, MTP BF16 intact) at
  `/mnt/data/models/llm/Qwen3.8-SGLang`.
- Consumers: Frigate + Paperless OCR/AI (vision), Hermes/Agent Zero/Open WebUI (text).
  Regression to a no-vision or no-concurrency backend is a non-answer.
- The shipped no-spec config in `docker/sglang/` is production. Experiments must not
  wedge it for hours without notice; it takes ~10 min to come back.

## 2. How the machinery actually works (and where the seams are)

Orientation map, current as of clone `65c7425` + nightly `67eab570`:

```
Prefill/verify batch
  └─ general_mm_embed_routine (FIRST PP rank only runs the vision tower)
       └─ sets forward_batch.mm_input_embeds (merged embeddings incl. vision)
  └─ Qwen3_5ForCausalLM.forward
       ├─ non-last rank: residual_batch.to_pp(hidden_states, fb)  [packs dict]
       │     + LOCAL PATCH (patches/qwen3_5_mm_relay.py) also packs mm_input_embeds
       └─ last rank: reads hidden_states (and mm_input_embeds) back from proxy

Draft (last rank only):
  └─ eagle_worker_v2 → spec_info.hidden_states (from verify output, relayed as
       draft_hidden_states, scheduler_pp_mixin.py:913)
  └─ Qwen3_5MTP.forward: cat(input_embeds, hidden_states) → fc → layer → logits

Verify (all ranks):
  └─ tree rebuilt identically on every rank from PPSpecRelayInput (pp_spec_relay.py)
  └─ recurrent-state commit via request-pool-stable rows (post-#40001)
```

Seams where a row/shape/content mismatch hides:

- **S1** `draft_input.hidden_states` relay: sized by the *composition that ran the
  forward*, which "can differ from the live batch by the time the result comes back
  around the ring" (pp_spec_relay.py docstring).
- **S2** mrope positions: qwen3_5-VL positions are (3, n); draft-extend rebuild on the
  last rank must reconstruct them (`eagle_draft_cuda_graph_runner.py:626` handles the
  graph shape, not necessarily the content).
- **S3** hidden-state capture point: NEXTN MTP expects *pre-final-norm* states and
  applies its own `pre_fc_norm_hidden`. If PP captures post-norm (or skips capture for
  the multimodal class and falls back somewhere), scales are wrong ⇒ all drafts die.
- **S4** `set_eagle3_layers_to_capture` asymmetry: `qwen3_5_text.py` just got #42002
  (Oct 3) for EAGLE3/DFLASH aux capture. The multimodal class `qwen3_5.py` did NOT get
  an analogous pass. Whether plain NEXTN uses that path at all is unknown — check before
  assuming relevance.
- **S5** my mm-relay patch itself: restores `fb.mm_input_embeds` on the last rank, but
  does the *draft's own* forward_batch (a different object built by the draft worker)
  ever see it? qwen4-exp's flow suggests the scheduler batch carries it; unverified for
  qwen3_5. Note: acceptance is 0 on **plain text**, so S5 alone cannot be the whole
  story — but it could be masking a second defect.

## 2.5 How this got found — process notes (steal the moves, not just the answers)

The conclusions in §1 were cheap; the *mis-conclusions* that preceded them were not.
These are the moves that worked, including the ones that caught earlier sessions'
mistakes. Use them before believing anything in a config file, docstring, or dashboard.

- **A file existing is not evidence it works.** An earlier session read
  `vllm/docker-compose.yml` (`VLLM_PP_LAYER_PARTITION=26,14`, 64k window) as proof PP
  worked on this hardware. It was the tombstone of a *failed* attempt. Whenever you
  cite an artifact as evidence, ask: does this prove success, or only that someone tried?
- **Distrust your own verification method, then verify that.** The first session
  concluded "still broken on today's main" — but the clone was shallow+sparse, so the
  merge-base check used to prove absence of the fix was itself invalid, and half the
  tree wasn't even on disk. The correction came from a one-line `ls` on the actual
  checkout. When a conclusion is load-bearing, name the mechanism that produced it and
  sanity-check the mechanism.
- **Read the source, not the docs, then read the source of the source.** "PP and spec
  are mutually exclusive" lived in a validation hook; the gate around it
  (`SGLANG_ENABLE_PP_SPEC`) was undocumented; the half-lifecycle of that feature was
  visible only on the upstream tracker issue (#39634), not in any release note. Budget
  time to find the *tracking issue* for a half-landed feature — that's where the truth
  about in-flight work concentrates. Searching for a tracker before debugging is often
  worth more than debugging.
- **Diff against time, not just code.** "Is the fix in the image?" was answered three
  ways and all three mattered: grep the running container's source (`docker run --
  entrypoint`), check the image's embedded git commit, then diff that commit against
  the research clone. Different answer dates = different failure modes blamed on the
  wrong cause.
- **Falsify structurally before falsifying empirically.** "PP1+MTP doesn't fit" cost
  one docker run and one log line (min mem-fraction 0.983). "DFLASH needs pp_size==1"
  cost one grep. Both would have cost many boot cycles if discovered by experiment.
  Rank candidate tests by (information gained ÷ minutes).
- **Instrument the negative, not just the success.** The acceptance=0 finding came from
  grepping logs for `accept len` AFTER noticing decode was slower — the crash was fixed
  but the feature was silently worthless. Every "it boots now" claim deserves a "and
  here's the metric that proves the feature does its job, not merely runs."

## 2.6 The efficiency substrate (why Step 0 is not optional)

Iteration cost asymmetry is the hidden boss fight here: reading source is free,
booting the 27B is ~10 min. Every experiment queued through the big model fights
against that budget. The countermeasures that exist or have been proposed:

- **Shrink the reference, not just the test.** The "small vision+GDN+MTP model at PP1"
  idea exists because the 27B physically cannot be the A/B reference (§1.3). If no
  suitable small model exists, building one (trim layers, keep the class trio) is a
  task with a *known boot time measured in seconds*, which converts every future
  question from 10 min to 10 s. This is the difference between a debugging session and
  a debugging lifestyle.
- **Warm the caches like they're infrastructure.** flashinfer/triton/inductor artifacts,
  the HF model dir, compiled CUDA graphs — every one is rebuildable and none should be
  rebuilt. `sglang-cache` exists in compose for this reason; one-off test containers
  that skip it pay the full JIT tax every time.
- **Pre-write the decision tree, not just the test.** Before booting the expensive
  harness, write down "if X, next experiment is Y; if not X, next is Z" for the top
  two outcomes. The 10 minutes then run while you think about something else, and you
  never fall for the sunk-cost "well it's up, let me also try..." meandering that eats
  GPU-hours.
- **Treat container boots as batches, not turns.** A single container can answer three
  questions if you script the probes before launching (accept-len probe, position
  dump, mrope dump in ONE run). Composing probes beats sequencing them.
- **Snapshot mid-state, not just end-state.** `git stash`, patch files under
  `patches/`, JSONL result logs — the point is that "what did I know when X happened"
  is always recoverable. Debugging sessions that destroy their own history force
  re-discovery. (This section itself survived a tree-clobber only because it had been
  committed minutes earlier — proven live.)
- **Don't fight the box's physics; instrument around it.** The resources that are
  scarce are boot cycles and GPU-hours, not CPU-side analysis. A cheap Python probe,
  a log grep, or a source read that *precedes* a boot is nearly free; treat every
  successful boot as a chance to answer three questions at once.

## 3. Hypotheses, each with a cheap falsification test

Orphaned assumptions are what made this bug long-lived. Test these in whatever order
your evidence suggests.

**H1 — relayed hidden states are misaligned or mis-ordered ( favourite ).**
Test: on the last rank, log `(rid, seq_len, draft_hidden_states.shape, checksum)`
alongside `spec_info` token ids for the first ~20 decode iterations of a single greedy
request; compare against the same capture from a *small PP1-working* reference (see §5).
An off-by-one or batch-composition offset shows immediately. Files: `eagle_worker_v2.py`
(hidden_states assignment), `scheduler_pp_mixin.py:913/1155`.

**H2 — draft never sees correct embeddings for its first token after verify.**
Test: force `forward_batch.mm_input_embeds = None` everywhere (pure text batch) and
compare draft's first-token argmax against the target's bonus token, manually, with a
known 10-token prompt ("The capital of France is"). If the draft argmax is an obvious
unrelated token class (gibberish, image special tokens), embeddings are scrambled — and
the checkpoint-copy path in `pp_draft_embedding.py` is suspect (it loads
`model.embed_tokens.weight` — confirm it didn't silently pick the MTP layer's own
embedding: `_LAYER_KEY_RE` in that module exists precisely to avoid that; check what it
actually picked from our checkpoint layout).

**H3 — positions (mrope) rebuilt wrongly under PP spec.**
Test: log `positions[:,-8:]` fed to the MTP draft vs the target at the same decode step.
Text requests have all mrope channels equal to the absolute position — any drift or
flattening is fatal and visible. This class of bug would be invisible to the existing
e2e test (llama EAGLE + hybrid GDN has no mrope).

**H4 — capture-site bug, multimodal class only.**
Test: compare what `spec_info.hidden_states` contains on the last rank with what
`Qwen3_5ForCausalLM.forward` emitted pre-norm (add a temporary capture). If they differ
by exactly the final norm, look at whether the capture happens through `logits_processor`
output vs an aux hook — and read #42002's diff for the pattern the text twin needed.

**H5 — not a relay bug at all: the draft is fine, but the verify-time acceptance
comparison uses the wrong reference token ids under PP.**
Test: with a tiny prompt, dump draft's top-1 proposed token ids AND the relayed tree
tokens (`PPSpecRelayInput.tokens`) on a non-last rank; if non-last ranks see a different
tree than the last rank proposed, the relay/degenerate padding path is wrong.
`PPSpecRelayInput.degenerate()` zero-pads topology — a request whose first decode comes
out of a *finished* prefill merged late might keep degenerate rows forever. Worth one
look at `adopt()`/merge order under churn.

If ALL of H1–H5 come back clean, the bug is somewhere nobody has looked yet — which is
real progress; write that down.

## 4. Instrumentation & loop discipline (suggestions, not rules)

(Read after §2.5–§2.6 — those sections are the meta-version of this one.)

- The single biggest lever on iteration speed is **graph-capture/JIT time** (~10 min
  boot). Sketches that help: bind-mount `/root/.cache` into every throwaway container
  (`sglang-cache` volume), use `--skip-server-warmup` for answer-irrelevant runs, pass
  `--cuda-graph-bs 1 2` to shrink capture sets, and keep one *long-lived* disposable
  container whose entrypoint you `docker exec` a new launcher into.
- For text-only A/B, the two gated consumers (Frigate/Paperless) don't need to be
  attached. Hit the container directly.
- Keep a "PP1 baseline" available even if not on this box: acceptance on a small
  vision+GDN+MTP model (a 2B-scale one if it exists, or trim a checkpoint) running
  single-GPU is the reference that turns 27B-PP2 failures from "is MTP broken
  anywhere?" into "is MTP broken *under PP*?" — the distinction that decides whether
  you patch SGLang or the model integration.
- Log to JSONL, not prose; results live in `tests/results/` alongside `bench.py` output.
  The naming habit so far: `pp<N>-<ctx>-<tag>.json`.

## 5. External arteries (watch, and feed)

- **sgl#39634** — PP×spec tracking issue; read before writing any fix; the landing order
  there may already include "the qwen3_5 half of #40501".
- **sgl#40501** (closed/merged) — qwen4-exp PP mm-relay; our `qwen3_5_mm_relay.py` is its
  qwen3_5 port. If upstream lands a native version, drop ours.
- **sgl#40001** (merged, in our nightly) — hybrid recurrent-state commit; already fixed
  the *crash*-class bug here.
- **sgl#40499, #39378** — PP-spec scheduling/PD gate work, watch for conflicts.
- **sgl#42094, #42002** — ruled out for us but document the qwen3_5-MTP assumptions
  people are hitting elsewhere.
- If you change engines instead: vLLM has its own open multimodal-MTP crash
  (vllm#58203) and the `1CatAI` fork PR #636 (stage-local drafter) is the most advanced
  public fix sketch in that ecosystem — an instructive read even if you stay on SGLang.

## 6. Graveyard (dead ends; add yours)

- Pinning v0.5.21 and waiting for flags — superseded by nightly (carries #40001).
- Bigger `--speculative-num-steps` to hide the acceptance defect — acceptance is 0;
  longer chains make it *slower*, monotonically.
- `--enable-mixed-chunk` — corrupts mamba radix state under load (#39342); off for cause,
  not by accident.
- SINGLE-GPU + MTP — physically infeasible (§1.3).
- TP2 — interconnect math kills it before any bug does.

## 7. What "done" looks like (any of these counts)

1. `accept len ≥ ~2.5` at 3/1/4 on this model under PP2, with decode ≥ ~1.5× the
   no-spec baseline at matched context; needle-at-148k still correct; gates+vision still pass.
2. OR: an upstream issue/PR accepted that credibly lands the fix on a known nightly, with
   this repo's toggle documented to flip on merge.
3. OR: a *different* architecture swapped in (engine, model quant, speculation family)
   beating the current 42.6 tps @ 2k / 37.9 @ 60k with equal-or-better context and
   concurrency — with the migration doc updated so the swap is explainable.

Whichever path you take: update this file's graveyard and ground-truth tables, and when
the picture changes materially, commit the `sglang/` dir again.
