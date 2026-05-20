"""Evaluate a local Qwen3-0.6B (+ optional LoRA, + optional xgrammar mask) on BFCL.

Supports either the in-category holdout split (matching what bfcl_sft.train_category
produced) or the full 100-case sample. Optionally toggles xgrammar grammar masking
with per-case grammar compilation (cached by catalog signature).

Outputs:
  <out>/cases.jsonl  — per-case rows (prompt, expected, predicted, ast_match, syntax_valid)
  <out>/summary.json — aggregate metrics (ast_match_rate, syntax_valid_rate, latencies)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import BFCLCase, load_category
from ganglion.dsl.catalog import Catalog
from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.dsl.json_extract import parse_json_dsl_lenient
from ganglion.dsl.tool_spec import DSLValidationError


SYSTEM_PROMPT_TEMPLATE = (
    "You convert user requests into the JSON DSL below. "
    "The response must be valid JSON.\n\n{catalog_dsl}"
)

_ALLOW_EMPTY = {"irrelevance"}


def build_catalog(case: BFCLCase) -> Catalog:
    mapper = compile_tool_calling_schema(
        list(case.tools),
        name=f"bfcl_{case.id}",
        allow_empty_calls=case.category in _ALLOW_EMPTY,
    )
    return mapper.catalog


def split_train_holdout(cases, *, train_ratio: float = 0.8, seed: int = 42):
    import random
    rng = random.Random(seed)
    idx = list(range(len(cases)))
    rng.shuffle(idx)
    cut = int(round(len(cases) * train_ratio))
    return [cases[i] for i in sorted(idx[:cut])], [cases[i] for i in sorted(idx[cut:])]


def _load_model(base_model: str, adapter_dir: str | None, *, bf16: bool = True):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    tok_src = adapter_dir if adapter_dir else base_model
    tokenizer = AutoTokenizer.from_pretrained(tok_src, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        dtype=torch.bfloat16 if bf16 else torch.float32,
        device_map="auto",
        trust_remote_code=True,
    )
    if adapter_dir:
        model = PeftModel.from_pretrained(model, adapter_dir)
    model.eval()
    return model, tokenizer


def _compile_grammar_cached(catalog: Catalog, tokenizer, cache: dict, model_vocab_size: int):
    """Cache by tool-set signature so identical schemas reuse the compiled grammar."""
    from ganglion.factory.grammar import compile_catalog_grammar
    sig = json.dumps(
        [(t.name, sorted(name for name, _ in t.args)) for t in catalog.tools],
        sort_keys=True,
    ) + str(catalog.allow_empty_calls)
    if sig not in cache:
        cache[sig] = compile_catalog_grammar(
            catalog, tokenizer, vocab_size=model_vocab_size
        )
    return cache[sig]


def _generate(model, tokenizer, catalog: Catalog, user_intent: str,
              *, max_new_tokens: int, compiled_grammar=None) -> str:
    import torch
    sys_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    messages = [{"role": "system", "content": sys_content},
                {"role": "user", "content": user_intent}]
    encoded = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt",
        return_dict=True, enable_thinking=False,
    )
    input_ids = encoded["input_ids"].to(model.device)
    attention_mask = encoded.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(model.device)
    gen_kwargs: dict = {
        "max_new_tokens": max_new_tokens,
        "pad_token_id": tokenizer.pad_token_id or tokenizer.eos_token_id,
        "do_sample": False,
    }
    if compiled_grammar is not None:
        from ganglion.factory.grammar import make_logits_processor
        gen_kwargs["logits_processor"] = [make_logits_processor(compiled_grammar)]
    with torch.no_grad():
        out = model.generate(input_ids=input_ids, attention_mask=attention_mask, **gen_kwargs)
    return tokenizer.decode(out[0][input_ids.shape[1]:], skip_special_tokens=True).strip()


def run_eval(
    category: str,
    out_dir: Path,
    *,
    base_model: str,
    adapter_dir: str | None,
    use_grammar_mask: bool,
    split: str = "holdout",  # 'holdout' | 'full'
    max_new_tokens: int = 384,
) -> dict:
    cases = load_category(category)[:100]
    if split == "holdout":
        _, eval_cases = split_train_holdout(cases)
    elif split == "full":
        eval_cases = cases
    else:
        raise ValueError(f"bad split {split}")

    out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = out_dir / "cases.jsonl"
    summary_path = out_dir / "summary.json"

    model, tokenizer = _load_model(base_model, adapter_dir)
    grammar_cache: dict = {}
    model_vocab_size = getattr(model.config, "vocab_size", None)

    n = len(eval_cases)
    syntax_valid = 0
    ast_correct = 0
    action_match = 0  # right tool count, regardless of args
    latencies: list[float] = []

    rows: list[dict[str, Any]] = []
    for i, case in enumerate(eval_cases):
        catalog = build_catalog(case)
        compiled_grammar = None
        if use_grammar_mask:
            try:
                compiled_grammar = _compile_grammar_cached(
                    catalog, tokenizer, grammar_cache, model_vocab_size
                )
            except Exception as exc:
                compiled_grammar = None
                grammar_compile_err = str(exc)
            else:
                grammar_compile_err = None
        else:
            grammar_compile_err = None

        t0 = time.perf_counter()
        try:
            raw = _generate(model, tokenizer, catalog, case.user_message,
                            max_new_tokens=max_new_tokens, compiled_grammar=compiled_grammar)
            gen_err = None
        except Exception as exc:
            raw = ""
            gen_err = f"generate failed: {exc}"
        latency_ms = (time.perf_counter() - t0) * 1000

        predicted_plan = None
        parse_err = None
        parse_strategy = None
        if gen_err is None:
            try:
                predicted_plan, parse_strategy = parse_json_dsl_lenient(
                    raw, catalog=catalog, prompt=case.user_message
                )
                syntax_valid += 1
            except DSLValidationError as exc:
                parse_err = str(exc)
            except Exception as exc:
                parse_err = f"unexpected: {exc}"

        ast_ok = False
        if predicted_plan is not None:
            try:
                result = ast_match(predicted_plan.calls, case)
                ast_ok = result.valid
                if predicted_plan.calls and case.ground_truth:
                    pred_names = sorted(c.action for c in predicted_plan.calls)
                    gt_names = sorted(
                        next(iter(gt.keys())) for gt in case.ground_truth
                    )
                    if pred_names == gt_names:
                        action_match += 1
                elif not predicted_plan.calls and case.ground_truth is None:
                    action_match += 1
            except Exception as exc:
                parse_err = f"grader failed: {exc}"
        if ast_ok:
            ast_correct += 1
        latencies.append(latency_ms)

        rows.append({
            "id": case.id,
            "category": case.category,
            "prompt": case.user_message,
            "predicted_raw": raw,
            "predicted": [
                {"action": c.action, "args": dict(c.args)} for c in (predicted_plan.calls if predicted_plan else [])
            ] if predicted_plan is not None else None,
            "ground_truth": list(case.ground_truth) if case.ground_truth else None,
            "syntax_valid": predicted_plan is not None,
            "parse_strategy": parse_strategy,
            "ast_match": ast_ok,
            "latency_ms": round(latency_ms, 2),
            "errors": {
                "generate": gen_err,
                "parse": parse_err,
                "grammar_compile": grammar_compile_err,
            },
        })

    cases_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )

    summary = {
        "category": category,
        "n": n,
        "split": split,
        "base_model": base_model,
        "adapter": adapter_dir,
        "grammar_mask": use_grammar_mask,
        "syntax_valid_rate": round(syntax_valid / n, 4) if n else None,
        "ast_match_rate": round(ast_correct / n, 4) if n else None,
        "action_match_rate": round(action_match / n, 4) if n else None,
        "latency_ms_mean": round(sum(latencies) / n, 2) if latencies else None,
        "latency_ms_p50": round(sorted(latencies)[n // 2], 2) if latencies else None,
        "latency_ms_p95": round(sorted(latencies)[min(n - 1, int(n * 0.95))], 2) if latencies else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[bfcl_eval:{category}] ast_match={summary['ast_match_rate']} "
          f"syntax={summary['syntax_valid_rate']} action={summary['action_match_rate']} "
          f"latency_p50={summary['latency_ms_p50']}ms n={n}")
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--category", required=True,
                   choices=["simple_python","multiple","parallel","parallel_multiple","irrelevance"])
    p.add_argument("--out", required=True)
    p.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    p.add_argument("--adapter", default=None)
    p.add_argument("--grammar-mask", action="store_true")
    p.add_argument("--split", choices=["holdout", "full"], default="holdout")
    p.add_argument("--max-new-tokens", type=int, default=384)
    args = p.parse_args()
    run_eval(
        args.category, Path(args.out),
        base_model=args.base_model, adapter_dir=args.adapter,
        use_grammar_mask=args.grammar_mask, split=args.split,
        max_new_tokens=args.max_new_tokens,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
