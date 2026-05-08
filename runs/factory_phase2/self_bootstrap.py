"""Self-bootstrap (S2c) — sample the trained model's own outputs on a pool
of intents, keep the validator-gated correct ones, write an augmented
training JSONL.

Output target form: each kept sample is recorded as (intent, expected_dsl,
strategy) where ``expected_dsl`` is the **canonical parsed plan** (i.e.,
the model's emit re-serialized after ``defaults_when_missing`` filled
omitted args). This is the "right" form for downstream SFT — training on
post-correction-completed outputs amortizes the rule across the whole
distribution rather than relying on it at inference forever.

Two-tier source design:
- Default ``--intents`` = Phase 1's train.jsonl. Same prompts the model
  already saw; what changes is *which* paraphrase of the answer the model
  now emits and validator-passes. Useful as a smoke test of the pipeline.
  Most of the rescued data here will overlap the original training set.
- Real lift comes from passing ``--intents`` a paraphrased pool (e.g.,
  fresh teacher-generated paraphrases of train intents). That's a future
  step; this MVP runs cleanly on either.

Pipeline:
  1. Load base + LoRA adapter
  2. Read source intents (jsonl with at least ``intent``, ``expected_dsl``)
  3. For each, sample N completions at temperature T
  4. parse_json_dsl each sample (post-correction is in the parser)
  5. Keep samples whose parsed plan equals expected_dsl gold
  6. Optional dedup vs original train pool (exact-string fallback if
     sentence-transformers missing)
  7. Write bootstrap.jsonl in SynthExample format

Then user chains:
  cat <train.jsonl> <bootstrap.jsonl> > augmented.jsonl
  python runs/factory_phase1/smoke_train_eval.py \\
      --catalog <c> --synth augmented.jsonl --base-model Qwen/Qwen3-0.6B \\
      --out runs/factory_phase2/sft_0.6B_v2/<c>
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import SynthExample, read_jsonl, write_jsonl
from ganglion.factory.customer.train_lora import (
    generate_dsl,
    load_base_for_inference,
    load_lora_for_inference,
)


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


def _exact_string_dedupe(
    new: list[SynthExample], existing_intents: set[str]
) -> list[SynthExample]:
    """Drop new examples whose intent already appears in the existing pool."""
    kept = []
    seen_in_new: set[str] = set()
    for ex in new:
        key = ex.intent.strip().lower()
        if key in existing_intents or key in seen_in_new:
            continue
        seen_in_new.add(key)
        kept.append(ex)
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter", default=None,
                        help="LoRA adapter dir; omit to bootstrap on raw base.")
    parser.add_argument("--intents", required=True,
                        help="JSONL with SynthExample-shaped rows (intent + expected_dsl).")
    parser.add_argument("--existing-train", default=None,
                        help="Optional reference JSONL for dedup against original training pool.")
    parser.add_argument("--out", required=True,
                        help="Where to write bootstrap.jsonl.")
    parser.add_argument("--samples-per-intent", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional cap on number of intents (smoke).")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    catalog = ingest_schema(args.catalog)
    intents = read_jsonl(Path(args.intents))
    if args.limit:
        intents = intents[: args.limit]
    print(f"[bootstrap] catalog={catalog.name} intents={len(intents)} "
          f"N={args.samples_per_intent} T={args.temperature}")

    if args.adapter:
        model, tokenizer = load_lora_for_inference(
            args.adapter, base_model=args.base_model,
        )
    else:
        model, tokenizer = load_base_for_inference(args.base_model)

    existing_intents: set[str] = set()
    if args.existing_train:
        for ex in read_jsonl(Path(args.existing_train)):
            existing_intents.add(ex.intent.strip().lower())
        print(f"[bootstrap] existing-train pool: {len(existing_intents)} unique intents")

    kept: list[SynthExample] = []
    n_attempted = 0
    n_parse_fail = 0
    n_match = 0
    n_no_match = 0

    started = time.perf_counter()
    for i, ex in enumerate(intents):
        try:
            expected_dict = json.loads(ex.expected_dsl)
        except json.JSONDecodeError:
            continue

        for sample_idx in range(args.samples_per_intent):
            n_attempted += 1
            try:
                raw = generate_dsl(
                    model, tokenizer, catalog, ex.intent,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                )
            except Exception:
                n_parse_fail += 1
                continue

            try:
                plan = catalog.parse_json_dsl(raw)
            except DSLValidationError:
                n_parse_fail += 1
                continue

            plan_jsonable = plan.to_jsonable()
            if plan_jsonable == expected_dict:
                # Canonical form: post-correction-completed plan re-serialized.
                # This is what we want the next SFT to imitate.
                canonical = json.dumps(
                    plan_jsonable, ensure_ascii=False, sort_keys=True,
                )
                kept.append(SynthExample(
                    intent=ex.intent,
                    expected_dsl=canonical,
                    strategy=f"bootstrap:s{sample_idx}",
                ))
                n_match += 1
                # Stop after first match per intent — we only need one canonical
                # paraphrase per intent. Skipping the rest saves wall time.
                break
            else:
                n_no_match += 1

        _release_memory()
        if (i + 1) % 25 == 0:
            elapsed = time.perf_counter() - started
            kept_so_far = len(kept)
            print(
                f"[bootstrap] intent {i + 1}/{len(intents)}  "
                f"kept={kept_so_far}  match-rate={kept_so_far / (i + 1):.1%}  "
                f"elapsed={elapsed:.0f}s",
                flush=True,
            )

    # Dedup vs existing pool
    pre_dedupe = len(kept)
    if existing_intents:
        kept = _exact_string_dedupe(kept, existing_intents)

    # Write bootstrap.jsonl
    write_jsonl(kept, out_path)

    elapsed = time.perf_counter() - started
    print()
    print("=" * 60)
    print("Self-bootstrap result")
    print("=" * 60)
    print(f"intents:                {len(intents)}")
    print(f"samples_attempted:      {n_attempted}")
    print(f"  parse-fail:           {n_parse_fail}")
    print(f"  parsed-but-wrong:     {n_no_match}")
    print(f"  parsed-and-match:     {n_match}")
    print(f"intents with ≥1 match:  {pre_dedupe}")
    print(f"after dedup vs train:   {len(kept)}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {out_path}")

    # Stats sidecar for §10/§12 reproducibility
    stats_path = out_path.with_name(out_path.stem + "_stats.json")
    stats_path.write_text(json.dumps({
        "catalog": catalog.name,
        "base_model": args.base_model,
        "adapter": args.adapter,
        "intents_path": str(Path(args.intents).resolve()),
        "n_intents": len(intents),
        "samples_per_intent": args.samples_per_intent,
        "temperature": args.temperature,
        "n_samples_attempted": n_attempted,
        "n_parse_fail": n_parse_fail,
        "n_no_match": n_no_match,
        "n_match": n_match,
        "n_intents_with_match": pre_dedupe,
        "n_kept_after_dedup": len(kept),
        "wall_seconds": round(elapsed, 1),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"stats:                  {stats_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
