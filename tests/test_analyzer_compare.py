"""Tests for ``ganglion.analyzer.compare`` — [[analyzer_compare]].

Runs are seeded through the real pipeline (``analyzer_fixtures.seed_run``):
run A with the degraded client (one ``set_light`` case loses ``state``),
run B with the rules client plus one human ``incorrect`` label so one case
regresses on B's own gold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.analyze import analyze_run
from ganglion.analyzer.compare import CompareResult, compare_runs, safe_run_name
from ganglion.analyzer.labels import LabelRecord, LabelStore, family_id_for, make_label_id, zone_for
from ganglion.analyzer.ledger import read_events
from ganglion.analyzer.manifest import run_dir
from ganglion.analyzer.trace import Trace, now_iso

from analyzer_fixtures import CATALOG, CATALOG_ID, DegradedRulesClient, load_cases, seed_run

CASES = load_cases(8)  # iot-001..iot-008; iot-006 (복도 불 꺼줘) fails under drop_state


def _incorrect_label(trace: Trace, expected_plan: dict) -> LabelRecord:
    family = family_id_for(trace.prompt)
    return LabelRecord(
        label_id=make_label_id(trace.trace_id, "human:op", "incorrect", expected_plan, "op", "b"),
        trace_id=trace.trace_id, case_id=trace.case_id, catalog_id=trace.catalog_id, run_id=trace.run_id,
        catalog_fingerprint=CATALOG.fingerprint(), model_id=trace.model_id, dataset_sha256="",
        origin="human:op", verdict="incorrect", expected_plan=expected_plan, endorsed="custom",
        saw_f0=True, failure_hint=None, order_sensitive=False, note="", family_id=family,
        zone=zone_for(family), labeler="op", label_batch_id="b", time_to_label_ms=None,
        supersedes=None, created_at=now_iso(),
    )


@pytest.fixture()
def two_runs(tmp_path: Path) -> Path:
    seed_run(tmp_path, "run-a", CASES, client=DegradedRulesClient(drop_state=True, echo_id=False),
             model_id="rules-degraded", iteration=0, started_at="2026-09-16T00:00:00Z")
    traces_b, _m, _r = seed_run(tmp_path, "run-b", CASES, iteration=1, parent_run_id="run-a",
                                started_at="2026-09-16T01:00:00Z")
    first = next(t for t in traces_b if t.case_id == "iot-001")
    LabelStore(tmp_path).append(_incorrect_label(
        first, {"calls": [{"action": "set_light", "args": {"room": "living", "state": "off"}}]},
    ))
    return tmp_path


def test_run_against_itself_is_a_null_delta(two_runs: Path) -> None:
    result = compare_runs(two_runs, CATALOG_ID, "run-b", "run-b")
    assert result.em_delta == 0.0 and result.em_a == result.em_b
    assert result.counts["fixed"] == 0 and result.counts["regressed"] == 0
    assert result.counts["same_pass"] + result.counts["same_fail"] == len(CASES)
    assert result.warnings == ()
    assert result.ci95 == (0.0, 0.0)
    assert (run_dir(two_runs, CATALOG_ID, "run-b") / "compare-run-b.json").exists()


def test_transition_matrix_and_per_case(two_runs: Path) -> None:
    analyze_run(two_runs, CATALOG_ID, "run-a")  # classified.jsonl for side A
    result = compare_runs(two_runs, CATALOG_ID, "run-a", "run-b")
    assert isinstance(result, CompareResult)
    assert result.n == len(CASES) and result.n_graded == len(CASES)
    assert result.counts == {
        "fixed": 1, "regressed": 1, "same_pass": 6, "same_fail": 0,
        "only_a": 0, "only_b": 0, "ungraded": 0,
    }
    rows = {r["case_id"]: r for r in result.per_case}
    fixed = rows["iot-006"]
    assert fixed["status_a"] == "invalid" and fixed["status_b"] == "pass" and fixed["direction"] == "fixed"
    assert fixed["failure_type_a"] == "missing_required_arg" and fixed["failure_type_b"] is None
    regressed = rows["iot-001"]
    assert regressed["status_a"] == "pass" and regressed["status_b"] == "fail"
    assert regressed["direction"] == "regressed"
    assert regressed["failure_type_a"] == "no_failure"
    assert all(r["trace_a"].startswith("tr-") and r["trace_b"].startswith("tr-") for r in result.per_case)
    assert result.em_delta == pytest.approx((1 - 1) / len(CASES))
    assert result.em_a == pytest.approx(7 / 8) and result.em_b == pytest.approx(7 / 8)
    assert result.manifest_diff["model_id"] == {"a": "rules-degraded", "b": "rules"}
    assert result.manifest_diff["iteration"] == {"a": 0, "b": 1}
    assert "decoding" not in result.manifest_diff and "dataset_sha256" not in result.manifest_diff
    # Persisted file + event.
    path = run_dir(two_runs, CATALOG_ID, "run-b") / "compare-run-a.json"
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(result.to_dict()))
    events = [e for e in read_events(two_runs, CATALOG_ID, "run-b") if e.name == "analyzer.compare.completed"]
    assert len(events) == 1 and events[0].payload["counts"]["fixed"] == 1
    assert events[0].payload["path"] == str(path)


def test_bootstrap_ci_is_deterministic_and_brackets_delta(two_runs: Path) -> None:
    first = compare_runs(two_runs, CATALOG_ID, "run-a", "run-b", bootstrap=500, seed=7)
    second = compare_runs(two_runs, CATALOG_ID, "run-a", "run-b", bootstrap=500, seed=7)
    assert first.ci95 is not None and len(first.ci95) == 2
    assert first.ci95[0] <= first.em_delta <= first.ci95[1]
    assert first.ci95 == second.ci95
    other_seed = compare_runs(two_runs, CATALOG_ID, "run-a", "run-b", bootstrap=500, seed=8)
    assert other_seed.em_delta == first.em_delta
    assert compare_runs(two_runs, CATALOG_ID, "run-a", "run-b", bootstrap=None).ci95 is None


def test_manifest_refusal_and_allow_diff(tmp_path: Path) -> None:
    seed_run(tmp_path, "repair-off", CASES)
    seed_run(tmp_path, "repair-on", CASES, decoding={
        "repair": True, "repair_max_attempts": 1, "thinking": False, "repeat": 1, "grammar_mask": False,
    })
    with pytest.raises(ValueError) as excinfo:
        compare_runs(tmp_path, CATALOG_ID, "repair-off", "repair-on")
    assert "decoding" in str(excinfo.value)
    assert not (run_dir(tmp_path, CATALOG_ID, "repair-on") / "compare-repair-off.json").exists()
    assert not [e for e in read_events(tmp_path, CATALOG_ID, "repair-on") if e.name == "analyzer.compare.completed"]
    result = compare_runs(tmp_path, CATALOG_ID, "repair-off", "repair-on", allow_diff=True)
    assert "manifest differs in decoding" in result.warnings
    assert result.manifest_diff["decoding"]["b"]["repair"] is True
    assert result.em_delta == 0.0
    with pytest.raises(ValueError):
        compare_runs(tmp_path, CATALOG_ID, "repair-off", "does-not-exist")


def test_safe_run_name_and_console_session_paths(tmp_path: Path) -> None:
    assert safe_run_name("console/s-1") == "console__s-1"
    assert safe_run_name("plain") == "plain"
    seed_run(tmp_path, "console/s-1", CASES[:3])
    seed_run(tmp_path, "console/s-2", CASES[:3])
    result = compare_runs(tmp_path, CATALOG_ID, "console/s-1", "console/s-2")
    assert Path(result.path) == tmp_path / CATALOG_ID / "console" / "s-2" / "compare-console__s-1.json"
    assert Path(result.path).exists()


def test_exclusion_and_partial_overlap(tmp_path: Path) -> None:
    seed_run(tmp_path, "a", CASES[:5])
    seed_run(tmp_path, "b", CASES[2:8])
    result = compare_runs(tmp_path, CATALOG_ID, "a", "b", exclude_case_ids=("iot-003",))
    assert result.counts["only_a"] == 2 and result.counts["only_b"] == 3
    assert result.counts["same_pass"] == 2 and result.n == 7 and result.n_graded == 2
    assert all(r["case_id"] != "iot-003" for r in result.per_case)
    only_b = next(r for r in result.per_case if r["direction"] == "only_b")
    assert only_b["trace_a"] is None and only_b["status_a"] is None
    assert result.ci95 == (0.0, 0.0)


def test_ungraded_side_counts_as_ungraded(tmp_path: Path) -> None:
    traces, _m, _r = seed_run(tmp_path, "a", CASES[:2])
    seed_run(tmp_path, "b", CASES[:2])
    # Strip the dataset gold from one side by rewriting the shard line (edge case).
    shard = tmp_path / CATALOG_ID / "a" / "traces.jsonl"
    rows = [json.loads(l) for l in shard.read_text(encoding="utf-8").splitlines()]
    rows[0]["expected_plan"] = None
    shard.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    result = compare_runs(tmp_path, CATALOG_ID, "a", "b")
    assert result.counts["ungraded"] == 1 and result.counts["same_pass"] == 1
    assert result.n_graded == 1 and result.ci95 is None  # graded n < 2
