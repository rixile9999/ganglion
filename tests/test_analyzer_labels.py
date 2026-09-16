"""Tests for ``ganglion.analyzer.labels`` — [[analyzer_label_store]].

Traces come from the real rules client (``analyzer_fixtures.seed_run``);
labels are the only hand-built records (they are the human input the
module stores).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.labels import (
    LabelRecord,
    LabelStore,
    export_sft,
    family_id_for,
    gold_map,
    make_label_id,
    resolve_gold,
    write_exports,
    zone_for,
)
from ganglion.analyzer.ledger import read_events
from ganglion.analyzer.trace import Trace, TraceStore, now_iso
from ganglion.contract.types import ActionPlan, ToolCall
from ganglion.lm.synth.pipeline import read_jsonl as synth_read_jsonl

from analyzer_fixtures import CATALOG, CATALOG_ID, load_cases, seed_run

RUN = "labels-run"


def _label(
    trace: Trace,
    verdict: str,
    expected_plan: dict | None = None,
    *,
    labeler: str = "operator",
    batch: str = "b1",
    supersedes: str | None = None,
    zone: str | None = None,
    created_at: str | None = None,
    origin: str | None = None,
) -> LabelRecord:
    family = family_id_for(trace.prompt)
    return LabelRecord(
        label_id=make_label_id(trace.trace_id, origin or f"human:{labeler}", verdict, expected_plan, labeler, batch),
        trace_id=trace.trace_id,
        case_id=trace.case_id,
        catalog_id=trace.catalog_id,
        run_id=trace.run_id,
        catalog_fingerprint=CATALOG.fingerprint(),
        model_id=trace.model_id,
        dataset_sha256="",
        origin=origin or f"human:{labeler}",
        verdict=verdict,
        expected_plan=expected_plan,
        endorsed=None,
        saw_f0=True,
        failure_hint=None,
        order_sensitive=False,
        note="",
        family_id=family,
        zone=zone or zone_for(family),
        labeler=labeler,
        label_batch_id=batch,
        time_to_label_ms=1200,
        supersedes=supersedes,
        created_at=created_at or now_iso(),
    )


@pytest.fixture()
def seeded(tmp_path: Path) -> tuple[Path, list[Trace]]:
    traces, _manifest, _results = seed_run(tmp_path, RUN, load_cases(6))
    return tmp_path, traces


def test_append_is_idempotent_and_emits_once(seeded: tuple[Path, list[Trace]]) -> None:
    base, traces = seeded
    store = LabelStore(base)
    rec = _label(traces[0], "correct")
    assert store.append(rec) == rec.label_id
    first_event = store.last_event
    assert first_event is not None and first_event.name == "analyzer.label.recorded"
    assert store.append(rec) == rec.label_id
    assert store.last_event is None  # idempotent skip → no event
    assert store.count(CATALOG_ID, RUN) == 1
    lines = (base / CATALOG_ID / RUN / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert LabelRecord.from_dict(json.loads(lines[0])) == rec
    events = [e for e in read_events(base, CATALOG_ID, RUN) if e.name == "analyzer.label.recorded"]
    assert len(events) == 1 and events[0].payload["verdict"] == "correct"
    assert events[0].correlation["trace_id"] == traces[0].trace_id


def test_append_rejects_bad_input(seeded: tuple[Path, list[Trace]]) -> None:
    base, traces = seeded
    store = LabelStore(base)
    with pytest.raises(ValueError):
        store.append(_label(traces[0], "maybe"))
    with pytest.raises(ValueError):
        store.append(_label(traces[0], "incorrect", {"tool": "x"}))  # not Action IR
    with pytest.raises(ValueError):
        store.append(_label(traces[0], "incorrect", None))  # incorrect needs a plan
    ghost = Trace.from_dict({**traces[0].to_dict(), "trace_id": "tr-0000000000000000"})
    with pytest.raises(ValueError):
        store.append(_label(ghost, "correct"))
    assert store.count(CATALOG_ID, RUN) == 0
    assert not (base / CATALOG_ID / RUN / "labels.jsonl").exists()


def test_latest_by_trace_resolves_supersedes(seeded: tuple[Path, list[Trace]]) -> None:
    base, traces = seeded
    store = LabelStore(base)
    trace = traces[1]
    old = _label(trace, "correct", created_at="2026-09-16T00:00:00Z")
    fix = {"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "on"}}]}
    new = _label(trace, "incorrect", fix, supersedes=old.label_id, created_at="2026-09-16T00:00:01Z")
    store.append(old)
    store.append(new)
    latest = store.latest_by_trace(CATALOG_ID, RUN)
    assert latest[trace.trace_id] == new
    assert store.count(CATALOG_ID, RUN) == 2
    assert [r.label_id for r in store.iter(CATALOG_ID, RUN)] == [old.label_id, new.label_id]
    assert [r.label_id for r in store.iter(catalog_id=CATALOG_ID)] == [old.label_id, new.label_id]
    assert [r.label_id for r in store.iter(run_id=RUN)] == [old.label_id, new.label_id]


def test_resolve_gold_precedence(seeded: tuple[Path, list[Trace]]) -> None:
    base, traces = seeded
    trace = traces[0]  # "거실 불 70%로 켜줘" → rules client passes
    # No label → dataset expected_plan.
    gold, origin = resolve_gold(trace, {})
    assert origin == "dataset" and gold is not None
    assert gold.to_jsonable() == trace.expected_plan
    # Human `incorrect` beats expected_plan.
    fix = {"calls": [{"action": "set_light", "args": {"room": "living", "state": "off"}}]}
    gold, origin = resolve_gold(trace, {trace.trace_id: _label(trace, "incorrect", fix)})
    assert origin == "human:operator"
    assert gold == ActionPlan((ToolCall("set_light", {"room": "living", "state": "off"}),))
    # `correct` → trace.plan is gold.
    gold, origin = resolve_gold(trace, {trace.trace_id: _label(trace, "correct")})
    assert origin == "human:operator" and gold is not None and gold.to_jsonable() == trace.plan
    # `should_abstain` → empty plan.
    gold, origin = resolve_gold(trace, {trace.trace_id: _label(trace, "should_abstain", {"calls": []})})
    assert gold == ActionPlan(calls=()) and origin == "human:operator"
    # `unsure` → falls back to the dataset gold.
    gold, origin = resolve_gold(trace, {trace.trace_id: _label(trace, "unsure")})
    assert origin == "dataset"
    # Unlabelled chat trace (no expected_plan) → ungraded.
    chat = Trace.from_dict({**trace.to_dict(), "expected_plan": None, "trace_id": ""})
    assert resolve_gold(chat, {}) == (None, "none")
    assert gold_map(traces, {})[trace.case_id].to_jsonable() == trace.expected_plan
    assert chat.case_id not in gold_map([chat], {})


def test_family_and_zone_are_deterministic() -> None:
    a = family_id_for("조명 장치 목록 보여줘 #8")
    b = family_id_for("조명 장치  목록 보여줘 #12 ")
    c = family_id_for("조명 장치 목록 보여줘!")
    assert a == b == c and a.startswith("fam-") and len(a) == 4 + 12
    assert family_id_for("Living room light OFF") == family_id_for("living room light off")
    assert family_id_for("anything", template_id="tpl-1") == family_id_for("other", template_id="tpl-1")
    assert family_id_for("x", template_id="tpl-1") != family_id_for("x", template_id="tpl-2")
    assert zone_for(a) == zone_for(a)
    assert zone_for(a) in {"train", "dev"}
    assert zone_for(a, release_families={a}) == "release"
    assert zone_for(a, train_share=100) == "train"
    assert zone_for(a, train_share=0) == "dev"
    fams = [family_id_for(f"prompt {i}") for i in range(200)]
    zones = {zone_for(f) for f in fams}
    assert zones == {"train", "dev"}


def test_export_sft_rows_and_write_exports(seeded: tuple[Path, list[Trace]], tmp_path: Path) -> None:
    base, traces = seeded
    store = LabelStore(base)
    by_id = {t.trace_id: t for t in traces}
    fix0 = {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 70}}]}
    fix1 = {"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:30", "state": "off"}}]}
    # Out-of-range brightness: no hook (default / strip / prompt correction) can rescue it.
    bad = {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 500}}]}
    labels = [
        _label(traces[0], "incorrect", fix0, zone="train"),
        _label(traces[1], "incorrect", fix1, zone="dev"),
        _label(traces[2], "correct", zone="train"),
        _label(traces[3], "unsure", zone="train"),
        _label(traces[4], "should_abstain", {"calls": []}, zone="release"),
        _label(traces[5], "incorrect", bad, zone="train"),  # fails the round-trip gate
    ]
    for rec in labels:
        store.append(rec)
    rows = export_sft(store.iter(CATALOG_ID, RUN), by_id, catalog=CATALOG)
    assert [set(r) for r in rows] == [{"intent", "expected", "strategy", "teacher_score", "id"}]
    assert rows[0]["intent"] == traces[0].prompt
    assert json.loads(rows[0]["expected"]) == fix0
    assert rows[0]["strategy"] == "human_label:incorrect:set_light"
    assert rows[0]["teacher_score"] == 1.0 and len(rows[0]["id"]) == 12
    assert export_sft.dropped == 1
    for row in rows:
        CATALOG.parse_json_dsl(row["expected"])
    with_correct = export_sft(labels, by_id, catalog=CATALOG, include_correct=True)
    assert len(with_correct) == 2 and with_correct[1]["strategy"].startswith("human_label:correct:")
    with_correct_store = export_sft(labels, TraceStore(base), catalog=CATALOG, include_correct=True)
    assert with_correct_store == with_correct
    hard = export_sft(labels, by_id, catalog=CATALOG, zones=("dev",))
    assert len(hard) == 1 and json.loads(hard[0]["expected"]) == fix1
    labels_dir = tmp_path / "labels"
    paths = write_exports(labels_dir, CATALOG_ID, rows, hard, zones={labels[0].family_id: "train"})
    assert set(paths) == {"human_sft", "hard_pool", "zones"}
    sft = synth_read_jsonl(Path(paths["human_sft"]))
    assert len(sft) == 1 and sft[0].intent == traces[0].prompt and sft[0].teacher_score == 1.0
    pool = synth_read_jsonl(Path(paths["hard_pool"]))
    assert len(pool) == 1 and pool[0].expected_dsl == hard[0]["expected"]
    zones_rows = [json.loads(l) for l in Path(paths["zones"]).read_text(encoding="utf-8").splitlines()]
    assert zones_rows == [{"family_id": labels[0].family_id, "zone": "train"}]
    assert not any(json.loads(r["expected"]) == fix1 for r in rows)  # dev never in human_sft
