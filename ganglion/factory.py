"""Composite orchestrator — Ganglion factory pipeline.

Implements `docs/tasks/factory_pipeline.md` (composite) at a minimum-but-correct
level: wires the `synth → finetune → benchmark → analyzer.{trace, failure,
metrics, rule} → contract.catalog.published` cycle into a single named entry
function (`run_pipeline`).

This file would be `ganglion/factory.py` per the spec target path, but the
legacy package at `ganglion/factory/` (Phase-1 per-customer LoRA pipeline) is
still on disk, and Python forbids a module and package from sharing a name.
We therefore land the composite under `ganglion/factory_pipeline.py`; consumers
of the spec target should rename once the legacy package retires.

Approach for this batch: **function-call orchestration**. The primitive layers
(`ganglion.lm.*`, `ganglion.benchmarks.*`, `ganglion.analyzer.*`,
`ganglion.contract.*`) do not yet emit events of their own, so a true
event-driven composite is not wireable end-to-end. We call the primitives in
sequence, then surface the artefacts that *would* travel on the event bus once
event emissions land. Down-stream pieces (classifier, rule synthesis, catalog
patching) are stubbed with `# TODO(post-M4-…)` markers and produce empty
`proposed_patches` lists.

Stop conditions (Contract §"Stop conditions, ranked"):
    1. exact_match_rate >= threshold       → "threshold_reached"
    2. iteration >= max_iter               → "max_iter_reached"
    3. plateau over K iterations           → "plateau"

For Batch 5 the loop is single-iteration unless the caller bumps `max_iter`,
because no real rule synthesis exists to mutate the catalog between cycles.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ganglion.analyzer.metrics import CaseResult, RunResult, summarize
from ganglion.analyzer.trace import Trace, TraceStore
from ganglion.benchmarks.bfcl.loader import (
    CATEGORIES as BFCL_CATEGORIES,
    load_category as load_bfcl_category,
)
from ganglion.benchmarks.bfcl.runner import run_bfcl, summarize_bfcl
from ganglion.benchmarks.iot.dataset import DEFAULT_DATASET, load_dataset
from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.lm.client import ModelClient, ModelResult

__all__ = [
    "IterationResult",
    "PipelineConfig",
    "PipelineOutcome",
    "run_pipeline",
]


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineConfig:
    """User-facing pipeline configuration.

    Mirrors the `factory.pipeline.start` payload in
    `docs/tasks/factory_pipeline.md` §Procedure.
    """

    catalog_id: str
    benchmark: str = "iot"  # "iot" | "bfcl"
    tier: Optional[str] = None  # used when benchmark == "iot"
    bfcl_category: Optional[str] = None  # used when benchmark == "bfcl"
    client_id: str = "rules"  # "rules" | "qwen" | "qwen-text" | "qwen-thinking" | "qwen-native"
    dataset_path: Optional[Path] = None  # override default IoT dataset
    limit: Optional[int] = None
    max_iter: int = 3
    threshold: float = 0.95
    auto_apply: bool = False
    plateau_K: int = 2
    plateau_eps: float = 0.01
    run_id: Optional[str] = None  # if None, generated per pipeline invocation
    trace_store_dir: Optional[Path] = None  # if None, traces are not persisted

    def __post_init__(self) -> None:
        if self.benchmark not in {"iot", "bfcl"}:
            raise ValueError(
                f"unknown benchmark: {self.benchmark!r}; expected 'iot' or 'bfcl'"
            )
        if self.benchmark == "bfcl" and self.bfcl_category is None:
            raise ValueError("benchmark='bfcl' requires bfcl_category")
        if self.benchmark == "bfcl" and self.bfcl_category not in BFCL_CATEGORIES:
            raise ValueError(
                f"unknown BFCL category: {self.bfcl_category!r}; "
                f"choose from {sorted(BFCL_CATEGORIES)}"
            )
        if self.max_iter < 1:
            raise ValueError(f"max_iter must be >= 1, got {self.max_iter}")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {self.threshold}")


@dataclass(frozen=True)
class IterationResult:
    """One pass through the synth → finetune → benchmark → analyze loop.

    Matches `factory.pipeline.iterated` payload in the composite spec.
    """

    catalog_id: str
    iteration: int
    eval_summary: dict[str, Any]
    proposed_patches: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PipelineOutcome:
    """Terminal state of one `run_pipeline` invocation.

    `iterations` holds every `IterationResult` emitted; `aborted` and `reason`
    mirror the `factory.pipeline.aborted` event.
    """

    iterations: tuple[IterationResult, ...]
    aborted: bool
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_client(name: str, catalog: Catalog) -> ModelClient:
    """Instantiate the requested model client.

    TODO(post-M4-A): replace with `ganglion.cli.build_client` once M4-A lands;
    today `ganglion.cli` does not exist on this branch.
    """
    if name == "rules":
        # Lazy import keeps the offline path free of optional deps.
        from ganglion.lm.rules import RuleBasedJSONDSLClient

        return RuleBasedJSONDSLClient()
    if name in {"qwen", "qwen-text", "qwen-thinking", "qwen-native"}:
        from ganglion.lm.dashscope import (
            QwenFreeformJSONDSLClient,
            QwenJSONDSLClient,
            QwenNativeToolClient,
        )

        if name == "qwen":
            return QwenJSONDSLClient(catalog=catalog)
        if name == "qwen-text":
            return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=False)
        if name == "qwen-thinking":
            return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=True)
        return QwenNativeToolClient(catalog=catalog)
    raise ValueError(f"unknown client_id: {name!r}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_from_run(
    *,
    case_id: str,
    catalog_id: str,
    run_id: str,
    source: str,
    prompt: str,
    expected_plan: dict[str, Any] | None,
    run: RunResult,
    client_id: str,
) -> Trace:
    """Convert one (case, run) pair into the canonical `Trace` shape."""
    raw_output = ""
    attempts: tuple[dict[str, Any], ...] = ()
    parse_strategy = "strict"
    if isinstance(run.raw, str):
        raw_output = run.raw
    elif isinstance(run.raw, dict):
        # QwenJSONDSLClient stores the final attempt's text in `raw["content"]`;
        # the lenient parsers also populate `raw["parse_strategy"]`.
        content = run.raw.get("content")
        if isinstance(content, str):
            raw_output = content
        raw_attempts = run.raw.get("attempts")
        if isinstance(raw_attempts, list):
            attempts = tuple(dict(att) for att in raw_attempts)
        ps = run.raw.get("parse_strategy")
        if isinstance(ps, str):
            parse_strategy = ps
    return Trace(
        case_id=case_id,
        catalog_id=catalog_id,
        run_id=run_id,
        source=source,
        prompt=prompt,
        raw_output=raw_output,
        parse_strategy=parse_strategy,
        latency_ms=float(run.latency_ms) if run.latency_ms is not None else 0.0,
        input_tokens_total=int(run.input_tokens or 0),
        output_tokens_total=int(run.output_tokens or 0),
        model_id=client_id,
        timestamp=_now_iso(),
        attempts=attempts,
        expected_plan=expected_plan,
        plan=run.plan.to_jsonable() if run.plan is not None else None,
        error_type=None if run.error is None else "runtime_error",
    )


def _ingest_iot_traces(
    *,
    case_results: list[CaseResult],
    catalog_id: str,
    run_id: str,
    client_id: str,
    store: TraceStore | None,
) -> None:
    """Persist IoT case traces into the (optional) `TraceStore`.

    Stand-in for what `analyzer.trace.recorded` events would carry once
    primitive layers emit them. See `docs/tasks/factory_pipeline.md`
    §Procedure: `on analyzer.trace.recorded(...)`.
    """
    if store is None:
        return
    for case in case_results:
        for run in case.runs:
            trace = _trace_from_run(
                case_id=case.id,
                catalog_id=catalog_id,
                run_id=run_id,
                source="iot",
                prompt=case.prompt,
                expected_plan=case.expected.to_jsonable(),
                run=run,
                client_id=client_id,
            )
            store.append(trace)


def _run_iot_iteration(
    *,
    catalog: Catalog,
    config: PipelineConfig,
    run_id: str,
) -> dict[str, Any]:
    """Run one IoT-tier evaluation pass and return its `summarize` payload."""
    dataset_path = config.dataset_path or DEFAULT_DATASET
    cases = load_dataset(dataset_path, limit=config.limit)
    if not cases:
        raise ValueError(f"IoT dataset is empty: {dataset_path}")
    client = _build_client(config.client_id, catalog)

    case_results: list[CaseResult] = []
    for case in cases:
        runs: list[RunResult] = []
        started = time.perf_counter()
        try:
            mr: ModelResult = client.invoke(case.prompt)
            runs.append(
                RunResult(
                    plan=mr.plan,
                    raw=mr.raw,
                    latency_ms=mr.latency_ms,
                    input_tokens=mr.input_tokens,
                    output_tokens=mr.output_tokens,
                )
            )
        except Exception as exc:  # pragma: no cover - smoke fallback
            runs.append(
                RunResult(
                    plan=None,
                    raw=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_tokens=None,
                    output_tokens=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        case_results.append(
            CaseResult(
                id=case.id,
                prompt=case.prompt,
                expected=case.expected,
                runs=tuple(runs),
            )
        )

    summary = summarize(case_results)
    summary["catalog_id"] = config.catalog_id
    summary["client_id"] = config.client_id
    summary["benchmark"] = "iot"

    store = TraceStore(config.trace_store_dir) if config.trace_store_dir else None
    _ingest_iot_traces(
        case_results=case_results,
        catalog_id=config.catalog_id,
        run_id=run_id,
        client_id=config.client_id,
        store=store,
    )
    return summary


def _run_bfcl_iteration(
    *,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Run one BFCL-category evaluation pass and return its `summarize_bfcl` payload."""
    assert config.bfcl_category is not None
    cases = load_bfcl_category(config.bfcl_category)
    if config.limit is not None:
        cases = cases[: config.limit]
    if not cases:
        raise ValueError(
            f"BFCL category '{config.bfcl_category}' produced 0 cases"
        )

    def factory(catalog: Catalog) -> ModelClient:
        return _build_client(config.client_id, catalog)

    results = run_bfcl(factory, cases)
    summary = summarize_bfcl(results)
    summary["catalog_id"] = config.catalog_id
    summary["client_id"] = config.client_id
    summary["benchmark"] = "bfcl"
    summary["bfcl_category"] = config.bfcl_category
    # Surface `exact_match_rate` so stop-condition checks have a uniform key
    # across IoT and BFCL benchmarks. AST match is BFCL's exact-match analogue.
    summary["exact_match_rate"] = summary.get("ast_match_rate", 0.0)
    return summary


