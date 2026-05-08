"""Generate DPO preference pairs by sampling the trained model and scoring
each completion with the verifier-graded reward.

Pipeline per intent:
  1. Sample N completions at temperature T from (base + adapter).
  2. Parse each via Catalog (post-correction applied automatically).
  3. Score each parsed plan against gold via graded_score (0.0-1.0).
  4. winner = max-score sample, loser = min-score sample.
  5. Keep the pair iff (winner_score - loser_score) >= --min-margin.
     Below the margin, the signal-to-noise of DPO drops sharply.
  6. Emit one row per intent: {prompt, chosen, rejected, win_score, lose_score}.

Output is TRL-compatible (loadable with `datasets.load_dataset("json", ...)`
into a DPOTrainer's expected schema). See dpo_train.py for the consumer.

Cost: pure local GPU/CPU inference. No API calls.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Any

from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.eval.metrics import graded_score
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import read_jsonl
from ganglion.factory.customer.train_lora import (
    SYSTEM_PROMPT_TEMPLATE,
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


def _format_chat_prompt(tokenizer, catalog, user_intent: str) -> str:
    """Render the same system+user chat the training path uses, returning
    a string suitable as DPO's `prompt` field.

    DPO loss conditions on this prompt; chosen/rejected are completion
    strings only (do NOT prepend the prompt to them).
    """
    system = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_intent},
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter", default=None,
                        help="LoRA adapter dir; required for non-trivial sampling.")
    parser.add_argument("--intents", required=True,
                        help="JSONL with SynthExample rows (intent + expected_dsl).")
    parser.add_argument("--out", required=True,
                        help="Output JSONL (DPO-shaped: prompt/chosen/rejected/...).")
    parser.add_argument("--samples-per-intent", type=int, default=8,
                        help="DPO standard 8; lower for smoke (cost ∝ N).")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--min-margin", type=float, default=0.5,
                        help="Keep only pairs with score gap ≥ this. 0.5 is a "
                        "good default for our 0.0/0.25/0.5+/1.0 gradient.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap source intents (smoke).")
    args = parser.parse_args()

    catalog = ingest_schema(args.catalog)
    intents = read_jsonl(Path(args.intents))
    if args.limit:
        intents = intents[: args.limit]
    print(f"[dpo_pairs] catalog={catalog.name} intents={len(intents)} "
          f"N={args.samples_per_intent} T={args.temperature} "
          f"min_margin={args.min_margin}")

    if args.adapter:
        model, tokenizer = load_lora_for_inference(
            args.adapter, base_model=args.base_model,
        )
    else:
        model, tokenizer = load_base_for_inference(args.base_model)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_pairs_kept = 0
    n_below_margin = 0
    n_no_variance = 0
    score_diffs: list[float] = []
    score_distribution: dict[str, int] = {
        "0.0": 0, "0.25": 0, "0.5+": 0, "1.0": 0,
    }
    pairs: list[dict[str, Any]] = []
    started = time.perf_counter()

    for i, ex in enumerate(intents):
        try:
            expected_plan = catalog.parse_json_dsl(ex.expected_dsl)
        except DSLValidationError:
            continue

        # Sample N completions
        samples: list[tuple[str, float]] = []  # (raw_text, score)
        for _ in range(args.samples_per_intent):
            try:
                raw = generate_dsl(
                    model, tokenizer, catalog, ex.intent,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                )
            except Exception:
                samples.append(("", 0.0))
                continue

            try:
                pred_plan = catalog.parse_json_dsl(raw, prompt=ex.intent)
            except DSLValidationError:
                samples.append((raw, 0.0))
                continue
            score = graded_score(pred_plan, expected_plan)
            samples.append((raw, score))

            # Distribution tracking (rough buckets)
            if score == 0.0:
                score_distribution["0.0"] += 1
            elif score == 0.25:
                score_distribution["0.25"] += 1
            elif score >= 1.0:
                score_distribution["1.0"] += 1
            else:
                score_distribution["0.5+"] += 1

        # Pick winner / loser
        sorted_samples = sorted(samples, key=lambda x: x[1])
        winner_text, winner_score = sorted_samples[-1]
        loser_text, loser_score = sorted_samples[0]
        margin = winner_score - loser_score

        if margin < 1e-9:
            n_no_variance += 1
        elif margin < args.min_margin:
            n_below_margin += 1
        else:
            prompt = _format_chat_prompt(tokenizer, catalog, ex.intent)
            pairs.append({
                "prompt": prompt,
                "chosen": winner_text,
                "rejected": loser_text,
                "winner_score": winner_score,
                "loser_score": loser_score,
                "margin": margin,
                "intent": ex.intent,
            })
            score_diffs.append(margin)
            n_pairs_kept += 1

        _release_memory()
        if (i + 1) % 25 == 0:
            elapsed = time.perf_counter() - started
            print(f"[dpo_pairs] {i + 1}/{len(intents)}  pairs={n_pairs_kept}  "
                  f"below-margin={n_below_margin}  no-variance={n_no_variance}  "
                  f"elapsed={elapsed:.0f}s",
                  flush=True)

    # Write pairs
    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    elapsed = time.perf_counter() - started
    print()
    print("=" * 60)
    print("DPO pair generation result")
    print("=" * 60)
    print(f"intents:                {len(intents)}")
    print(f"pairs kept:             {n_pairs_kept}")
    print(f"below-margin dropped:   {n_below_margin}")
    print(f"no-variance dropped:    {n_no_variance}")
    if score_diffs:
        avg_margin = sum(score_diffs) / len(score_diffs)
        print(f"avg kept margin:        {avg_margin:.3f}")
    print(f"sample score buckets:")
    for k, v in score_distribution.items():
        print(f"  {k:>5}:  {v}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {out_path}")

    stats_path = out_path.with_name(out_path.stem + "_stats.json")
    stats_path.write_text(json.dumps({
        "catalog": catalog.name,
        "base_model": args.base_model,
        "adapter": args.adapter,
        "intents_path": str(Path(args.intents).resolve()),
        "n_intents": len(intents),
        "samples_per_intent": args.samples_per_intent,
        "temperature": args.temperature,
        "min_margin": args.min_margin,
        "n_pairs_kept": n_pairs_kept,
        "n_below_margin": n_below_margin,
        "n_no_variance": n_no_variance,
        "score_distribution": score_distribution,
        "avg_margin": (sum(score_diffs) / len(score_diffs)) if score_diffs else None,
        "wall_seconds": round(elapsed, 1),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"stats:                  {stats_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
