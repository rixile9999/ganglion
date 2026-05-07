"""Per-customer LoRA pipeline.

Modules in dependency order:
    ingest    — schema input → Catalog
    verifier  — Catalog → reward fn
    synth     — teacher + verifier-gated intent synthesis
    train_lora — TRL SFT on Qwen3-1.7B
    eval      — held-out evaluation
    pack      — bundle artifact
"""
