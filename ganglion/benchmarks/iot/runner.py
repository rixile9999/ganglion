"""IoT-tier per-case evaluation loop.

Consumer-side runner for the IoT benchmark: given a pre-constructed
`ModelClient` and a sequence of `EvalCase`s, invoke the client for each
case (optionally `repeat` times for latency stats) and accumulate the
results into `CaseResult` records that downstream `analyzer.metrics`
consumes.

This module is the canonical implementation that supersedes the IoT path
inside `ganglion/eval/runner.py`. The CLI dispatch lives in
`ganglion.cli`; this module is the library entry point.

See `docs/tasks/benchmark_iot.md` for the full spec.
"""
from __future__ import annotations

from collections.abc import Iterable

from ganglion.analyzer.metrics import CaseResult, RunResult
from ganglion.benchmarks.iot.dataset import EvalCase
from ganglion.lm.client import ModelClient

__all__ = ["run_iot"]


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
    try:
        result = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001 — failed runs are recorded, not raised
        return RunResult(
            plan=None,
            raw=None,
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
