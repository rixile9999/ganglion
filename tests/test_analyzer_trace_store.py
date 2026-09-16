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


# ---------------------------------------------------------------------------
# 2026-09-16 console-batch revision: raw normalisation, raw_plan / repeat_index,
# id payload, list_shards split rule, lock/refresh, by_id shard hint.
# ---------------------------------------------------------------------------

from ganglion.analyzer.trace import attempts_from_raw, raw_plan_from_attempts  # noqa: E402


def test_attempts_from_raw_none_is_empty():
    assert attempts_from_raw(None) == ()


def test_attempts_from_raw_str_is_single_attempt():
    assert attempts_from_raw('{"calls": []}') == ({"attempt": 0, "content": '{"calls": []}'},)


def test_attempts_from_raw_repair_chain_passthrough():
    raw = {
        "attempts": [
            {"attempt": 0, "content": "bad", "input_tokens": 1, "output_tokens": 2, "error": "x"},
            {"attempt": 1, "content": '{"calls": []}', "input_tokens": 3, "output_tokens": 4},
        ],
        "final_content": '{"calls": []}',
    }
    attempts = attempts_from_raw(raw)
    assert len(attempts) == 2
    assert attempts[0]["error"] == "x"
    assert attempts[1]["content"] == '{"calls": []}'
    assert attempts[1]["attempt"] == 1


def test_attempts_from_raw_content_mapping():
    raw = {"content": "```json\n{}\n```", "parse_strategy": "fenced", "thinking_enabled": False}
    assert attempts_from_raw(raw) == ({"attempt": 0, "content": "```json\n{}\n```"},)


def test_attempts_from_raw_native_tool_calls_both_item_shapes():
    """Errata E12: `{name, arguments}` and `{action, args}` items both convert."""
    raw = [
        {"name": "set_light", "arguments": {"room": "living", "state": "on"}},
        {"action": "get_light_state", "args": {"room": "bedroom"}},
        {"name": "list_devices", "arguments": "{}"},  # JSON-string arguments
    ]
    [attempt] = attempts_from_raw(raw)
    assert attempt["attempt"] == 0
    decoded = json.loads(attempt["content"])
    assert decoded == {
        "calls": [
            {"action": "set_light", "args": {"room": "living", "state": "on"}},
            {"action": "get_light_state", "args": {"room": "bedroom"}},
            {"action": "list_devices", "args": {}},
        ]
    }


def test_attempts_from_raw_other_mapping_is_json_dumped():
    """The rules client returns its `{"calls": [...]}` payload as raw."""
    raw = {"calls": [{"action": "list_devices", "args": {}}]}
    [attempt] = attempts_from_raw(raw)
    assert json.loads(attempt["content"]) == raw


def test_raw_plan_from_attempts_takes_last_decodable_mapping():
    attempts = (
        {"attempt": 0, "content": '{"calls": [{"action": "a", "args": {}}]}'},
        {"attempt": 1, "content": "not json at all"},
        {"attempt": 2, "content": "[1, 2, 3]"},  # decodes, but not a mapping
    )
    assert raw_plan_from_attempts(attempts) == {"calls": [{"action": "a", "args": {}}]}
    assert raw_plan_from_attempts(()) is None
    assert raw_plan_from_attempts(({"attempt": 0, "content": "garbage"},)) is None
    assert raw_plan_from_attempts(({"attempt": 0, "content": ""},)) is None


def test_trace_raw_plan_and_repeat_index_roundtrip():
    import dataclasses

    raw_plan = {"calls": [{"action": "set_light", "args": {"room": "living"}}]}
    # `_make_trace` substitutes an empty plan for None; a validation failure
    # is `plan=None` with `raw_plan` set, so replace after construction
    # (trace_id="" makes __post_init__ re-derive the id).
    trace = dataclasses.replace(
        _make_trace(raw_plan=raw_plan, repeat_index=2), plan=None, trace_id="",
    )
    payload = trace.to_dict()
    assert payload["plan"] is None
    assert payload["raw_plan"] == raw_plan
    assert payload["repeat_index"] == 2
    restored = Trace.from_dict(payload)
    assert restored == trace
    assert restored.raw_plan == raw_plan and restored.repeat_index == 2


def test_from_dict_tolerates_pre_revision_shards():
    """Older shards have neither `raw_plan` nor `repeat_index`."""
    payload = _make_trace().to_dict()
    del payload["raw_plan"]
    del payload["repeat_index"]
    restored = Trace.from_dict(payload)
    assert restored.raw_plan is None
    assert restored.repeat_index == 0


