"""S2c' — self-bootstrap on BFCL with per-case catalogs + AST grading.

Forked from runs/factory_phase2/self_bootstrap.py. The key differences:

* Source pool is the BFCL augmented training corpus (`sft_pool_v2.jsonl`),
  which carries per-row catalogs in its system prompt — the model must
  see the same shape it saw during training.
* Grade is `bfcl.grader.ast_match` (not strict ActionPlan equality).
  A rollout is *kept* iff the AST grader accepts it as equivalent to the
  case's canonical plan. This widens the bootstrap yield since BFCL's
  argument space accepts multiple values per key.
* Sampling temperature is non-zero (default 0.7) so the model produces
  novel phrasings; temperature=0 would only re-emit the training-time
  output and dedup to nothing.

Inputs:
  --pool      examples/bfcl/v4/train/sft_pool_v2.jsonl  (training rows in
              messages format with per-row catalog system prompt)
  --train-root examples/bfcl/v4/train                   (original BFCL
              jsonls used to recover per-row {function, ground_truth})
  --adapter   the V2 LoRA adapter (we bootstrap on top of V2)
  --base-model Qwen/Qwen3-0.6B

Output:
  --out       examples/bfcl/v4/train/bootstrap_v3.jsonl
              SFT-pool rows (messages = [system, user, assistant]) whose
              assistant content is the V2-sampled output that the AST
              grader accepted.

We *do not* dedup against the original pool by intent — BFCL paraphrase
training rows already share prompts, so the bootstrap rolls add new
*output* phrasings (different argument orderings, optional fields, etc.)
that the AST grader still accepts. Those are the new training signal.
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
from ganglion.dsl.types import ActionPlan


def _release_memory() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, AttributeError):
        pass


def _load_pool(pool_path: Path) -> list[dict]:
    return [json.loads(line) for line in pool_path.read_text().splitlines() if line.strip()]


def _load_train_index(train_root: Path) -> dict[str, dict]:
    """Map id -> raw BFCL row so we can recover {function, ground_truth} per case.

    Covers train/*.jsonl AND train/synth.jsonl since paraphrase ids
    `<orig>_p{0..2}` live in synth.jsonl.
    """
    idx: dict[str, dict] = {}
    for path in train_root.glob("*.jsonl"):
        if path.name in {"sft_pool.jsonl", "sft_pool_v2.jsonl", "stats.json"}:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            idx[row["id"]] = row
    return idx


def _row_to_case(row: dict) -> BFCLCase:
    category = "irrelevance" if row.get("ground_truth") is None else "callable"
    # category recovery isn't load-bearing for the grader; the latter
    # only branches on parallel/multiple in the id, so preserve it:
    for known in ("parallel_multiple", "simple_python", "multiple", "parallel", "irrelevance"):
        if row["id"].startswith(known + "_") or row["id"].startswith(known + "_p") or known in row["id"]:
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
    parser.add_argument("--pool", required=True, type=Path,
                        help="sft_pool_v2.jsonl (messages format).")
    parser.add_argument("--train-root", required=True, type=Path,
                        help="examples/bfcl/v4/train (for id → case lookup).")
    parser.add_argument("--adapter", required=True, type=str,
                        help="Path to v2 LoRA adapter.")
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--samples-per-intent", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pool_rows = _load_pool(args.pool)
    if args.limit:
        pool_rows = pool_rows[: args.limit]
    print(f"[bootstrap_bfcl] pool={args.pool} pool_rows={len(pool_rows)}")
    print(f"[bootstrap_bfcl] adapter={args.adapter} base={args.base_model}")
    print(f"[bootstrap_bfcl] N={args.samples_per_intent} T={args.temperature}")

    train_idx = _load_train_index(args.train_root)
    print(f"[bootstrap_bfcl] train index entries: {len(train_idx)}")

    # Load V2 adapter
    from ganglion.factory.customer.train_lora import (
        load_lora_for_inference, generate_dsl,
    )
    model, tokenizer = load_lora_for_inference(
        args.adapter, base_model=args.base_model,
    )

    kept: list[dict] = []
    n_attempted = 0
    n_parse_fail = 0
    n_match = 0
    n_no_match = 0
    n_missing_case = 0
    by_category: dict[str, int] = {}

    started = time.perf_counter()

    for i, row in enumerate(pool_rows):
        row_id = row.get("id")
        category = row.get("category", "?")
        bf_row = train_idx.get(row_id)
        if bf_row is None:
            n_missing_case += 1
            continue
        case = _row_to_case(bf_row)

        # Catalog is rendered with allow_empty_calls=True to match the
        # V2 SFT distribution and the eval-time runner setting.
        catalog = compile_tool_calling_schema(
            list(case.tools), allow_empty_calls=True,
        ).catalog

        for sample_idx in range(args.samples_per_intent):
            n_attempted += 1
            try:
                raw = generate_dsl(
                    model, tokenizer, catalog, case.user_message,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                )
            except Exception:
                n_parse_fail += 1
                continue
            try:
                plan = catalog.parse_json_dsl(raw, prompt=case.user_message)
            except DSLValidationError:
                n_parse_fail += 1
                continue
            grade = ast_match(plan.calls, case)
            if not grade.valid:
                n_no_match += 1
                continue
            # Canonical re-serialised plan as the new SFT target.
            canonical = json.dumps(plan.to_jsonable(), ensure_ascii=False, separators=(",", ":"))
            new_row = deepcopy(row)
            new_row["id"] = f"{row_id}_b{sample_idx}"
            new_row["messages"] = [
                row["messages"][0],
                row["messages"][1],
                {"role": "assistant", "content": canonical},
            ]
            kept.append(new_row)
            n_match += 1
            by_category[category] = by_category.get(category, 0) + 1
            break  # one canonical sample per intent is enough

        _release_memory()
        if (i + 1) % args.progress_every == 0:
            elapsed = time.perf_counter() - started
            print(
                f"[bootstrap_bfcl] {i + 1}/{len(pool_rows)}  kept={len(kept)}  "
                f"match-rate={len(kept)/(i+1):.1%}  elapsed={elapsed:.0f}s",
                flush=True,
            )

    elapsed = time.perf_counter() - started
    with out_path.open("w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats_path = out_path.with_name(out_path.stem + "_stats.json")
    stats = {
        "pool": str(args.pool.resolve()),
        "adapter": str(Path(args.adapter).resolve()),
        "base_model": args.base_model,
        "n_pool_rows": len(pool_rows),
        "n_attempted": n_attempted,
        "n_parse_fail": n_parse_fail,
        "n_no_match": n_no_match,
        "n_match": n_match,
        "kept_ratio": round(n_match / max(1, len(pool_rows)), 4),
        "by_category": by_category,
        "missing_train_cases": n_missing_case,
        "samples_per_intent": args.samples_per_intent,
        "temperature": args.temperature,
        "wall_seconds": round(elapsed, 1),
    }
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 60)
    print("BFCL self-bootstrap result")
    print("=" * 60)
    print(f"pool rows:              {len(pool_rows)}")
    print(f"attempts (N×rows):      {n_attempted}")
    print(f"  parse-fail:           {n_parse_fail}")
    print(f"  parsed-but-wrong:     {n_no_match}")
    print(f"  parsed-and-AST-match: {n_match}")
    print(f"kept:                   {len(kept)} ({n_match/max(1,len(pool_rows)):.1%})")
    print(f"by category:            {by_category}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {out_path}")
    print(f"stats:                  {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
