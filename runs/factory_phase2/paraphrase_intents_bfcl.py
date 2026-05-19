"""S1' — Paraphrase BFCL user messages while preserving per-case catalogs.

For each row in `examples/bfcl/v4/train/<category>.jsonl`, ask the DashScope
qwen3.6-plus teacher for N alternative phrasings of `question[0][-1].content`
that preserve the same meaning. Emit one new BFCL-native row per paraphrase
with:

* `id`        = `<orig_id>_p<k>`
* `question`  = original turn list but with the user content replaced
* `function`  = unchanged (per-case catalog is bound to the original case)
* `ground_truth` = unchanged when present (irrelevance has none)

Output is a single `synth.jsonl` that the existing `ganglion.bfcl.loader`
reads without modification — the augmented corpus is just "BFCL-shaped train
data with more phrasings."

Why this differs from IoT's `paraphrase_intents.py`: the IoT version reads
flat SynthExample rows (intent + expected_dsl) because IoT shares one
catalog across all cases. BFCL ships a tool list per case, so we keep the
native record shape and paraphrase only the user-visible turn. Downstream
SFT loads via `load_cases()` and renders the catalog from `row['function']`
exactly as the eval runner does.

Cost: ~$0.001 per source case at qwen3.6-plus pricing. 740 cases × K=3
paraphrases ≈ $0.74 worst case.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from ganglion.factory.customer.synth import DashScopeTeacher, estimate_cost


CATEGORIES = (
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
)


_PROMPT_SYSTEM = (
    "You generate alternative phrasings of a user's request for a tool-using "
    "AI assistant. The paraphrases must preserve the EXACT meaning — same "
    "intent, same numbers, same units, same entities, same constraints. "
    "Same language as the original (do NOT translate). Vary sentence "
    "structure, formality, and length but do not add or remove information. "
    "Do not invent new constraints. Return JSON only: "
    '{"paraphrases": ["...", "...", ...]}.'
)


def _build_user_prompt(user_message: str, n: int) -> str:
    return (
        f"Original user request:\n{user_message}\n\n"
        f"Write {n} alternative phrasings preserving the exact meaning. "
        f'Return JSON only with key "paraphrases".'
    )


def _parse_paraphrases(content: str) -> list[str]:
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return []
    arr = obj.get("paraphrases", [])
    if not isinstance(arr, list):
        return []
    out: list[str] = []
    for p in arr:
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
    return out


def _read_train_rows(train_root: Path, categories: Iterable[str]) -> list[dict]:
    rows: list[dict] = []
    for cat in categories:
        path = train_root / f"{cat}.jsonl"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def _user_message(row: dict) -> str | None:
    turn = row.get("question", [[]])
    if not turn:
        return None
    msgs = turn[0]
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


def _emit_paraphrase_row(orig: dict, paraphrase: str, k: int) -> dict:
    """Clone the BFCL row, replace only the user message and the id."""
    new_row = deepcopy(orig)
    new_row["id"] = f"{orig['id']}_p{k}"
    new_row["question"] = [
        [
            {"role": m.get("role", "user"), "content": paraphrase if m.get("role") == "user" else m.get("content", "")}
            for m in new_row["question"][0]
        ]
    ]
    return new_row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-root",
        default="examples/bfcl/v4/train",
        help="Directory containing <category>.jsonl files (output of build_train.py).",
    )
    parser.add_argument(
        "--out",
        default="examples/bfcl/v4/train/synth.jsonl",
        help="Output JSONL path. Stats sibling will be written too.",
    )
    parser.add_argument("--n-per-intent", type=int, default=3)
    parser.add_argument(
        "--categories",
        default=",".join(CATEGORIES),
        help="Comma-separated list of categories to paraphrase.",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap on source cases (for smoke). Categories share this pool.")
    parser.add_argument("--max-cost-usd", type=float, default=1.5,
                        help="Hard budget cap.")
    parser.add_argument("--teacher-model", default="qwen3.6-plus")
    parser.add_argument("--teacher-temperature", type=float, default=0.85)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("DASHSCOPE_API_KEY is not set", file=sys.stderr)
        return 2

    categories = [c for c in args.categories.split(",") if c.strip()]
    for c in categories:
        if c not in CATEGORIES:
            print(f"unknown category: {c!r}", file=sys.stderr)
            return 2

    train_root = Path(args.train_root)
    rows = _read_train_rows(train_root, categories)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print(f"no rows found under {train_root} for {categories}", file=sys.stderr)
        return 2

    teacher = DashScopeTeacher(
        model=args.teacher_model,
        temperature=args.teacher_temperature,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path = out_path.with_name(out_path.stem + "_stats.json")

    print(
        f"[paraphrase_bfcl] source={train_root} "
        f"n_rows={len(rows)} k={args.n_per_intent} "
        f"categories={categories} budget=${args.max_cost_usd}",
        flush=True,
    )

    in_toks_total = 0
    out_toks_total = 0
    n_calls = 0
    n_empty = 0
    n_paraphrases = 0
    cost = 0.0
    started = time.perf_counter()

    by_cat_counts: dict[str, int] = {c: 0 for c in categories}

    with out_path.open("w", encoding="utf-8") as fh:
        for i, row in enumerate(rows):
            if cost >= args.max_cost_usd:
                print(f"[paraphrase_bfcl] budget cap reached at row {i}; stopping",
                      flush=True)
                break
            # Longest prefix wins — otherwise "parallel_multiple_*" matches
            # the "parallel_" prefix first and the counter under-reports.
            cat = "?"
            for known in sorted(CATEGORIES, key=len, reverse=True):
                if row["id"].startswith(known + "_"):
                    cat = known
                    break

            user_msg = _user_message(row)
            if not user_msg:
                continue

            messages = [
                {"role": "system", "content": _PROMPT_SYSTEM},
                {"role": "user", "content": _build_user_prompt(user_msg, args.n_per_intent)},
            ]
            try:
                content, in_toks, out_toks = teacher.call(messages)
            except Exception as exc:
                print(f"[paraphrase_bfcl] row {i} ({row['id']}): teacher error: {exc}",
                      flush=True)
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
                if p.strip() == user_msg.strip():
                    continue
                new_row = _emit_paraphrase_row(row, p, k)
                fh.write(json.dumps(new_row, ensure_ascii=False) + "\n")
                n_paraphrases += 1
                by_cat_counts[cat] = by_cat_counts.get(cat, 0) + 1

            if (i + 1) % args.progress_every == 0:
                elapsed = time.perf_counter() - started
                print(
                    f"[paraphrase_bfcl] {i + 1}/{len(rows)}  calls={n_calls}  "
                    f"paraphrases={n_paraphrases}  cost=${cost:.3f}  "
                    f"elapsed={elapsed:.0f}s",
                    flush=True,
                )

    elapsed = time.perf_counter() - started

    stats = {
        "source": str(train_root.resolve()),
        "categories": categories,
        "n_source_rows": len(rows),
        "n_per_intent": args.n_per_intent,
        "n_calls": n_calls,
        "n_empty": n_empty,
        "n_paraphrases": n_paraphrases,
        "by_category": by_cat_counts,
        "input_tokens": in_toks_total,
        "output_tokens": out_toks_total,
        "cost_usd": round(cost, 4),
        "wall_seconds": round(elapsed, 1),
        "teacher_model": args.teacher_model,
        "teacher_temperature": args.teacher_temperature,
    }
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 60)
    print("BFCL paraphrase generation result")
    print("=" * 60)
    print(f"source rows:            {len(rows)}")
    print(f"teacher calls:          {n_calls}")
    print(f"empty/parse-fail:       {n_empty}")
    print(f"paraphrases generated:  {n_paraphrases}")
    print(f"by category:            {by_cat_counts}")
    print(f"input tokens:           {in_toks_total}")
    print(f"output tokens:          {out_toks_total}")
    print(f"cost:                   ${cost:.3f}")
    print(f"wall:                   {elapsed:.0f}s")
    print(f"output:                 {out_path}")
    print(f"stats:                  {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
