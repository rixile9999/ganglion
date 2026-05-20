"""Phase 2 S2b — self-bootstrap from a trained category adapter on BFCL.

Mirrors `docs/factory_phase2_session_2026-05-08.md` §S2c bootstrap recipe:

  1. Load the v1 LoRA adapter for `--category` (sft_0.6B_bfcl_<cat>/adapter).
  2. For each row in the 80-case training split, sample N=4 outputs at T=0.7.
  3. Grade each sample via the BFCL `ast_match` checker. Pass = sample becomes
     a new training row whose `assistant` text is the canonical DSL derived from
     the sample (not from ground_truth — the model's own correct attempt is
     reinforced).
  4. Concatenate (original train rows that already had a usable expected_dsl)
     + (bootstrapped passing samples) into `augmented_train.jsonl`.

The downstream v2 SFT is just `bfcl_sft.py --category <cat> --out <v2>` reading
this jsonl via a one-line patch (TODO if invoked).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import load_category
from ganglion.dsl.json_extract import parse_json_dsl_lenient
from ganglion.dsl.tool_spec import DSLValidationError

sys.path.insert(0, "/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
from bfcl_sft import (  # noqa: E402
    SYSTEM_PROMPT_TEMPLATE,
    build_catalog,
    expected_dsl_from_ground_truth,
    split_train_holdout,
)


def _generate_sample(model, tokenizer, catalog, user_msg: str, *, temperature: float, max_new_tokens: int) -> str:
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--category", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--out", required=True, help="Path to augmented_train.jsonl")
    p.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    p.add_argument("--n-samples", type=int, default=4)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-new-tokens", type=int, default=384)
    args = p.parse_args()

    cases = load_category(args.category)[:100]
    train_cases, _ = split_train_holdout(cases)

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, args.adapter)
    model.eval()

    out_rows: list[dict] = []
    seen_ids: set[str] = set()
    stats = {"original_kept": 0, "samples_pass": 0, "samples_fail": 0}

    # Keep the originals (those that parse cleanly).
    for c in train_cases:
        try:
            catalog = build_catalog(c)
            expected = expected_dsl_from_ground_truth(c)
            catalog.parse_json_dsl(expected)
        except Exception:
            continue
        sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
        out_rows.append({
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": c.user_message},
                {"role": "assistant", "content": expected},
            ],
            "origin": "original",
            "case_id": c.id,
        })
        stats["original_kept"] += 1

    # Bootstrap samples.
    for c in train_cases:
        catalog = build_catalog(c)
        sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
        for _ in range(args.n_samples):
            raw = _generate_sample(model, tokenizer, catalog, c.user_message,
                                   temperature=args.temperature,
                                   max_new_tokens=args.max_new_tokens)
            try:
                plan, _ = parse_json_dsl_lenient(raw, catalog=catalog, prompt=c.user_message)
            except DSLValidationError:
                stats["samples_fail"] += 1
                continue
            try:
                if ast_match(plan.calls, c).valid:
                    expected_str = json.dumps(
                        {"calls": [{"action": call.action, "args": dict(call.args)} for call in plan.calls]},
                        ensure_ascii=False,
                    )
                    out_rows.append({
                        "messages": [
                            {"role": "system", "content": sys_content},
                            {"role": "user", "content": c.user_message},
                            {"role": "assistant", "content": expected_str},
                        ],
                        "origin": "bootstrap",
                        "case_id": c.id,
                    })
                    stats["samples_pass"] += 1
                else:
                    stats["samples_fail"] += 1
            except Exception:
                stats["samples_fail"] += 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows), encoding="utf-8"
    )
    stats_path = out_path.with_suffix(".stats.json")
    stats_path.write_text(json.dumps(stats, indent=2))
    total_pass_rate = stats["samples_pass"] / (stats["samples_pass"] + stats["samples_fail"]) if (stats["samples_pass"] + stats["samples_fail"]) else 0
    print(f"[bootstrap:{args.category}] kept={stats['original_kept']} +sampled_pass={stats['samples_pass']} pass_rate={total_pass_rate:.2%} out={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
