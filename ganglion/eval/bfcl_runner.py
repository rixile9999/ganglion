"""BFCL v4 runner — per-case catalog + AST grader.

Distinct from `eval/runner.py` which evaluates the hand-written IoT dataset
with a single shared `Catalog`. BFCL cases each ship their own tool list
(`case.tools`), so we compile a fresh `Catalog` per case via
`compile_tool_calling_schema` and hand it to a client factory.

This module is the M1'-M4' measurement entry point. M5' (irrelevance /
abstention) reuses the same scaffold but with abstention-aware catalogs and
client adjustments; that work lands separately.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Any, Protocol

from ganglion.bfcl.grader import GraderResult, ast_match
from ganglion.bfcl.loader import BFCLCase
from ganglion.dsl.catalog import Catalog
from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.dsl.types import ActionPlan
from ganglion.runtime.types import ModelResult


class ModelClient(Protocol):
    def invoke(self, user_prompt: str) -> ModelResult: ...


ClientFactory = Callable[[Catalog], ModelClient]


@dataclass(frozen=True)
class BFCLRunResult:
    plan: ActionPlan | None
    raw: Any
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None


@dataclass(frozen=True)
class BFCLCaseResult:
    case: BFCLCase
    runs: tuple[BFCLRunResult, ...]
    grade: GraderResult
    dsl_chars: int
    native_chars: int

    @property
    def predicted(self) -> ActionPlan | None:
        if not self.runs:
            return None
        return self.runs[0].plan


def build_case_catalog(case: BFCLCase) -> Catalog:
    """Compile a per-case `Catalog` from the BFCL tool list.

    `compile_tool_calling_schema` accepts a sequence of tool schemas and
    returns a `CompiledToolMapper`; we keep the underlying `Catalog` so the
    runner can call `render_json_dsl()` and `render_openai_tools()` directly.
    """
    mapper = compile_tool_calling_schema(list(case.tools), name=f"bfcl_{case.id}")
    return mapper.catalog


def run_bfcl(
    client_factory: ClientFactory,
    cases: Sequence[BFCLCase],
    *,
    repeat: int = 1,
) -> list[BFCLCaseResult]:
    """Run a sequence of BFCL cases with a fresh catalog + client per case."""
    results: list[BFCLCaseResult] = []
    for case in cases:
        catalog = build_case_catalog(case)
        dsl_chars = len(catalog.render_json_dsl())
        native_chars = len(json.dumps(catalog.render_openai_tools()))
        client = client_factory(catalog)

        runs: list[BFCLRunResult] = []
        for _ in range(max(1, repeat)):
            started = time.perf_counter()
            try:
                model_result = client.invoke(case.user_message)
                runs.append(
                    BFCLRunResult(
                        plan=model_result.plan,
                        raw=model_result.raw,
                        latency_ms=model_result.latency_ms,
                        input_tokens=model_result.input_tokens,
                        output_tokens=model_result.output_tokens,
                    )
                )
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                runs.append(
                    BFCLRunResult(
                        plan=None,
                        raw=None,
                        latency_ms=latency_ms,
                        input_tokens=None,
                        output_tokens=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        first_plan = runs[0].plan if runs else None
        predicted_calls = first_plan.calls if first_plan is not None else ()
        grade = ast_match(predicted_calls, case)
        results.append(
            BFCLCaseResult(
                case=case,
                runs=tuple(runs),
                grade=grade,
                dsl_chars=dsl_chars,
                native_chars=native_chars,
            )
        )
    return results


def summarize_bfcl(results: list[BFCLCaseResult]) -> dict[str, Any]:
    total = len(results)
    syntax_valid = sum(
        1 for r in results if r.runs and r.runs[0].plan is not None
    )
    ast_passed = sum(1 for r in results if r.grade.valid)

    latencies: list[float] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    dsl_chars: list[int] = []
    native_chars: list[int] = []
    error_types: Counter[str] = Counter()
    by_category: dict[str, dict[str, int]] = {}

    for result in results:
        bucket = by_category.setdefault(
            result.case.category, {"total": 0, "ast_pass": 0, "syntax_valid": 0}
        )
        bucket["total"] += 1
        if result.grade.valid:
            bucket["ast_pass"] += 1
        if result.runs and result.runs[0].plan is not None:
            bucket["syntax_valid"] += 1
        if not result.grade.valid and result.grade.error_type:
            error_types[result.grade.error_type] += 1

        dsl_chars.append(result.dsl_chars)
        native_chars.append(result.native_chars)
        for run in result.runs:
            if run.latency_ms is not None:
                latencies.append(run.latency_ms)
            if run.input_tokens is not None:
                input_tokens.append(run.input_tokens)
            if run.output_tokens is not None:
                output_tokens.append(run.output_tokens)

    category_rates = {
        cat: {
            "total": stats["total"],
            "ast_match_rate": _rate(stats["ast_pass"], stats["total"]),
            "syntax_valid_rate": _rate(stats["syntax_valid"], stats["total"]),
        }
        for cat, stats in by_category.items()
    }

    return {
        "total": total,
        "ast_match_rate": _rate(ast_passed, total),
        "syntax_valid_rate": _rate(syntax_valid, total),
        "latency_ms_mean": _mean(latencies),
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "latency_ms_stddev": _stddev(latencies),
        "input_tokens_total": sum(input_tokens) if input_tokens else None,
        "output_tokens_total": sum(output_tokens) if output_tokens else None,
        "dsl_chars_mean": _mean([float(v) for v in dsl_chars]) if dsl_chars else None,
        "native_chars_mean": _mean([float(v) for v in native_chars]) if native_chars else None,
        "by_category": category_rates,
        "error_type_counts": dict(error_types) if error_types else None,
        "failures": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "user_message": r.case.user_message,
                "ground_truth": list(r.case.ground_truth)
                if r.case.ground_truth is not None
                else None,
                "predicted": r.predicted.to_jsonable() if r.predicted is not None else None,
                "error": r.runs[0].error if r.runs else None,
                "grade_error_type": r.grade.error_type,
                "grade_errors": r.grade.errors,
            }
            for r in results
            if not r.grade.valid
        ],
    }


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    if percentile == 50:
        return round(median(values), 2)
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile / 100))
    return round(ordered[index], 2)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pstdev(values), 2)


__all__ = [
    "BFCLCaseResult",
    "BFCLRunResult",
    "ClientFactory",
    "ModelClient",
    "build_case_catalog",
    "run_bfcl",
    "summarize_bfcl",
]
