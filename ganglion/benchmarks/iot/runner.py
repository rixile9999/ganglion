"""IoT-tier per-case evaluation loop.

Consumer-side runner for the IoT benchmark: given a pre-constructed
`ModelClient` and a sequence of `EvalCase`s, invoke the client for each
case (optionally `repeat` times for latency stats) and accumulate the
results into `CaseResult` records that downstream `analyzer.metrics`
consumes.

This module is the canonical implementation that supersedes the IoT path
inside `ganglion/eval/runner.py`. The CLI dispatch lives in
`ganglion.cli`; this module is the library entry point.

Two invariants from [[benchmark_iot]] (docs/tasks/benchmark_iot.md):

- **Failure raw preservation** — `_invoke_once` never raises; when the client
  raises it records `raw=getattr(exc, "raw", None)` (what `ModelOutputError` /
  `RepairExhaustedError` carry) so the failed model output is not lost.
- **Single trace materialiser** — `traces_from_results` is the only place an
  IoT `CaseResult` becomes an [[analyzer_trace_store]] `Trace`; the CLI's
  `--trace-store` path and `ganglion.factory` both call it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ganglion.analyzer.metrics import CaseResult, RunResult
from ganglion.analyzer.trace import (
    Trace,
    attempts_from_raw,
    now_iso,
    raw_plan_from_attempts,
)
from ganglion.benchmarks.iot.dataset import EvalCase
from ganglion.lm.client import ModelClient

__all__ = ["run_iot", "traces_from_results"]


def run_iot(
    client: ModelClient,
    dataset: Iterable[EvalCase],
    *,
    repeat: int = 1,
) -> list[CaseResult]:
    """Run `client` against the IoT `dataset`, repeating each case `repeat` times.

    Each `client.invoke()` call becomes a `RunResult`; failures are captured
    in the run's `error` field rather than aborting the batch. The returned
    list preserves dataset order; `repeat=N` produces `N` runs per case
    inside the corresponding `CaseResult.runs` tuple.
    """
    results: list[CaseResult] = []
    repeats = max(1, repeat)
    for case in dataset:
        runs = tuple(_invoke_once(client, case.prompt) for _ in range(repeats))
        results.append(
            CaseResult(
                id=case.id,
                prompt=case.prompt,
                expected=case.expected,
                runs=runs,
            )
        )
    return results


def _invoke_once(client: ModelClient, prompt: str) -> RunResult:
    """Invoke the client once; never raises.

    On exception the run records `error=f"{type(exc).__name__}: {exc}"` and
    `raw=getattr(exc, "raw", None)` — the failed output attached by
    `ModelOutputError` / `RepairExhaustedError` — so `traces_from_results`
    can still populate `attempts` / `raw_plan`.
    """
    try:
        result = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001 — failed runs are recorded, not raised
        return RunResult(
            plan=None,
            raw=getattr(exc, "raw", None),
            latency_ms=None,
            input_tokens=None,
            output_tokens=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    return RunResult(
        plan=result.plan,
        raw=result.raw,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


_DEFAULT_PARSE_STRATEGY = "json_object"


def _parse_strategy_from_raw(raw: object) -> str:
    """`raw["parse_strategy"]` when the client reports one, else `"json_object"`."""
    if isinstance(raw, Mapping):
        strategy = raw.get("parse_strategy")
        if isinstance(strategy, str) and strategy:
            return strategy
    return _DEFAULT_PARSE_STRATEGY


def _trace_from_run(
    *,
    case: CaseResult,
    run: RunResult,
    repeat_index: int,
    catalog_id: str,
    run_id: str,
    model_id: str,
    source: str,
    timestamp: str,
) -> Trace:
    attempts = attempts_from_raw(run.raw)
    raw_output = str(attempts[-1].get("content", "")) if attempts else ""
    return Trace(
        case_id=case.id,
        catalog_id=catalog_id,
        run_id=run_id,
        source=source,
        prompt=case.prompt,
        raw_output=raw_output,
        parse_strategy=_parse_strategy_from_raw(run.raw),
        latency_ms=float(run.latency_ms) if run.latency_ms is not None else 0.0,
        input_tokens_total=int(run.input_tokens or 0),
        output_tokens_total=int(run.output_tokens or 0),
        model_id=model_id,
        timestamp=timestamp,
        attempts=attempts,
        expected_plan=case.expected.to_jsonable(),
        plan=run.plan.to_jsonable() if run.plan is not None else None,
        error_type=run.error,
        raw_plan=raw_plan_from_attempts(attempts),
        repeat_index=repeat_index,
    )


def traces_from_results(
    results: list[CaseResult],
    *,
    catalog_id: str,
    run_id: str,
    model_id: str,
    source: str = "benchmark.iot",
    prompts_system: str = "",
) -> list[Trace]:
    """Materialise one `Trace` per `(case, repeat_index)` from IoT results.

    Field mapping ([[benchmark_iot]] / [[analyzer_trace_store]]):
    `attempts = attempts_from_raw(run.raw)`, `raw_output` = last attempt's
    content (`""` when none), `raw_plan = raw_plan_from_attempts(attempts)`,
    `plan = run.plan.to_jsonable()` or `None`, `expected_plan =
    case.expected.to_jsonable()`, `error_type = run.error`, `parse_strategy`
    from `raw["parse_strategy"]` when present else `"json_object"`, token
    totals `0` when the client reported `None`, `timestamp` = now.

    `prompts_system` is accepted for signature stability (the system prompt
    the run used); `Trace` has no field for it today, so it is not persisted.
    """
    del prompts_system  # reserved — not part of the Trace schema yet
    timestamp = now_iso()
    traces: list[Trace] = []
    for case in results:
        for repeat_index, run in enumerate(case.runs):
            traces.append(
                _trace_from_run(
                    case=case,
                    run=run,
                    repeat_index=repeat_index,
                    catalog_id=catalog_id,
                    run_id=run_id,
                    model_id=model_id,
                    source=source,
                    timestamp=timestamp,
                )
            )
    return traces
