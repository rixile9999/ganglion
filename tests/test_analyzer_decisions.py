"""Tests for ``ganglion.analyzer.decisions`` — [[analyzer_patch_decision]]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.decisions import (
    DecisionStore,
    PatchDecision,
    make_decision_id,
    precision_summary,
)
from ganglion.analyzer.ledger import read_events
from ganglion.analyzer.rules import RulePatch, make_patch_id, write_proposed_patches_sidecar
from ganglion.analyzer.taxonomy import FailureType

CATALOG_ID = "iot_light_5"
RUN = "dec-run"


def _patch(op: str, tool: str, payload: dict, confidence: float) -> RulePatch:
    return RulePatch(
        patch_id=make_patch_id(CATALOG_ID, op, tool, payload),
        catalog_id=CATALOG_ID,
        target_tool=tool,
        operation=op,
        payload=payload,
        evidence={"failure_count": 5, "support_share": 0.5, "example_trace_ids": [], "confidence": confidence},
        source_failure_type=FailureType.MISSING_REQUIRED_ARG,
        created_at="2026-09-16T00:00:00Z",
    )


PATCHES = [
    _patch("set_default", "set_light", {"arg": "state", "default": "on", "predicate_hint": {"requires_args": ["room"]}}, 0.9),
    _patch("enable_strip_unknown_args", "list_devices", {"strip_unknown_args": True}, 0.8),
    _patch("add_alias", "set_light", {"arg": "room", "aliases": {"안방": "bedroom"}, "kind": "enum"}, 0.5),
    _patch("ESCALATE", "schedule_light", {"arg": "at", "blocked_reason": "x"}, 0.3),
]


def _decision(
    patch_id: str,
    stage: str,
    decision: str,
    *,
    decided_at: str = "2026-09-16T00:00:00Z",
    commit_sha: str | None = None,
) -> PatchDecision:
    return PatchDecision(
        decision_id=make_decision_id(patch_id, stage, decision, decided_at),
        patch_id=patch_id,
        catalog_id=CATALOG_ID,
        run_id=RUN,
        catalog_fingerprint="cf-000000000000",
        stage=stage,
        decision=decision,
        reason="because",
        decided_by="operator",
        time_to_decide_ms=3000,
        commit_sha=commit_sha,
        decided_at=decided_at,
    )


def test_append_two_stages_and_idempotency(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    pid = PATCHES[0].patch_id
    blind = _decision(pid, "blind", "accept")
    after = _decision(pid, "after_preview", "hold", decided_at="2026-09-16T00:00:05Z")
    assert store.append(blind) == blind.decision_id
    assert store.last_event is not None and store.last_event.name == "analyzer.patch.decided"
    assert store.last_event.correlation["patch_id"] == pid
    store.append(after)
    assert store.append(blind) == blind.decision_id  # duplicate → no new line
    assert store.last_event is None
    path = tmp_path / CATALOG_ID / RUN / "patch_decisions.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert PatchDecision.from_dict(json.loads(lines[0])) == blind
    latest = store.latest_by_patch(CATALOG_ID, RUN)
    assert set(latest[pid]) == {"blind", "after_preview"}
    assert latest[pid]["blind"].decision == "accept"
    assert latest[pid]["after_preview"].decision == "hold"
    assert len([e for e in read_events(tmp_path, CATALOG_ID, RUN) if e.name == "analyzer.patch.decided"]) == 2
    assert [d.decision_id for d in store.iter(CATALOG_ID, RUN)] == [blind.decision_id, after.decision_id]
    # Newest decided_at wins per stage.
    newer = _decision(pid, "blind", "reject", decided_at="2026-09-16T01:00:00Z")
    store.append(newer)
    assert store.latest_by_patch(CATALOG_ID, RUN)[pid]["blind"].decision == "reject"


def test_vocabulary_and_ported_guard(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    pid = PATCHES[1].patch_id
    with pytest.raises(ValueError):
        store.append(_decision(pid, "ported", "ported"))  # no commit_sha
    with pytest.raises(ValueError):
        store.append(_decision(pid, "blind", "ported"))
    with pytest.raises(ValueError):
        store.append(_decision(pid, "sideways", "accept"))
    with pytest.raises(ValueError):
        store.append(_decision(pid, "blind", "maybe"))
    assert not (tmp_path / CATALOG_ID / RUN / "patch_decisions.jsonl").exists()
    store.append(_decision(pid, "ported", "ported", commit_sha="deadbeef"))
    assert store.latest_by_patch(CATALOG_ID, RUN)[pid]["ported"].commit_sha == "deadbeef"


def test_precision_summary_fixture(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.append(_decision(PATCHES[0].patch_id, "blind", "accept"))
    store.append(_decision(PATCHES[3].patch_id, "blind", "accept"))
    store.append(_decision(PATCHES[1].patch_id, "blind", "hold"))
    store.append(_decision(PATCHES[2].patch_id, "blind", "reject"))
    store.append(_decision(PATCHES[2].patch_id, "ported", "ported", commit_sha="abc123"))
    decisions = store.latest_by_patch(CATALOG_ID, RUN)
    summary = precision_summary([p.to_dict() for p in PATCHES], decisions)
    assert summary["n_proposed"] == 4
    assert summary["n_conf_ge_threshold"] == 2
    assert summary["accepted_blind"] == 2
    assert summary["accepted_after_preview"] == 0
    assert summary["held"] == 1 and summary["rejected"] == 1 and summary["retired"] == 0
    assert summary["ported"] == 1
    assert summary["patch_acceptance_rate"] == 0.5
    assert summary["precision_at_conf"] == 0.5
    assert sum(v["proposed"] for v in summary["by_operation"].values()) == 4
    assert summary["by_operation"]["set_default"] == {"proposed": 1, "accepted": 1}
    assert summary["by_operation"]["ESCALATE"] == {"proposed": 1, "accepted": 1}
    empty = precision_summary([], {})
    assert empty["patch_acceptance_rate"] == 0.0 and empty["precision_at_conf"] == 0.0


def test_decisions_do_not_touch_patches_or_fingerprint(tmp_path: Path) -> None:
    from ganglion.contract.builtins import get_catalog

    patches_path = tmp_path / CATALOG_ID / RUN / "proposed_patches.jsonl"
    write_proposed_patches_sidecar(PATCHES, patches_path)
    before = patches_path.read_bytes()
    fingerprint = get_catalog(CATALOG_ID).fingerprint()
    DecisionStore(tmp_path).append(_decision(PATCHES[0].patch_id, "blind", "accept"))
    assert patches_path.read_bytes() == before
    assert get_catalog(CATALOG_ID).fingerprint() == fingerprint
