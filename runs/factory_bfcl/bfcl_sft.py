"""Per-category Qwen3-0.6B LoRA SFT on BFCL v4 single-turn cases.

For each BFCL category:
  1. Load the 100-case deterministic sample.
  2. Deterministic 80/20 train/holdout split (seed=42).
  3. For each train row build per-case Catalog (via the schema compiler) and
     a canonical expected DSL string from the first acceptable value of each
     ground_truth slot. Irrelevance rows use the M5' null contract:
     expected = '{"calls": []}'.
  4. Build OpenAI-style messages (system = per-case Catalog DSL, user =
     question, assistant = expected DSL) so train/inference distributions match
     ``ganglion.runtime.qwen._dsl_messages``.
  5. Train Qwen3-0.6B + LoRA r=32 / lr=2e-4 / 3 epochs / max_seq=2048 via
     trl SFTTrainer.

Use ``run_eval`` to evaluate a trained adapter on its 20-case holdout, recording
both the BFCL ast_match grade and Ganglion's structural exact-match.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ganglion.bfcl.loader import BFCLCase, load_category
from ganglion.dsl.catalog import Catalog
from ganglion.dsl.compiler import compile_tool_calling_schema


SYSTEM_PROMPT_TEMPLATE = (
    "You convert user requests into the JSON DSL below. "
    "The response must be valid JSON.\n\n{catalog_dsl}"
)


def _category_allows_empty(category: str) -> bool:
    return category == "irrelevance"


def build_catalog(case: BFCLCase) -> Catalog:
    mapper = compile_tool_calling_schema(
        list(case.tools),
        name=f"bfcl_{case.id}",
        allow_empty_calls=_category_allows_empty(case.category),
    )
    return mapper.catalog


def expected_dsl_from_ground_truth(case: BFCLCase) -> str:
    """Pick the first acceptable value per arg → one canonical DSL string."""
    if case.ground_truth is None:
        return json.dumps({"calls": []}, ensure_ascii=False)

    calls = []
    for gt_call in case.ground_truth:
        for fn_name, arg_spec in gt_call.items():
            args: dict[str, Any] = {}
            for arg_name, accepted_values in arg_spec.items():
                if not accepted_values:
                    continue
                first = accepted_values[0]
                if first == "" and len(accepted_values) > 1:
                    # BFCL convention: empty string means "default / omit allowed".
                    # Prefer a non-empty alternative if one exists.
                    for v in accepted_values[1:]:
                        if v != "":
                            first = v
                            break
                args[arg_name] = first
            calls.append({"action": fn_name, "args": args})
    return json.dumps({"calls": calls}, ensure_ascii=False)


def split_train_holdout(
    cases: list[BFCLCase], *, train_ratio: float = 0.8, seed: int = 42
) -> tuple[list[BFCLCase], list[BFCLCase]]:
    import random

    rng = random.Random(seed)
    indices = list(range(len(cases)))
    rng.shuffle(indices)
    cut = int(round(len(cases) * train_ratio))
    train_idx = sorted(indices[:cut])
    hold_idx = sorted(indices[cut:])
    return [cases[i] for i in train_idx], [cases[i] for i in hold_idx]


def build_messages_for_case(case: BFCLCase) -> dict | None:
    """Build training row. Returns None if the row's expected DSL cannot be
    parsed against its own per-case catalog (BFCL data quirk; skip for SFT,
    eval grader still handles it via AST accept-list semantics).
    """
    try:
        catalog = build_catalog(case)
        expected = expected_dsl_from_ground_truth(case)
        catalog.parse_json_dsl(expected)
    except Exception:
        return None
    sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    return {
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": case.user_message},
            {"role": "assistant", "content": expected},
        ]
    }


@dataclass(frozen=True)
class TrainArgs:
    base_model: str = "Qwen/Qwen3-0.6B"
    epochs: int = 3
    per_device_batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.05
    lr_scheduler_type: str = "cosine"
    lora_rank: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05
    bf16: bool = True
    gradient_checkpointing: bool = True
    max_seq_length: int = 2048
    seed: int = 42
    logging_steps: int = 5


def train_category(category: str, out_dir: Path, ta: TrainArgs) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_category(category)[:100]
    train_cases, hold_cases = split_train_holdout(cases)
    print(f"[bfcl_sft:{category}] cases total={len(cases)} train={len(train_cases)} hold={len(hold_cases)}")

    # Persist split for reproducibility / later eval.
    (out_dir / "train.jsonl").write_text(
        "\n".join(json.dumps({"id": c.id, "user": c.user_message,
                                "ground_truth": list(c.ground_truth) if c.ground_truth else None})
                  for c in train_cases),
        encoding="utf-8",
    )
    (out_dir / "holdout.jsonl").write_text(
        "\n".join(json.dumps({"id": c.id, "user": c.user_message,
                                "ground_truth": list(c.ground_truth) if c.ground_truth else None})
                  for c in hold_cases),
        encoding="utf-8",
    )

    # Build training rows. Each row carries its own per-case Catalog DSL in system.
    train_rows_raw = [(c, build_messages_for_case(c)) for c in train_cases]
    train_rows = [r for _, r in train_rows_raw if r is not None]
    skipped = [c.id for c, r in train_rows_raw if r is None]
    if skipped:
        print(f"[bfcl_sft:{category}] skipped {len(skipped)} rows (un-parsable expected): {skipped[:5]}{'…' if len(skipped)>5 else ''}")
        (out_dir / "skipped_train.txt").write_text("\n".join(skipped), encoding="utf-8")
    print(f"[bfcl_sft:{category}] usable train rows: {len(train_rows)}")

    # Lazy imports to keep ganglion.factory importable without HF stack
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(ta.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        ta.base_model,
        dtype=torch.bfloat16 if ta.bf16 else torch.float32,
        device_map="auto",
        trust_remote_code=True,
    )
    if ta.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()

    lora_config = LoraConfig(
        r=ta.lora_rank,
        lora_alpha=ta.lora_alpha,
        lora_dropout=ta.lora_dropout,
        target_modules="all-linear",
        bias="none",
        task_type="CAUSAL_LM",
    )

    ds = Dataset.from_list(train_rows)

    sft_args = SFTConfig(
        output_dir=str(out_dir / "trainer"),
        num_train_epochs=ta.epochs,
        per_device_train_batch_size=ta.per_device_batch_size,
        gradient_accumulation_steps=ta.gradient_accumulation_steps,
        learning_rate=ta.learning_rate,
        warmup_ratio=ta.warmup_ratio,
        lr_scheduler_type=ta.lr_scheduler_type,
        bf16=ta.bf16,
        gradient_checkpointing=ta.gradient_checkpointing,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_length=ta.max_seq_length,
        save_strategy="epoch",
        save_total_limit=1,
        logging_steps=ta.logging_steps,
        seed=ta.seed,
        report_to=[],
        save_only_model=True,
        assistant_only_loss=True,
        completion_only_loss=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_args,
        train_dataset=ds,
        processing_class=tokenizer,
        peft_config=lora_config,
    )

    t0 = time.perf_counter()
    train_result = trainer.train()
    elapsed = time.perf_counter() - t0
    metrics = dict(train_result.metrics)
    metrics["wall_seconds"] = round(elapsed, 1)
    metrics["category"] = category

    adapter_dir = out_dir / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    (out_dir / "train_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    print(f"[bfcl_sft:{category}] saved adapter to {adapter_dir} "
          f"(loss={metrics.get('train_loss', float('nan')):.4f}, "
          f"runtime={elapsed:.0f}s)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True,
                        choices=["simple_python","multiple","parallel","parallel_multiple","irrelevance"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--bs", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq", type=int, default=2048)
    args = parser.parse_args()

    ta = TrainArgs(
        base_model=args.base_model,
        epochs=args.epochs,
        per_device_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq,
    )
    train_category(args.category, Path(args.out), ta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
