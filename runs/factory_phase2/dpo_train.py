"""S3 — DPO training on top of the v2 SFT adapter.

Inputs:
  - DPO pairs JSONL (from dpo_pairs.py): {prompt, chosen, rejected, ...}
  - v2 LoRA adapter (the SFT+bootstrap-augmented checkpoint)
  - base model (Qwen3-0.6B)

Output:
  - v3 LoRA adapter (DPO-trained on top of v2)

Reference model handling: when DPOTrainer receives an adapter-wrapped
policy and ref_model=None, TRL transparently creates the reference by
*disabling* adapters on the same model — saves a full base-model copy
on memory-constrained devices. This is the PEFT-DPO standard pattern.

Hyperparameter defaults (Phase 2 plan §11.5):
  - β = 0.1                    (sweep {0.05, 0.1, 0.3} if it plateaus)
  - learning_rate = 5e-7       (DPO is sensitive; SFT 2e-4 is far too high)
  - num_train_epochs = 1       (DPO converges fast on small data)
  - per_device_batch_size = 1  (M1 Ultra-friendly; raise on CUDA boxes)
  - gradient_accumulation = 4  (effective bs=4)

Smoke mode: --smoke runs 5 steps so you can confirm wiring on a tiny
slice before committing GPU time. Use this as the dry-run before any
multi-hour DPO run.

Example (production):
  python runs/factory_phase2/dpo_train.py \\
      --catalog iot_light_5 \\
      --base-model Qwen/Qwen3-0.6B \\
      --adapter runs/factory_phase2/sft_0.6B_v2/iot_light_5/adapter \\
      --pairs runs/factory_phase2/dpo_pairs_iot_light_5.jsonl \\
      --out runs/factory_phase2/dpo_0.6B/iot_light_5
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path


def _release_memory() -> None:
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, AttributeError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter", required=True,
                        help="Path to the SFT (v2) LoRA adapter to start from.")
    parser.add_argument("--pairs", required=True,
                        help="DPO pairs JSONL (from dpo_pairs.py).")
    parser.add_argument("--out", required=True,
                        help="Output dir for v3 adapter + trainer state.")
    parser.add_argument("--beta", type=float, default=0.1,
                        help="DPO β. Higher → policy stays close to ref. "
                        "Sweep {0.05, 0.1, 0.3} if v3 plateaus.")
    parser.add_argument("--learning-rate", type=float, default=5e-7,
                        help="DPO is sensitive; far below SFT's 2e-4.")
    parser.add_argument("--num-train-epochs", type=int, default=1)
    parser.add_argument("--per-device-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--max-prompt-length", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--smoke", action="store_true",
                        help="Run only 5 steps for a wiring sanity check.")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lazy imports — DPOTrainer pulls torch + transformers + trl
    import torch
    from datasets import load_dataset
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    print(f"[dpo_train] catalog={args.catalog} base={args.base_model} "
          f"adapter={args.adapter} pairs={args.pairs}")
    print(f"[dpo_train] β={args.beta} lr={args.learning_rate} "
          f"epochs={args.num_train_epochs} bs={args.per_device_batch_size} "
          f"grad_accum={args.gradient_accumulation_steps}")

    # Load tokenizer from adapter dir (so chat template + padding match training).
    tokenizer = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # Load base + apply v2 adapter as the policy. TRL handles the reference
    # automatically (disables adapters on the same model when ref_model=None).
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    policy = PeftModel.from_pretrained(base, args.adapter, is_trainable=True)

    # Load dataset
    dataset = load_dataset("json", data_files=args.pairs, split="train")
    print(f"[dpo_train] loaded {len(dataset)} preference pairs from {args.pairs}")
    if args.smoke:
        print("[dpo_train] --smoke: capping at 5 steps")

    # DPOConfig — TRL 1.3
    config = DPOConfig(
        output_dir=str(out_dir / "trainer"),
        beta=args.beta,
        learning_rate=args.learning_rate,
        num_train_epochs=args.num_train_epochs,
        per_device_train_batch_size=args.per_device_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        max_length=args.max_length,
        max_prompt_length=args.max_prompt_length,
        seed=args.seed,
        bf16=True,
        logging_steps=5,
        save_strategy="epoch" if not args.smoke else "no",
        report_to=[],  # disable wandb/etc by default
        max_steps=5 if args.smoke else -1,
        # TRL 1.3 quirk: padding_value defaults to None for some tokenizers
        padding_value=tokenizer.pad_token_id,
    )

    trainer = DPOTrainer(
        model=policy,
        ref_model=None,  # TRL auto-derives via adapter disable on PEFT models
        args=config,
        train_dataset=dataset,
        processing_class=tokenizer,
    )

    print("[dpo_train] starting DPO loop...")
    started = time.perf_counter()
    train_result = trainer.train()
    elapsed = time.perf_counter() - started
    print(f"[dpo_train] training finished in {elapsed:.1f}s")

    # Save the new adapter (v3)
    adapter_dir = out_dir / "adapter"
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"[dpo_train] v3 adapter saved to {adapter_dir}")

    metrics = train_result.metrics if hasattr(train_result, "metrics") else {}
    metrics_path = out_dir / "train_metrics.json"
    metrics_path.write_text(json.dumps({
        "catalog": args.catalog,
        "base_model": args.base_model,
        "ref_adapter": args.adapter,
        "pairs": args.pairs,
        "n_pairs": len(dataset),
        "beta": args.beta,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.num_train_epochs,
        "per_device_batch_size": args.per_device_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "wall_seconds": round(elapsed, 1),
        "smoke": args.smoke,
        "trainer_metrics": {
            k: float(v) if isinstance(v, (int, float)) else str(v)
            for k, v in metrics.items()
        },
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[dpo_train] metrics saved to {metrics_path}")

    _release_memory()
    return 0


if __name__ == "__main__":
    sys.exit(main())
