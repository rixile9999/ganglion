"""S3' — DPO preference-pair generation against BFCL AST grader.

Forked from runs/factory_phase2/dpo_pairs.py. Differences:

* Source pool is the BFCL augmented training corpus (`sft_pool_v2.jsonl`),
  each row carrying its own per-case catalog in the system prompt.
* Catalog is compiled per case via `compile_tool_calling_schema(case.tools,
  allow_empty_calls=True)` so the prompt rendered to the model matches the
  eval-time setting.
* Score is **BFCL-grader-aware**, not the IoT graded_score:
    - 1.0  if ast_match accepts the predicted plan
    - 0.5  if it parses + has the right function name(s) but fails value/count
    - 0.0  if it doesn't parse at all
  This yields a three-level reward gradient that DPO can learn from.

Output is TRL-compatible (prompt / chosen / rejected / scores / margin).
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import BFCLCase
from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.dsl.tool_spec import DSLValidationError


def _release_memory() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, AttributeError):
        pass


def _bfcl_score(plan, case: BFCLCase) -> float:
    """Three-level reward: 0.0 / 0.5 / 1.0."""
    if plan is None:
        return 0.0
    grade = ast_match(plan.calls, case)
    if grade.valid:
        return 1.0
    # If the grader rejected on function-name or call-count grounds, it's a
    # structural fail. If only on value/string grounds, it's partial (right
    # function picked but wrong args).
    if grade.error_type and (
        "wrong_func_name" in grade.error_type
        or "wrong_count" in grade.error_type
        or "cannot_find_match" in grade.error_type
    ):
        return 0.0
    return 0.5


def _load_pool(pool_path: Path) -> list[dict]:
    return [json.loads(line) for line in pool_path.read_text().splitlines() if line.strip()]


def _load_train_index(train_root: Path) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for path in train_root.glob("*.jsonl"):
        if path.name in {"sft_pool.jsonl", "sft_pool_v2.jsonl",
                         "sft_pool_v3.jsonl", "bootstrap_v3.jsonl"}:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            idx[row["id"]] = row
    return idx


def _row_to_case(row: dict) -> BFCLCase:
    category = "callable"
    for known in ("parallel_multiple", "simple_python", "multiple", "parallel", "irrelevance"):
        if known in row["id"]:
            category = known
            break
    return BFCLCase(
        id=row["id"],
        category=category,
        user_message=row["question"][0][-1]["content"],
        tools=tuple(row["function"]),
        ground_truth=tuple(row["ground_truth"]) if row.get("ground_truth") is not None else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--train-root", required=True, type=Path)
    parser.add_argument("--adapter", required=True, type=str)
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--samples-per-intent", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--min-margin", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from ganglion.factory.customer.train_lora import (
        SYSTEM_PROMPT_TEMPLATE, load_lora_for_inference, generate_dsl,
    )

    pool_rows = _load_pool(args.pool)
    if args.limit:
        pool_rows = pool_rows[: args.limit]
    train_idx = _load_train_index(args.train_root)
    print(f"[dpo_pairs_bfcl] pool={len(pool_rows)} train_idx={len(train_idx)}")
    print(f"[dpo_pairs_bfcl] adapter={args.adapter} N={args.samples_per_intent} "
          f"T={args.temperature} margin>={args.min_margin}")

    model, tokenizer = load_lora_for_inference(
        args.adapter, base_model=args.base_model,
    )

    pairs: list[dict] = []
    n_attempted = 0
    n_parse_fail = 0
    n_no_variance = 0
    n_below_margin = 0
    n_kept = 0
    score_buckets = {"0.0": 0, "0.5": 0, "1.0": 0}
    by_category: dict[str, int] = {}

    started = time.perf_counter()
    for i, row in enumerate(pool_rows):
        bf_row = train_idx.get(row["id"])
        if bf_row is None:
            continue
        case = _row_to_case(bf_row)
        cat = case.category
        catalog = compile_tool_calling_schema(
            list(case.tools), allow_empty_calls=True,
        ).catalog
        system_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
        prompt_chat = tokenizer.apply_chat_template(
            [
                {"role": "system", "content": system_content},
                {"role": "user", "content": case.user_message},
            ],
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )

        samples: list[tuple[str, float]] = []
        for _ in range(args.samples_per_intent):
            n_attempted += 1
            try:
                raw = generate_dsl(
                    model, tokenizer, catalog, case.user_message,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                )
            except Exception:
                samples.append(("", 0.0))
                n_parse_fail += 1
                continue
            try:
                plan = catalog.parse_json_dsl(raw, prompt=case.user_message)
            except DSLValidationError:
                samples.append((raw, 0.0))
                continue
            score = _bfcl_score(plan, case)
            samples.append((raw, score))
            if score == 0.0: score_buckets["0.0"] += 1
            elif score == 0.5: score_buckets["0.5"] += 1
            else: score_buckets["1.0"] += 1

        sorted_samples = sorted(samples, key=lambda x: x[1])
        loser_text, loser_score = sorted_samples[0]
        winner_text, winner_score = sorted_samples[-1]
        margin = winner_score - loser_score

        if margin < 1e-9:
            n_no_variance += 1
        elif margin < args.min_margin:
            n_below_margin += 1
        else:
            pairs.append({
                "prompt": prompt_chat,
                "chosen": winner_text,
                "rejected": loser_text,
                "winner_score": winner_score,
                "loser_score": loser_score,
                "margin": margin,
                "intent": case.user_message,
                "case_id": case.id,
                "category": cat,
            })
            n_kept += 1
            by_category[cat] = by_category.get(cat, 0) + 1

        _release_memory()
        if (i + 1) % args.progress_every == 0:
            elapsed = time.perf_counter() - started
            print(f"[dpo_pairs_bfcl] {i + 1}/{len(pool_rows)}  pairs={n_kept}  "
                  f"below-margin={n_below_margin}  no-variance={n_no_variance}  "
                  f"elapsed={elapsed:.0f}s", flush=True)

    elapsed = time.perf_counter() - started
    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    stats_path = out_path.with_name(out_path.stem + "_stats.json")
    stats = {
        "pool": str(args.pool.resolve()),
        "adapter": str(Path(args.adapter).resolve()),
        "base_model": args.base_model,
        "n_pool_rows": len(pool_rows),
        "samples_per_intent": args.samples_per_intent,
        "temperature": args.temperature,
        "min_margin": args.min_margin,
        "n_attempted": n_attempted,
        "n_parse_fail": n_parse_fail,
        "n_no_variance": n_no_variance,
        "n_below_margin": n_below_margin,
        "n_pairs_kept": n_kept,
        "by_category": by_category,
        "score_buckets": score_buckets,
        "wall_seconds": round(elapsed, 1),
    }
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 60)
    print("BFCL DPO pair generation result")
    print("=" * 60)
    print(f"pool rows:              {len(pool_rows)}")
    print(f"attempts:               {n_attempted}")
    print(f"  parse-fail:           {n_parse_fail}")
    print(f"  no-variance:          {n_no_variance}")
    print(f"  below margin:         {n_below_margin}")
    print(f"pairs kept:             {n_kept}")
    print(f"by category:            {by_category}")
    print(f"score buckets:          {score_buckets}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
