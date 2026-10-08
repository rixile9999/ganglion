"""Measured streaming/recovery experiment on synthetic Korean/English text."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from ganglion.adapters.recovery import generate_keypair, restore_document
from ganglion.domains.pii.dataset import generate, RECIPES
from ganglion.domains.pii.pipeline import run_document
from ganglion.domains.pii.rules import RulesDetector
from ganglion.programs.specs import builtins, preprocessing, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/pii/qwen-0.8b-v2"))
    parser.add_argument("--output", type=Path, default=Path("runs/pii/streaming-report.json"))
    parser.add_argument("--documents", type=int, default=1008)
    parser.add_argument("--recipe", choices=RECIPES, default="legacy")
    args = parser.parse_args()
    if args.documents < 1:
        parser.error("documents must be positive")
    import torch
    from ganglion.domains.pii.native import NativeDetector
    torch.cuda.reset_peak_memory_stats()
    loaded = time.perf_counter()
    native = NativeDetector(args.checkpoint)
    load_ms = (time.perf_counter() - loaded) * 1000
    keys = generate_keypair()
    report = {"fixture": "seeded synthetic test template concatenation", "recipe": args.recipe, "seed": 42,
              "fixture_records": args.documents, "gpu": torch.cuda.get_device_name(),
              "native_load_ms": load_ms, "backbone_parameters": sum(p.numel() for p in native.backbone.parameters()),
              "head_parameters": sum(p.numel() for p in native.heads.parameters()),
              "checkpoint_head_bytes": (args.checkpoint / "heads.safetensors").stat().st_size,
              "results": {}}
    with tempfile.TemporaryDirectory(prefix="ganglion-pii-bench-") as temp:
        root = Path(temp)
        rows = generate(args.documents, split="test", recipe=args.recipe)
        original = ("\r\n\r\n".join(row["text"] for row in rows) + "\r\n").encode()
        gold, offset = set(), 0
        for row in rows:
            for span in row["spans"]:
                gold.add((offset + len(row["text"][:span["start"]].encode()),
                          offset + len(row["text"][:span["end"]].encode()), span["type"]))
            offset += len(row["text"].encode()) + 4
        source = root / "source.txt"
        source.write_bytes(original)
        report["input_bytes"] = len(original)
        native_spec = builtins()["pii-qwen"]
        semantic = preprocessing(native_spec)
        fixed = {"strategy": "fixed", "max_chars": 1024, "overlap_chars": 128, "max_tokens": 1024}
        native_fixed = {**fixed, "max_tokens": min(fixed["max_tokens"], native.metadata.get("max_tokens", 1024))}
        report["native_spec_fingerprint"] = fingerprint(native_spec)
        for name, detector, config in (("rules", RulesDetector(), fixed), ("native_fixed", native, native_fixed), ("native_semantic", native, semantic)):
            output = root / name
            result = run_document(source, output, detector, public_key=keys["public_key"], document_id="synthetic-benchmark",
                                  strategy=config["strategy"], window_chars=config["max_chars"],
                                  overlap_chars=config["overlap_chars"], max_tokens=config["max_tokens"])
            restored = root / (name + "-restored.txt")
            predicted = {(op["source_span"][0], op["source_span"][1], op["type"])
                         for op in (json.loads(line) for line in (output / "plan.jsonl").read_text().splitlines())}
            tp, fp, fn = len(gold & predicted), len(predicted - gold), len(gold - predicted)
            precision, recall = tp / max(1, tp + fp), tp / max(1, tp + fn)
            result["exact_span_quality"] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
                                            "f1": 2 * precision * recall / max(1e-12, precision + recall)}
            restore_started = time.perf_counter()
            restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], restored)
            result.update(restore_latency_ms=(time.perf_counter() - restore_started) * 1000,
                          exact_byte_roundtrip=restored.read_bytes() == original,
                          bytes_per_second=len(original) / (result["latency_ms"] / 1000),
                          recovery_bytes=(output / "recovery.bin").stat().st_size)
            report["results"][name] = result
            print(json.dumps({"backend": name, "units": result["units"], "f1": result["exact_span_quality"]["f1"],
                              "latency_ms": result["latency_ms"], "roundtrip": result["exact_byte_roundtrip"]}), flush=True)
    report["peak_gpu_memory_bytes"] = torch.cuda.max_memory_allocated()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