def _check_stop(
    *,
    iteration: int,
    em_curve: list[float],
    config: PipelineConfig,
) -> Optional[str]:
    """Apply the ranked stop conditions from the spec.

    Returns the abort reason if a stop condition matched, else `None`.
    """
    em = em_curve[-1] if em_curve else 0.0
    if em >= config.threshold:
        return "threshold_reached"
    if iteration >= config.max_iter:
        return "max_iter_reached"
    if _plateau(em_curve, K=config.plateau_K, eps=config.plateau_eps):
        return "plateau"
    return None


def _plateau(em_curve: list[float], *, K: int, eps: float) -> bool:
    """No improvement above `eps` for the last `K` iterations."""
    if len(em_curve) <= K:
        return False
    window = em_curve[-(K + 1):]
    baseline = window[0]
    return all((value - baseline) < eps for value in window[1:])


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def run_pipeline(config: PipelineConfig) -> PipelineOutcome:
    """Drive one pipeline instance to its terminal state.

    Returns a `PipelineOutcome` that carries every emitted `IterationResult`
    plus a final `aborted` flag and reason. Re-emission with the same
    `(catalog_id, iteration)` is a no-op (idempotency keyed by tuple identity
    within the returned list).

    Catalog resolution failures are converted into an aborted outcome rather
    than propagated, so callers can treat `PipelineOutcome` as the single
    failure surface.
    """
    try:
        catalog = get_catalog(config.catalog_id)
    except ValueError as exc:
        return PipelineOutcome(
            iterations=(),
            aborted=True,
            reason=f"protocol_violation:catalog:{exc}",
        )

    run_id = config.run_id or f"run-{uuid.uuid4().hex[:12]}"
    iterations: list[IterationResult] = []
    em_curve: list[float] = []

    iteration = 0
    while True:
        iteration += 1
        try:
            if config.benchmark == "iot":
                summary = _run_iot_iteration(
                    catalog=catalog,
                    config=config,
                    run_id=f"{run_id}/iter-{iteration}",
                )
            else:
                summary = _run_bfcl_iteration(config=config)
        except ValueError as exc:
            return PipelineOutcome(
                iterations=tuple(iterations),
                aborted=True,
                reason=f"primitive_failed:dataset:{exc}",
            )

        em_curve.append(float(summary.get("exact_match_rate") or 0.0))

        # TODO(post-M4-D): subscribe to analyzer.failure.classified and
        # accumulate a failure histogram here. M4-D will land the classifier.
        # TODO(post-M4-E): subscribe to analyzer.rule.proposed and populate
        # proposed_patches; auto_apply gates contract.catalog.published.
        proposed_patches: tuple[dict[str, Any], ...] = ()

        iterations.append(
            IterationResult(
                catalog_id=config.catalog_id,
                iteration=iteration,
                eval_summary=summary,
                proposed_patches=proposed_patches,
            )
        )

        reason = _check_stop(
            iteration=iteration,
            em_curve=em_curve,
            config=config,
        )
        if reason is not None:
            # Per spec §"Stop conditions": every terminal stop emits
            # `factory.pipeline.aborted(reason=…)`, even the successful
            # `threshold_reached` branch. Distinguish success vs. failure via
            # `reason`, not `aborted`.
            return PipelineOutcome(
                iterations=tuple(iterations),
                aborted=True,
                reason=reason,
            )

        # If we have no patch synthesis yet, every additional iteration would
        # re-evaluate the same catalog and produce the same EM. Bail before the
        # second pass when proposed_patches is empty to avoid pointless work.
        if not proposed_patches:
            return PipelineOutcome(
                iterations=tuple(iterations),
                aborted=True,
                reason="no_patches_proposed",
            )
