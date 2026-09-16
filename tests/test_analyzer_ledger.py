"""Tests for ``ganglion.analyzer.ledger`` — [[analyzer_event_ledger]].

Success criteria from the spec: idempotent emit (one line, equal Events),
run-correlated rows land in the run's ``events.jsonl`` and uncorrelated in
``ledger/global.jsonl``, filtered reads, ``ValueError`` on an undeclared
name with no file created, and 8 threads → 8 well-formed lines.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ganglion.analyzer.ledger import EVENT_NAMES, Event, emit, events_path, read_events


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_emit_is_idempotent_and_content_addressed(tmp_path: Path) -> None:
    kwargs = dict(
        producer="test",
        correlation={"catalog_id": "iot_light_5", "run_id": "r1"},
        refs={"traces": "x/traces.jsonl"},
    )
    first = emit(tmp_path, "analyzer.trace.recorded", {"trace_id": "tr-1"}, **kwargs)
    second = emit(tmp_path, "analyzer.trace.recorded", {"trace_id": "tr-1"}, **kwargs)
    assert first == second
    assert first.event_id.startswith("ev-") and len(first.event_id) == 3 + 16
    path = tmp_path / "iot_light_5" / "r1" / "events.jsonl"
    rows = _lines(path)
    assert len(rows) == 1
    assert rows[0]["event_id"] == first.event_id
    assert rows[0]["ts"].endswith("Z")
    assert Event.from_dict(rows[0]) == first
    # Different payload → different id, second line.
    third = emit(tmp_path, "analyzer.trace.recorded", {"trace_id": "tr-2"}, **kwargs)
    assert third.event_id != first.event_id
    assert len(_lines(path)) == 2


def test_target_file_selection(tmp_path: Path) -> None:
    run_event = emit(
        tmp_path, "analyzer.run.recorded", {"n_cases": 3},
        producer="t", correlation={"catalog_id": "iot_light_5", "run_id": "console/s-1"},
    )
    global_event = emit(
        tmp_path, "contract.catalog.compiled", {"catalog_id": "compiled/abc"},
        producer="t", correlation={"catalog_id": "compiled/abc"},
    )
    assert (tmp_path / "iot_light_5" / "console" / "s-1" / "events.jsonl").exists()
    assert (tmp_path / "ledger" / "global.jsonl").exists()
    assert events_path(tmp_path, {"catalog_id": "iot_light_5", "run_id": "x"}).name == "events.jsonl"
    assert events_path(tmp_path, {}) == tmp_path / "ledger" / "global.jsonl"
    assert read_events(tmp_path, "iot_light_5", "console/s-1") == [run_event]
    everything = read_events(tmp_path)
    assert run_event in everything and global_event in everything
    assert everything[-1] == global_event  # global rows last
    assert read_events(tmp_path, catalog_id="iot_light_5") == [run_event]
    assert read_events(tmp_path, run_id="console/s-1") == [run_event]
    assert read_events(tmp_path, "iot_light_5", "missing") == []


def test_unknown_name_raises_before_io(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        emit(tmp_path, "not.a.declared.event", {}, producer="t", correlation={"catalog_id": "c", "run_id": "r"})
    assert not (tmp_path / "c").exists()
    assert not (tmp_path / "ledger").exists()
    assert "analyzer.rule.proposed" in EVENT_NAMES and len(EVENT_NAMES) == 12


def test_non_serialisable_payload_raises_type_error(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        emit(tmp_path, "analyzer.run.recorded", {"bad": object()}, producer="t",
             correlation={"catalog_id": "c", "run_id": "r"})
    assert not (tmp_path / "c" / "r" / "events.jsonl").exists()


def test_eight_threads_no_interleaving(tmp_path: Path) -> None:
    correlation = {"catalog_id": "iot_light_5", "run_id": "threads"}
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            emit(tmp_path, "lm.inference.completed", {"i": i, "pad": "x" * 2000},
                 producer="t", correlation=correlation)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = _lines(tmp_path / "iot_light_5" / "threads" / "events.jsonl")
    assert len(rows) == 8
    assert sorted(r["payload"]["i"] for r in rows) == list(range(8))


def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    event = emit(tmp_path, "analyzer.metrics.summarized", {"em": 0.9}, producer="t",
                 correlation={"catalog_id": "c", "run_id": "r"})
    path = tmp_path / "c" / "r" / "events.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")
    assert read_events(tmp_path, "c", "r") == [event]
