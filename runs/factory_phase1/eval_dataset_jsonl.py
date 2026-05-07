"""Evaluate a trained LoRA against the curated dataset.jsonl human queries.

Unlike synth holdout, this dataset is human-curated and predates Phase 1
synthesis — so passing here demonstrates the trained model handles the
*real* user-query distribution, not just teacher-shaped paraphrases.

Same eval_report.{json,md} format as smoke_train_eval.py.

Usage:
    python runs/factory_phase1/eval_dataset_jsonl.py \\
        --catalog smart_home_50 \\
        --adapter runs/factory_phase1/smart_home_50/holdout_eval/adapter \\
        --dataset examples/iot_light/dataset.jsonl \\
        --out runs/factory_phase1/smart_home_50/dataset_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ganglion.factory.customer.eval import (
    EvalConfig,
    evaluate_lora,
    write_report,
)
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import SynthExample
from ganglion.factory.customer.train_lora import load_lora_for_inference


def load_dataset_jsonl(path: Path, *, limit: int | None = None) -> list[SynthExample]:
    """Load dataset.jsonl rows as SynthExample (intent + expected DSL)."""
    rows: list[SynthExample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        intent = obj["prompt"]
        expected = obj["expected"]
        # dataset.jsonl stores expected as dict; eval expects str
        if isinstance(expected, dict):
            expected = json.dumps(expected, ensure_ascii=False, sort_keys=True)
        rows.append(
            SynthExample(
                intent=intent,
                expected_dsl=expected,
                strategy=f"dataset:{obj.get('id', 'anon')}",
            )
        )
        if limit and len(rows) >= limit:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--adapter", required=True, help="Path to LoRA adapter dir")
    parser.add_argument("--dataset", default="examples/iot_light/dataset.jsonl")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional: only eval first N cases (for quick smoke)")
    parser.add_argument("--base-model", default="Qwen/Qwen3-1.7B")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = ingest_schema(args.catalog)
    examples = load_dataset_jsonl(Path(args.dataset), limit=args.limit)
    print(f"[dataset_eval] catalog={catalog.name} cases={len(examples)}")
    print(f"[dataset_eval] adapter={args.adapter}")

    model, tokenizer = load_lora_for_inference(args.adapter, base_model=args.base_model)

    summary, results = evaluate_lora(
        catalog, examples, model, tokenizer, config=EvalConfig()
    )
    # Override "per_strategy" — for dataset eval there's no useful strategy split,
    # so collapse to a single "dataset" bucket for reporting.
    n = len(examples)
    summary["per_strategy"] = {
        "dataset.jsonl": {
            "n": n,
            "syntax_valid": summary["syntax_valid_rate"],
            "action_match": summary["action_match_rate"],
            "exact_match": summary["exact_match_rate"],
        }
    }
    write_report(
        summary, results, out_dir,
        catalog_name=catalog.name, n_train=0, n_holdout=len(examples),
    )

    print()
    print("=" * 60)
    print("Dataset.jsonl eval result")
    print("=" * 60)
    print(f"cases:               {len(examples)}")
    print(f"syntax_valid_rate:   {summary['syntax_valid_rate']:.1%}")
    print(f"action_match_rate:   {summary['action_match_rate']:.1%}")
    print(f"exact_match_rate:    {summary['exact_match_rate']:.1%}")
    if summary.get("latency_ms_p50") is not None:
        print(f"latency P50:         {summary['latency_ms_p50']:.0f} ms")
        print(f"latency P95:         {summary['latency_ms_p95']:.0f} ms")
    print(f"Report: {out_dir}/eval_report.md")

    threshold = 0.80  # external-distribution threshold
    decision = "PASS" if summary["exact_match_rate"] >= threshold else "FAIL"
    print(f"Decision (exact_match >= {threshold:.0%}): {decision}")
    return 0 if summary["exact_match_rate"] >= threshold else 1


if __name__ == "__main__":
    sys.exit(main())
