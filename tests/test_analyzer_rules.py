"""Tests for analyzer.rules — rule synthesis (M4-E).

Covers the matchers from docs/tasks/analyzer_rule_synthesis.md: one fixture
per FailureType bucket where a patch should fire, plus the empty / threshold
degenerate cases, sort order, ``RulePatch`` round-trip, and the synthesis
summary histogram shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ganglion.analyzer.rules import (
    RulePatch,
    RuleSynthConfig,
    synthesize_rules,
    write_proposed_patches_sidecar,
    write_synthesis_summary,
)
from ganglion.analyzer.taxonomy import Classification, FailureType
from ganglion.analyzer.trace import Trace
from ganglion.contract.builtins import get_catalog

CATALOG = get_catalog("iot_light_5")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_trace(
    *,
    case_id: str,
    plan: dict | None,
    expected_plan: dict | None,
    prompt: str = "test prompt",
) -> Trace:
    return Trace(
        case_id=case_id,
        catalog_id="iot_light_5",
        run_id="run-test",
        source="benchmark.iot",
        prompt=prompt,
        raw_output=json.dumps(plan) if plan is not None else "",
        parse_strategy="strict",
        latency_ms=10.0,
        input_tokens_total=10,
        output_tokens_total=5,
        model_id="rules",
        timestamp="2026-05-20T00:00:00Z",
        attempts=(
            {
                "attempt": 0,
                "content": json.dumps(plan) if plan is not None else "",
                "input_tokens": 10,
                "output_tokens": 5,
            },
        ),
        expected_plan=expected_plan,
        plan=plan,
    )


def _classification(
    *,
    trace_id: str,
    failure_type: FailureType,
    evidence: dict[str, Any],
    confidence: float = 1.0,
) -> Classification:
    return Classification(
        trace_id=trace_id,
        failure_type=failure_type,
        confidence=confidence,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Matcher fixtures
# ---------------------------------------------------------------------------


def test_missing_required_arg_emits_set_default():
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(5):
        tid = f"tr-mra-{i}"
        # Pred omits `state`; gold supplies state="on" with brightness.
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "brightness": 70}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on", "brightness": 70}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        # Wire the freshly computed trace_id into the classification so the
        # matcher can join classifications back to their source traces.
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.MISSING_REQUIRED_ARG,
            evidence={"action": "set_light", "arg_name": "state",
                      "declared_args": ["room", "state", "brightness", "color_temp"]},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.operation == "set_default"
    assert patch.target_tool == "set_light"
    assert patch.payload["arg"] == "state"
    assert patch.payload["default"] == "on"
    assert patch.evidence["confidence"] >= 0.2  # frequency (5/20) × consistency × narrowness


def test_unknown_arg_emits_strip_unknown():
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(5):
        plan = {"calls": [{"action": "list_devices",
                           "args": {"phantom_id": "8"}}]}
        gold = {"calls": [{"action": "list_devices", "args": {}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.UNKNOWN_ARG,
            evidence={"action": "list_devices", "arg_name": "phantom_id",
                      "declared_args": []},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.operation == "enable_strip_unknown_args"
    assert patch.target_tool == "list_devices"
    assert patch.payload["strip_unknown_args"] is True


def test_value_out_of_enum_emits_add_alias():
    classifs: list[Classification] = []
    traces: list[Trace] = []
    # Five traces all showing "living room" → "living".
    for i in range(5):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living room", "state": "on"}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on"}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.VALUE_OUT_OF_ENUM,
            evidence={"action": "set_light", "arg_name": "room",
                      "value": "living room", "allowed": ["living", "bedroom"]},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.operation == "add_alias"
    assert patch.target_tool == "set_light"
    assert patch.payload["aliases"] == {"living room": "living"}
    assert patch.payload["kind"] == "enum"


def test_zero_failures_returns_empty_list():
    patches = synthesize_rules([], [], CATALOG)
    assert patches == []


def test_below_threshold_returns_empty_list():
    # Only 2 traces — below the default min_failure_count=5.
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(2):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "brightness": 70}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on", "brightness": 70}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.MISSING_REQUIRED_ARG,
            evidence={"action": "set_light", "arg_name": "state",
                      "declared_args": []},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert patches == []


def test_patches_sorted_by_confidence_descending():
    """When multiple patches are emitted, highest-confidence one comes first."""
    classifs: list[Classification] = []
    traces: list[Trace] = []

    # Group A: 20 traces (frequency saturated → high confidence).
    for i in range(20):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "brightness": 70}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on", "brightness": 70}}]}
        traces.append(_make_trace(case_id=f"a-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.MISSING_REQUIRED_ARG,
            evidence={"action": "set_light", "arg_name": "state",
                      "declared_args": []},
        ))

    # Group B: 5 traces of value_out_of_enum (lower frequency → lower confidence).
    for i in range(5):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living room", "state": "on"}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on"}}]}
        traces.append(_make_trace(case_id=f"b-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.VALUE_OUT_OF_ENUM,
            evidence={"action": "set_light", "arg_name": "room",
                      "value": "living room", "allowed": []},
        ))

    patches = synthesize_rules(classifs, traces, CATALOG)
    assert len(patches) >= 2
    confs = [p.evidence["confidence"] for p in patches]
    assert confs == sorted(confs, reverse=True), (
        f"patches not sorted by confidence desc: {confs}"
    )


def test_rule_patch_to_dict_round_trips():
    patch = RulePatch(
        patch_id="rs-iot_light_5-abcdef012345",
        catalog_id="iot_light_5",
        target_tool="set_light",
        operation="set_default",
        payload={"arg": "state", "default": "on", "predicate_hint": {"requires_args": ["brightness"]}},
        evidence={"failure_count": 12, "support_share": 0.86,
                  "example_trace_ids": ["t1", "t2", "t3"], "confidence": 0.92},
        source_failure_type=FailureType.MISSING_REQUIRED_ARG,
        created_at="2026-05-20T00:00:00Z",
    )
    d = patch.to_dict()
    restored = RulePatch.from_dict(d)
    assert restored == patch
    assert restored.source_failure_type == FailureType.MISSING_REQUIRED_ARG


def test_write_synthesis_summary_histogram_shape(tmp_path: Path):
    patches = [
        RulePatch(
            patch_id=f"rs-x-{i:012x}",
            catalog_id="iot_light_5",
            target_tool="set_light",
            operation="set_default",
            payload={"arg": "state", "default": "on"},
            evidence={"failure_count": 5, "support_share": 1.0,
                      "example_trace_ids": [], "confidence": c},
            source_failure_type=FailureType.MISSING_REQUIRED_ARG,
            created_at="2026-05-20T00:00:00Z",
        )
        for i, c in enumerate([0.9, 0.75, 0.5, 0.45, 0.2, 0.1])
    ]
    summary_path = tmp_path / "summary.json"
    summary = write_synthesis_summary(patches, summary_path)
    assert summary["total_patches"] == 6
    assert summary["by_failure_type"] == {"missing_required_arg": 6}
    # Confidence bands: low < 0.4 → 2, mid 0.4..0.7 → 2, high ≥ 0.7 → 2.
    assert summary["confidence_histogram"] == {"low": 2, "mid": 2, "high": 2}
    on_disk = json.loads(summary_path.read_text())
    assert on_disk == summary


def test_write_proposed_patches_sidecar_jsonl(tmp_path: Path):
    patches = [
        RulePatch(
            patch_id="rs-iot_light_5-aaaaaaaaaaaa",
            catalog_id="iot_light_5",
            target_tool="set_light",
            operation="set_default",
            payload={"arg": "state", "default": "on"},
            evidence={"failure_count": 5, "support_share": 0.5,
                      "example_trace_ids": [], "confidence": 0.8},
            source_failure_type=FailureType.MISSING_REQUIRED_ARG,
            created_at="2026-05-20T00:00:00Z",
        ),
    ]
    out_path = tmp_path / "proposed_patches.jsonl"
    write_proposed_patches_sidecar(patches, out_path)
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["operation"] == "set_default"
    assert row["target_tool"] == "set_light"
    # Round-trips through from_dict cleanly.
    restored = RulePatch.from_dict(row)
    assert restored.patch_id == patches[0].patch_id


def test_write_proposed_patches_empty_yields_empty_file(tmp_path: Path):
    out_path = tmp_path / "proposed_patches.jsonl"
    write_proposed_patches_sidecar([], out_path)
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == ""


def test_patch_id_is_deterministic_for_same_payload():
    """Same operation + target + payload → same patch_id (content addressing)."""
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(5):
        plan = {"calls": [{"action": "list_devices", "args": {"phantom_id": "8"}}]}
        gold = {"calls": [{"action": "list_devices", "args": {}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.UNKNOWN_ARG,
            evidence={"action": "list_devices", "arg_name": "phantom_id"},
        ))
    a = synthesize_rules(classifs, traces, CATALOG)
    b = synthesize_rules(classifs, traces, CATALOG)
    assert [p.patch_id for p in a] == [p.patch_id for p in b]


def test_below_threshold_for_enum_returns_empty():
    """Enum alias matcher needs K=3 traces; 2 should not emit."""
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(2):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living room", "state": "on"}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on"}}]}
        traces.append(_make_trace(case_id=f"c-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.VALUE_OUT_OF_ENUM,
            evidence={"action": "set_light", "arg_name": "room",
                      "value": "living room"},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert patches == []


def test_abstention_miss_should_call_emits_prompt_correction():
    classifs: list[Classification] = []
    traces: list[Trace] = []
    for i in range(5):
        # Empty pred plan, gold has set_light.
        plan = {"calls": []}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on"}}]}
        traces.append(_make_trace(
            case_id=f"c-{i}", plan=plan, expected_plan=gold,
            prompt="turn on living room light",
        ))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.ABSTENTION_MISS_SHOULD_CALL,
            evidence={"predicted_count": 0, "expected_count": 1},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.operation == "add_prompt_correction"
    assert patch.target_tool == "set_light"
    assert "system_nudge" in patch.payload


def test_non_functional_alias_map_skipped():
    """If "living room" maps to BOTH "living" and "bedroom" across traces, drop the patch."""
    classifs: list[Classification] = []
    traces: list[Trace] = []
    # 3 traces: living room → living
    for i in range(3):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living room", "state": "on"}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "living", "state": "on"}}]}
        traces.append(_make_trace(case_id=f"a-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.VALUE_OUT_OF_ENUM,
            evidence={"action": "set_light", "arg_name": "room",
                      "value": "living room"},
        ))
    # 2 traces: living room → bedroom (contradictory mapping)
    for i in range(2):
        plan = {"calls": [{"action": "set_light",
                           "args": {"room": "living room", "state": "on"}}]}
        gold = {"calls": [{"action": "set_light",
                           "args": {"room": "bedroom", "state": "on"}}]}
        traces.append(_make_trace(case_id=f"b-{i}", plan=plan, expected_plan=gold))
        classifs.append(_classification(
            trace_id=traces[-1].trace_id,
            failure_type=FailureType.VALUE_OUT_OF_ENUM,
            evidence={"action": "set_light", "arg_name": "room",
                      "value": "living room"},
        ))
    patches = synthesize_rules(classifs, traces, CATALOG)
    # Functional check fails → no add_alias patch emitted.
    assert all(p.operation != "add_alias" for p in patches)
