"""S3a + S3b — paraphrase and teacher-synthesized BFCL training data.

S3a paraphrase  : K variants of intent per case, same ground_truth.
S3b synth      : N new (intent, ground_truth) pairs per category, same per-case
                 tool schema, different semantics.

Teacher is DashScope `qwen3.6-plus` (via `GANGLION_MODEL` override). Each
response is validated by re-parsing the resulting expected_dsl against the
per-case Catalog; invalid responses are dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
from bfcl_sft import (  # noqa: E402
    build_catalog,
    expected_dsl_from_ground_truth,
    split_train_holdout,
    SYSTEM_PROMPT_TEMPLATE,
)
from ganglion.bfcl.loader import BFCLCase, load_category


def _teacher() -> Any:
    from openai import OpenAI
    base_url = os.environ.get("DASHSCOPE_BASE_URL",
                              "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    return OpenAI(api_key=os.environ["DASHSCOPE_API_KEY"], base_url=base_url)


_TEACHER_MODEL = os.environ.get("GANGLION_TEACHER_MODEL", "qwen3.6-plus")


def _chat(client, system: str, user: str, *, temperature: float = 0.7,
          max_tokens: int = 600) -> str:
    response = client.chat.completions.create(
        model=_TEACHER_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


# --------------------- S3a paraphrase ---------------------

PARAPHRASE_SYS = (
    "You rephrase a user request without changing the underlying intent or any "
    "of the numerical / categorical values it contains. The same tool call must "
    "still answer the rephrased version. Return JSON: "
    '{"variants": ["...", "...", "..."]}.'
)


def _paraphrase_one(client, case: BFCLCase, k: int) -> list[str]:
    expected = expected_dsl_from_ground_truth(case)
    user_prompt = (
        f"Original request: {case.user_message}\n\n"
        f"Expected tool call (do NOT change the values, just say it differently in English):\n"
        f"{expected}\n\n"
        f"Produce {k} distinct paraphrases of the original request that map to the SAME tool call."
    )
    for _ in range(2):  # 1 retry
        try:
            raw = _chat(client, PARAPHRASE_SYS, user_prompt, temperature=0.7, max_tokens=400)
            obj = json.loads(raw)
            variants = obj.get("variants") or []
            cleaned = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
            if cleaned:
                return cleaned[:k]
        except Exception:
            time.sleep(0.5)
    return []


def run_paraphrase(category: str, out_dir: Path, k: int = 4, max_workers: int = 6) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_category(category)[:100]
    train_cases, _ = split_train_holdout(cases)
    client = _teacher()

    stats = {"category": category, "k_per_case": k, "valid_rows": 0,
             "filtered_rows": 0, "cases_attempted": len(train_cases)}
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_paraphrase_one, client, c, k): c for c in train_cases}
        for fut in as_completed(futures):
            case = futures[fut]
            try:
                variants = fut.result()
            except Exception:
                continue
            try:
                catalog = build_catalog(case)
                expected = expected_dsl_from_ground_truth(case)
                catalog.parse_json_dsl(expected)  # must parse
            except Exception:
                stats["filtered_rows"] += len(variants)
                continue
            for v in variants:
                rows.append({
                    "case_id": case.id,
                    "category": case.category,
                    "user_message": v,
                    "ground_truth": list(case.ground_truth) if case.ground_truth else None,
                    "origin": "paraphrase",
                })
                stats["valid_rows"] += 1

    (out_dir / "paraphrased.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"[paraphrase:{category}] valid_rows={stats['valid_rows']} "
          f"filtered={stats['filtered_rows']} attempted={stats['cases_attempted']}")


# --------------------- S3b teacher synth ---------------------

SYNTH_SYS = (
    "You generate new BFCL-style benchmark cases that test the SAME tool schema "
    "but with DIFFERENT semantic intent (different numbers, different entities, "
    "different scenarios). Return JSON: "
    '{"intent": "...", "call": {"action": "<fn_name>", "args": {...}}}. '
    "All args MUST satisfy the tool's parameter spec. Do NOT include extra args "
    "outside the spec. Use plausible English."
)


def _synth_one(client, case: BFCLCase) -> dict | None:
    catalog = build_catalog(case)
    user_prompt = (
        f"Tool catalog (single tool, schema shown below):\n{catalog.render_json_dsl()}\n\n"
        f"Existing example intent: {case.user_message}\n\n"
        f"Generate ONE new (intent, call) pair using the same tool, with different values."
    )
    for _ in range(2):
        try:
            raw = _chat(client, SYNTH_SYS, user_prompt, temperature=0.9, max_tokens=400)
            obj = json.loads(raw)
            intent = obj.get("intent")
            call = obj.get("call")
            if not (isinstance(intent, str) and isinstance(call, dict)):
                continue
            # Validate the call by parsing it as DSL
            dsl_str = json.dumps({"calls": [call]}, ensure_ascii=False)
            catalog.parse_json_dsl(dsl_str)
            # Build BFCL-style ground_truth from the call args.
            args = call.get("args", {})
            gt = {call["action"]: {k: [v] for k, v in args.items()}}
            return {
                "case_id": f"{case.id}_synth",
                "category": case.category,
                "user_message": intent,
                "ground_truth": [gt],
                "origin": "synth",
                "parent_case_id": case.id,
                "tool": list(case.tools),
            }
        except Exception:
            time.sleep(0.5)
    return None


def run_synth(category: str, out_dir: Path, n: int = 50, max_workers: int = 6) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_category(category)[:100]
    train_cases, _ = split_train_holdout(cases)
    # Pick parents deterministically so re-runs are stable.
    rng = random.Random(42)
    pool = list(train_cases)
    rng.shuffle(pool)
    parents = (pool * ((n // len(pool)) + 1))[:n]

    client = _teacher()
    rows: list[dict] = []
    stats = {"category": category, "attempted": len(parents), "valid_rows": 0, "filtered": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_synth_one, client, c) for c in parents]
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception:
                stats["filtered"] += 1
                continue
            if r is None:
                stats["filtered"] += 1
            else:
                rows.append(r)
                stats["valid_rows"] += 1

    (out_dir / "synth.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"[synth:{category}] valid_rows={stats['valid_rows']} filtered={stats['filtered']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["paraphrase", "synth"])
    p.add_argument("--category", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("-k", type=int, default=4, help="K variants per case (paraphrase)")
    p.add_argument("-n", type=int, default=50, help="N new cases (synth)")
    args = p.parse_args()

    out = Path(args.out)
    if args.mode == "paraphrase":
        run_paraphrase(args.category, out, k=args.k)
    else:
        run_synth(args.category, out, n=args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
