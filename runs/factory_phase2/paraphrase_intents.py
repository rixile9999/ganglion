"""Teacher-side paraphrase generator for the S2c self-bootstrap pool.

Given a JSONL of (intent, expected_dsl) pairs, asks DashScope's
qwen3.6-plus to write N alternative phrasings that preserve the same
meaning. Each paraphrase is paired with the original ``expected_dsl``
(intent meaning unchanged → gold answer unchanged) and written out as a
SynthExample-shaped JSONL row, ready to feed ``self_bootstrap.py``.

Why this exists: a same-intent self-bootstrap is a no-op (every kept
sample dedups against the original train pool). The lift comes from the
model encountering *new phrasings* of intents it has already learned and
producing matching outputs — that's genuine new training signal.

Cost: ~$0.001 per intent at qwen3.6-plus pricing (~70 input tokens for
the prompt + ~150 output tokens for 3 paraphrases). 100 intents × 3
paraphrases ≈ $0.10 worst case. Negligible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ganglion.factory.customer.synth import (
    DashScopeTeacher,
    SynthExample,
    estimate_cost,
    read_jsonl,
    write_jsonl,
)


_PROMPT_SYSTEM = (
    "You generate alternative phrasings of a user's smart-home command. "
    "The paraphrases must preserve the *exact meaning* — same target room, "
    "same on/off intent, same brightness/color value if specified, same "
    "scene name. Mix Korean and English. Vary sentence length and "
    "politeness. Avoid trivial rewordings (don't just swap synonyms). "
    "Return JSON only: {\"paraphrases\": [\"...\", \"...\", ...]}"
)


def _build_user_prompt(intent: str, n: int) -> str:
    return (
        f"Original user command:\n{intent}\n\n"
        f"Write {n} alternative phrasings preserving the exact meaning. "
        f"Return JSON only with key \"paraphrases\"."
    )


def _parse_paraphrases(content: str) -> list[str]:
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return []
    arr = obj.get("paraphrases", [])
    if not isinstance(arr, list):
        return []
    return [str(p).strip() for p in arr if isinstance(p, str) and p.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intents", required=True,
                        help="JSONL with SynthExample rows (intent + expected_dsl).")
    parser.add_argument("--out", required=True,
                        help="Output JSONL path for paraphrased examples.")
    parser.add_argument("--n-per-intent", type=int, default=3,
                        help="Paraphrases per source intent (default 3).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional cap on source intents (for smoke).")
    parser.add_argument("--max-cost-usd", type=float, default=1.0,
                        help="Hard budget cap. Aborts when exceeded.")
    parser.add_argument("--teacher-model", default="qwen3.6-plus")
    parser.add_argument("--teacher-temperature", type=float, default=0.85)
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("DASHSCOPE_API_KEY is not set", file=sys.stderr)
        return 2

    intents = read_jsonl(Path(args.intents))
    if args.limit:
        intents = intents[: args.limit]

    teacher = DashScopeTeacher(
        model=args.teacher_model, temperature=args.teacher_temperature,
    )

    print(f"[paraphrase] source={args.intents} n_intents={len(intents)} "
          f"n_per_intent={args.n_per_intent} budget=${args.max_cost_usd}")

    out: list[SynthExample] = []
    in_toks_total = 0
    out_toks_total = 0
    cost = 0.0
    n_calls = 0
    n_empty = 0
    started = time.perf_counter()

    for i, ex in enumerate(intents):
        if cost >= args.max_cost_usd:
            print(f"[paraphrase] budget cap reached at intent {i}; stopping")
            break
        messages = [
            {"role": "system", "content": _PROMPT_SYSTEM},
            {"role": "user", "content": _build_user_prompt(ex.intent, args.n_per_intent)},
        ]
        try:
            content, in_toks, out_toks = teacher.call(messages)
        except Exception as e:
            print(f"[paraphrase] intent {i}: teacher error: {e}")
            continue
        n_calls += 1
        in_toks_total += in_toks
        out_toks_total += out_toks
        cost = estimate_cost(args.teacher_model, in_toks_total, out_toks_total)

        paraphrases = _parse_paraphrases(content)
        if not paraphrases:
            n_empty += 1
            continue
        for k, p in enumerate(paraphrases):
            out.append(SynthExample(
                intent=p,
                expected_dsl=ex.expected_dsl,
                strategy=f"paraphrase:s{i}:k{k}",
            ))

        if (i + 1) % 25 == 0:
            elapsed = time.perf_counter() - started
            print(f"[paraphrase] {i + 1}/{len(intents)}  "
                  f"calls={n_calls}  paraphrases={len(out)}  "
                  f"cost=${cost:.3f}  elapsed={elapsed:.0f}s",
                  flush=True)

    write_jsonl(out, Path(args.out))

    elapsed = time.perf_counter() - started
    print()
    print("=" * 60)
    print("Paraphrase generation result")
    print("=" * 60)
    print(f"intents processed:      {n_calls}")
    print(f"empty/parse-fail:       {n_empty}")
    print(f"paraphrases generated:  {len(out)}")
    print(f"input tokens:           {in_toks_total}")
    print(f"output tokens:          {out_toks_total}")
    print(f"cost:                   ${cost:.3f}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {args.out}")

    stats_path = Path(args.out).with_name(Path(args.out).stem + "_stats.json")
    stats_path.write_text(json.dumps({
        "source": str(Path(args.intents).resolve()),
        "n_intents": len(intents),
        "n_per_intent": args.n_per_intent,
        "n_calls": n_calls,
        "n_empty": n_empty,
        "n_paraphrases": len(out),
        "input_tokens": in_toks_total,
        "output_tokens": out_toks_total,
        "cost_usd": round(cost, 4),
        "wall_seconds": round(elapsed, 1),
        "teacher_model": args.teacher_model,
        "teacher_temperature": args.teacher_temperature,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
