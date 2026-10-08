"""Qwen shared backbone with non-generative BIO/type heads; imports are lazy."""
from __future__ import annotations

import json
from pathlib import Path
import threading

from .types import ENTITY_TYPES, LABELS, Span


def load_backbone(base_model: str, device: str = "cuda", revision: str | None = None):
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration
    tokenizer = AutoTokenizer.from_pretrained(base_model, use_fast=True, revision=revision)
    full = Qwen3_5ForConditionalGeneration.from_pretrained(
        base_model, dtype=torch.bfloat16 if device.startswith("cuda") else torch.float32,
        attn_implementation="sdpa", revision=revision)
    backbone = full.model.language_model.to(device).eval()
    backbone.ganglion_revision = full.config._commit_hash
    del full
    return backbone, tokenizer


def make_heads(hidden_size: int, width: int = 128, *, dropout: float = 0.0,
               layers: int = 1, boundary_classes: int = 0):
    import torch
    from torch import nn

    if width < 4 or width % 4 or not 0 <= dropout < 1 or layers < 1 or boundary_classes < 0:
        raise ValueError("invalid typed-head architecture")

    class TypedHeads(nn.Module):
        def __init__(self):
            super().__init__()
            self.projection = nn.Linear(hidden_size, width)
            self.context = nn.TransformerEncoder(
                nn.TransformerEncoderLayer(width, 4, width * 2, dropout=dropout,
                                           batch_first=True, activation="gelu"), layers,
                enable_nested_tensor=False)
            self.bio = nn.Linear(width, len(LABELS))
            self.types = nn.Linear(width, len(ENTITY_TYPES) + 1)
            if boundary_classes:
                self.start_offsets = nn.Linear(width, boundary_classes)
                self.end_offsets = nn.Linear(width, boundary_classes)

        def forward(self, hidden, mask):
            values = self.context(self.projection(hidden.float()), src_key_padding_mask=~mask.bool())
            outputs = self.bio(values), self.types(values)
            if boundary_classes:
                outputs += self.start_offsets(values), self.end_offsets(values)
            return outputs

    return TypedHeads()


def decode_spans(offsets, outputs, *, temperature=1.0, decode_mode="argmax", joint_type_weight=0.0):
    """Decode token labels and learned character trims without text/rule heuristics.

    v2 checkpoints retain argmax and untrimmed token coordinates. v3 uses a
    legal BIO path and predicts the actual character boundaries inside tokens.
    Probability fields remain the type head's uncalibrated marginals.
    """
    import math
    import numpy as np
    import torch
    if not math.isfinite(temperature) or temperature <= 0 or not math.isfinite(joint_type_weight) or joint_type_weight < 0:
        raise ValueError("invalid probability/decoder settings")
    bio, type_logits = outputs[:2]
    if len(outputs) not in (2, 4) or bio.shape != (len(offsets), len(LABELS)) or type_logits.shape != (len(offsets), len(ENTITY_TYPES) + 1):
        raise ValueError("native logit shapes do not match the typed contract")
    if any(not bool(torch.isfinite(value).all()) for value in outputs):
        raise ValueError("non-finite native logits")
    probabilities = (type_logits.float() / temperature).softmax(-1).detach().cpu()
    scores = bio.float().log_softmax(-1).detach().cpu().numpy().copy()
    if joint_type_weight:
        types = probabilities.clamp_min(1e-12).log().numpy()
        indices = [len(ENTITY_TYPES)] + [ENTITY_TYPES.index(label[2:]) for label in LABELS[1:]]
        scores += joint_type_weight * types[:, indices]
    for i, (start, end) in enumerate(offsets):
        if end <= start:
            scores[i, 1:] = -np.inf
    if not len(offsets):
        return []
    if decode_mode == "argmax":
        labels = scores.argmax(-1).tolist()
    elif decode_mode == "bio-viterbi":
        transitions = np.zeros((len(LABELS), len(LABELS)), dtype=np.float32)
        for following, label in enumerate(LABELS):
            if label.startswith("I-"):
                for previous, predecessor in enumerate(LABELS):
                    if predecessor not in ("B-" + label[2:], label):
                        transitions[previous, following] = -np.inf
        values = scores[0].copy()
        values[[i for i, label in enumerate(LABELS) if label.startswith("I-")]] = -np.inf
        parents = []
        for step in scores[1:]:
            candidates = values[:, None] + transitions
            parents.append(candidates.argmax(0))
            values = candidates.max(0) + step
        last = int(values.argmax())
        labels = [last]
        for row in reversed(parents):
            last = int(row[last])
            labels.append(last)
        labels.reverse()
    else:
        raise ValueError("unknown BIO decoder")
    starts, ends = [value.detach().float().cpu() for value in outputs[2:]] if len(outputs) == 4 else (None, None)
    spans, beginning, kind = [], None, None

    def finish(end):
        nonlocal beginning, kind
        if beginning is not None and kind:
            left, right = offsets[beginning][0], offsets[end - 1][1]
            if starts is not None:
                first_width = offsets[beginning][1] - left
                last_width = right - offsets[end - 1][0]
                left_trim = int(starts[beginning, :min(first_width, starts.shape[-1])].argmax())
                right_trim = int(ends[end - 1, :min(last_width, ends.shape[-1])].argmax())
                if beginning == end - 1 and left_trim + right_trim >= right - left:
                    candidates = [(float(starts[beginning, a] + ends[beginning, b]), a, b)
                                  for a in range(min(first_width, starts.shape[-1]))
                                  for b in range(min(last_width, ends.shape[-1])) if a + b < right - left]
                    _, left_trim, right_trim = max(candidates)
                left, right = left + left_trim, right - right_trim
            if right > left:
                distribution = dict(zip((*ENTITY_TYPES, "NOT_PII"), probabilities[beginning:end].mean(0).tolist()))
                spans.append(Span(left, right, kind, distribution[kind], "qwen_native", distribution))
        beginning, kind = None, None

    for index, label_id in enumerate(labels):
        label = LABELS[label_id]
        if label == "O" or offsets[index][1] <= offsets[index][0]:
            finish(index)
            continue
        prefix, following = label.split("-", 1)
        if prefix == "B" or kind != following:
            finish(index)
            beginning, kind = index, following
    finish(len(labels))
    return spans


