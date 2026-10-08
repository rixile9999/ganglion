"""Capture frozen Ganglion predictions without passing gold labels to inference."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("runs/pii/diverse-v1/test.jsonl"))
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/pii/qwen-0.8b-v4-lora16"))
    parser.add_argument("--output", type=Path, default=Path("runs/pii/baselines/ganglion-lora.predictions.jsonl"))
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch size must be positive")
    from ganglion.domains.pii.native import NativeDetector
    from ganglion.domains.pii.train import pack_tokens, predict_tokens
    import torch

    source_bytes = args.data.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    rows = [json.loads(line) for line in source_bytes.decode().splitlines()]
    # pack_tokens creates auxiliary training targets, which predict_tokens never
    # uses. Supply empty labels rather than the benchmark's evaluation oracle.
    inputs = [{"id": row["id"], "text": row["text"], "spans": []} for row in rows]
    loaded = time.perf_counter()
    detector = NativeDetector(args.checkpoint)
    load_seconds = time.perf_counter() - loaded
    packed = pack_tokens(detector.tokenizer, inputs, detector.device, detector.metadata.get("boundary_classes", 32))
    started = time.perf_counter()
    predictions = predict_tokens(detector.backbone, detector.heads, packed, detector.metadata, args.batch_size)
    seconds = time.perf_counter() - started
    if hashlib.sha256(args.data.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Frozen input changed during inference")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as target:
        for row, spans in zip(inputs, predictions, strict=True):
            values = [{"start": span.start, "end": span.end, "type": span.kind, "score": span.score} for span in spans]
            target.write(json.dumps({"id": row["id"], "spans": values, "raw_model_spans": values}) + "\n")
    metadata = {"model_id": "Ganglion Qwen3.5-0.8B LoRA v4", "checkpoint": str(args.checkpoint),
                "model_revision": detector.metadata["base_model_revision"],
                "test_sha256": source_hash, "documents": len(inputs),
                "prediction_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "checkpoint_sha256": {str(path.relative_to(args.checkpoint)): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in sorted(args.checkpoint.rglob("*"))
                                      if path.is_file() and path.suffix in (".safetensors", ".json")
                                      and path.name not in ("evaluation.json", "training-cap.json")},
                "source": "local frozen validation-selected checkpoint; no further optimization",
                "base_parameters": detector.metadata["backbone_parameters"],
                "adapter_parameters": detector.metadata["trainable_backbone_parameters"],
                "head_parameters": detector.metadata["head_parameters"],
                "generation_tokens": 0, "batch_size": args.batch_size,
                "load_seconds": load_seconds, "inference_seconds": seconds,
                "timing_scope": "batched backbone+readout; concurrent GPU jobs, not a speed ranking",
                "precision": "BF16 backbone/FP32 heads", "float32_matmul_precision": torch.get_float32_matmul_precision(),
                "labels": detector.metadata["labels"], "package_versions": {"torch": torch.__version__},
                "decoder": {key: detector.metadata[key] for key in ("boundary_classes", "decode_mode", "joint_type_weight")}}
    args.output.with_name(args.output.name.replace(".predictions.jsonl", ".metadata.json")).write_text(json.dumps(metadata, indent=2))
    print(json.dumps({"output": str(args.output), "documents": len(inputs), "inference_seconds": seconds}))


if __name__ == "__main__":
    main()
