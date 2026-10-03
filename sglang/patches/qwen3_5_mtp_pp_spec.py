# Copyright 2023-2024 SGLang Team
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Inference-only Qwen3_5 MTP model."""

import copy
import logging
from contextlib import ExitStack
from typing import Iterable, Optional, Tuple

import torch
from torch import nn
from transformers import PretrainedConfig

from sglang.srt.environ import envs
from sglang.srt.eplb.expert_distribution import get_global_expert_distribution_recorder
from sglang.srt.eplb.expert_location import ModelConfigForExpertLocation
from sglang.srt.layers.layernorm import GemmaRMSNorm
from sglang.srt.layers.logits_processor import LogitsProcessor
from sglang.srt.layers.moe.fused_moe_triton.layer import FusedMoE
from sglang.srt.layers.vocab_parallel_embedding import ParallelLMHead
from sglang.srt.model_executor.forward_batch_info import ForwardBatch
from sglang.srt.model_loader.weight_utils import default_weight_loader
from sglang.srt.models.qwen3_5 import QWEN3_5_KV_SCALE_MAPPER, Qwen3_5ForCausalLM
from sglang.srt.platforms import current_platform
from sglang.srt.runtime_context import (
    get_model,
    get_parallel,
    get_spec,
)
from sglang.srt.utils import add_prefix, get_bool_env_var, is_hip, is_npu

logger = logging.getLogger(__name__)

_is_hip = is_hip()
_use_aiter = get_bool_env_var("SGLANG_USE_AITER") and _is_hip


def _mtp_quant_config(quant_config):
    """The quantization the MTP module itself is built with.

    The MTP module often ships unquantized even though the target checkpoint is
    quantized; the loader's fusion gate has to see the same normalization the
    constructor applies, or it would answer for the target's quantization.
    """
    # Serialized Qwen3.5 ModelOpt checkpoints keep embedded MTP weights in
    # BF16. Disable quantization for those checkpoints; non-serialized
    # modelopt_fp4 still converts MoE expert weights on load.
    if quant_config and quant_config.get_name() == "modelopt_mixed":
        # MIXED_PRECISION lists mtp.* layers only when the MTP head is quantized.
        if any(name.startswith("mtp.") for name in quant_config.quantized_layers):
            return quant_config
        return None
    if quant_config and (
        quant_config.get_name() == "modelopt_fp4"
        and quant_config.is_checkpoint_nvfp4_serialized
    ):
        return None
    if is_npu() and get_spec().speculative_draft_model_quantization is None:
        return None
    # Some Quark-quantized Qwen3.5 MXFP4 checkpoints ship the MTP module
    # entirely in bf16, listing every `mtp.*` layer under the quantization
    # exclude list. Skip quantization for those so linear/MoE weight loaders
    # allocate bf16 shapes (see sgl-project/sglang#23146).
    #
    # Others are mixed: the routed experts stay MXFP4 while attention, the
    # shared expert and fc are excluded. Skipping there would make the MoE
    # loader allocate bf16 experts that the MXFP4 checkpoint shards no longer
    # fit. The routed experts are the bulk of the draft, so use them as the
    # signal and skip only when they are excluded too; the per-layer
    # exclusions keep the remaining bf16 modules bf16 on their own.
    if quant_config and quant_config.get_name() == "quark":
        mtp_excludes = [
            layer
            for layer in getattr(quant_config, "exclude_layers", [])
            if isinstance(layer, str) and layer.startswith("mtp.")
        ]
        if mtp_excludes and any("mlp.experts" in layer for layer in mtp_excludes):
            return None
    if quant_config and quant_config.get_name().replace("_", "-") == "compressed-tensors":
        # PATCH (local): Compressed-tensors checkpoints (AWQ/Marlin W4A16, e.g.
        # ukisai/Swift-1.5-Qwen3.8-27B) ship the embedded MTP head in raw BF16
        # (`model-mtp-bf16.safetensors`) with NO `mtp.*` entries in the quant
        # ignore list. Building the draft as W4A16 would expect packed
        # int32 weights + scales that do not exist on disk; what loads is the
        # raw BF16 tensor, producing NaN through .weight-shaped matmuls
        # (observed: draft logits all-NaN -> argmax 0 -> acceptance 1.00
        # under NEXTN/EAGLE). If no mtp.* group is listed as quantized, build
        # the draft in bf16.
        ignore = getattr(quant_config, "ignore", None) or []
        groups = getattr(quant_config, "config_groups", {}) or {}
        mtp_quantized = any(
            isinstance(t, str) and t.startswith("mtp.")
            for g in groups.values()
            for t in getattr(g, "targets", []) or []
        )
        if not mtp_quantized:
            import logging
            logging.getLogger(__name__).warning(
                "MTP draft: compressed-tensors checkpoint has no mtp.* "
                "quantization targets -- building the draft head in bf16 "
                "(embedded MTP ships unquantized)."
            )
            return None
    return quant_config


class _Fp8RowEmbedding(torch.nn.Module):
    """PATCH (local): PP forces a private 2.4 GB bf16 embedding copy into the
    draft on the last stage. Draft-token quality tolerates row-scaled fp8
    (lookup -> bf16 -> pre_fc_norm_embedding -> fc), and this returns ~1.2 GB
    to the KV pool (pool tokens are the whole game at 262k ctx on 40 GB).
    Verification in prod: accept len 3.0-3.9 unchanged, decode 81-84 tok/s."""

    def __init__(self, weight_bf16: torch.Tensor):
        super().__init__()
        # Stay in bf16 and chunk: a fp32 view of 248320x5120 is 4.7 GB and
        # OOMs the last stage at load time.
        n = weight_bf16.shape[0]
        fp8 = torch.empty_like(weight_bf16, dtype=torch.float8_e4m3fn)
        scale = torch.empty((n, 1), dtype=weight_bf16.dtype,
                            device=weight_bf16.device)
        step = 32768
        for i in range(0, n, step):
            blk = weight_bf16[i : i + step].float()
            s = (blk.abs().amax(dim=1, keepdim=True) / 448.0).clamp_min_(1e-12)
            fp8[i : i + step] = (blk / s).to(torch.float8_e4m3fn)
            scale[i : i + step] = s.to(weight_bf16.dtype)
            del blk, s
        self.weight = torch.nn.Parameter(fp8, requires_grad=False)
        self.scale = torch.nn.Parameter(scale, requires_grad=False)
        self.out_dtype = weight_bf16.dtype

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        idx = input_ids.long()
        return (self.weight[idx].to(self.out_dtype) * self.scale[idx]).to(
            self.out_dtype
        )


def _maybe_fp8_draft_embed(module):
    """Swap model.embed_tokens for the fp8 row wrapper; idempotent."""
    mod = module.model.embed_tokens
    if isinstance(mod, _Fp8RowEmbedding):
        return
    w = getattr(mod, "weight", None)
    if w is None or w.dtype == torch.float8_e4m3fn:
        return
    import logging
    logging.getLogger(__name__).warning(
        "MTP draft: storing draft embed_tokens as fp8 rows (%.2f GB -> %.2f GB)",
        w.numel() * 2 / 1e9, w.numel() / 1e9,
    )
    module.model.embed_tokens = _Fp8RowEmbedding(w.detach())
    del mod
    torch.cuda.empty_cache() if torch.cuda.is_available() else None


class Qwen3_5ForCausalLMMTP(nn.Module):
    # The loader reads this off the model class and hands it to the quant
    # config, which needs it to expand fused module names (qkv_proj ->
    # q/k/v_proj) before matching them against an exclude list. Without it an
    # excluded attention projection is not recognised as excluded.
    packed_modules_mapping = Qwen3_5ForCausalLM.packed_modules_mapping

    @staticmethod
    def shared_experts_fusion_disable_reason(hf_config, quant_config):
        return Qwen3_5ForCausalLM.shared_experts_fusion_disable_reason(
            getattr(hf_config, "text_config", hf_config),
            _mtp_quant_config(quant_config),
        )

    def __init__(
        self,
        config: PretrainedConfig,
        quant_config=None,
        prefix: str = "",
    ) -> None:
        nn.Module.__init__(self)

        self.is_multimodal = hasattr(config, "text_config")
        if self.is_multimodal:
            config = config.text_config

        # Deep-copy so MTP mutations below don't leak into the target's config.
        config = copy.deepcopy(config)

        quant_config = _mtp_quant_config(quant_config)

        self.config = config
        self.quant_config = quant_config
        self.pp_group = get_parallel().pp_group

        self.fc = nn.Linear(2 * config.hidden_size, config.hidden_size, bias=False)
        RMSNorm_cls = GemmaRMSNorm
        self.pre_fc_norm_embedding = RMSNorm_cls(
            config.hidden_size, config.rms_norm_eps
        )
        self.pre_fc_norm_hidden = RMSNorm_cls(config.hidden_size, config.rms_norm_eps)
        mtp_config = copy.deepcopy(config)
        mtp_config.num_hidden_layers = 1
        mtp_config.full_attention_interval = 1
        self.model = Qwen3_5ForCausalLM(
            mtp_config,
            quant_config,
            prefix=add_prefix("mtp", prefix),
            is_nextn=True,
        )

        if get_parallel().pp_group.is_last_rank:
            if config.tie_word_embeddings:
                self.lm_head = self.model.embed_tokens
            else:
                self.lm_head = ParallelLMHead(
                    config.vocab_size,
                    config.hidden_size,
                    quant_config=quant_config,
                    prefix=add_prefix("lm_head", prefix),
                )

        self.logits_processor = LogitsProcessor(config)

    @classmethod
    def get_model_config_for_expert_location(cls, config):
        text_config = getattr(config, "text_config", config)
        return ModelConfigForExpertLocation(
            num_layers=text_config.num_hidden_layers,
            num_logical_experts=text_config.num_experts,
            num_groups=None,
        )

    def get_embed_and_head(self):
        return self.model.embed_tokens.weight, self.lm_head.weight

    def set_embed_and_head(self, embed, head):
        # A last-stage draft can share only the target lm_head under PP; retain its
        # own embedding for the first-stage half it cannot receive.
        if embed is not None:
            del self.model.embed_tokens.weight
            self.model.embed_tokens.weight = embed
        if head is not None and not self.config.tie_word_embeddings:
            del self.lm_head.weight
            self.lm_head.weight = head
        # PATCH (local): downcast the draft's private embedding copy to fp8
        # (see _Fp8RowEmbedding). Runs during load, before KV pool sizing.
        import os as _os2
        if embed is not None and _os2.environ.get("SGLANG_DRAFT_FP8_EMBED", "1") == "1":
            _maybe_fp8_draft_embed(self)
        current_platform.empty_cache()
        current_platform.synchronize()

    def set_lm_head_from_target(self, target_lm_head):
        if self.config.tie_word_embeddings:
            return

        self.lm_head = target_lm_head

    @torch.no_grad()
    def forward(
        self,
        input_ids: torch.Tensor,
        positions: torch.Tensor,
        forward_batch: ForwardBatch,
        input_embeds: Optional[torch.Tensor] = None,
        **kwargs,
    ):
        import os as _os, logging as _logging
        _DBG = _os.environ.get("SGLANG_DEBUG_MTP", "0") == "1" and not torch.cuda.is_current_stream_capturing()
        _dbg_state = {}
        if _DBG and not _dbg_state.get("_weights_checked"):
            try:
                if not getattr(self, "_dbg_weights_done", False):
                    self._dbg_weights_done = True
                    _w = {}
                    for name, mod in [("fc", self.fc), ("lm_head", self.lm_head)]:
                        w = getattr(mod, "weight", None)
                        _w[name] = (round(w.norm().item(), 2) if w is not None else None)
                        sc = getattr(mod, "weight_scale", None)
                        if sc is not None:
                            _w[name + "_scale"] = round(sc.norm().item(), 4)
                    inner = getattr(getattr(self.model, "layers", [None])[0] if hasattr(self.model, "layers") and len(self.model.layers) else self.model, "self_attn", None)
                    wq = getattr(getattr(inner, "qkv_proj", None), "weight", None)
                    _w["qkv"] = (round(wq.norm().item(), 2) if wq is not None else None)
                    _logging.getLogger(__name__).warning("MTP_DBG weights %s", _w)
            except Exception as e:
                _logging.getLogger(__name__).warning("MTP_DBG weight-probe err %s", str(e)[:120])
        if _DBG:
            try:
                hs_probe = forward_batch.spec_info.hidden_states
                mrope = getattr(forward_batch, "mrope_positions", None)
                _dbg_state = dict(
                    mode=str(forward_batch.forward_mode),
                    mm=forward_batch.contains_mm_inputs(),
                    mm_embeds=forward_batch.mm_input_embeds is not None,
                    ids_shape=tuple(input_ids.shape),
                    pos_shape=tuple(positions.shape),
                    pos_tail=positions.flatten()[-4:].tolist(),
                    mrope_none=mrope is None,
                    hs_norm=(round(hs_probe.norm().item(), 3) if hs_probe is not None else None),
                    hs_rows=(hs_probe.shape[0] if hs_probe is not None else None),
                    seq_lens=forward_batch.seq_lens.tolist()[:8],
                )
            except Exception as e:  # never let debug kill the forward
                _dbg_state = {"err": str(e)[:120]}
        exit_stack = ExitStack()
        if (
            is_npu()
            and self.quant_config is None
            and get_model().quantization is not None
        ):
            # ascend mtp unquant
            exit_stack.enter_context(envs.SGLANG_DEEPEP_BF16_DISPATCH.override(True))
            exit_stack.enter_context(
                envs.DEEP_NORMAL_MODE_USE_INT8_QUANT.override(False)
            )

        try:
            assert input_embeds is None
            input_embeds = forward_batch.mm_input_embeds

            # PATCH (local): PP + spec decode gives the draft worker no
            # mm_input_embeds while contains_mm_inputs() is still True, so the
            # original assert below aborted startup. Fall back first.
            if input_embeds is None:
                input_embeds = self.model.embed_tokens(input_ids)

            if (
                forward_batch.forward_mode.is_extend()
                and forward_batch.contains_mm_inputs()
                and not forward_batch.forward_mode.is_draft_extend_v2()
                and forward_batch.mm_input_embeds is not None
            ):
                last_indices = (
                    forward_batch.extend_start_loc + forward_batch.extend_seq_lens - 1
                ).long()
                input_embeds[last_indices] = self.model.embed_tokens(
                    input_ids[last_indices]
                )

            if input_embeds is None:
                input_embeds = self.model.embed_tokens(input_ids)

            hidden_states = forward_batch.spec_info.hidden_states

            if not forward_batch.forward_mode.is_idle():
                input_embeds = self.pre_fc_norm_embedding(input_embeds)
                hidden_states = self.pre_fc_norm_hidden(hidden_states)
            # Captured prefill gives padded embeddings but real-height target states;
            # place the real rows in an equal-height slot whose padding stays unread.
            if hidden_states.shape[0] != input_embeds.shape[0]:
                rows = min(hidden_states.shape[0], input_embeds.shape[0])
                slot = hidden_states.new_zeros(
                    (input_embeds.shape[0], hidden_states.shape[1])
                )
                slot[:rows] = hidden_states[:rows]
                hidden_states = slot

            hidden_states = torch.cat([input_embeds, hidden_states], dim=-1)

            hidden_states = self.fc(hidden_states)
            if _DBG:
                _dbg_state["fc_out_norm"] = round(hidden_states.float().norm().item(), 3)
                _dbg_state["fc_out_nan"] = bool(hidden_states.isnan().any().item())
                if not getattr(self, "_dbg_hooks", None):
                    try:
                        log = _logging.getLogger(__name__)
                        lay = getattr(self.model, "layers", [None])[0]
                        log.warning("MTP_DBG inner-inspect model=%s lay=%s", type(self.model).__name__, type(lay).__name__ if lay is not None else None)
                        if lay is not None:
                            try:
                                qm = getattr(lay.qkv_proj, "quant_method", None)
                                log.warning("MTP_DBG qkv quant_method=%s cls=%s", type(qm).__name__ if qm else None, _os.environ.get("SGLANG_DEBUG_MTP") and type(lay.qkv_proj).__name__)
                                for n, prm in list(lay.qkv_proj.named_parameters()) + list(lay.qkv_proj.named_buffers()):
                                    if torch.is_tensor(prm):
                                        t = prm.detach()
                                        gt = t.float() if t.dtype not in (torch.int32, torch.int64, torch.uint8) else t
                                        log.warning("MTP_DBG qkv_param %s %s %s norm=%s nan=%s",
                                                    n, tuple(t.shape), str(t.dtype),
                                                    round(gt.float().abs().sum().item(), 1) if gt.dtype.is_floating_point else "int",
                                                    bool(t.isnan().any().item()) if t.dtype.is_floating_point else "-")
                            except Exception as e:
                                log.warning("MTP_DBG qkv-inspect err %s", e)
                            def _mk(nm):
                                def hook(mod, inp, out):
                                    if _os.environ.get("SGLANG_DEBUG_MTP") != "1" or torch.cuda.is_current_stream_capturing():
                                        return
                                    t = out[0] if isinstance(out, tuple) else out
                                    if torch.is_tensor(t):
                                        log.warning("MTP_SUB %s norm=%s nan=%s", nm,
                                                    round(t.float().norm().item(), 3),
                                                    bool(t.isnan().any().item()))
                                return hook
                            log.warning("MTP_DBG registering submodule hooks, layer=%s", type(lay).__name__)
                            self._dbg_hooks = [
                                lay.input_layernorm.register_forward_hook(_mk("in_norm")),
                                lay.qkv_proj.register_forward_hook(_mk("qkv")),
                                lay.o_proj.register_forward_hook(_mk("o_proj")),
                                lay.post_attention_layernorm.register_forward_hook(_mk("post_norm")),
                                lay.mlp.register_forward_hook(_mk("mlp")),
                            ]
                    except Exception as e:
                        _logging.getLogger(__name__).warning("MTP_DBG hook err %s", e)

            with get_global_expert_distribution_recorder().disable_this_region():
                hidden_states = self.model(
                    input_ids,
                    positions,
                    forward_batch,
                    hidden_states,
                )
            if _DBG:
                _dbg_state["inner_out_norm"] = round(hidden_states.float().norm().item(), 3)
                _dbg_state["inner_out_nan"] = bool(hidden_states.isnan().any().item())
                inner = self.model.model if hasattr(self.model, "model") else None
                if inner is not None and getattr(inner, "norm", None) is not None:
                    _dbg_state["inner_norm_w"] = round(inner.norm.weight.float().norm().item(), 3)
                # dump the inner layer's attention out if reachable
                try:
                    lay = inner.layers[0]
                    for nm in ("self_attn", "mlp"):
                        sub = getattr(lay, nm, None)
                        if sub is None:
                            _dbg_state[nm + "_w"] = None
                            continue
                        w = getattr(getattr(sub, "qkv_proj", sub), "weight", None)
                        if w is None and hasattr(sub, "q_proj"):
                            w = sub.q_proj.weight
                        _dbg_state[nm + "_w"] = (
                            (round(w.float().norm().item(), 2), str(getattr(w, "dtype", None)), tuple(w.shape)) if w is not None else None)
                except Exception:
                    pass
        finally:
            exit_stack.close()

        out = self.logits_processor(
            input_ids, hidden_states, self.lm_head, forward_batch
        )
        if _DBG and _dbg_state:
            try:
                ntl = getattr(out, "next_token_logits", None)
                if ntl is not None:
                    _dbg_state["draft_top1"] = ntl.argmax(-1).tolist()[:8]
                    _f = ntl.float()
                    _dbg_state["logits_nan"] = bool(_f.isnan().any().item())
                    _dbg_state["logits_max_std"] = [round(_f.max().item(), 3), round(_f.std().item(), 3)]
                _dbg_state["emb_src"] = ("mm" if forward_batch.mm_input_embeds is not None else "token")
                _logging.getLogger(__name__).warning("MTP_DBG %s", _dbg_state)
            except Exception:
                pass
        return out

    def load_weights(
        self, weights: Iterable[Tuple[str, torch.Tensor]], is_mtp: bool = False
    ):
        weights = QWEN3_5_KV_SCALE_MAPPER.apply(weights)
        stacked_params_mapping = [
            # (param_name, shard_name, shard_id)
            ("qkv_proj", "q_proj", "q"),
            ("qkv_proj", "k_proj", "k"),
            ("qkv_proj", "v_proj", "v"),
            ("gate_up_proj", "gate_proj", 0),
            ("gate_up_proj", "up_proj", 1),
        ]

        # Params for MoE experts (non-fused/fused)
        num_experts = getattr(self.config, "num_experts", None)
        # A fused shared expert lives in routed slot `num_experts`.
        num_fused_shared_experts = 0
        if _use_aiter:
            for module in self.modules():
                fused = getattr(module, "num_fused_shared_experts", 0)
                if fused:
                    num_fused_shared_experts = fused
                    break
        if num_experts is not None:
            expert_params_mapping = FusedMoE.make_expert_params_mapping(
                ckpt_gate_proj_name="gate_proj",
                ckpt_down_proj_name="down_proj",
                ckpt_up_proj_name="up_proj",
                num_experts=num_experts + num_fused_shared_experts,
            )
        else:
            expert_params_mapping = []

        # Skip loading extra parameters for GPTQ/modelopt models.
        ignore_suffixes = (
            ".bias",
            "_bias",
            ".k_scale",
            "_k_scale",
            ".v_scale",
            "_v_scale",
            ".weight_scale",
            "_weight_scale",
            ".input_scale",
            "_input_scale",
        )

        # Fused checkpoint tensors: experts.gate_up_proj / experts.down_proj.
        # The checkpoint interleaves these with separate shared-expert tensors,
        # so picking one mapping must not affect the next weight.
        fused_expert_params_mapping = [
            ("experts.w13_weight", "experts.gate_up_proj", 0, "w1"),
            ("experts.w2_weight", "experts.down_proj", 0, "w2"),
        ]

        def load_fused_expert_weights(
            name: str,
            params_dict: dict,
            loaded_weight: torch.Tensor,
            shard_id: str,
            num_experts: int,
        ):
            param = params_dict[name]
            weight_loader = param.weight_loader
            # Let EP MoE layer handle expert_ids that do not belong to local moe rank
            for expert_id in range(num_experts):
                curr_expert_weight = loaded_weight[expert_id]
                weight_loader(
                    param,
                    curr_expert_weight,
                    name,
                    shard_id,
                    expert_id,
                )
            return True

        params_dict = dict(self.named_parameters())
        loaded_params: set[str] = set()

        for name, loaded_weight in weights:
            # The last-stage MTP draft cannot share the target embedding on PP0.
            # Load the checkpoint embedding into its retained local copy instead
            # of leaving the torch.empty() allocation uninitialized.
            if name in (
                "model.embed_tokens.weight",
                "model.language_model.embed_tokens.weight",
            ):
                param_name = "model.embed_tokens.weight"
                if param_name in params_dict:
                    param = params_dict[param_name]
                    weight_loader = getattr(
                        param, "weight_loader", default_weight_loader
                    )
                    weight_loader(param, loaded_weight)
                    loaded_params.add(param_name)
                continue

            if "rotary_emb.inv_freq" in name:
                continue

            # Only process MTP branch weights
            if "mtp" not in name:
                continue

            if name.startswith("mtp."):
                # Remove the mtp. prefix for processing
                name = name.replace("mtp.", "model.")

                name = name.replace("model.fc", "fc")
                name = name.replace("model.pre_fc", "pre_fc")

            if ".self_attn." in name:
                name = name.replace(".self_attn", "")

            if (
                _use_aiter
                and num_fused_shared_experts > 0
                and "mlp.shared_expert." in name
            ):
                # Map mlp.shared_expert.xx_proj to mlp.experts.{num_experts}.xx_proj
                name = name.replace(
                    "mlp.shared_expert.",
                    f"mlp.experts.{num_experts}.",
                )

            is_fused_expert = (
                "experts.gate_up_proj" in name or "experts.down_proj" in name
            )
            current_expert_params_mapping = (
                fused_expert_params_mapping
                if is_fused_expert
                else expert_params_mapping
            )

            # 1) Process stacked parameters (q_proj/k_proj/v_proj & gate_proj/up_proj)
            for param_name, weight_name, shard_id in stacked_params_mapping:
                # Skip non-matching weights
                if weight_name not in name:
                    continue

                # Skip MoE experts.* here, handled separately below
                if "mlp.experts" in name:
                    continue

                name_mapped = name.replace(weight_name, param_name)

                # Skip loading extra parameters for GPTQ/modelopt models.
                if (
                    name_mapped.endswith(ignore_suffixes)
                    and name_mapped not in params_dict
                ):
                    continue

                if name_mapped not in params_dict:
                    continue

                param = params_dict[name_mapped]
                weight_loader = getattr(param, "weight_loader", default_weight_loader)
                weight_loader(param, loaded_weight, shard_id)
                name = name_mapped
                break
            else:
                # 2) Process MoE expert weights (including fused experts)
                is_expert_weight = False

                for mapping in current_expert_params_mapping:
                    param_name, weight_name, expert_id, shard_id = mapping
                    if weight_name not in name:
                        continue

                    is_expert_weight = True
                    name_mapped = name.replace(weight_name, param_name)

                    # Fused experts: single checkpoint weight contains multiple experts
                    if is_fused_expert and num_experts is not None:
                        if "experts.gate_up_proj" in name:
                            # gate_up_proj fused: split into w1 / w3
                            loaded_w1, loaded_w3 = loaded_weight.chunk(2, dim=-2)
                            load_fused_expert_weights(
                                name_mapped,
                                params_dict,
                                loaded_w1,
                                "w1",
                                num_experts,
                            )
                            load_fused_expert_weights(
                                name_mapped,
                                params_dict,
                                loaded_w3,
                                "w3",
                                num_experts,
                            )
                        else:
                            # down_proj fused: distribute entire weight
                            load_fused_expert_weights(
                                name_mapped,
                                params_dict,
                                loaded_weight,
                                shard_id,
                                num_experts,
                            )
                    else:
                        # Non-fused expert, load by expert_id/shard
                        if (
                            name_mapped.endswith(ignore_suffixes)
                            and name_mapped not in params_dict
                        ):
                            continue
                        if name_mapped not in params_dict:
                            break
                        param = params_dict[name_mapped]
                        weight_loader = param.weight_loader
                        weight_loader(
                            param,
                            loaded_weight,
                            name_mapped,
                            shard_id=shard_id,
                            expert_id=expert_id,
                        )
                    name = name_mapped
                    break
                else:
                    # Skip expert weight if not handled by current rank
                    if is_expert_weight:
                        continue

                    # 3) Regular non-stacked / non-expert parameters, use default loader
                    if name.endswith(ignore_suffixes) and name not in params_dict:
                        continue

                    if name in params_dict:
                        param = params_dict[name]
                        weight_loader = getattr(
                            param, "weight_loader", default_weight_loader
                        )
                        weight_loader(param, loaded_weight)
                    else:
                        logger.warning_once(
                            f"Parameter {name} not found in params_dict, skip loading"
                        )

            loaded_params.add(name)
        return loaded_params


EntryClass = [Qwen3_5ForCausalLMMTP]
