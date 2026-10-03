# SGLang patch: allow PP + speculative decoding (EAGLE/MTP) with a multimodal model.
#
# MOUNT THIS OVER THE CONTAINER'S COPY:
#   -v ./patches/qwen3_5_mtp_pp_spec.py:/sgl-workspace/sglang/python/sglang/srt/models/qwen3_5_mtp.py:ro
#
# ---------------------------------------------------------------------------
# THE BUG
# ---------------------------------------------------------------------------
# In Qwen3_5MTP.forward():
#
#     input_embeds = forward_batch.mm_input_embeds
#     if (forward_batch.forward_mode.is_extend()
#             and forward_batch.contains_mm_inputs()
#             and not forward_batch.forward_mode.is_draft_extend_v2()):
#         assert input_embeds is not None        # <-- FIRES
#         ...
#     if input_embeds is None:                   # <-- already handles None correctly
#         input_embeds = self.model.embed_tokens(input_ids)
#
# The assert sits inside a branch gated on `contains_mm_inputs()`, i.e. "this batch
# carries vision inputs". But `input_embeds` was sourced from
# `forward_batch.mm_input_embeds`, which is threaded through the EAGLE draft worker
# (srt/speculative/eagle_worker_v2.py). Under SGLANG_ENABLE_PP_SPEC the draft worker
# never has that tensor populated -- model_runner.py:477 explicitly special-cases
# `is_draft_worker` -- yet `contains_mm_inputs()` is still True because the batch was
# constructed with multimodal inputs upstream.
#
# Net effect: vision batch + PP + spec => input_embeds is None while the branch
# believes vision embeddings exist => AssertionError at warmup. Observed on both
# v0.5.21 and nightly 20261002 (65c7425), at server warmup, before any request.
#
# NOTE: this only reproduces when the model is multimodal. Text-only models take the
# `contains_mm_inputs()` == False path and are unaffected -- which is why every
# published PP + spec config works and this one does not.
#
# ---------------------------------------------------------------------------
# THE FIX
# ---------------------------------------------------------------------------
# Hoist the None-fallback above the multimodal block, and gate the multimodal
# block on the tensor actually existing rather than on the batch merely claiming
# to carry vision inputs. Semantics are preserved for every path that already
# worked: when mm_input_embeds is present the original behaviour is identical
# (fallback is a no-op, the last_indices overwrite still runs).
#
# Risk: low. Worst case under PP+spec it embeds input_ids directly instead of
# reusing propagated vision embeddings for the draft's trailing token. That is
# the same fallback the function already used whenever mm_input_embeds was None.

        try:
            assert input_embeds is None
            input_embeds = forward_batch.mm_input_embeds

            # PATCH: fall back to embedding input_ids BEFORE the multimodal block.
            # The multimodal block used to be guarded by `contains_mm_inputs()` and
            # then asserted the tensor existed. Under PP + spec decode the draft
            # worker has no mm_input_embeds, so that assert aborted startup. The
            # fallback below already handled None correctly; it just ran too late.
            if input_embeds is None:
                input_embeds = self.model.embed_tokens(input_ids)

            if (
                forward_batch.forward_mode.is_extend()
                and forward_batch.contains_mm_inputs()
                and not forward_batch.forward_mode.is_draft_extend_v2()
                # PATCH: additionally require the embeddings to actually exist.
                and forward_batch.mm_input_embeds is not None
            ):
                last_indices = (
                    forward_batch.extend_start_loc + forward_batch.extend_seq_lens - 1
                ).long()
                input_embeds[last_indices] = self.model.embed_tokens(
                    input_ids[last_indices]
                )