"""Top-level Ganglion CLI dispatch.

Exposes `python -m ganglion.cli --llm … --tier …` (IoT benchmark) and
`python -m ganglion.cli --llm … --bfcl …` (BFCL benchmark). The legacy
`python -m ganglion.eval.runner …` invocation is preserved via the
shim at `ganglion.eval.runner` which re-exports `main` and `build_client`.

This module is the only place that knows about every concrete client and
benchmark adapter at once; the underlying runners (`benchmarks/iot/runner.py`,
`benchmarks/bfcl/runner.py`) stay benchmark-specific and import-clean.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ganglion.analyzer.metrics import CaseResult, summarize
from ganglion.analyzer.repair import RepairConfig
from ganglion.benchmarks.bfcl.loader import CATEGORIES as BFCL_CATEGORIES, load_category
from ganglion.benchmarks.bfcl.runner import run_bfcl, summarize_bfcl
from ganglion.benchmarks.iot.dataset import (
    ADVERSARIAL_DATASET,
    DEFAULT_DATASET,
    load_dataset,
)
from ganglion.benchmarks.iot.runner import run_iot
from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.lm.client import ModelClient
from ganglion.lm.dashscope import (
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
)
from ganglion.lm.rules import RuleBasedJSONDSLClient

__all__ = ["build_client", "main", "run_eval"]


BFCL_CALLABLE_CATEGORIES = ("simple_python", "multiple", "parallel", "parallel_multiple")


def build_client(
    name: str,
    catalog: Catalog,
    *,
    repair: RepairConfig | None = None,
) -> ModelClient:
    """Factory that maps a `--llm` choice to a concrete `ModelClient`."""
    if name == "rules":
        return RuleBasedJSONDSLClient()
    if name == "qwen":
        return QwenJSONDSLClient(catalog=catalog, repair=repair)
    if name == "qwen-text":
        return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=False)
    if name == "qwen-thinking":
        return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=True)
    if name == "qwen-native":
        return QwenNativeToolClient(catalog=catalog)
    raise ValueError(f"unknown llm: {name}")


def run_eval(
    client: ModelClient,
    dataset_path: Path,
    limit: int | None,
    *,
    repeat: int = 1,
) -> list[CaseResult]:
    """Legacy entry point: load dataset from disk then run the IoT loop.

    Kept for backward compatibility with `tests/test_eval_runner.py` and
    the deprecated `ganglion.eval.runner.run_eval` import path. New code
    should call `ganglion.benchmarks.iot.runner.run_iot` directly on a
    pre-loaded `Sequence[EvalCase]`.
    """
    cases = load_dataset(dataset_path, limit=limit)
    return run_iot(client, cases, repeat=repeat)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--llm",
        choices=["rules", "qwen", "qwen-text", "qwen-thinking", "qwen-native"],
        default="rules",
        help="Model path to evaluate.",
    )
    parser.add_argument(
        "--tier",
        default="iot_light_5",
        help="Catalog tier: iot_light_5 | home_iot_20 | smart_home_50.",
    )
    parser.add_argument(
        "--bfcl",
        default=None,
        help=(
            "Run BFCL v4 sample instead of the IoT dataset. "
            "Use a category name (simple_python | multiple | parallel | "
            "parallel_multiple | irrelevance), 'callable' for the four "
            "non-irrelevance categories, or 'all' for all five."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL dataset path. Use examples/iot_light/adversarial_cases.jsonl for adversarial-only cases.",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Use merged dataset: main + adversarial cases (M4).",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each case N times for latency stats (M3).",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Enable validator-error repair loop (qwen path only, M4).",
    )
    parser.add_argument(
        "--repair-max-attempts",
        type=int,
        default=1,
        help="Max repair retry attempts after the initial call.",
    )
    parser.add_argument(
        "--bfcl-per-category",
        type=int,
        default=None,
        help="Take the first N cases from each BFCL category before merging.",
    )
    parser.add_argument(
        "--bfcl-skip-per-category",
        type=int,
        default=0,
        help="Skip the first N cases from each BFCL category (use with --bfcl-per-category to take a slice).",
    )
    parser.add_argument(
        "--bfcl-output",
        type=Path,
        default=None,
        help="Write per-case BFCL records as JSONL to this path.",
    )
    parser.add_argument(
        "--bfcl-allow-empty-calls",
        action="store_true",
        help=(
            "Allow the BFCL DSL path to emit {\"calls\":[]} when no listed "
            "tool is needed (M5 abstention/no-call support)."
        ),
    )
    args = parser.parse_args()

    repair = RepairConfig(
        enabled=args.repair,
        max_attempts=max(1, args.repair_max_attempts),
    )

    if args.bfcl is not None:
        _run_bfcl_path(args, repair)
        return

    _run_iot_path(args, repair)


def _run_iot_path(args: argparse.Namespace, repair: RepairConfig) -> None:
    catalog = get_catalog(args.tier)
    client = build_client(args.llm, catalog, repair=repair)

    dataset_path = args.dataset
    if args.adversarial:
        main_cases = load_dataset(args.dataset, limit=None)
        adv_cases = load_dataset(ADVERSARIAL_DATASET, limit=None)
        merged_path = Path("examples/iot_light/merged_dataset.jsonl")
        with merged_path.open("w", encoding="utf-8") as f:
            for case in main_cases + adv_cases:
                row = {
                    "id": case.id,
                    "prompt": case.prompt,
                    "expected": {
                        "calls": [
                            {"action": c.action, "args": c.args}
                            for c in case.expected.calls
                        ]
                    },
                }
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        dataset_path = merged_path
        print(
            f"Using merged dataset: {dataset_path} "
            f"({len(main_cases)} + {len(adv_cases)} cases)"
        )

    cases = load_dataset(dataset_path, limit=args.limit)
    results = run_iot(client, cases, repeat=args.repeat)
    summary = summarize(results)
    summary["tier"] = args.tier
    summary["llm"] = args.llm
    summary["dsl_catalog_chars"] = len(catalog.render_json_dsl())
    summary["openai_tools_chars"] = len(json.dumps(catalog.render_openai_tools()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _run_bfcl_path(args: argparse.Namespace, repair: RepairConfig) -> None:
    if args.llm == "rules":
        raise SystemExit(
            "--llm rules has no BFCL adapter; use qwen or qwen-native."
        )
    categories = _resolve_bfcl_categories(args.bfcl)
    cases = []
    for category in categories:
        cat_cases = load_category(category)
        if args.bfcl_skip_per_category:
            cat_cases = cat_cases[args.bfcl_skip_per_category:]
        if args.bfcl_per_category is not None:
            cat_cases = cat_cases[: args.bfcl_per_category]
        cases.extend(cat_cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    def factory(catalog: Catalog) -> ModelClient:
        return build_client(args.llm, catalog, repair=repair)

    results = run_bfcl(
        factory,
        cases,
        repeat=args.repeat,
        allow_empty_calls=args.bfcl_allow_empty_calls,
    )
    if args.bfcl_output is not None:
        _write_bfcl_per_case(results, args.bfcl_output)
    summary = summarize_bfcl(results)
    summary["llm"] = args.llm
    summary["bfcl_categories"] = list(categories)
    summary["bfcl_per_category"] = args.bfcl_per_category
    summary["bfcl_skip_per_category"] = args.bfcl_skip_per_category
    summary["bfcl_allow_empty_calls"] = args.bfcl_allow_empty_calls
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _write_bfcl_per_case(results, path: Path) -> None:
    """Persist per-case BFCL outcomes for post-hoc analysis (Phase E/G)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            run = r.runs[0] if r.runs else None
            row = {
                "id": r.case.id,
                "category": r.case.category,
                "tool_count": len(r.case.tools),
                "expects_call": r.case.expects_call,
                "ast_valid": r.grade.valid,
                "grade_error_type": r.grade.error_type,
                "syntax_valid": run is not None and run.plan is not None,
                "error": run.error if run else None,
                "latency_ms": run.latency_ms if run else None,
                "input_tokens": run.input_tokens if run else None,
                "output_tokens": run.output_tokens if run else None,
                "dsl_chars": r.dsl_chars,
                "native_chars": r.native_chars,
                "predicted": r.predicted.to_jsonable() if r.predicted is not None else None,
            }
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _resolve_bfcl_categories(arg: str) -> tuple[str, ...]:
    if arg == "all":
        return BFCL_CATEGORIES
    if arg == "callable":
        return BFCL_CALLABLE_CATEGORIES
    if arg in BFCL_CATEGORIES:
        return (arg,)
    raise SystemExit(
        f"unknown --bfcl value: {arg!r}. "
        f"Choose one of: {', '.join(BFCL_CATEGORIES)}, callable, all."
    )


if __name__ == "__main__":
    main()
