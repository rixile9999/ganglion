"""Tests for ``ganglion.analyzer.analyze`` — [[analyzer_analyze]] (composite).

The ``rules-degraded-seed`` fixture is produced by the degraded rules
client over real dataset rows (errata E8) so ``missing_required_arg``
buckets and a ``set_default`` patch exist offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ganglion.analyzer.analyze import (
    HISTOGRAM_KEYS,
    UNCLASSIFIED,
    analyze_run,
    histogram,
    is_unclassified,
    read_classified,
    read_patches,
)
from ganglion.analyzer.catalogs import CatalogNotResolvable
from ganglion.analyzer.labels import LabelStore
from ganglion.analyzer.ledger import read_events
from ganglion.analyzer.rules import RuleSynthConfig
from ganglion.analyzer.taxonomy import FailureType
from ganglion.analyzer.trace import TraceStore
from ganglion.benchmarks.iot.dataset import EvalCase
from ganglion.contract.types import ActionPlan, ToolCall

from analyzer_fixtures import CATALOG_ID, DegradedRulesClient, load_cases, seed_run

RUN = "rules-degraded-seed"
_CREATED_AT = re.compile(r'"created_at": "[^"]*"')


def _strip_created_at(text: str) -> str:
    return _CREATED_AT.sub('"created_at": ""', text)


@pytest.fixture()
def degraded(tmp_path: Path) -> Path:
    seed_run(tmp_path, RUN, load_cases(60), client=DegradedRulesClient(), model_id="rules-degraded", iteration=0)
    return tmp_path


def test_analyze_run_on_degraded_seed(degraded: Path) -> None:
    result = analyze_run(degraded, CATALOG_ID, RUN)
    assert result["n_traces"] == 60 and result["n_classified"] == 60
    hist = result["histogram"]
    assert set(hist) == set(HISTOGRAM_KEYS)
    assert hist["missing_required_arg"] > 0
    assert sum(hist.values()) == result["n_classified"]
    assert result["n_patches"] >= 1
    patches = read_patches(degraded, CATALOG_ID, RUN)
    assert len(patches) == result["n_patches"]
    ops = {p["operation"] for p in patches}
    assert ops & {"set_default", "enable_strip_unknown_args"}
    set_default = next(p for p in patches if p["operation"] == "set_default")
    assert set_default["target_tool"] == "set_light" and set_default["payload"]["arg"] == "state"
    assert set_default["catalog_id"] == CATALOG_ID and set_default["patch_id"].startswith("rs-iot_light_5-")
    for key, path in result["paths"].items():
        assert Path(path).exists(), key
    assert set(result["paths"]) == {
        "classified", "proposed_patches", "proposed_patches_summary", "corrections", "corrections_summary",
    }
    classified = read_classified(degraded, CATALOG_ID, RUN)
    assert len(classified) == 60 and histogram(classified) == hist
    corrections = result["corrections"]
    assert corrections["n"] == 60 and corrections["unattributable"] == 0
    assert corrections["em_fk"] > corrections["em_f0"] >= 0.0  # strip rescues #N echoes
    assert corrections["em_fk"] - corrections["em_f0"] == pytest.approx(
        corrections["rescue"] - corrections["regression"], abs=1e-3,
    )
    assert json.loads(Path(result["paths"]["corrections_summary"]).read_text(encoding="utf-8")) == corrections
    summary = json.loads(Path(result["paths"]["proposed_patches_summary"]).read_text(encoding="utf-8"))
    assert summary["total_patches"] == result["n_patches"]
    names = {e.name for e in read_events(degraded, CATALOG_ID, RUN)}
    assert {"analyzer.failure.classified", "analyzer.rule.proposed", "analyzer.correction.attributed"} <= names


def test_second_call_is_idempotent(degraded: Path) -> None:
    analyze_run(degraded, CATALOG_ID, RUN)
    directory = degraded / CATALOG_ID / RUN
    sidecars = ["classified.jsonl", "proposed_patches.jsonl", "proposed_patches.summary.json",
                "corrections.jsonl", "corrections.summary.json"]
    before = {name: _strip_created_at((directory / name).read_text(encoding="utf-8")) for name in sidecars}
    events_before = (directory / "events.jsonl").read_text(encoding="utf-8")
    analyze_run(degraded, CATALOG_ID, RUN)
    after = {name: _strip_created_at((directory / name).read_text(encoding="utf-8")) for name in sidecars}
    assert after == before
    assert (directory / "events.jsonl").read_text(encoding="utf-8") == events_before


def test_labels_change_the_gold(degraded: Path) -> None:
    from ganglion.analyzer.labels import LabelRecord, family_id_for, make_label_id, zone_for
    from ganglion.analyzer.trace import now_iso

    store = TraceStore(degraded)
    trace = next(t for t in store.iter(CATALOG_ID, RUN) if t.case_id == "iot-001")
    fix = {"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 70}}]}
    family = family_id_for(trace.prompt)
    LabelStore(degraded).append(LabelRecord(
        label_id=make_label_id(trace.trace_id, "human:op", "incorrect", fix, "op", "b"),
        trace_id=trace.trace_id, case_id=trace.case_id, catalog_id=CATALOG_ID, run_id=RUN,
        catalog_fingerprint="", model_id=trace.model_id, dataset_sha256="", origin="human:op",
        verdict="incorrect", expected_plan=fix, endorsed="custom", saw_f0=False, failure_hint=None,
        order_sensitive=False, note="", family_id=family, zone=zone_for(family), labeler="op",
        label_batch_id="b", time_to_label_ms=None, supersedes=None, created_at=now_iso(),
    ))
    result = analyze_run(degraded, CATALOG_ID, RUN)
    row = read_classified(degraded, CATALOG_ID, RUN)[trace.trace_id]
    assert row["failure_type"] == FailureType.PARTIAL_ARG_VALUE_MISMATCH.value
    assert "human:op" in result["corrections"]["by_gold_origin"]
    assert result["corrections"]["by_gold_origin"]["human:op"]["n"] == 1


def test_retire_candidates_join_proposed_patches(tmp_path: Path) -> None:
    gold = ActionPlan((ToolCall("set_light", {"room": "living", "state": "on"}),))
    cases = [EvalCase(f"echo-{i}", f"거실 불 켜줘 #{i}", gold) for i in range(40)]
    seed_run(tmp_path, "retire", cases, client=DegradedRulesClient(drop_state=True, echo_id=True))
    result = analyze_run(tmp_path, CATALOG_ID, "retire", config=RuleSynthConfig(min_failure_count=5))
    patches = read_patches(tmp_path, CATALOG_ID, "retire")
    retire = [p for p in patches if p["operation"] == "retire_rule"]
    assert len(retire) == 1
    assert retire[0]["payload"] == {"hook_kind": "strip_unknown_args", "arg": None}
    assert retire[0]["source_failure_type"] == "no_failure"
    assert result["corrections"]["n_retire_candidates"] == 1
    assert result["histogram"]["missing_required_arg"] == 40
    assert any(p["operation"] == "set_default" for p in patches)
    proposed = [e for e in read_events(tmp_path, CATALOG_ID, "retire") if e.name == "analyzer.rule.proposed"]
    assert {e.correlation["patch_id"] for e in proposed} == {p["patch_id"] for p in patches}


def test_bfcl_raises_and_touches_nothing(tmp_path: Path) -> None:
    with pytest.raises(CatalogNotResolvable):
        analyze_run(tmp_path, "bfcl/simple_python", "r1")
    assert not (tmp_path / "bfcl").exists()


def test_empty_run_writes_empty_sidecars(tmp_path: Path) -> None:
    result = analyze_run(tmp_path, CATALOG_ID, "empty")
    assert result["n_traces"] == 0 and result["n_patches"] == 0
    assert all(v == 0 for v in result["histogram"].values())
    assert result["corrections"]["n"] == 0 and result["corrections"]["by_hook"] == {}
    assert read_classified(tmp_path, CATALOG_ID, "empty") == {}
    assert read_patches(tmp_path, CATALOG_ID, "empty") == []
    assert read_patches(tmp_path, CATALOG_ID, "never-analyzed") == []


def test_histogram_unclassified_rule() -> None:
    rows = {
        "a": {"failure_type": "no_failure", "confidence": 1.0},
        "b": {"failure_type": "no_failure", "confidence": 0.0},
        "c": {"failure_type": "missing_required_arg", "confidence": 1.0},
        "d": {"failure_type": "something_new", "confidence": 1.0},
    }
    hist = histogram(rows)
    assert hist["no_failure"] == 1 and hist[UNCLASSIFIED] == 2 and hist["missing_required_arg"] == 1
    assert len(hist) == 15 and list(hist)[-1] == UNCLASSIFIED
    assert is_unclassified(rows["b"]) and not is_unclassified(rows["a"])
    assert histogram({}) == {k: 0 for k in HISTOGRAM_KEYS}
