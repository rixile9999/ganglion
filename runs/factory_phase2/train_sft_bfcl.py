"""S2a' — SFT a Qwen3-0.6B LoRA on the BFCL augmented pool.

Differs from ``train_v2_cuda.py`` in one crucial way: BFCL ships a tool
list per case, so we can't share a single catalog across rows. Instead,
``build_sft_pool_bfcl.py`` already baked the per-case ``catalog.render_json_dsl()``
into each row's system prompt. This trainer just feeds those rows
straight into TRL's SFTTrainer with ``assistant_only_loss=True``.

Hyperparams mirror the IoT v2 run: r=32 α=64, all-linear, bf16, gc on,
epochs=3, lr=2e-4 cosine, warmup 5%, seed=42. ``max_seq_length=2048``
because some BFCL catalogs (multiple/parallel_multiple) are larger than
the IoT-light 5-tool catalog.

Usage:
    python runs/factory_phase2/train_sft_bfcl.py \\
        --pool examples/bfcl/v4/train/sft_pool.jsonl \\
        --out  runs/factory_phase2/sft_0.6B_bfcl/v1
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrainConfig:
    base_model: str = "Qwen/Qwen3-0.6B"
    lora_rank: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05
    epochs: int = 3
    per_device_batch_size: int = 4
    gradient_accumulation_steps: int = 2
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.05
    lr_scheduler_type: str = "cosine"
    bf16: bool = True
    gradient_checkpointing: bool = True
    max_seq_length: int = 2048
    seed: int = 42
    logging_steps: int = 10
    save_strategy: str = "epoch"
    save_total_limit: int = 1


def _load_messages_dataset(pool_path: Path):
    from datasets import Dataset

    rows: list[dict] = []
    with pool_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rows.append({"messages": r["messages"]})
    return Dataset.from_list(rows)


def train(pool_path: Path, output_dir: Path, cfg: TrainConfig) -> Path:
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = output_dir / "adapter"

    print(f"[train_sft_bfcl] base_model={cfg.base_model}")
    train_ds = _load_messages_dataset(pool_path)
    print(f"[train_sft_bfcl] examples={len(train_ds)} epochs={cfg.epochs} "
          f"bs={cfg.per_device_batch_size}x{cfg.gradient_accumulation_steps} "
          f"lr={cfg.learning_rate} max_seq_length={cfg.max_seq_length}")

    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        cfg.base_model,
        dtype=torch.bfloat16 if cfg.bf16 else torch.float32,
        device_map="auto",
        trust_remote_code=True,
    )
    if cfg.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()

    lora_config = LoraConfig(
        r=cfg.lora_rank,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules="all-linear",
        bias="none",
        task_type="CAUSAL_LM",
    )

    sft_args = SFTConfig(
        output_dir=str(output_dir / "trainer"),
        num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.per_device_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        learning_rate=cfg.learning_rate,
        warmup_ratio=cfg.warmup_ratio,
        lr_scheduler_type=cfg.lr_scheduler_type,
        bf16=cfg.bf16,
        gradient_checkpointing=cfg.gradient_checkpointing,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_length=cfg.max_seq_length,
        save_strategy=cfg.save_strategy,
        save_total_limit=cfg.save_total_limit,
        logging_steps=cfg.logging_steps,
        seed=cfg.seed,
        report_to=[],
        save_only_model=True,
        assistant_only_loss=True,
        completion_only_loss=False,
        dataset_kwargs={"skip_prepare_dataset": False},
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_args,
        train_dataset=train_ds,
        processing_class=tokenizer,
        peft_config=lora_config,
    )

    train_result = trainer.train()
    metrics = train_result.metrics
    print(f"[train_sft_bfcl] final loss={metrics.get('train_loss', float('nan')):.4f}")
    print(f"[train_sft_bfcl] runtime={metrics.get('train_runtime', float('nan')):.1f}s")

    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    (output_dir / "train_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    return adapter_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--bs", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

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
    adapter_dir = train(args.pool, args.out, cfg)
    print(f"[train_sft_bfcl] adapter saved to {adapter_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
