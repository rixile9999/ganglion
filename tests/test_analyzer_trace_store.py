"""Tests for analyzer.trace — Trace dataclass + TraceStore (M4-C).

Covers the contract from docs/tasks/analyzer_trace_store.md:
    - Trace.to_dict / from_dict round-trip.
    - TraceStore.append + iter on a tmp_path.
    - Idempotency: same trace_id twice → one line on disk.
    - by_id resolution (and None for unknown ids).
    - 10 distinct synthetic traces produce 10 distinct trace_ids.
"""

from __future__ import annotations

import json
from pathlib import Path

from ganglion.analyzer.trace import Trace, TraceStore


def _make_trace(
    *,
    case_id: str = "case-1",
    catalog_id: str = "iot_light_5",
    run_id: str = "run-test",
    model_id: str = "rules",
    attempts: tuple[dict, ...] | None = None,
    plan: dict | None = None,
    **extra,
) -> Trace:
    if attempts is None:
        attempts = (
            {
                "attempt": 0,
                "content": '{"calls": []}',
                "input_tokens": 10,
                "output_tokens": 5,
            },
        )
    defaults: dict = {
        "source": "benchmark.iot",
        "prompt": "turn on the living room light",
        "raw_output": '{"calls": []}',
        "parse_strategy": "strict",
        "latency_ms": 12.5,
        "input_tokens_total": 10,
        "output_tokens_total": 5,
        "timestamp": "2026-05-20T00:00:00Z",
        "expected_plan": {"calls": []},
        "plan": plan if plan is not None else {"calls": []},
        "error_type": None,
    }
    defaults.update(extra)
    return Trace(
        case_id=case_id,
        catalog_id=catalog_id,
        run_id=run_id,
        model_id=model_id,
        attempts=attempts,
        **defaults,
    )


def test_trace_to_dict_roundtrip():
    trace = _make_trace()
    payload = trace.to_dict()
    assert payload["trace_id"] == trace.trace_id
    assert payload["case_id"] == "case-1"
    # JSON-serialisable end-to-end.
    json.dumps(payload)
    restored = Trace.from_dict(payload)
    assert restored == trace
    # And the restored trace_id matches without re-deriving from scratch.
    assert restored.trace_id == trace.trace_id


def test_trace_id_is_content_addressed():
    a = _make_trace(case_id="case-1")
    b = _make_trace(case_id="case-1")
    assert a.trace_id == b.trace_id, "same content → same id"

    c = _make_trace(case_id="case-2")
    assert a.trace_id != c.trace_id, "different case_id → different id"


def test_trace_id_changes_with_attempt_chain():
    base = _make_trace()
    longer = _make_trace(
        attempts=(
            {"attempt": 0, "content": "fail", "input_tokens": 10, "output_tokens": 1},
            {"attempt": 1, "content": '{"calls": []}', "input_tokens": 12, "output_tokens": 5},
        )
    )
    assert base.trace_id != longer.trace_id


def test_trace_id_uses_tr_prefix():
    trace = _make_trace()
    assert trace.trace_id.startswith("tr-")
    # 3 ("tr-") + 16 hex chars
    assert len(trace.trace_id) == 19


def test_append_writes_jsonl_line(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    trace = _make_trace()
    tid = store.append(trace)
    assert tid == trace.trace_id

    path = tmp_path / "iot_light_5" / "run-test" / "traces.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["trace_id"] == tid


def test_append_then_iter(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    trace = _make_trace()
    store.append(trace)

    got = list(store.iter(catalog_id="iot_light_5", run_id="run-test"))
    assert len(got) == 1
    assert got[0] == trace


def test_append_is_idempotent(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    trace = _make_trace()
    tid1 = store.append(trace)
    tid2 = store.append(trace)
    assert tid1 == tid2

    path = tmp_path / "iot_light_5" / "run-test" / "traces.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "duplicate trace_id should not produce a second line"


def test_append_idempotent_across_store_instances(tmp_path: Path):
    """Re-opening the store and re-appending the same trace is a no-op.

    This guards the benchmark-replay use case: a second invocation must not
    re-write traces it already persisted on a prior run.
    """
    store_a = TraceStore(base_dir=tmp_path)
    trace = _make_trace()
    store_a.append(trace)

    store_b = TraceStore(base_dir=tmp_path)
    store_b.append(trace)

    path = tmp_path / "iot_light_5" / "run-test" / "traces.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_by_id_resolves_known_and_returns_none_for_unknown(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    trace = _make_trace()
    tid = store.append(trace)

    assert store.by_id(tid) == trace
    assert store.by_id("tr-deadbeefdeadbeef") is None


def test_batch_of_10_unique_traces(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    ids: set[str] = set()
    for i in range(10):
        trace = _make_trace(case_id=f"case-{i}")
        ids.add(store.append(trace))

    assert len(ids) == 10, "10 distinct case_ids must hash to 10 distinct trace_ids"

    path = tmp_path / "iot_light_5" / "run-test" / "traces.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10

    # iter() yields all 10 back.
    seen = list(store.iter(catalog_id="iot_light_5", run_id="run-test"))
    assert {tr.trace_id for tr in seen} == ids


def test_iter_filters_by_catalog_and_run(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    a = _make_trace(case_id="a", catalog_id="iot_light_5", run_id="r1")
    b = _make_trace(case_id="b", catalog_id="iot_light_5", run_id="r2")
    c = _make_trace(case_id="c", catalog_id="home_iot_20", run_id="r1")
    for tr in (a, b, c):
        store.append(tr)

    all_traces = list(store.iter())
    assert len(all_traces) == 3

    iot_only = list(store.iter(catalog_id="iot_light_5"))
    assert {tr.case_id for tr in iot_only} == {"a", "b"}

    iot_r1 = list(store.iter(catalog_id="iot_light_5", run_id="r1"))
    assert [tr.case_id for tr in iot_r1] == ["a"]


def test_iter_on_empty_store_is_empty(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path / "nonexistent")
    assert list(store.iter()) == []
    assert list(store.iter(catalog_id="x", run_id="y")) == []


def test_nested_catalog_id_path(tmp_path: Path):
    """BFCL produces catalog_ids like `bfcl/simple_python/<case_id>`; ensure
    the nested path renders on-disk verbatim."""
    store = TraceStore(base_dir=tmp_path)
    trace = _make_trace(catalog_id="bfcl/simple_python/case-42", source="benchmark.bfcl")
    store.append(trace)

    path = tmp_path / "bfcl" / "simple_python" / "case-42" / "run-test" / "traces.jsonl"
    assert path.exists()

    got = list(store.iter(catalog_id="bfcl/simple_python/case-42", run_id="run-test"))
    assert len(got) == 1
    assert got[0] == trace
