"""A/B grammar-masking ablation on a single (model, catalog) configuration.

Loads the model+tokenizer once, compiles the grammar once, and evaluates
``dataset.jsonl`` twice — with and without the XGrammar logits processor.
Writes mask_off / mask_on / ablation_report side-by-side under ``--out``.

Designed to be run on a GPU box; the local Mac can syntax-check it but
not execute (no CUDA, no model weights). The script is the headline
deliverable for Phase 2 A3 — it produces the data point that decides
whether Arc A (0.6B max-out) is viable.

Examples:
    # Untuned 0.6B base on iot_light_5 — Arc A decision data point:
    python runs/factory_phase2/grammar_ablation.py \\
        --catalog iot_light_5 \\
        --base-model Qwen/Qwen3-0.6B \\
        --out runs/factory_phase2/grammar_ablation/qwen3-0.6B-iot_light_5

    # Phase 1 1.7B+LoRA on smart_home_50 — checks 6.2% syntax gap closes:
    python runs/factory_phase2/grammar_ablation.py \\
        --catalog smart_home_50 \\
        --base-model Qwen/Qwen3-1.7B \\
        --adapter runs/factory_phase1/smart_home_50/holdout_eval/adapter \\
        --out runs/factory_phase2/grammar_ablation/1.7B-lora-smart_home_50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ganglion.factory.customer.eval import (
    EvalConfig,
    evaluate_lora,
    write_report,
)
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.synth import SynthExample
from ganglion.factory.customer.train_lora import (
    load_base_for_inference,
    load_lora_for_inference,
)
from ganglion.factory.grammar import compile_catalog_grammar


def load_dataset_jsonl(path: Path, *, limit: int | None = None) -> list[SynthExample]:
    rows: list[SynthExample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        expected = obj["expected"]
        if isinstance(expected, dict):
            expected = json.dumps(expected, ensure_ascii=False, sort_keys=True)
        rows.append(
            SynthExample(
                intent=obj["prompt"],
                expected_dsl=expected,
                strategy=f"dataset:{obj.get('id', 'anon')}",
            )
        )
        if limit and len(rows) >= limit:
            break
    return rows


def _run_one(label: str, *, catalog, examples, model, tokenizer,
             compiled_grammar, out_dir: Path) -> dict:
    print(f"[ablation] === {label} (n={len(examples)}) ===")
    started = time.perf_counter()
    summary, results = evaluate_lora(
        catalog, examples, model, tokenizer,
        config=EvalConfig(compiled_grammar=compiled_grammar),
    )
    elapsed = time.perf_counter() - started
    summary["wall_seconds"] = round(elapsed, 1)
    summary["per_strategy"] = {
        "dataset.jsonl": {
            "n": len(examples),
            "syntax_valid": summary["syntax_valid_rate"],
            "action_match": summary["action_match_rate"],
            "exact_match":  summary["exact_match_rate"],
        }
    }

    sub = out_dir / label
    sub.mkdir(parents=True, exist_ok=True)
    write_report(summary, results, sub,
                 catalog_name=catalog.name, n_train=0, n_holdout=len(examples))
    print(f"[ablation] {label}: "
          f"syntax {summary['syntax_valid_rate']:.1%} / "
          f"action {summary['action_match_rate']:.1%} / "
          f"exact {summary['exact_match_rate']:.1%}  "
          f"({elapsed:.0f}s)")
    return summary


def _write_ablation_report(off: dict, on: dict, out_dir: Path,
                            *, catalog_name: str, base_model: str,
                            adapter: str | None, n: int) -> None:
    def pct(d: dict, k: str) -> str:
        v = d.get(k)
        return f"{v:.1%}" if isinstance(v, (int, float)) else "n/a"

    def delta(metric: str) -> str:
        a, b = off.get(metric), on.get(metric)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return ""
        d = (b - a) * 100
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.1f}pp"

    lines = [
        f"# Grammar masking ablation — {catalog_name}",
        "",
        f"- base model: `{base_model}`",
        f"- adapter:    `{adapter or 'none (untuned base)'}`",
        f"- dataset:    n={n}",
        "",
        "## Headline",
        "",
        "| metric | mask off | mask on | Δ |",
        "|---|---:|---:|---:|",
        f"| syntax_valid_rate | {pct(off, 'syntax_valid_rate')} | {pct(on, 'syntax_valid_rate')} | {delta('syntax_valid_rate')} |",
        f"| action_match_rate | {pct(off, 'action_match_rate')} | {pct(on, 'action_match_rate')} | {delta('action_match_rate')} |",
        f"| exact_match_rate  | {pct(off, 'exact_match_rate')} | {pct(on, 'exact_match_rate')} | {delta('exact_match_rate')} |",
        "",
        "## Latency",
        "",
        "| metric | mask off | mask on |",
        "|---|---:|---:|",
        f"| latency P50 (ms) | {off.get('latency_ms_p50', 'n/a')} | {on.get('latency_ms_p50', 'n/a')} |",
        f"| latency P95 (ms) | {off.get('latency_ms_p95', 'n/a')} | {on.get('latency_ms_p95', 'n/a')} |",
        f"| wall seconds     | {off.get('wall_seconds', 'n/a')} | {on.get('wall_seconds', 'n/a')} |",
        "",
        "## Verdict",
        "",
    ]
    syn_off = off.get("syntax_valid_rate") or 0
    syn_on = on.get("syntax_valid_rate") or 0
    if syn_on >= 0.999:
        lines.append("- syntax_valid_rate hit 100% with masking on — A3 contract met.")
    else:
        lines.append(
            f"- syntax_valid_rate {syn_off:.1%} → {syn_on:.1%}: "
            "masking did not reach 100%. Investigate: tokenizer vocab_size mismatch? "
            "grammar covering all paths?"
        )
    exa_off = off.get("exact_match_rate") or 0
    exa_on = on.get("exact_match_rate") or 0
    lines.append(f"- exact_match Δ = {(exa_on - exa_off) * 100:+.1f}pp")
    if base_model.lower().endswith("0.6b"):
        if exa_on >= 0.60:
            lines.append("- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.")
        elif exa_on >= 0.50:
            lines.append("- 0.6B + masking 50–60% → marginal Arc A; weigh against Arc B effort.")
        else:
            lines.append("- 0.6B + masking <50% → capacity floor likely, lean Arc B (1.7B polish).")
    lines.append("")
    (out_dir / "ablation_report.md").write_text("\n".join(lines), encoding="utf-8")

    summary_path = out_dir / "ablation_summary.json"
    summary_path.write_text(json.dumps({
        "catalog": catalog_name,
        "base_model": base_model,
        "adapter": adapter,
        "n": n,
        "mask_off": off,
        "mask_on": on,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True,
                        help="Catalog tier: iot_light_5 | home_iot_20 | smart_home_50")
    parser.add_argument("--base-model", required=True,
                        help="HF model ID (e.g. Qwen/Qwen3-0.6B, Qwen/Qwen3-1.7B)")
    parser.add_argument("--adapter", default=None,
                        help="Optional LoRA adapter dir; omit for untuned base")
    parser.add_argument("--dataset", default="examples/iot_light/dataset.jsonl")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional: only eval first N cases")
    parser.add_argument("--no-bf16", action="store_true",
                        help="Use float32 instead of bfloat16 (slower, more memory)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = ingest_schema(args.catalog)
    examples = load_dataset_jsonl(Path(args.dataset), limit=args.limit)
    print(f"[ablation] catalog={catalog.name} cases={len(examples)} "
          f"base_model={args.base_model} adapter={args.adapter}")

    if args.adapter:
        model, tokenizer = load_lora_for_inference(
            args.adapter, base_model=args.base_model, bf16=not args.no_bf16,
        )
    else:
        model, tokenizer = load_base_for_inference(
            args.base_model, bf16=not args.no_bf16,
        )

    print("[ablation] compiling grammar...")
    vocab_size = getattr(model.config, "vocab_size", None)
    compiled_grammar = compile_catalog_grammar(
        catalog, tokenizer, vocab_size=vocab_size,
    )

    off = _run_one("mask_off", catalog=catalog, examples=examples,
                   model=model, tokenizer=tokenizer,
                   compiled_grammar=None, out_dir=out_dir)
    on = _run_one("mask_on", catalog=catalog, examples=examples,
                  model=model, tokenizer=tokenizer,
                  compiled_grammar=compiled_grammar, out_dir=out_dir)

    _write_ablation_report(off, on, out_dir,
                           catalog_name=catalog.name,
                           base_model=args.base_model,
                           adapter=args.adapter, n=len(examples))

    print()
    print("=" * 60)
    print(f"Ablation complete. Report: {out_dir}/ablation_report.md")
    print("=" * 60)

    # Exit code: 0 if masking achieved 100% syntax_valid (A3 contract);
    # 1 otherwise (signals investigation needed).
    return 0 if (on.get("syntax_valid_rate") or 0) >= 0.999 else 1


if __name__ == "__main__":
    sys.exit(main())
