from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Protocol

from ganglion.bfcl.loader import CATEGORIES as BFCL_CATEGORIES, load_category
from ganglion.dsl.catalog import Catalog
from ganglion.eval.bfcl_runner import run_bfcl, summarize_bfcl
from ganglion.eval.dataset import DEFAULT_DATASET, ADVERSARIAL_DATASET, load_dataset
from ganglion.eval.metrics import CaseResult, RunResult, summarize
from ganglion.runtime.qwen import (
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
    RepairConfig,
)
from ganglion.runtime.rules import RuleBasedJSONDSLClient
from ganglion.runtime.types import ModelResult
from ganglion.schema import get_catalog


BFCL_CALLABLE_CATEGORIES = ("simple_python", "multiple", "parallel", "parallel_multiple")


class ModelClient(Protocol):
    def invoke(self, user_prompt: str) -> ModelResult: ...


def build_client(
    name: str,
    catalog: Catalog,
    *,
    repair: RepairConfig | None = None,
) -> ModelClient:
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
    results: list[CaseResult] = []
    for case in load_dataset(dataset_path, limit=limit):
        runs: list[RunResult] = []
        for _ in range(max(1, repeat)):
            try:
                model_result = client.invoke(case.prompt)
                runs.append(
                    RunResult(
                        plan=model_result.plan,
                        raw=model_result.raw,
                        latency_ms=model_result.latency_ms,
                        input_tokens=model_result.input_tokens,
                        output_tokens=model_result.output_tokens,
                    )
                )
            except Exception as exc:
                runs.append(
                    RunResult(
                        plan=None,
                        raw=None,
                        latency_ms=None,
                        input_tokens=None,
                        output_tokens=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        results.append(
            CaseResult(
                id=case.id,
                prompt=case.prompt,
                expected=case.expected,
                runs=tuple(runs),
            )
        )
    return results


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
        return

    catalog = get_catalog(args.tier)
    client = build_client(args.llm, catalog, repair=repair)

    # Determine dataset path
    dataset_path = args.dataset
    if args.adversarial:
        from ganglion.eval.dataset import load_dataset as _load
        main_cases = _load(args.dataset, limit=None)
        adv_cases = _load(ADVERSARIAL_DATASET, limit=None)
        # Write merged dataset
        merged_path = Path("examples/iot_light/merged_dataset.jsonl")
        with merged_path.open("w", encoding="utf-8") as f:
            for case in main_cases + adv_cases:
                row = {
                    "id": case.id,
                    "prompt": case.prompt,
                    "expected": {"calls": [{"action": c.action, "args": c.args} for c in case.expected.calls]},
                }
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        dataset_path = merged_path
        print(f"Using merged dataset: {dataset_path} ({len(main_cases)} + {len(adv_cases)} cases)")

    results = run_eval(client, dataset_path, args.limit, repeat=args.repeat)
    summary = summarize(results)
    summary["tier"] = args.tier
    summary["llm"] = args.llm
    summary["dsl_catalog_chars"] = len(catalog.render_json_dsl())
    summary["openai_tools_chars"] = len(json.dumps(catalog.render_openai_tools()))
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
