"""Quick inference-only smoke against an already-trained adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.train_lora import (
    generate_dsl,
    load_lora_for_inference,
)


def main() -> int:
    catalog = ingest_schema("iot_light_5")
    adapter_dir = Path("runs/factory_phase1/iot_light_5/lora_smoke/adapter")
    print(f"[smoke_inference] loading adapter from {adapter_dir}")
    model, tokenizer = load_lora_for_inference(adapter_dir)

    test_intents = [
        "Turn on the kitchen light",
        "거실 불 꺼져 있어?",
        "Schedule the bedroom light to turn on at 7am",
        "Set up a movie scene with the living room and bedroom lights on",
        "What lights are available?",
    ]

    samples_out = []
    for intent in test_intents:
        out = generate_dsl(model, tokenizer, catalog, intent, max_new_tokens=200)
        action = None
        valid = True
        try:
            plan = catalog.parse_json_dsl(out)
            action = plan.calls[0].action if plan.calls else "(empty)"
        except Exception as exc:
            valid = False
            action = f"PARSE_ERROR: {exc}"
        samples_out.append({"intent": intent, "raw": out, "action": action, "valid": valid})
        print(f"intent: {intent}")
        print(f"  raw:    {out[:200]}")
        print(f"  action: {action}")
        print(f"  valid:  {valid}")
        print()

    Path("runs/factory_phase1/iot_light_5/lora_smoke/smoke_inference.json").write_text(
        json.dumps(samples_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    n_valid = sum(1 for s in samples_out if s["valid"])
    print(f"[smoke_inference] {n_valid}/{len(samples_out)} parsed OK")
    return 0 if n_valid == len(samples_out) else 1


if __name__ == "__main__":
    sys.exit(main())
