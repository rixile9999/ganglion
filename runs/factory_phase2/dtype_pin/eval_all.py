"""Evaluate every dtype-pin cell adapter under one canonical inference env.

For each ``results/<hw>_<dtype>_seed<N>/adapter/`` that exists, runs the model
on ``examples/iot_light/dataset.jsonl`` (n=500) with:

  - mask off (no grammar-constrained decoding)
  - post-correction default rules WILL apply (they're in the parser; no eval-side toggle)
  - bf16 inference dtype (canonical) — set --no-bf16 for fp32 inference

Writes ``eval_report.json`` and ``failures.json`` next to each adapter dir, so
``analyze.py`` can pick them up automatically.

Run on the CUDA box. Eval takes ~12-15 min per adapter on RTX 4090 BF16; total
for 8 cells ≈ 100-120 min.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ganglion.eval.metrics import CaseResult
from ganglion.factory.customer.eval import EvalConfig, evaluate_lora, write_report
from ganglion.factory.customer.ingest import ingest_schema
from ganglion.factory.customer.train_lora import load_lora_for_inference

# Reuse the dataset loader from grammar_ablation
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grammar_ablation import load_dataset_jsonl  # noqa: E402


def serialize_failures(results: list[CaseResult]) -> list[dict]:
    """Pull failures.json-shape from per-case results."""
    out = []
    for case in results:
        if case.exact_match:
            continue
        run = case.runs[0] if case.runs else None
        out.append(
            {
                "id": case.id,
                "prompt": case.prompt,
                "expected": case.expected.to_dict() if case.expected else None,
                "predicted": (
                    run.plan.to_dict() if run and run.plan else None
                ),
                "raw_output": (
                    (run.raw or {}).get("raw_output") if run else None
                ),
                "error": run.error if run else None,
                "syntax_valid": case.valid,
                "action_match": case.action_match,
            }
        )
    return out


def discover_cells(results_dir: Path) -> list[Path]:
    """Return adapter dirs in deterministic order."""
    cells = []
    for sub in sorted(results_dir.iterdir()):
        if not sub.is_dir():
            continue
        adapter = sub / "adapter"
        if (adapter / "adapter_config.json").exists():
            cells.append(sub)
    return cells


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default="runs/factory_phase2/dtype_pin/results",
    )
    parser.add_argument("--catalog", default="iot_light_5")
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--dataset", default="examples/iot_light/dataset.jsonl")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional: only eval first N cases per adapter")
    parser.add_argument("--no-bf16", action="store_true",
                        help="Use fp32 for inference (default: bf16)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip cells that already have eval_report.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    results_dir = (
        Path(args.results_dir)
        if Path(args.results_dir).is_absolute()
        else repo_root / args.results_dir
    )
    dataset_path = (
        Path(args.dataset)
        if Path(args.dataset).is_absolute()
        else repo_root / args.dataset
    )

    cells = discover_cells(results_dir)
    if not cells:
        print(f"no adapters found under {results_dir}", file=sys.stderr)
        return 1

    catalog = ingest_schema(args.catalog)
    examples = load_dataset_jsonl(dataset_path, limit=args.limit)
    print(f"[eval_all] catalog={catalog.name} cases={len(examples)} "
          f"cells={len(cells)} bf16={not args.no_bf16}")

    overall_started = time.perf_counter()
    summary_rows: list[dict] = []
    for i, cell_dir in enumerate(cells, 1):
        out_path = cell_dir / "eval_report.json"
        if args.skip_existing and out_path.exists():
            print(f"[eval_all] [{i}/{len(cells)}] skip {cell_dir.name} "
                  f"(eval_report.json exists)")
            continue

        adapter_dir = cell_dir / "adapter"
        print(f"[eval_all] [{i}/{len(cells)}] {cell_dir.name} ...")
        cell_started = time.perf_counter()

        model, tokenizer = load_lora_for_inference(
            adapter_dir, base_model=args.base_model, bf16=not args.no_bf16,
        )
        summary, results = evaluate_lora(
            catalog, examples, model, tokenizer, config=EvalConfig(),
        )
        elapsed = time.perf_counter() - cell_started
        summary["wall_seconds"] = round(elapsed, 1)

        # Drop into the cell dir directly (eval_report.json + .md)
        write_report(
            summary, results, cell_dir,
            catalog_name=catalog.name, n_train=0, n_holdout=len(examples),
        )
        # Also emit failures.json for analyze.py's Jaccard
        (cell_dir / "failures.json").write_text(
            json.dumps(serialize_failures(results), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[eval_all] [{i}/{len(cells)}] {cell_dir.name}: "
              f"syntax {summary['syntax_valid_rate']:.1%} / "
              f"action {summary['action_match_rate']:.1%} / "
              f"exact {summary['exact_match_rate']:.1%}  "
              f"({elapsed:.0f}s)")

        summary_rows.append({
            "cell": cell_dir.name,
            "syntax": summary["syntax_valid_rate"],
            "action": summary["action_match_rate"],
            "exact":  summary["exact_match_rate"],
            "wall_seconds": elapsed,
        })

        # Free memory between cells (matters mainly for MPS but harmless on CUDA)
        del model, tokenizer

    total_elapsed = time.perf_counter() - overall_started
    print()
    print("=" * 70)
    print(f"[eval_all] {len(summary_rows)} cells evaluated in {total_elapsed/60:.1f} min")
    print("=" * 70)
    for row in summary_rows:
        print(f"  {row['cell']:30s}  exact={row['exact']:.1%}  "
              f"action={row['action']:.1%}  wall={row['wall_seconds']:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
