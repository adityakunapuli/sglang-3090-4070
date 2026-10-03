"""Model-agnostic draft embedding for speculative decoding under pipeline parallelism.

The draft runs only on the last stage while the target embedding lives only on the
first stage, so the draft cannot share ``target.get_embed_and_head()``. This module
reads the target embed/head PP-safely and loads the checkpoint's input embedding
into the draft's own embedding parameter, touching only the shard that holds it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable, List, Optional, Tuple

import torch
from torch import nn

from sglang.srt.configs.load_config import LoadConfig, LoadFormat
from sglang.srt.layers.utils.common import PPMissingLayer
from sglang.srt.model_loader.weight_utils import default_weight_loader

logger = logging.getLogger(__name__)

SAFETENSORS_INDEX_NAME = "model.safetensors.index.json"

# Module attribute names under which model families hang the input embedding.
_EMBED_ATTR_NAMES: Tuple[str, ...] = (
    "embed_tokens",
    "word_embeddings",
    "tok_embeddings",
    "embed",
)
# Checkpoint spellings of the input embedding, most common first.
EMBED_KEY_CANDIDATES: Tuple[str, ...] = (
    "model.embed_tokens.weight",
    "model.language_model.embed_tokens.weight",
    "language_model.model.embed_tokens.weight",
    "model.word_embeddings.weight",
    "tok_embeddings.weight",
    "embed_tokens.weight",
    "embed.weight",
)
_EMBED_KEY_SUFFIXES: Tuple[str, ...] = tuple(f"{n}.weight" for n in _EMBED_ATTR_NAMES)
# MTP / NextN layers carry their own embedding under ``layers.<n>.``; never pick it.
_LAYER_KEY_RE = re.compile(r"(^|\.)layers\.\d+\.")
_DRAFT_SUBMODULE_MARKERS: Tuple[str, ...] = ("mtp", "nextn", "eagle", "draft")
# Streaming and cache-transport formats have no weight files to re-open.
_REOPENABLE_LOAD_FORMATS = (
    LoadFormat.AUTO,
    LoadFormat.SAFETENSORS,
    LoadFormat.FASTSAFETENSORS,
    LoadFormat.MISTRAL,
    LoadFormat.PT,
    LoadFormat.NPCACHE,
)


def _target_input_embedding_is_missing(target_model: nn.Module) -> bool:
    """True when the target's input embedding on this stage is a ``PPMissingLayer``."""
    for name, module in target_model.named_modules():
        if name.rsplit(".", 1)[-1] in _EMBED_ATTR_NAMES and isinstance(
            module, PPMissingLayer
        ):
            return True
    return False


def _weight_or_none(module: Optional[nn.Module]) -> Optional[torch.Tensor]:
    if module is None or isinstance(module, PPMissingLayer):
        return None
    from sglang.srt.lora.layers import unwrap_lora_layer

    return unwrap_lora_layer(module).weight


def resolve_target_embed_and_head(
    target_model: nn.Module,
) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    """PP-safe getter: a PPMissingLayer embedding maps to ``embed=None``; any
    other AttributeError propagates."""
    try:
        return target_model.get_embed_and_head()
    except AttributeError:
        if not _target_input_embedding_is_missing(target_model):
            raise
        return None, _weight_or_none(target_model.lm_head)


def find_draft_embedding_param(
    draft_model: nn.Module,
) -> Optional[Tuple[str, nn.Parameter]]:
    """The draft's own input embedding: its single ``VocabParallelEmbedding``."""
    from sglang.srt.layers.vocab_parallel_embedding import (
        ParallelLMHead,
        VocabParallelEmbedding,
    )

    found = [
        (f"{name}.weight", module.weight)
        for name, module in draft_model.named_modules()
        if isinstance(module, VocabParallelEmbedding)
        and not isinstance(module, ParallelLMHead)
    ]
    return found[0] if len(found) == 1 else None


def _is_input_embedding_key(key: str) -> bool:
    if not key.endswith(_EMBED_KEY_SUFFIXES) or _LAYER_KEY_RE.search(key):
        return False
    lowered = key.lower()
    return not any(marker in lowered for marker in _DRAFT_SUBMODULE_MARKERS)


def _pick_embedding_key(keys: Iterable[str]) -> Optional[str]:
    keys = set(keys)
    for candidate in EMBED_KEY_CANDIDATES:
        if candidate in keys:
            return candidate
    fallback = sorted((k for k in keys if _is_input_embedding_key(k)), key=len)
    return fallback[0] if fallback else None


def _safetensors_keys(path: str) -> List[str]:
    import safetensors

    with safetensors.safe_open(path, framework="pt", device="cpu") as f:
        return list(f.keys())


def _read_safetensors_tensor(path: str, key: str) -> torch.Tensor:
    import safetensors

    with safetensors.safe_open(path, framework="pt", device="cpu") as f:
        return f.get_tensor(key)


def prepare_checkpoint_files(
    model_path: str, *, revision: Optional[str], load_config: LoadConfig
) -> Tuple[str, List[str], bool]:
    """``(folder, weight_files, use_safetensors)`` via the standard model loader,
    so ModelScope resolution, ``--download-dir`` and ``load_format`` are honored."""
    from sglang.srt.model_loader.loader import DefaultModelLoader

    if load_config.load_format not in _REOPENABLE_LOAD_FORMATS:
        raise ValueError(
            "Pipeline-parallel speculative decoding needs to re-open the target "
            f"checkpoint for the draft embedding, which load format "
            f"{load_config.load_format!r} does not allow; use a disk-backed format."
        )
    return DefaultModelLoader(load_config)._prepare_weights(
        model_path, revision, fall_back_to_pt=True
    )


