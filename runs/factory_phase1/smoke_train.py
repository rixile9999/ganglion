"""Day-5 mini-SFT smoke: train a LoRA on the existing 126 synth pairs.

Goal: validate train_lora.py mechanics — model loads, LoRA attaches,
training runs to completion without OOM/NaN, adapter saves, and the
saved adapter can do inference. *Not* a full quality run.

Usage:
    python runs/factory_phase1/smoke_train.py \\
        --catalog iot_light_5 \\
        --synth runs/factory_phase1/iot_light_5/synth.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import read_jsonl
from ganglion.factory.customer.train_lora import (
    TrainConfig,
    generate_dsl,
    load_lora_for_inference,
    train_lora,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="iot_light_5")
    parser.add_argument("--synth", required=True, help="Path to synth.jsonl")
    parser.add_argument("--out", default="runs/factory_phase1/iot_light_5/lora_smoke")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--bs", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()

    catalog = ingest_schema(args.catalog)
    examples = read_jsonl(Path(args.synth))
    print(f"[smoke_train] catalog={catalog.name} examples={len(examples)}")

    cfg = TrainConfig(
        epochs=args.epochs,
        lora_rank=args.rank,
        per_device_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
    )

    out_dir = Path(args.out)
    adapter_dir = train_lora(catalog, examples, out_dir, config=cfg)
    print(f"[smoke_train] adapter saved to {adapter_dir}")

    # Inference smoke — make sure the saved LoRA actually loads and emits DSL
    print()
    print("=" * 60)
    print("Inference smoke (3 held-out intents)")
    print("=" * 60)
    model, tokenizer = load_lora_for_inference(adapter_dir, base_model=cfg.base_model)
    test_intents = [
        "Turn on the kitchen light",
        "거실 불 꺼져 있어?",
        "Schedule the bedroom light to turn on at 7am",
    ]
    samples_out = []
    for intent in test_intents:
        out = generate_dsl(model, tokenizer, catalog, intent, max_new_tokens=200)
        # Try to parse via catalog
        ok = True
        action = None
        try:
            plan = catalog.parse_json_dsl(out)
            action = plan.calls[0].action if plan.calls else None
        except Exception as exc:
            ok = False
            action = f"PARSE_ERROR: {exc}"
        samples_out.append({"intent": intent, "raw": out, "action": action, "valid": ok})
        print(f"intent: {intent}")
        print(f"  raw:    {out[:200]}")
        print(f"  action: {action}")
        print(f"  valid:  {ok}")
        print()

    (out_dir / "smoke_inference.json").write_text(
        json.dumps(samples_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    n_valid = sum(1 for s in samples_out if s["valid"])
    print(f"[smoke_train] {n_valid}/{len(samples_out)} inference samples parsed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
