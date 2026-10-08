"""Coordinate-only PII feedback never turns a verdict into a gold label."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import re
import threading
import time

import pytest

from ganglion.console.api import ConsoleAPI
from ganglion.lm.registry import Registry, RULES_SPEC
from ganglion.programs.specs import builtins
from ganglion.domains.pii.types import Span


@pytest.fixture
def api(tmp_path):
    app = ConsoleAPI(base_dir=tmp_path / "runs" / "traces", registry=Registry([RULES_SPEC]))
    yield app
    app.close()


def call(api, method, path, body=None):
    status, payload = api.handle(method, "/api/v2/" + path, {}, body)
    assert 200 <= status < 300, (status, payload)
    return payload


def wait_job(api, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = call(api, "GET", "jobs/" + job_id)
        if job["status"] not in {"queued", "running", "cancelling"}:
            return job
        time.sleep(.005)
    pytest.fail("PII feedback fixture job did not complete")


def byte_spans(text, literal, kind):
    return [{"start": len(text[:match.start()].encode()),
             "end": len(text[:match.end()].encode()), "type": kind}
            for match in re.finditer(re.escape(literal), text)]


def complete_job(api, monkeypatch, *, strategy="fixed", paragraphs=6, execute=False):
    import ganglion.domains.pii.rules as rules

    # Controlled raw errors exercise the real Interpreter and byte conversion;
    # no GPU or recognition quality claim is involved in these tests.
    class WhitespaceDetector(rules.RulesDetector):
        def detect(self, text):
            found = []
            for literal, kind in ((" 김민수 ", "PERSON"), ("a@b.example", "EMAIL")):
                found.extend(Span(match.start(), match.end(), kind)
                             for match in re.finditer(re.escape(literal), text))
            return found

    monkeypatch.setattr(rules, "RulesDetector", WhitespaceDetector)
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec["id"] = "feedback-coordinate-test"
    spec["preprocessor"] = {"adapter": "utf8-windows", "version": 1, "config": {
        "strategy": strategy, "max_chars": 64, "overlap_chars": 16, "max_tokens": 128}}
    call(api, "POST", "specs", spec)
    text = "😀e\u0301 中文 공개\r\n이름: 김민수 \r\n메일 a@b.example\r\n\r\n" * paragraphs
    upload = call(api, "POST", "uploads", {"data": base64.b64encode(text.encode()).decode()})["upload_id"]
    inputs = {"document": upload, "execute": execute}
    keys = None
    if execute:
        keys = call(api, "POST", "keys", {})
        inputs["public_key"] = keys["public_key"]
    started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": inputs})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    gold = sorted(byte_spans(text, "김민수", "PERSON") + byte_spans(text, "a@b.example", "EMAIL"),
                  key=lambda span: span["start"])
    return job, text, gold, keys


def feedback(api, job, **body):
    return call(api, "POST", f"jobs/{job['job_id']}/feedback", {"verdict": "correct", **body})


@pytest.mark.parametrize("strategy", ["fixed", "semantic"])
def test_unicode_trace_coordinates_are_absolute_utf8_bytes_and_deduplicated_for_analysis(api, monkeypatch, strategy):
    job, text, gold, _keys = complete_job(api, monkeypatch, strategy=strategy)
    artifact_dir = api._programs.root / "jobs" / job["job_id"] / "artifacts"
    trace = [json.loads(line) for line in (artifact_dir / "trace.jsonl").read_text().splitlines()]
    raw_expected = set()
    raw_count = 0
    for unit in trace:
        char_start = unit["source_character_start"]
        char_end = char_start + unit["characters"]
        assert unit["source_byte_start"] == len(text[:char_start].encode())
        assert unit["source_byte_start"] > char_start or char_start == 0
        for character_span, byte_span in zip(unit["raw_spans"], unit["raw_byte_spans"], strict=True):
            start = char_start + character_span["start"]
            end = char_start + character_span["end"]
            assert char_start <= start < end <= char_end
            assert byte_span == {"start": len(text[:start].encode()), "end": len(text[:end].encode()),
                                 "type": character_span["type"]}
            actual = text.encode()[byte_span["start"]:byte_span["end"]].decode()
            assert actual in {" 김민수 ", "a@b.example"}
            raw_expected.add((byte_span["start"], byte_span["end"], byte_span["type"]))
            raw_count += 1
    assert len(trace) > 1
    plan = [json.loads(line) for line in (artifact_dir / "plan.jsonl").read_text().splitlines()]
    assert [{"start": row["source_span"][0], "end": row["source_span"][1], "type": row["type"]} for row in plan] == gold
    row = feedback(api, job, expected_spans=gold, gold_complete=True)
    analysis = row["analysis"]
    assert row["status"] == "awaiting_independent_validation"
    assert analysis["coordinate"] == "utf8-byte"
    assert analysis["raw"]["predicted"] == len(raw_expected) == 12
    assert analysis["final"]["predicted"] == 12
    assert analysis["raw"]["f1"] == .5
    assert analysis["final"]["f1"] == 1.0
    assert analysis["attribution_scope"] == "interpreter_and_window_reconciliation"
    assert analysis["attribution"]["rescued_count"] == 6
    assert analysis["attribution"]["regressed_count"] == 0
    if strategy == "fixed":
        assert raw_count > len(raw_expected), "fixture must exercise duplicate overlap predictions"


def test_correct_verdict_without_gold_does_not_create_f1_or_mark_feedback_validated(api, monkeypatch):
    job, _text, _gold, _keys = complete_job(api, monkeypatch)
    row = feedback(api, job)
    assert row["expected_spans"] is None and row["gold_complete"] is False
    assert row["status"] == "awaiting_independent_validation"
    analysis = row["analysis"]
    assert analysis["status"] == "unclassified"
    assert analysis["attribution"] is None and analysis["attribution_status"] == "unclassified"
    for stage in ("raw", "final"):
        assert analysis[stage]["f1"] is None
        assert analysis[stage]["precision"] is None
        assert analysis[stage]["recall"] is None
        assert analysis[stage]["gold"] is None
        assert analysis[stage]["unclassified_predictions"] == 12


def test_partial_gold_never_treats_unannotated_predictions_as_false_positives(api, monkeypatch):
    job, _text, gold, _keys = complete_job(api, monkeypatch)
    row = feedback(api, job, expected_spans=[gold[0]])
    analysis = row["analysis"]
    assert row["gold_complete"] is False
    assert analysis["status"] == "partial"
    assert analysis["final"]["tp"] == 1
    assert analysis["final"]["recall"] == 1.0
    assert analysis["final"]["fp"] is None
    assert analysis["final"]["precision"] is None
    assert analysis["final"]["f1"] is None
    assert analysis["attribution"]["false_positives_removed"] is None
    assert analysis["attribution"]["false_positives_introduced"] is None
    assert not any(failure["kind"] == "fp" for failure in analysis["failures"])
    assert analysis["final"]["unclassified_predictions"] == 11


@pytest.mark.parametrize("complete", [False, True])
def test_explicit_empty_gold_has_no_silent_completeness_or_perfect_f1(api, monkeypatch, complete):
    job, _text, _gold, _keys = complete_job(api, monkeypatch)
    row = feedback(api, job, expected_spans=[], gold_complete=complete)
    final = row["analysis"]["final"]
    assert row["status"] == "awaiting_independent_validation"
    assert final["gold"] == 0
    if complete:
        assert final["fp"] == 12 and final["f1"] == 0.0
        assert row["analysis"]["status"] == "fail"
    else:
        assert final["fp"] is None and final["f1"] is None
        assert row["analysis"]["status"] == "partial"


def test_full_gold_measures_exact_boundaries_despite_correct_verdict(api, monkeypatch):
    job, _text, gold, _keys = complete_job(api, monkeypatch)
    imperfect = copy.deepcopy(gold)
    imperfect[0]["end"] += 1
    row = feedback(api, job, expected_spans=imperfect, gold_complete=True)
    analysis = row["analysis"]
    assert analysis["status"] == "fail"
    assert analysis["final"]["tp"] == 11
    assert analysis["final"]["fp"] == analysis["final"]["fn"] == 1
    assert analysis["final"]["f1"] == pytest.approx(11 / 12)
    assert analysis["histogram"] == {"boundary_mismatch": 1}
    assert row["status"] == "awaiting_independent_validation"


def test_older_trace_keeps_final_analysis_but_does_not_invent_raw_attribution(api, monkeypatch):
    job, _text, gold, _keys = complete_job(api, monkeypatch)
    path = api._programs.root / "jobs" / job["job_id"] / "artifacts" / "trace.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for unit in rows:
        unit.pop("raw_byte_spans")
        unit.pop("source_byte_start")
    path.write_text("".join(json.dumps(unit) + "\n" for unit in rows))
    analysis = feedback(api, job, expected_spans=gold, gold_complete=True)["analysis"]
    assert analysis["raw"] is None
    assert analysis["attribution"] is None
    assert analysis["attribution_status"] == "unavailable"
    assert analysis["final"]["f1"] == 1.0


def test_analysis_limit_records_unavailability_without_truncation_as_a_score(api, monkeypatch):
    import ganglion.programs.service as service

    job, _text, gold, _keys = complete_job(api, monkeypatch)
    monkeypatch.setattr(service, "ANALYSIS_SPAN_LIMIT", 2)
    row = feedback(api, job, expected_spans=gold, gold_complete=True)
    assert row["analysis"] == {"status": "unavailable", "reason": "analysis_span_limit", "limit": 2}
    assert row["status"] == "awaiting_independent_validation"
    assert "f1" not in row["analysis"]


def test_raw_analysis_limit_is_enforced_even_if_final_plan_is_small(api, monkeypatch):
    import ganglion.programs.service as service

    job, _text, gold, _keys = complete_job(api, monkeypatch, paragraphs=1)
    trace_path = api._programs.root / "jobs" / job["job_id"] / "artifacts" / "trace.jsonl"
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    # An additional bounded raw boundary variant still yields only two final
    # entities; this specifically exercises the raw-stage budget path.
    rows[0]["raw_byte_spans"].append(dict(gold[0]))
    trace_path.write_text("".join(json.dumps(unit) + "\n" for unit in rows))
    monkeypatch.setattr(service, "ANALYSIS_SPAN_LIMIT", 2)
    row = feedback(api, job, expected_spans=gold, gold_complete=True)
    assert row["analysis"] == {"status": "unavailable", "reason": "analysis_span_limit", "limit": 2}


@pytest.mark.parametrize("body", [{"gold_complete": True}, {"gold_complete": 1}, {"gold_complete": 0},
                                  {"gold_complete": "true"}, {"gold_complete": None},
                                  {"gold_complete": True, "expected_spans": None}])
def test_invalid_completeness_or_missing_explicit_gold_is_rejected_before_write(api, monkeypatch, body):
    job, _text, _gold, _keys = complete_job(api, monkeypatch)
    status, _payload = api.handle("POST", f"/api/v2/jobs/{job['job_id']}/feedback", {},
                                  {"verdict": "correct", **body})
    assert status == 400
    assert not (api._programs.root / "jobs" / job["job_id"] / "feedback.jsonl").exists()


def test_feedback_refuses_a_running_job_and_does_not_persist_a_pending_verdict(api, monkeypatch):
    import ganglion.domains.pii.rules as rules
    entered, release = threading.Event(), threading.Event()

    class BlockingDetector(rules.RulesDetector):
        def detect(self, text):
            entered.set()
            assert release.wait(5)
            return []

    monkeypatch.setattr(rules, "RulesDetector", BlockingDetector)
    upload = call(api, "POST", "uploads", {"data": base64.b64encode(b"public content").decode()})["upload_id"]
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": upload, "execute": False}})
    try:
        assert entered.wait(5)
        status, _ = api.handle("POST", f"/api/v2/jobs/{started['job_id']}/feedback", {}, {"verdict": "correct"})
        assert status == 400
        assert not (api._programs.root / "jobs" / started["job_id"] / "feedback.jsonl").exists()
    finally:
        release.set()
    assert wait_job(api, started["job_id"])["status"] == "complete"


@pytest.mark.skipif(importlib.util.find_spec("nacl") is None, reason="install ganglion[pii]")
def test_executed_feedback_keeps_source_and_both_keys_out_of_persisted_analysis(api, monkeypatch):
    job, text, gold, keys = complete_job(api, monkeypatch, execute=True)
    row = feedback(api, job, expected_spans=gold, gold_complete=True)
    assert row["analysis"]["final"]["f1"] == 1.0
    directory = api._programs.root / "jobs" / job["job_id"]
    for path in directory.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            for private_value in (text, "김민수", "a@b.example", keys["private_key"], keys["public_key"]):
                assert private_value.encode() not in content
    assert api._programs.uploads == {}
    assert not list((api._programs.root / "private").glob("*"))