def load_embedding_tensor(
    folder: str, weight_files: List[str], *, use_safetensors: bool
) -> Tuple[str, torch.Tensor]:
    """``(key, tensor)`` of the checkpoint's input embedding; with an index only
    the owning shard is read, otherwise the key is picked over all shard headers."""
    if use_safetensors:
        index_file = os.path.join(folder, SAFETENSORS_INDEX_NAME)
        if os.path.exists(index_file):
            with open(index_file) as f:
                weight_map = json.load(f).get("weight_map", {}) or {}
            key = _pick_embedding_key(weight_map.keys())
            if key is not None:
                shard = os.path.join(folder, weight_map[key])
                return key, _read_safetensors_tensor(shard, key)
        keys_by_file = {path: _safetensors_keys(path) for path in weight_files}
        key = _pick_embedding_key(k for keys in keys_by_file.values() for k in keys)
        if key is not None:
            shard = next(p for p, keys in keys_by_file.items() if key in keys)
            return key, _read_safetensors_tensor(shard, key)
    else:
        from sglang.srt.model_loader.weight_utils import pt_weights_iterator

        for name, tensor in pt_weights_iterator(weight_files):
            if name in EMBED_KEY_CANDIDATES or _is_input_embedding_key(name):
                return name, tensor
    raise ValueError(
        f"No input embedding found in checkpoint under {folder}; looked for "
        f"{EMBED_KEY_CANDIDATES} or '*.<{'|'.join(_EMBED_ATTR_NAMES)}>.weight'."
    )


def load_draft_embedding_from_checkpoint(
    draft_model: nn.Module,
    model_path: str,
    *,
    revision: Optional[str],
    load_config: LoadConfig,
) -> nn.Parameter:
    """Load the checkpoint's input embedding into the draft's own parameter and
    return it; the parameter's ``weight_loader`` applies the TP shard."""
    found = find_draft_embedding_param(draft_model)
    if found is None:
        # PATCH (local): check for our fp8 skeleton BEFORE failing on the
        # missing VocabParallelEmbedding (qwen3_5_mtp_pp_spec.py replaced the
        # draft's embed module with _Fp8RowEmbedding pre-sizing).
        emb_mod = getattr(getattr(draft_model, "model", None), "embed_tokens", None)
        if emb_mod is not None and type(emb_mod).__name__ == "_Fp8RowEmbedding":
            folder, weight_files, use_safetensors = prepare_checkpoint_files(
                model_path, revision=revision, load_config=load_config
            )
            key, loaded = load_embedding_tensor(
                folder, weight_files, use_safetensors=use_safetensors
            )
            # Fill from CPU in row chunks: quantize per-row to fp8 without a
            # GPU bf16 transient (2.5 GB bf16 + 1.3 GB fp8 > free VRAM).
            logger.info(
                "Filling deferred fp8 draft embedding from key %s %s (chunked CPU quantize).",
                key, tuple(loaded.shape),
            )
            w_fp8 = emb_mod.weight.data
            scale = emb_mod.scale.data
            step = 8192
            import torch as _torch
            with _torch.no_grad():
                for i in range(0, loaded.shape[0], step):
                    blk = loaded[i : i + step].to(w_fp8.device, dtype=_torch.bfloat16).float()
                    s = (blk.abs().amax(dim=1, keepdim=True) / 448.0).clamp_min_(1e-12)
                    w_fp8[i : i + step] = (blk / s).to(_torch.float8_e4m3fn)
                    scale[i : i + step] = s.to(scale.dtype)
                    del blk, s
            return emb_mod.weight
        raise ValueError(
            f"Draft model {draft_model.__class__.__name__} has no single "
            "VocabParallelEmbedding, and the target cannot share its embedding from "
            "this pipeline stage (https://github.com/sgl-project/sglang/issues/39634)."
        )
    param_name, param = found
    if load_config.load_format == LoadFormat.DUMMY:
        return param

    folder, weight_files, use_safetensors = prepare_checkpoint_files(
        model_path, revision=revision, load_config=load_config
    )
    key, loaded = load_embedding_tensor(
        folder, weight_files, use_safetensors=use_safetensors
    )
    if getattr(param, "is_meta", False):
        # PATCH (local): qwen3_5_mtp_pp_spec deferred the draft's embed table to
        # a meta placeholder so KV pool sizing doesn't see its 2.4 GB. This call
        # happens after sizing; materialize the real parameter here and splice
        # it back into the owning module.
        parts = param_name.split(".")
        obj = draft_model
        for pth in parts[:-1]:
            obj = getattr(obj, pth)
        real_param = nn.Parameter(
            loaded.to(device=torch.cuda.current_device()), requires_grad=False
        )
        setattr(obj, parts[-1], real_param)
        logger.info(
            "Materialized deferred (meta) draft embedding %s from checkpoint key "
            "%s %s for pipeline-parallel speculative decoding.",
            param_name, key, tuple(loaded.shape),
        )
        param = real_param
    else:
        weight_loader = getattr(param, "weight_loader", default_weight_loader)
        weight_loader(param, loaded)
        logger.info(
            "Loaded draft embedding %s from checkpoint key %s %s for pipeline-parallel "
            "speculative decoding.",
            param_name,
            key,
            tuple(loaded.shape),
        )
    return param


def resolve_draft_embed_and_head(
    *,
    target_model: nn.Module,
    draft_model: nn.Module,
    model_path: str,
    revision: Optional[str],
    load_config: LoadConfig,
) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    """Embed/head to bind into a draft; a stage without the target embedding
    loads the draft's own from the checkpoint."""
    embed, head = resolve_target_embed_and_head(target_model)
    if embed is None:
        embed = load_draft_embedding_from_checkpoint(
            draft_model, model_path, revision=revision, load_config=load_config
        )
    return embed, head
