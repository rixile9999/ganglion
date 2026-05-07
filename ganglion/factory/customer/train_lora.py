"""SFT a per-customer LoRA on Qwen3-1.7B over synth examples.

Loads ``Qwen/Qwen3-1.7B`` (base, no chat-tuned variant), applies a LoRA
adapter on all linear modules, and trains via TRL's SFTTrainer with
``assistant_only_loss=True`` so we don't waste capacity learning the
catalog system prompt.

The training format mirrors ``ganglion.runtime.qwen._dsl_messages`` exactly
so train/inference distributions match. Anything that changes here MUST
also change there (or vice versa) — that is the load-bearing invariant of
the whole factory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ganglion.dsl.catalog import Catalog
from ganglion.factory.customer.synth import SynthExample


SYSTEM_PROMPT_TEMPLATE = (
    "You convert user requests into the JSON DSL below. "
    "The response must be valid JSON.\n\n{catalog_dsl}"
)


@dataclass(frozen=True)
class TrainConfig:
    base_model: str = "Qwen/Qwen3-1.7B"
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
    max_seq_length: int = 1024

    seed: int = 42
    logging_steps: int = 5
    save_strategy: str = "epoch"
    save_total_limit: int = 1


def build_messages(catalog: Catalog, example: SynthExample) -> list[dict]:
    """Build the (system, user, assistant) message triplet for one example."""
    system_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": example.intent},
        {"role": "assistant", "content": example.expected_dsl},
    ]


def _dataset_from_examples(catalog: Catalog, examples: Iterable[SynthExample]):
    """Convert synth examples to a HuggingFace Dataset with a 'messages' column."""
    from datasets import Dataset

    rows = [{"messages": build_messages(catalog, ex)} for ex in examples]
    return Dataset.from_list(rows)


def train_lora(
    catalog: Catalog,
    examples: list[SynthExample],
    output_dir: Path | str,
    config: TrainConfig | None = None,
) -> Path:
    """SFT-train a LoRA adapter and save it to ``output_dir/adapter``.

    Returns the path to the saved adapter directory.
    """
    cfg = config or TrainConfig()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = output_dir / "adapter"

    if not examples:
        raise ValueError("no examples to train on")

    # Lazy imports — keep ganglion.factory importable without HF stack
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    print(f"[train_lora] base_model={cfg.base_model}")
    print(f"[train_lora] examples={len(examples)} epochs={cfg.epochs} "
          f"bs={cfg.per_device_batch_size}x{cfg.gradient_accumulation_steps}")

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

    train_ds = _dataset_from_examples(catalog, examples)

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
    print(f"[train_lora] final loss={metrics.get('train_loss', float('nan')):.4f}")
    print(f"[train_lora] runtime={metrics.get('train_runtime', float('nan')):.1f}s")

    # Save the adapter only — base weights stay separate (HF cache)
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    (output_dir / "train_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )

    return adapter_dir


def load_lora_for_inference(
    adapter_dir: Path | str,
    *,
    base_model: str = "Qwen/Qwen3-1.7B",
    bf16: bool = True,
):
    """Load a trained LoRA adapter on top of the base model for inference.

    Returns ``(model, tokenizer)``.
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(adapter_dir, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        dtype=torch.bfloat16 if bf16 else torch.float32,
        device_map="auto",
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, str(adapter_dir))
    model.eval()
    return model, tokenizer


def generate_dsl(
    model,
    tokenizer,
    catalog: Catalog,
    user_intent: str,
    *,
    max_new_tokens: int = 256,
    temperature: float = 0.0,
) -> str:
    """Run a single inference: intent → DSL string."""
    import torch

    system_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_intent},
    ]
    encoded = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
        enable_thinking=False,  # Qwen3-specific; ignored by other tokenizers
    )
    input_ids = encoded["input_ids"].to(model.device)
    attention_mask = encoded.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(model.device)

    gen_kwargs: dict = {
        "max_new_tokens": max_new_tokens,
        "pad_token_id": tokenizer.pad_token_id or tokenizer.eos_token_id,
    }
    if temperature > 0:
        gen_kwargs["do_sample"] = True
        gen_kwargs["temperature"] = temperature
    else:
        gen_kwargs["do_sample"] = False

    with torch.no_grad():
        output = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            **gen_kwargs,
        )
    generated = output[0][input_ids.shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()
