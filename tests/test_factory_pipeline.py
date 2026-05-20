"""Smoke tests for `ganglion.factory` (M4-B composite stub)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ganglion.factory import (
    IterationResult,
    PipelineConfig,
    PipelineOutcome,
    run_pipeline,
)


def test_iot_rules_pipeline_returns_one_iteration() -> None:
    outcome = run_pipeline(
        PipelineConfig(
            catalog_id="iot_light_5",
            benchmark="iot",
            tier="iot_light_5",
            client_id="rules",
            max_iter=1,
        )
    )
    assert isinstance(outcome, PipelineOutcome)
    assert outcome.aborted is True
    # Rules client hits exact_match_rate=1.0 on the IoT dataset, so the
    # ranked-first stop condition fires.
    assert outcome.reason == "threshold_reached"
    assert len(outcome.iterations) == 1

    iteration = outcome.iterations[0]
    assert isinstance(iteration, IterationResult)
    assert iteration.iteration == 1
    assert iteration.catalog_id == "iot_light_5"
    assert iteration.eval_summary["exact_match_rate"] == 1.0
    assert iteration.eval_summary["benchmark"] == "iot"
    assert iteration.proposed_patches == ()


def test_unknown_catalog_id_returns_aborted_outcome() -> None:
    outcome = run_pipeline(
        PipelineConfig(
            catalog_id="does_not_exist",
            benchmark="iot",
            tier="does_not_exist",
            client_id="rules",
            max_iter=1,
        )
    )
    assert outcome.aborted is True
    assert outcome.reason is not None
    assert outcome.reason.startswith("protocol_violation:catalog:")
    assert outcome.iterations == ()


def test_invalid_benchmark_raises_value_error() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(
            catalog_id="iot_light_5",
            benchmark="not_a_benchmark",
            client_id="rules",
        )


def test_bfcl_requires_category() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(
            catalog_id="iot_light_5",
            benchmark="bfcl",
            client_id="qwen",
        )


def test_iot_pipeline_persists_traces(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    outcome = run_pipeline(
        PipelineConfig(
            catalog_id="iot_light_5",
            benchmark="iot",
            tier="iot_light_5",
            client_id="rules",
            max_iter=1,
            limit=3,
            trace_store_dir=trace_dir,
        )
    )
    assert len(outcome.iterations) == 1
    # Three cases ⇒ three trace files appended under <catalog>/<run>/traces.jsonl.
    jsonl_files = list(trace_dir.rglob("traces.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
