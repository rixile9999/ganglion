"""S3f + S3g — build DPO pairs from sft_v2 sampling, then DPO-train.

Pair construction:
  For each training prompt, sample N at T=0.7 from the v2 adapter. Score each
  sample with the BFCL ast_match grader. Form (chosen, rejected) pairs:
    chosen   = a sample with ast_match=True (prefer strict parse).
    rejected = a sample with ast_match=False (prefer parse-failure > arg-fail).
  Skip prompts where all 4 pass or all 4 fail.

DPO training:
  TRL `DPOTrainer` on top of the v2 adapter. β=0.1, lr=5e-7, 1 epoch.

Both stages exposed via subcommands `pairs` and `train`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
from bfcl_sft import (  # noqa: E402
    SYSTEM_PROMPT_TEMPLATE, build_catalog, expected_dsl_from_ground_truth,
    split_train_holdout,
)
from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import load_category
from ganglion.dsl.json_extract import parse_json_dsl_lenient
from ganglion.dsl.tool_spec import DSLValidationError


def _generate_sample(model, tokenizer, catalog, user_msg: str,
                     *, temperature: float, max_new_tokens: int) -> str:
    import torch
    sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    msgs = [{"role": "system", "content": sys_content},
            {"role": "user", "content": user_msg}]
    enc = tokenizer.apply_chat_template(
        msgs, add_generation_prompt=True, return_tensors="pt",
        return_dict=True, enable_thinking=False,
    )
    input_ids = enc["input_ids"].to(model.device)
    attention_mask = enc.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(model.device)
    with torch.no_grad():
        out = model.generate(
            input_ids=input_ids, attention_mask=attention_mask,
            do_sample=True, temperature=temperature, top_p=0.95,
            max_new_tokens=max_new_tokens,
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
    return tokenizer.decode(out[0][input_ids.shape[1]:], skip_special_tokens=True).strip()


def cmd_pairs(args) -> int:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    cases = load_category(args.category)[:100]
    train_cases, _ = split_train_holdout(cases)

    tokenizer = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, args.adapter)
    model.eval()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pairs: list[dict] = []
    stats = {"prompts_seen": 0, "skipped_all_pass": 0, "skipped_all_fail": 0,
             "pairs_built": 0, "samples_total": 0, "samples_pass": 0}

    for c in train_cases:
        catalog = build_catalog(c)
        sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
        prompt_full = (sys_content, c.user_message)
        samples: list[dict] = []
        for _ in range(args.n_samples):
            raw = _generate_sample(model, tokenizer, catalog, c.user_message,
                                   temperature=args.temperature,
                                   max_new_tokens=args.max_new_tokens)
            stats["samples_total"] += 1
            try:
                plan, strat = parse_json_dsl_lenient(raw, catalog=catalog, prompt=c.user_message)
                ok = ast_match(plan.calls, c).valid if plan is not None else False
                samples.append({"raw": raw, "parse_ok": True, "strat": strat, "ast": ok})
                if ok:
                    stats["samples_pass"] += 1
            except DSLValidationError:
                samples.append({"raw": raw, "parse_ok": False, "strat": None, "ast": False})

        stats["prompts_seen"] += 1
        passing = [s for s in samples if s["ast"]]
        failing = [s for s in samples if not s["ast"]]
        if not passing:
            stats["skipped_all_fail"] += 1
            continue
        if not failing:
            stats["skipped_all_pass"] += 1
            continue
        # Prefer strict parsed pass; prefer parse-failing reject.
        chosen = next((s for s in passing if s["strat"] == "strict"), passing[0])
        rejected = next((s for s in failing if not s["parse_ok"]), failing[0])
        pairs.append({
            "case_id": c.id,
            "messages_prompt": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": c.user_message},
            ],
            "chosen": chosen["raw"],
            "rejected": rejected["raw"],
        })
        stats["pairs_built"] += 1

    out_path.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs), encoding="utf-8"
    )
    out_path.with_suffix(".stats.json").write_text(json.dumps(stats, indent=2))
    print(f"[dpo_pairs:{args.category}] pairs={stats['pairs_built']} "
          f"pass_rate={stats['samples_pass']/max(1,stats['samples_total']):.2%}")
    return 0


def cmd_train(args) -> int:
    import torch
    from peft import LoraConfig, PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer
    from datasets import Dataset

    pairs = [json.loads(l) for l in Path(args.pairs).read_text().splitlines() if l.strip()]
    if not pairs:
        print(f"[dpo_train:{args.category}] no pairs; abort", file=sys.stderr)
        return 1

    tokenizer = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base = AutoModelForCausalLM.from_pretrained(
        args.base_model, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, args.adapter, is_trainable=True)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()

    # Build TRL DPO dataset format: prompt + chosen + rejected (strings).
    rows = []
    for p in pairs:
        # Render the chat prompt to a single string via the tokenizer template
        prompt_str = tokenizer.apply_chat_template(
            p["messages_prompt"], add_generation_prompt=True, tokenize=False,
        )
        rows.append({
            "prompt": prompt_str,
            "chosen": p["chosen"],
            "rejected": p["rejected"],
        })

    ds = Dataset.from_list(rows)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dpo_cfg = DPOConfig(
        output_dir=str(out_dir / "trainer"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        beta=args.beta,
        max_length=args.max_seq,
        bf16=True,
        save_strategy="epoch",
        save_total_limit=1,
        logging_steps=5,
        report_to=[],
        save_only_model=True,
        seed=42,
    )
    trainer = DPOTrainer(
        model=model, args=dpo_cfg, train_dataset=ds, processing_class=tokenizer,
    )
    result = trainer.train()
    metrics = dict(result.metrics)
    metrics["category"] = args.category
    metrics["pair_count"] = len(rows)

    trainer.model.save_pretrained(out_dir / "adapter")
    tokenizer.save_pretrained(out_dir / "adapter")
    (out_dir / "train_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[dpo_train:{args.category}] adapter saved (loss={metrics.get('train_loss', float('nan')):.4f})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("pairs")
    pp.add_argument("--category", required=True)
    pp.add_argument("--adapter", required=True)
    pp.add_argument("--out", required=True)
    pp.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    pp.add_argument("--n-samples", type=int, default=4)
    pp.add_argument("--temperature", type=float, default=0.7)
    pp.add_argument("--max-new-tokens", type=int, default=384)
    pp.set_defaults(func=cmd_pairs)

    pt = sub.add_parser("train")
    pt.add_argument("--category", required=True)
    pt.add_argument("--pairs", required=True)
    pt.add_argument("--adapter", required=True, help="v2 LoRA to continue from")
    pt.add_argument("--out", required=True)
    pt.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    pt.add_argument("--epochs", type=int, default=1)
    pt.add_argument("--bs", type=int, default=2)
    pt.add_argument("--grad-accum", type=int, default=4)
    pt.add_argument("--lr", type=float, default=5e-7)
    pt.add_argument("--beta", type=float, default=0.1)
    pt.add_argument("--max-seq", type=int, default=2048)
    pt.add_argument("--gradient-checkpointing", action="store_true", default=True)
    pt.set_defaults(func=cmd_train)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
