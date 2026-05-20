"""Phase 3 SFT driver — reads BFCL train cases + auxiliary jsonl files
(paraphrased / synth / bootstrap) and trains a LoRA on the union.

Auxiliary rows expected shape:
  {"case_id": str, "user_message": str, "ground_truth": [...], "origin": str,
   "tool": [...] (optional, only when ground_truth's catalog differs from
                  load_category(case_id_prefix))}

For paraphrase rows, the catalog comes from the parent BFCL case
(`case_id` matches a base BFCL id). For synth rows, the catalog is the
parent's BFCL tools (passed through the "tool" field).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
from bfcl_sft import (  # noqa: E402
    SYSTEM_PROMPT_TEMPLATE, TrainArgs, _category_allows_empty,
    expected_dsl_from_ground_truth, split_train_holdout,
)
from ganglion.bfcl.loader import BFCLCase, load_category
from ganglion.dsl.catalog import Catalog
from ganglion.dsl.compiler import compile_tool_calling_schema


def _strip_synth_suffix(case_id: str) -> str:
    if case_id.endswith("_synth"):
        return case_id[:-len("_synth")]
    return case_id


def _catalog_from_tools(tools: list[dict], cat: str, name: str) -> Catalog:
    mapper = compile_tool_calling_schema(
        list(tools), name=name, allow_empty_calls=_category_allows_empty(cat),
    )
    return mapper.catalog


def _expected_from_raw_gt(ground_truth: list[dict]) -> str:
    """Same as expected_dsl_from_ground_truth but takes raw list (not BFCLCase)."""
    if not ground_truth:
        return json.dumps({"calls": []}, ensure_ascii=False)
    calls = []
    for gt_call in ground_truth:
        for fn_name, arg_spec in gt_call.items():
            args: dict[str, Any] = {}
            for arg_name, accepted in arg_spec.items():
                if not accepted:
                    continue
                first = accepted[0]
                if first == "" and len(accepted) > 1:
                    for v in accepted[1:]:
                        if v != "":
                            first = v
                            break
                args[arg_name] = first
            calls.append({"action": fn_name, "args": args})
    return json.dumps({"calls": calls}, ensure_ascii=False)


def _aux_to_row(aux: dict, base_cases: dict[str, BFCLCase]) -> dict | None:
    """Convert an aux row to a {messages: [...]} training row. None on failure."""
    base_id = _strip_synth_suffix(aux["case_id"])
    base = base_cases.get(base_id)
    tools = aux.get("tool") or (list(base.tools) if base else None)
    if tools is None:
        return None
    cat = aux.get("category") or (base.category if base else None)
    if cat is None:
        return None
    try:
        catalog = _catalog_from_tools(tools, cat, f"aux_{aux['case_id']}")
        expected = _expected_from_raw_gt(aux["ground_truth"])
        catalog.parse_json_dsl(expected)
    except Exception:
        return None
    sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    return {
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": aux["user_message"]},
            {"role": "assistant", "content": expected},
        ],
        "origin": aux.get("origin", "aux"),
        "case_id": aux["case_id"],
    }


def collect_train_rows(category: str, *, aux_paths: list[Path]) -> list[dict]:
    base_cases = {c.id: c for c in load_category(category)[:100]}
    train_cases, _ = split_train_holdout(list(base_cases.values()))

    rows: list[dict] = []
    # Original (Phase 2) usable rows
    from bfcl_sft import build_messages_for_case
    for c in train_cases:
        r = build_messages_for_case(c)
        if r is not None:
            r["origin"] = "original"
            r["case_id"] = c.id
            rows.append(r)

    # Aux rows
    for path in aux_paths:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            aux = json.loads(line)
            row = _aux_to_row(aux, base_cases)
            if row is not None:
                rows.append(row)

    return rows


def train_with_rows(category: str, rows: list[dict], out_dir: Path, ta: TrainArgs) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[bfcl_sft_v2:{category}] training on {len(rows)} rows")

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
        r=ta.lora_rank, lora_alpha=ta.lora_alpha, lora_dropout=ta.lora_dropout,
        target_modules="all-linear", bias="none", task_type="CAUSAL_LM",
    )

    ds = Dataset.from_list(rows)
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
        model=model, args=sft_args, train_dataset=ds,
        processing_class=tokenizer, peft_config=lora_config,
    )
    result = trainer.train()
    metrics = dict(result.metrics)
    metrics["category"] = category
    metrics["row_count"] = len(rows)

    adapter_dir = out_dir / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    (out_dir / "train_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[bfcl_sft_v2:{category}] adapter saved (loss={metrics.get('train_loss', float('nan')):.4f})")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--category", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--aux", nargs="*", default=[], help="Paths to aux jsonl files (paraphrase/synth/bootstrap)")
    p.add_argument("--epochs", type=int, default=3)
    args = p.parse_args()
    ta = TrainArgs(epochs=args.epochs)
    rows = collect_train_rows(args.category, aux_paths=[Path(p) for p in args.aux])
    if not rows:
        print(f"[bfcl_sft_v2:{args.category}] no rows; abort", file=sys.stderr)
        return 1
    train_with_rows(args.category, rows, Path(args.out), ta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
