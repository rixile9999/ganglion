"""Day-5b: stratified split → SFT → holdout eval.

The honest signal we couldn't get from smoke_train.py (which evaluated only
3 hand-crafted prompts). This script:

  1. Loads synth.jsonl
  2. Stratified split into train (80%) / holdout (20%)
  3. Trains a fresh LoRA on the train split
  4. Generates DSL for every holdout intent
  5. Computes syntax / action / exact match against gold
  6. Writes Markdown + JSON report

Acceptance signal: holdout exact_match_rate ≥ 0.85 means the 126-example
recipe generalizes; below that, we need more data or longer training.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ganglion.factory.customer.eval import (
    EvalConfig,
    evaluate_lora,
    split_train_eval,
    write_report,
    write_split_jsonls,
)
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import read_jsonl
from ganglion.factory.customer.train_lora import (
    TrainConfig,
    load_lora_for_inference,
    train_lora,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="iot_light_5")
    parser.add_argument("--synth", required=True)
    parser.add_argument("--out", default="runs/factory_phase1/iot_light_5/holdout_eval")
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--bs", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = ingest_schema(args.catalog)
    examples = read_jsonl(Path(args.synth))
    print(f"[smoke_train_eval] catalog={catalog.name} total_examples={len(examples)}")

    train, holdout = split_train_eval(
        examples, holdout_ratio=args.holdout_ratio, seed=args.seed
    )
    print(f"[smoke_train_eval] train={len(train)} holdout={len(holdout)}")
    write_split_jsonls(train, holdout, out_dir)

    cfg = TrainConfig(
        epochs=args.epochs,
        lora_rank=args.rank,
        per_device_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        seed=args.seed,
        max_seq_length=args.max_seq_length,
    )
    print(f"[smoke_train_eval] training LoRA on {len(train)} examples...")
    adapter_dir = train_lora(catalog, train, out_dir, config=cfg)
    print(f"[smoke_train_eval] adapter saved to {adapter_dir}")

    print(f"[smoke_train_eval] loading adapter for inference...")
    model, tokenizer = load_lora_for_inference(adapter_dir, base_model=cfg.base_model)

    print(f"[smoke_train_eval] running eval on {len(holdout)} holdout examples...")
    summary, results = evaluate_lora(
        catalog, holdout, model, tokenizer, config=EvalConfig()
    )
    write_report(
        summary, results, out_dir,
        catalog_name=catalog.name, n_train=len(train), n_holdout=len(holdout),
    )

    print()
    print("=" * 60)
    print("Holdout eval result")
    print("=" * 60)
    print(f"holdout size:        {len(holdout)}")
    print(f"syntax_valid_rate:   {summary['syntax_valid_rate']:.1%}")
    print(f"action_match_rate:   {summary['action_match_rate']:.1%}")
    print(f"exact_match_rate:    {summary['exact_match_rate']:.1%}")
    if summary.get("latency_ms_p50") is not None:
        print(f"latency P50:         {summary['latency_ms_p50']:.0f} ms")
        print(f"latency P95:         {summary['latency_ms_p95']:.0f} ms")
    print()
    print("Per-strategy:")
    for strategy, stats in summary.get("per_strategy", {}).items():
        print(
            f"  {strategy:35s}  n={stats['n']:3d}  "
            f"syntax={stats['syntax_valid']:.0%}  "
            f"action={stats['action_match']:.0%}  "
            f"exact={stats['exact_match']:.0%}"
        )
    print()
    print(f"Report: {out_dir}/eval_report.md")

    # Soft acceptance signal
    threshold = 0.85
    decision = "PASS" if summary["exact_match_rate"] >= threshold else "FAIL"
    print(f"Decision (exact_match >= {threshold:.0%}): {decision}")
    return 0 if summary["exact_match_rate"] >= threshold else 1


if __name__ == "__main__":
    sys.exit(main())
