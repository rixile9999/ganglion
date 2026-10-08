#!/usr/bin/env python3
"""Frozen, gold-blind HF PII baseline inference; evaluation is a separate step.

Korean BIOES decoding and normalization follow the MIT-licensed official helper:
https://huggingface.co/FrameByFrame/korean-pii-e5-base/blob/a308c54b4407819624a5661e31e162a269f39818/usage.py
The helper intentionally does not close entities on E/S tokens. We preserve
that published behavior rather than silently improving it with benchmark labels.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import time
import unicodedata
from pathlib import Path


PROFILES = {
    "piiranha": {
        "model": "iiiorg/piiranha-v1-detect-personal-information",
        "revision": "255acde67a2f34cf452eb42e365b24d2957352fc",
        "languages": ["en", "es", "fr", "de", "it", "nl"],
        "license": "cc-by-nc-nd-4.0", "dtype": "float32",
        "map": {"GIVENNAME": "PERSON", "SURNAME": "PERSON",
                "BUILDINGNUM": "ADDRESS", "CITY": "ADDRESS", "STREET": "ADDRESS", "ZIPCODE": "ADDRESS",
                "TELEPHONENUM": "PHONE", "EMAIL": "EMAIL",
                **{key: "IDENTIFIER" for key in ("ACCOUNTNUM", "CREDITCARDNUMBER", "DRIVERLICENSENUM", "IDCARDNUM", "SOCIALNUM", "TAXNUM")}},
    },
    "korean-e5": {
        "model": "FrameByFrame/korean-pii-e5-base",
        "revision": "a308c54b4407819624a5661e31e162a269f39818",
        "languages": ["ko", "en"], "license": "mit", "dtype": "bfloat16",
        "map": {"private_person": "PERSON", "private_address": "ADDRESS",
                "private_phone": "PHONE", "private_email": "EMAIL", "account_number": "IDENTIFIER"},
    },
}
_TRAILING_JOSA = ["이에요", "이라고", "입니다", "이야", "이랑", "한테", "에게", "으로", "이가", "이는",
                  "에서", "이고", "예요", "씨", "님", "이", "가", "은", "는", "을", "를", "야", "아", "에", "의", "랑", "께", "고"]
_DATE_END = re.compile(r".*(?:일|[0-9])", re.S)


def normalize_korean(text, label, start, end):
    """Exact official helper normalization, including its name-suffix heuristic."""
    while start < end and text[start] in " .,\t\n":
        start += 1
    while end > start and text[end - 1] in " .,\t\n":
        end -= 1
    if label == "private_date":
        match = _DATE_END.match(text[start:end])
        if match and match.end() > 0:
            end = start + match.end()
    elif label in ("private_person", "personal_handle", "private_address"):
        for _ in range(2):
            for suffix in _TRAILING_JOSA:
                if text[start:end].endswith(suffix) and end - start - len(suffix) >= 2:
                    end -= len(suffix)
                    break
            else:
                break
    return start, end


def decode_native(offsets, labels, scores, korean=False):
    """Aggregate same native categories; retain all categories, even unmapped PII."""
    spans, active = [], None

    def flush():
        nonlocal active
        if active:
            spans.append({"label": active["label"], "start": active["start"], "end": active["end"],
                          "score": sum(active["scores"]) / len(active["scores"])})
            active = None

    for (start, end), label, score in zip(offsets, labels, scores):
        if start == end or label == "O":
            flush()
            continue
        prefix, category = label.split("-", 1) if "-" in label else ("I", label)
        if active is None or active["label"] != category or prefix in ("B", "S"):
            flush()
            active = {"label": category, "start": start, "end": end, "scores": [float(score)]}
        else:
            active["end"] = end
            active["scores"].append(float(score))
        # Korean deliberately follows the published helper's non-closing E/S.
    flush()
    return spans


def map_spans(spans, label_map):
    return [{"start": span["start"], "end": span["end"], "type": label_map[span["label"]], "score": span["score"]}
            for span in spans if span["label"] in label_map]


def canonicalize(text, spans):
    """Static coarse-class coalescing across only whitespace/punctuation gaps.

    Empty/overlapping boundaries also coalesce. General words/digits in gaps
    never qualify. Scores are the character-weighted mean of component scores.
    """
    result = []
    for original in sorted(spans, key=lambda item: (item["start"], item["end"], item["type"])):
        span = dict(original)
        while span["start"] < span["end"] and (text[span["start"]].isspace() or text[span["start"]] in ".,;:"):
            span["start"] += 1
        while span["end"] > span["start"] and (text[span["end"] - 1].isspace() or text[span["end"] - 1] in ".,;:"):
            span["end"] -= 1
        if span["end"] <= span["start"]:
            continue
        span["_weight"] = span["end"] - span["start"]
        if result and result[-1]["type"] == span["type"]:
            previous = result[-1]
            gap = text[previous["end"]:span["start"]]
            if all(char.isspace() or unicodedata.category(char).startswith("P") for char in gap):
                weight = previous["_weight"] + span["_weight"]
                previous["score"] = (previous["score"] * previous["_weight"] + span["score"] * span["_weight"]) / weight
                previous["_weight"] = weight
                previous["end"] = max(previous["end"], span["end"])
                continue
        result.append(span)
    return [{key: value for key, value in span.items() if key != "_weight"} for span in result]


def infer(args):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    profile = PROFILES[args.model]
    input_bytes = args.input.read_bytes()
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    # Only these two public fields cross the inference boundary. Gold, language
    # labels, templates and other annotations are neither retained nor inspected.
    rows = [{"id": row["id"], "text": row["text"]}
            for row in (json.loads(line) for line in input_bytes.decode().splitlines() if line.strip())]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Input IDs must be unique")
    timer = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(profile["model"], revision=profile["revision"], use_fast=True)
    enc = tokenizer([row["text"] for row in rows], return_offsets_mapping=True, truncation=False)
    lengths = [len(ids) for ids in enc["input_ids"]]
    if max(lengths, default=0) > args.max_tokens:
        raise ValueError(f"Input exceeds frozen token budget: {max(lengths)} > {args.max_tokens}; no silent truncation permitted")
    dtype = getattr(torch, profile["dtype"])
    model = AutoModelForTokenClassification.from_pretrained(profile["model"], revision=profile["revision"],
                                                           torch_dtype=dtype, use_safetensors=True).to(args.device).eval()
    if args.device.startswith("cuda"):
        torch.cuda.synchronize()
    load_seconds = time.perf_counter() - timer
    timer = time.perf_counter()
    predictions = []
    for begin in range(0, len(rows), args.batch_size):
        batch_rows = rows[begin:begin + args.batch_size]
        tokenized = tokenizer([row["text"] for row in batch_rows], padding=True, truncation=False,
                              return_offsets_mapping=True, return_tensors="pt")
        offsets = tokenized.pop("offset_mapping").tolist()
        mask = tokenized["attention_mask"].tolist()
        with torch.inference_mode():
            logits = model(**{key: value.to(args.device) for key, value in tokenized.items()}).logits
            probabilities = logits.float().softmax(dim=-1)
            confidence, predicted = probabilities.max(dim=-1)
            predicted, confidence = predicted.cpu().tolist(), confidence.cpu().tolist()
        for row, offset, valid, ids, scores in zip(batch_rows, offsets, mask, predicted, confidence):
            length = sum(valid)
            labels = [model.config.id2label[index] for index in ids[:length]]
            raw = decode_native(offset[:length], labels, scores[:length], korean=args.model == "korean-e5")
            mapped = map_spans(raw, profile["map"])
            profiles = {"native_mapped": mapped, "canonical": canonicalize(row["text"], mapped)}
            normalized = None
            if args.model == "korean-e5":
                normalized = []
                for span in raw:
                    start, end = normalize_korean(row["text"], span["label"], span["start"], span["end"])
                    if row["text"][start:end].strip():
                        normalized.append(dict(span, start=start, end=end))
                profiles["card_normalized"] = map_spans(normalized, profile["map"])
                profiles["card_normalized_canonical"] = canonicalize(row["text"], profiles["card_normalized"])
            default_profile = "card_normalized_canonical" if normalized is not None else "canonical"
            result = {"id": row["id"], "spans": profiles[default_profile], "raw_model_spans": raw,
                      "profiles": profiles}
            if normalized is not None:
                result["card_normalized_model_spans"] = normalized
            predictions.append(result)
        print(json.dumps({"model": args.model, "documents_complete": len(predictions), "seconds": round(time.perf_counter() - timer, 2)}), flush=True)
    if args.device.startswith("cuda"):
        torch.cuda.synchronize()
    inference_seconds = time.perf_counter() - timer
    if hashlib.sha256(args.input.read_bytes()).hexdigest() != input_hash:
        raise RuntimeError("Frozen input changed during inference")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions))
    metadata = {
        "model": profile["model"], "model_revision": profile["revision"], "modelcard_url": f"https://huggingface.co/{profile['model']}/blob/{profile['revision']}/README.md",
        "license": profile["license"], "advertised_languages": profile["languages"], "parameters": sum(p.numel() for p in model.parameters()),
        "packages": {package: importlib.metadata.version(package) for package in ["torch", "transformers", "tokenizers", "safetensors", "huggingface-hub"]},
        "python": platform.python_version(), "input_sha256": input_hash, "documents": len(rows), "gold_blind": True,
        "inference_input_fields": ["id", "text"], "label_map": profile["map"], "native_labels": model.config.id2label,
        "unmapped_labels": sorted({label.split("-", 1)[-1] for label in model.config.id2label.values() if label != "O"} - profile["map"].keys()),
        "decode": ("argmax, official BIOES helper: B/S starts a new entity; E/S does not immediately close it"
                   if args.model == "korean-e5" else "argmax, contiguous I-label native-category aggregation"),
        "threshold": None, "threshold_policy": "all argmax non-O predictions, no validation or test tuning",
        "score": "mean assigned native-token softmax probability; canonical spans character-weighted mean; uncalibrated",
        "profiles": list(predictions[0]["profiles"]) if predictions else [], "default_profile": default_profile if predictions else None,
        "canonical_policy": "strip whitespace and . , ; : at edges; merge adjacent same coarse type across only whitespace or Unicode punctuation; never general words/digits",
        "korean_normalization": "unaltered official MIT usage.py Josa/date/whitespace normalizer" if args.model == "korean-e5" else None,
        "max_tokens": args.max_tokens, "observed_max_tokens": max(lengths, default=0), "total_input_tokens": sum(lengths),
        "generated_tokens": 0, "truncation": False, "batch_size": args.batch_size, "device": args.device, "dtype": profile["dtype"],
        "load_seconds": load_seconds, "inference_seconds": inference_seconds,
        "timing_context": "shared H100, concurrent experiment; indicative runtime, not isolated latency benchmark",
        "gpu": torch.cuda.get_device_name() if args.device.startswith("cuda") else None,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated() if args.device.startswith("cuda") else None,
        "prediction_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"finished": str(args.output), "metadata": str(args.metadata), "inference_seconds": inference_seconds}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=PROFILES, required=True)
    parser.add_argument("--input", type=Path, default=Path("runs/pii/diverse-v1/test.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    if args.batch_size < 1 or args.max_tokens < 2:
        parser.error("positive batch size and at least two max tokens required")
    infer(args)


if __name__ == "__main__":
    main()