def test_trace_id_payload_includes_catalog_and_repeat_index():
    base = _make_trace()
    assert _make_trace(catalog_id="home_iot_20").trace_id != base.trace_id
    assert _make_trace(repeat_index=1).trace_id != base.trace_id
    # raw_plan / plan are derived views, not part of the identity.
    assert _make_trace(raw_plan={"calls": []}).trace_id == base.trace_id


def test_shard_path_and_base_dir(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    assert store.base_dir == tmp_path
    assert store.shard_path("iot_light_5", "console/s-1") == (
        tmp_path / "iot_light_5" / "console" / "s-1" / "traces.jsonl"
    )


def test_list_shards_split_rule(tmp_path: Path):
    """Errata E13: `compiled/` and `bfcl/` catalog ids span two segments."""
    store = TraceStore(base_dir=tmp_path)
    for catalog_id, run_id in [
        ("iot_light_5", "r1"),
        ("compiled/abc123def456", "r2"),
        ("bfcl/simple_python", "r3"),
        ("iot_light_5", "console/s-1"),
    ]:
        store.append(_make_trace(catalog_id=catalog_id, run_id=run_id))
    # A sidecar must not be mistaken for a shard.
    (tmp_path / "iot_light_5" / "r1" / "labels.jsonl").write_text("{}\n", encoding="utf-8")
    assert store.list_shards() == [
        ("bfcl/simple_python", "r3"),
        ("compiled/abc123def456", "r2"),
        ("iot_light_5", "console/s-1"),
        ("iot_light_5", "r1"),
    ]


def test_iter_by_derived_run_id_with_slash(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    a = _make_trace(case_id="a", run_id="console/s-1")
    b = _make_trace(case_id="b", run_id="r1")
    store.append(a)
    store.append(b)
    assert [tr.case_id for tr in store.iter(run_id="console/s-1")] == ["a"]
    assert [tr.case_id for tr in store.iter(catalog_id="iot_light_5", run_id="console/s-1")] == ["a"]
    assert {tr.case_id for tr in store.iter(catalog_id="iot_light_5")} == {"a", "b"}


def test_by_id_with_shard_hint_opens_only_that_shard(tmp_path: Path):
    store = TraceStore(base_dir=tmp_path)
    a = _make_trace(case_id="a", run_id="r1")
    b = _make_trace(case_id="b", run_id="r2")
    store.append(a)
    store.append(b)
    fresh = TraceStore(base_dir=tmp_path)
    assert fresh.by_id(a.trace_id, catalog_id="iot_light_5", run_id="r1") == a
    assert fresh.by_id(a.trace_id, catalog_id="iot_light_5", run_id="r2") is None
    # No global index was built by the hinted lookup.
    assert fresh._by_id_cache is None
    assert fresh.by_id(b.trace_id) == b


def test_second_store_sees_external_append_after_refresh(tmp_path: Path):
    """Cross-process visibility: a shard rewritten elsewhere is re-read on
    the next touch when its (size, mtime) changed."""
    writer = TraceStore(base_dir=tmp_path)
    reader = TraceStore(base_dir=tmp_path)
    first = _make_trace(case_id="first")
    writer.append(first)
    assert [tr.case_id for tr in reader.iter(catalog_id="iot_light_5", run_id="run-test")] == ["first"]
    assert reader.by_id(first.trace_id) == first

    second = _make_trace(case_id="second")
    writer.append(second)
    got = {tr.case_id for tr in reader.iter(catalog_id="iot_light_5", run_id="run-test")}
    assert got == {"first", "second"}
    assert reader.by_id(second.trace_id) == second
    assert reader.shard_refresh_count >= 1
    # And the reader's idempotency index caught up: re-appending is a no-op.
    reader.append(second)
    lines = (tmp_path / "iot_light_5" / "run-test" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_concurrent_appends_are_serialised(tmp_path: Path):
    import threading

    store = TraceStore(base_dir=tmp_path)
    traces = [_make_trace(case_id=f"case-{i}") for i in range(32)]

    def worker(chunk):
        for tr in chunk:
            store.append(tr)

    threads = [threading.Thread(target=worker, args=(traces[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (tmp_path / "iot_light_5" / "run-test" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 32
    assert {json.loads(line)["trace_id"] for line in lines} == {tr.trace_id for tr in traces}
