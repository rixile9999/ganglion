"""OOD-targeted paraphrase generator for the S3 DPO data blocker.

The S2c paraphrase pool used same-meaning paraphrases drawn from a near-
distribution teacher prompt. The v2 adapter trained on those + train.jsonl
is now ~85% saturated on the same pool, leaving ~6% pair yield for DPO.

This script generates paraphrases that intentionally drift OFF the train
distribution while preserving meaning exactly: oblique phrasings, slang,
code-switched Korean-English, compound trailing commands, unusual numeric
wordings. These should produce a wider distribution of model outputs at
T=1.0-1.2, which is what DPO needs to mine (chosen, rejected) pairs.

Usage:
    python runs/factory_phase2/paraphrase_ood.py \
        --intents runs/factory_phase2/sft_0.6B_v2/iot_light_5/train.jsonl \
        --out runs/factory_phase2/ood_iot_light_5.jsonl \
        --n-per-intent 3
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
    "You generate paraphrases of a smart-home command that mean EXACTLY the "
    "same thing as the original but use SURFACE FORMS that are unusual or "
    "rare in typical training data.\n\n"
    "MANDATORY meaning invariance — the paraphrase must keep:\n"
    "  - the same target room (거실/living, 침실/bedroom, 주방/kitchen, etc.)\n"
    "  - the same on/off intent\n"
    "  - the exact same numeric values (brightness percent, time, color temp)\n"
    "  - the same scene name if any\n"
    "  - the same action type (set vs schedule vs query vs list vs create_scene)\n\n"
    "REQUIRED variation strategies (mix several across the paraphrases):\n"
    "  - oblique / situational phrasing  (\"I'm headed to bed\" → turn off bedroom)\n"
    "  - compound trailing or leading commands  (\"and while you're at it…\")\n"
    "  - unusual numeric wordings  (\"quarter past nine\", \"max brightness\", \"열 시 십오 분\")\n"
    "  - technical / dev-speak  (\"set state=on brightness=80 room=kitchen\")\n"
    "  - long-winded honorific Korean  (해 주실 수 있을까요? 형식)\n"
    "  - very casual / slang  (\"yo flick the kitchen light\", \"불 좀 까줘 ㅋㅋ\")\n"
    "  - mid-sentence Korean↔English code-switch\n"
    "  - rhetorical-question form for query/list intents\n\n"
    "Avoid: trivial synonym swaps, identical sentence structure to the original.\n"
    "Each of the N paraphrases should use a different strategy if possible.\n\n"
    "Return JSON only: {\"paraphrases\": [\"...\", \"...\", ...]}"
)


def _build_user_prompt(intent: str, n: int) -> str:
    return (
        f"Original command:\n{intent}\n\n"
        f"Write {n} OOD paraphrases preserving the EXACT meaning. "
        f"Each must use a different variation strategy from the list. "
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
    parser.add_argument("--intents", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n-per-intent", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    parser.add_argument("--teacher-model", default="qwen3.6-plus")
    parser.add_argument("--teacher-temperature", type=float, default=1.0,
                        help="Higher than S2c (0.85) to push variety further.")
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

    print(f"[paraphrase_ood] source={args.intents} n_intents={len(intents)} "
          f"n_per_intent={args.n_per_intent} T={args.teacher_temperature} "
          f"budget=${args.max_cost_usd}")

    out: list[SynthExample] = []
    in_toks_total = 0
    out_toks_total = 0
    cost = 0.0
    n_calls = 0
    n_empty = 0
    started = time.perf_counter()

    for i, ex in enumerate(intents):
        if cost >= args.max_cost_usd:
            print(f"[paraphrase_ood] budget cap reached at intent {i}; stopping")
            break
        messages = [
            {"role": "system", "content": _PROMPT_SYSTEM},
            {"role": "user", "content": _build_user_prompt(ex.intent, args.n_per_intent)},
        ]
        try:
            content, in_toks, out_toks = teacher.call(messages)
        except Exception as e:
            print(f"[paraphrase_ood] intent {i}: teacher error: {e}")
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
                strategy=f"ood_paraphrase:s{i}:k{k}",
            ))

        if (i + 1) % 25 == 0:
            elapsed = time.perf_counter() - started
            print(f"[paraphrase_ood] {i + 1}/{len(intents)}  "
                  f"calls={n_calls}  paraphrases={len(out)}  "
                  f"cost=${cost:.3f}  elapsed={elapsed:.0f}s",
                  flush=True)

    write_jsonl(out, Path(args.out))

    elapsed = time.perf_counter() - started
    print()
    print("=" * 60)
    print("OOD paraphrase generation result")
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
        "mode": "ood",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
