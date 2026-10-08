"""Run released GLiNER PII models on an unchanged, externally scored corpus.

Install in an isolated environment; GLiNER2 currently requires Transformers 4:
    python -m venv --system-site-packages /tmp/ganglion-gliner-baselines
    /tmp/ganglion-gliner-baselines/bin/python -m pip install \
        gliner==0.2.29 'gliner2[local]==2.0.0' protobuf

The model receives text and a fixed public label schema. Gold annotations are
projected out at load time. No threshold, label, or boundary tuning uses test
annotations, and this runner does not calculate quality metrics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import time


CONFIGS = {
    "gliner": {
        "model_id": "urchade/gliner_multi_pii-v1",
        "revision": "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d",
        "threshold": .3,
        "labels": {"person": "PERSON", "address": "ADDRESS", "phone number": "PHONE", "email": "EMAIL",
                   "national id number": "IDENTIFIER", "social security number": "IDENTIFIER",
                   "identity card number": "IDENTIFIER"},
        "source": "https://huggingface.co/urchade/gliner_multi_pii-v1",
        "threshold_source": "https://huggingface.co/spaces/urchade/gliner_multi_pii-v1/blob/main/app.py",
        "threshold_reason": "Official author's application sets the threshold slider default to 0.3.",
        "pii_training_languages": ["en", "fr", "es", "de", "it", "pt"],
    },
    "gliner2": {
        "model_id": "fastino/gliner2-privacy-filter-PII-multi",
        "revision": "1cb4166094dc58fa8d836429f060d6c95f62b495",
        "threshold": .5,
        "labels": {"person": "PERSON", "address": "ADDRESS", "phone_number": "PHONE", "email": "EMAIL",
                   "government_id": "IDENTIFIER", "national_id_number": "IDENTIFIER"},
        "source": "https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi",
        "threshold_source": "https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi#quick-start",
        "threshold_reason": "Official model-card quickstart uses threshold=0.5.",
        "pii_training_languages": ["en", "fr", "es", "de", "it", "pt", "nl"],
    },
}


def load_inputs(path: Path):
    rows = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
                raise ValueError("each input requires a string id and text")
            rows.append({"id": row["id"], "text": row["text"]})
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate input ids")
    return rows


def _raw_gliner2(result):
    return [{"start": entity["start"], "end": entity["end"], "label": label,
             "score": float(entity["confidence"])}
            for label, values in result["entities"].items() for entity in values]


def canonicalize(raw, labels, length):
    """Map native labels only; preserve native boundaries and confidence."""
    canonical = {}
    native = []
    for entity in raw:
        start, end = entity["start"], entity["end"]
        label, score = entity["label"], float(entity["score"])
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            raise ValueError("baseline returned invalid character offsets")
        if label not in labels or not 0 <= score <= 1:
            raise ValueError("baseline returned an unknown label or invalid score")
        native.append({"start": start, "end": end, "label": label, "score": score})
        key = start, end, labels[label]
        canonical[key] = max(canonical.get(key, 0), score)
    spans = [{"start": start, "end": end, "type": kind, "score": score}
             for (start, end, kind), score in sorted(canonical.items())]
    return spans, native


def run(backend, *, data, output, device="cuda", batch_size=8):
    import torch

    config = CONFIGS[backend]
    rows = load_inputs(data)
    load_started = time.perf_counter()
    if backend == "gliner":
        from gliner import GLiNER
        model = GLiNER.from_pretrained(config["model_id"], revision=config["revision"], map_location=device)
    else:
        from gliner2 import AutoExtractor
        model = AutoExtractor.from_pretrained(config["model_id"], revision=config["revision"], map_location=device)
    model.eval()
    load_seconds = time.perf_counter() - load_started
    output.mkdir(parents=True, exist_ok=True)
    prediction_path = output / f"{backend}.predictions.jsonl"
    temporary = prediction_path.with_suffix(".jsonl.tmp")
    total_spans = total_native = 0
    if device.startswith("cuda"):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    inference_started = time.perf_counter()
    with temporary.open("w", encoding="utf-8") as destination, torch.inference_mode():
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            texts = [row["text"] for row in batch]
            labels = list(config["labels"])
            if backend == "gliner":
                predictions = model.batch_predict_entities(texts, labels, flat_ner=True,
                                                           threshold=config["threshold"], batch_size=batch_size)
            else:
                predictions = [_raw_gliner2(result) for result in model.batch_extract_entities(
                    texts, labels, batch_size=batch_size, threshold=config["threshold"],
                    include_confidence=True, include_spans=True)]
            if len(predictions) != len(batch):
                raise ValueError("baseline returned a different number of predictions")
            for row, prediction in zip(batch, predictions, strict=True):
                spans, native = canonicalize(prediction, config["labels"], len(row["text"]))
                total_spans += len(spans)
                total_native += len(native)
                destination.write(json.dumps({"id": row["id"], "spans": spans, "raw_model_spans": native},
                                              ensure_ascii=False) + "\n")
            if start == 0 or (start + len(batch)) % 200 == 0 or start + len(batch) == len(rows):
                print(json.dumps({"backend": backend, "completed": start + len(batch), "rows": len(rows)}), flush=True)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    inference_seconds = time.perf_counter() - inference_started
    temporary.replace(prediction_path)
    packages = {}
    for package in ("torch", "transformers", "gliner", "gliner2", "huggingface_hub", "tokenizers", "sentencepiece", "protobuf"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    metadata = {
        "format": "ganglion-pii-external-baseline-v1", "backend": backend, **config,
        "created_at": datetime.now(timezone.utc).isoformat(), "coordinate": "character",
        "data": str(data), "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "rows": len(rows), "model_input_fields": ["text"], "gold_passed_to_model": False,
        "configuration_policy": "official defaults frozen before test; no test-driven threshold or label tuning",
        "supported_canonical_types": sorted(set(config["labels"].values())),
        "postprocessing": "native label mapping and exact-coordinate/type deduplication only; no boundary corrections",
        "native_span_count": total_native, "canonical_span_count": total_spans,
        "predictions": str(prediction_path), "prediction_sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "parameter_bytes": sum(parameter.numel() * parameter.element_size() for parameter in model.parameters()),
        "dtype": str(next(model.parameters()).dtype), "device": device,
        "gpu": torch.cuda.get_device_name() if device.startswith("cuda") else None,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated() if device.startswith("cuda") else None,
        "model_load_seconds": load_seconds, "inference_seconds": inference_seconds, "batch_size": batch_size,
        "timing_caveat": "Concurrent workloads may share H100. These timings are not a controlled speed ranking or an iPhone measurement.",
        "input_tokens": None, "token_measurement": "Model text+schema input token lengths not instrumented; no token-generation decoder.",
        "generated_output_tokens": 0, "python": platform.python_version(), "packages": packages,
        "language_caveat": "Korean is not listed in PII training languages; no pretrained Korean quality guarantee. The unchanged corpus measures Korean/English/mixed transfer.",
        "evaluation_caveat": "This corpus is Ganglion's fabricated synthetic dataset. Ganglion was trained on its training split; released baselines are zero-shot.",
    }
    (output / f"{backend}.metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backend": backend, "status": "complete", "rows": len(rows), "spans": total_spans,
                      "parameters": metadata["parameters"], "predictions": str(prediction_path)}), flush=True)
    del model
    gc.collect()
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("gliner", "gliner2", "both"), default="both")
    parser.add_argument("--data", type=Path, default=Path("runs/pii/diverse-v1/test.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("runs/pii/baselines"))
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("batch size must be positive")
    for backend in ("gliner", "gliner2") if args.backend == "both" else (args.backend,):
        run(backend, data=args.data, output=args.output, device=args.device, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
