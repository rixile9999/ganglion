"""Re-train the v2 0.6B LoRA on CUDA from the committed augmented_train.jsonl.

The original v2 was trained on M1 Ultra; this script reproduces it on RTX 4090
so that S3 (DPO) can run on CUDA with a comparable starting point. We skip the
80/20 split because we are not gathering a fresh holdout — dataset.jsonl via
grammar_ablation.py is the authoritative signal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import read_jsonl
from ganglion.factory.customer.train_lora import TrainConfig, train_lora


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="iot_light_5")
    parser.add_argument("--synth", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--bs", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    catalog = ingest_schema(args.catalog)
    examples = read_jsonl(Path(args.synth))
    print(f"[train_v2_cuda] catalog={catalog.name} examples={len(examples)}")

    cfg = TrainConfig(
        base_model=args.base_model,
        epochs=args.epochs,
        lora_rank=args.rank,
        per_device_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_length,
        seed=args.seed,
    )

    out_dir = Path(args.out)
    adapter_dir = train_lora(catalog, examples, out_dir, config=cfg)
    print(f"[train_v2_cuda] adapter saved to {adapter_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