class NativeDetector:
    backend = "qwen_native"
    score_kind = "uncalibrated_probability"

    def __init__(self, checkpoint: Path | str, device: str | None = None):
        import torch
        from safetensors.torch import load_file
        self.path = Path(checkpoint)
        self.metadata = json.loads((self.path / "model.json").read_text())
        if self.metadata.get("labels") != list(LABELS):
            raise ValueError("native checkpoint label contract mismatch")
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.backbone, self.tokenizer = load_backbone(self.metadata["base_model"], self.device, self.metadata.get("base_model_revision"))
        self.heads = make_heads(self.backbone.config.hidden_size, self.metadata.get("head_width", 128),
                                dropout=self.metadata.get("head_dropout", 0.0),
                                layers=self.metadata.get("head_layers", 1),
                                boundary_classes=self.metadata.get("boundary_classes", 0)).to(self.device)
        self.heads.load_state_dict(load_file(str(self.path / "heads.safetensors"), device=self.device))
        if (self.path / "adapter").is_dir():
            from peft import PeftModel
            self.backbone = PeftModel.from_pretrained(self.backbone, self.path / "adapter").eval()
        self.heads.eval()
        self.temperature = float(self.metadata.get("temperature", 1.0))
        import math
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("invalid calibration temperature")
        self.lock = threading.Lock()

    def token_count(self, text: str) -> int:
        with self.lock:
            return len(self.tokenizer.encode(text, add_special_tokens=False))

    def detect(self, text: str) -> list[Span]:
        import torch
        with self.lock:
            encoded = self.tokenizer(text, return_tensors="pt", return_offsets_mapping=True, add_special_tokens=False)
        offsets = encoded.pop("offset_mapping")[0].tolist()
        if not offsets:
            return []
        if len(offsets) > self.metadata.get("max_tokens", 1024):
            raise ValueError("native model token budget exceeded; input was not truncated")
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        with self.lock, torch.inference_mode():
            hidden = self.backbone(**encoded, use_cache=False).last_hidden_state
            outputs = tuple(value[0].cpu() for value in self.heads(hidden, encoded["attention_mask"]))
        return decode_spans(offsets, outputs, temperature=self.temperature,
                            decode_mode=self.metadata.get("decode_mode", "argmax"),
                            joint_type_weight=self.metadata.get("joint_type_weight", 0.0))
