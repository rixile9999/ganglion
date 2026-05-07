"""Held-out evaluation for a trained LoRA adapter.

Reuses :mod:`ganglion.eval.metrics` (``CaseResult``, ``RunResult``,
``summarize``) so factory eval and the existing tier eval produce
compatible JSON reports.

Provides:
    split_train_eval()   — stratified train/holdout split by strategy
    evaluate_lora()      — run inference on holdout, compute metrics
    write_report()       — Markdown + JSON report
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.eval.metrics import CaseResult, RunResult, summarize
from ganglion.factory.customer.synth import SynthExample
from ganglion.factory.customer.train_lora import generate_dsl


@dataclass(frozen=True)
class EvalConfig:
    max_new_tokens: int = 256
    temperature: float = 0.0


def split_train_eval(
    examples: list[SynthExample],
    *,
    holdout_ratio: float = 0.2,
    seed: int = 42,
) -> tuple[list[SynthExample], list[SynthExample]]:
    """Stratified split: each strategy contributes ``holdout_ratio`` to holdout.

    For tool-anchored synth, ``strategy`` equals ``tool_anchored:<tool_name>``,
    so this guarantees every tool is represented in both splits (assuming the
    tool had at least 2 kept examples).
    """
    rng = random.Random(seed)
    by_strategy: dict[str, list[SynthExample]] = defaultdict(list)
    for ex in examples:
        by_strategy[ex.strategy].append(ex)

    train: list[SynthExample] = []
    holdout: list[SynthExample] = []
    for strategy, items in by_strategy.items():
        shuffled = list(items)
        rng.shuffle(shuffled)
        n_hold = max(1, int(round(len(shuffled) * holdout_ratio)))
        holdout.extend(shuffled[:n_hold])
        train.extend(shuffled[n_hold:])
    rng.shuffle(train)
    rng.shuffle(holdout)
    return train, holdout


def evaluate_lora(
    catalog: Catalog,
    holdout: Iterable[SynthExample],
    model,
    tokenizer,
    *,
    config: EvalConfig | None = None,
) -> tuple[dict[str, Any], list[CaseResult]]:
    """Run the model on each holdout example, return (summary, per-case results)."""
    cfg = config or EvalConfig()
    results: list[CaseResult] = []
    for ex in holdout:
        case_id = hashlib.sha1(ex.intent.encode("utf-8")).hexdigest()[:8]
        try:
            expected_plan = catalog.parse_json_dsl(ex.expected_dsl)
        except DSLValidationError as exc:
            raise ValueError(
                f"holdout example has invalid expected DSL: {exc}"
            ) from exc

        started = time.perf_counter()
        try:
            raw_output = generate_dsl(
                model,
                tokenizer,
                catalog,
                ex.intent,
                max_new_tokens=cfg.max_new_tokens,
                temperature=cfg.temperature,
            )
        except Exception as exc:  # generation crashed
            run = RunResult(
                plan=None,
                raw={"intent": ex.intent},
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=None,
                output_tokens=None,
                error=f"generation failed: {exc}",
            )
            results.append(
                CaseResult(
                    id=case_id, prompt=ex.intent, expected=expected_plan, runs=(run,)
                )
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            predicted_plan = catalog.parse_json_dsl(raw_output)
            error: str | None = None
        except DSLValidationError as exc:
            predicted_plan = None
            error = str(exc)
        except Exception as exc:
            predicted_plan = None
            error = f"unexpected: {exc}"

        run = RunResult(
            plan=predicted_plan,
            raw={"intent": ex.intent, "raw_output": raw_output},
            latency_ms=latency_ms,
            input_tokens=None,
            output_tokens=None,
            error=error,
        )
        results.append(
            CaseResult(id=case_id, prompt=ex.intent, expected=expected_plan, runs=(run,))
        )

    summary = summarize(results)
    summary["per_strategy"] = _per_strategy_breakdown(results, holdout)
    return summary, results


def _per_strategy_breakdown(
    results: list[CaseResult], holdout: Iterable[SynthExample]
) -> dict[str, dict[str, Any]]:
    """Compute exact / action match per strategy (≈ per-tool for tool-anchored)."""
    holdout_list = list(holdout)
    by_strategy: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "valid": 0, "exact": 0, "action": 0}
    )
    for case, ex in zip(results, holdout_list):
        bucket = by_strategy[ex.strategy]
        bucket["n"] += 1
        if case.valid:
            bucket["valid"] += 1
        if case.exact_match:
            bucket["exact"] += 1
        if case.action_match:
            bucket["action"] += 1
    out: dict[str, dict[str, Any]] = {}
    for strategy, b in by_strategy.items():
        n = max(b["n"], 1)
        out[strategy] = {
            "n": b["n"],
            "syntax_valid": round(b["valid"] / n, 4),
            "exact_match": round(b["exact"] / n, 4),
            "action_match": round(b["action"] / n, 4),
        }
    return out


def write_report(
    summary: dict[str, Any],
    results: list[CaseResult],
    out_dir: Path,
    *,
    catalog_name: str,
    n_train: int,
    n_holdout: int,
) -> None:
    """Persist eval_report.json + eval_report.md."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "eval_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md: list[str] = [
        f"# Eval report — {catalog_name}",
        "",
        f"- train: {n_train}",
        f"- holdout: {n_holdout}",
        "",
        "## Headline metrics",
        "",
        f"- syntax_valid_rate: **{summary['syntax_valid_rate']:.1%}**",
        f"- exact_match_rate:  **{summary['exact_match_rate']:.1%}**",
        f"- action_match_rate: **{summary['action_match_rate']:.1%}**",
    ]
    if summary.get("latency_ms_p50") is not None:
        md.append(f"- latency P50: {summary['latency_ms_p50']:.0f} ms")
        md.append(f"- latency P95: {summary['latency_ms_p95']:.0f} ms")
    md.append("")

    md.append("## Per-strategy breakdown")
    md.append("")
    md.append("| strategy | n | syntax | action | exact |")
    md.append("|---|---|---|---|---|")
    for strategy, stats in summary.get("per_strategy", {}).items():
        md.append(
            f"| {strategy} | {stats['n']} | "
            f"{stats['syntax_valid']:.1%} | "
            f"{stats['action_match']:.1%} | "
            f"{stats['exact_match']:.1%} |"
        )
    md.append("")

    failures = summary.get("failures", []) or []
    if failures:
        md.append(f"## Failures ({len(failures)})")
        md.append("")
        for fail in failures[:20]:
            md.append(f"### `{fail['id']}`")
            md.append(f"**prompt:** {fail['prompt']}")
            md.append(f"**expected:** `{json.dumps(fail['expected'], ensure_ascii=False)}`")
            predicted = fail.get("predicted")
            if predicted is not None:
                md.append(
                    f"**predicted:** `{json.dumps(predicted, ensure_ascii=False)}`"
                )
            else:
                md.append("**predicted:** *(parse failed)*")
            if fail.get("error"):
                md.append(f"**error:** {fail['error']}")
            raw = fail.get("raw")
            if isinstance(raw, dict) and "raw_output" in raw:
                md.append(f"**raw:** `{raw['raw_output'][:200]}`")
            md.append("")

    (out_dir / "eval_report.md").write_text("\n".join(md), encoding="utf-8")


def write_split_jsonls(
    train: list[SynthExample],
    holdout: list[SynthExample],
    out_dir: Path,
) -> None:
    """Persist the train/holdout split as separate JSONL files."""
    from ganglion.factory.customer.synth import write_jsonl

    out_dir = Path(out_dir)
    write_jsonl(train, out_dir / "train.jsonl")
    write_jsonl(holdout, out_dir / "holdout.jsonl")
