"""Tests for analyzer.taxonomy — deterministic failure-type classifier (M4-D).

Covers the priority-ordered matchers from
docs/tasks/analyzer_failure_taxonomy.md: one trace fixture per FailureType,
plus a small bulk-classification smoke and the JSONL sidecar writer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ganglion.analyzer.taxonomy import (
    Classification,
    FailureType,
    classify,
    classify_traces,
    write_classified_sidecar,
)
from dataclasses import replace

from ganglion.analyzer.trace import Trace
from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import StringArg, ToolSpec
from ganglion.contract.types import ActionPlan, ToolCall


CATALOG = get_catalog("iot_light_5")


def _make_trace(
    *,
    case_id: str = "case-1",
    parse_strategy: str = "strict",
    plan: dict | None = None,
    error_type: str | None = None,
    raw_output: str = '{"calls": []}',
) -> Trace:
    return Trace(
        case_id=case_id,
        catalog_id="iot_light_5",
        run_id="run-test",
        source="benchmark.iot",
        prompt="turn on the living room light",
        raw_output=raw_output,
        parse_strategy=parse_strategy,
        latency_ms=10.0,
        input_tokens_total=10,
        output_tokens_total=5,
        model_id="rules",
        timestamp="2026-05-20T00:00:00Z",
        attempts=(
            {
                "attempt": 0,
                "content": raw_output,
                "input_tokens": 10,
                "output_tokens": 5,
            },
        ),
        expected_plan=None,
        plan=plan,
        error_type=error_type,
    )


def _plan(action: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"calls": [{"action": action, "args": args}]}


def _action_plan(action: str, args: dict[str, Any]) -> ActionPlan:
    return ActionPlan(calls=(ToolCall(action=action, args=args),))


# ---------------------------------------------------------------------------
# Per-FailureType fixtures
# ---------------------------------------------------------------------------


def test_syntax_invalid_via_parse_strategy():
    trace = _make_trace(parse_strategy="failed", plan=None, raw_output="{not json")
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.SYNTAX_INVALID
    assert result.confidence == 1.0
    assert "raw" in result.evidence


def test_syntax_invalid_via_error_type():
    trace = _make_trace(
        parse_strategy="strict",
        plan=None,
        error_type="invalid JSON: unexpected token",
    )
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.SYNTAX_INVALID


def test_unknown_tool():
    trace = _make_trace(plan=_plan("activate_warp_drive", {"speed": 9}))
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.UNKNOWN_TOOL
    assert result.evidence["predicted_action"] == "activate_warp_drive"
    assert "set_light" in result.evidence["known_actions"]


def test_wrong_action():
    # Both actions exist in the catalog; predicted is the wrong one.
    pred = _plan("get_light_state", {"room": "living"})
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    trace = _make_trace(plan=pred)
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.WRONG_ACTION
    assert result.evidence["predicted_action"] == "get_light_state"
    assert result.evidence["expected_action"] == "set_light"


def test_abstention_miss_should_call():
    trace = _make_trace(plan={"calls": []})
    gold = _action_plan("get_light_state", {"room": "living"})
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.ABSTENTION_MISS_SHOULD_CALL
    assert result.evidence == {"predicted_count": 0, "expected_count": 1}


def test_abstention_miss_should_abstain():
    trace = _make_trace(plan=_plan("get_light_state", {"room": "living"}))
    gold = ActionPlan(calls=())
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.ABSTENTION_MISS_SHOULD_ABSTAIN
    assert result.evidence == {"predicted_count": 1, "expected_count": 0}


def test_missing_required_arg():
    # set_light requires `room` and `state`; here `state` is missing.
    trace = _make_trace(plan=_plan("set_light", {"room": "living"}))
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.MISSING_REQUIRED_ARG
    assert result.evidence["arg_name"] == "state"
    assert result.evidence["action"] == "set_light"


def test_unknown_arg():
    # set_light has no `tempo` arg.
    trace = _make_trace(
        plan=_plan("set_light", {"room": "living", "state": "on", "tempo": "fast"})
    )
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.UNKNOWN_ARG
    assert result.evidence["arg_name"] == "tempo"
    assert result.evidence["action"] == "set_light"


def test_value_out_of_enum():
    # `state` is an EnumArg with values ("on", "off"); "blue" matches no
    # value or alias.
    trace = _make_trace(plan=_plan("set_light", {"room": "living", "state": "blue"}))
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.VALUE_OUT_OF_ENUM
    assert result.evidence["arg_name"] == "state"
    assert result.evidence["value"] == "blue"
    assert "on" in result.evidence["allowed"]


def test_value_out_of_range():
    # `brightness` is IntArg(min=0, max=100). 150 should trip the range
    # matcher (state="on" is required + present so we get past the earlier
    # priority matchers).
    trace = _make_trace(
        plan=_plan(
            "set_light",
            {"room": "living", "state": "on", "brightness": 150},
        )
    )
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.VALUE_OUT_OF_RANGE
    assert result.evidence["arg_name"] == "brightness"
    assert result.evidence["value"] == 150


def test_no_failure_on_exact_match():
    pred = _plan("set_light", {"room": "living", "state": "on"})
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    trace = _make_trace(plan=pred)
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.NO_FAILURE
    assert result.confidence == 1.0


def test_no_failure_degenerate_when_no_catalog_no_gold():
    trace = _make_trace(plan=_plan("set_light", {"room": "living", "state": "on"}))
    result = classify(trace)
    assert result.failure_type == FailureType.NO_FAILURE
    # Degenerate: no catalog + no gold → low-confidence NO_FAILURE.
    assert result.confidence == 0.0


def test_partial_arg_value_mismatch():
    pred = _plan("set_light", {"room": "living", "state": "off"})
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    trace = _make_trace(plan=pred)
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.PARTIAL_ARG_VALUE_MISMATCH
    assert "differing" in result.evidence
    assert "state" in result.evidence["differing"]


def test_alias_unrecognised_heuristic():
    # Build a tiny ad-hoc catalog with a StringArg that has aliases, so the
    # alias-look-alike matcher can fire on an unmapped value.
    custom = Catalog(
        name="aliasable",
        tools=(
            ToolSpec(
                name="say_room",
                description="echo room",
                args=(
                    (
                        "room",
                        StringArg(aliases={"living": "living", "거실": "living"}),
                    ),
                ),
            ),
        ),
    )
    trace = _make_trace(plan=_plan("say_room", {"room": "lounge"}))
    result = classify(trace, catalog=custom)
    assert result.failure_type == FailureType.ALIAS_UNRECOGNISED
    # Heuristic matcher → confidence in 0.7-0.9 band.
    assert 0.7 <= result.confidence < 1.0
    assert result.evidence["value"] == "lounge"


def test_parallel_order_mismatch():
    pred = {
        "calls": [
            {"action": "get_light_state", "args": {"room": "bedroom"}},
            {"action": "get_light_state", "args": {"room": "living"}},
        ]
    }
    gold = ActionPlan(
        calls=(
            ToolCall(action="get_light_state", args={"room": "living"}),
            ToolCall(action="get_light_state", args={"room": "bedroom"}),
        )
    )
    trace = _make_trace(plan=pred)
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.PARALLEL_ORDER_MISMATCH


# ---------------------------------------------------------------------------
# Bulk + sidecar
# ---------------------------------------------------------------------------


def test_bulk_classify_traces_preserves_unique_trace_ids():
    traces = []
    for i, plan in enumerate(
        [
            None,  # syntax_invalid via parse_strategy
            _plan("activate_warp_drive", {}),  # unknown_tool
            _plan("set_light", {"room": "living"}),  # missing_required_arg
            _plan("set_light", {"room": "living", "state": "blue"}),  # value_out_of_enum
            _plan("set_light", {"room": "living", "state": "on"}),  # no_failure
        ]
    ):
        traces.append(
            _make_trace(
                case_id=f"case-{i}",
                plan=plan,
                parse_strategy="failed" if plan is None else "strict",
            )
        )

    classifications = classify_traces(traces, catalog=CATALOG)
    assert len(classifications) == 5
    # Unique trace_ids preserved 1:1 with input order.
    assert [c.trace_id for c in classifications] == [t.trace_id for t in traces]
    assert len({c.trace_id for c in classifications}) == 5

    types = [c.failure_type for c in classifications]
    assert types[0] == FailureType.SYNTAX_INVALID
    assert types[1] == FailureType.UNKNOWN_TOOL
    assert types[2] == FailureType.MISSING_REQUIRED_ARG
    assert types[3] == FailureType.VALUE_OUT_OF_ENUM
    assert types[4] == FailureType.NO_FAILURE


def test_classify_traces_uses_golds_mapping():
    pred = _plan("get_light_state", {"room": "living"})
    trace = _make_trace(plan=pred)
    golds = {
        trace.case_id: _action_plan("set_light", {"room": "living", "state": "on"}),
    }
    [result] = classify_traces([trace], catalog=CATALOG, golds=golds)
    assert result.failure_type == FailureType.WRONG_ACTION


def test_write_classified_sidecar_jsonl_roundtrip(tmp_path: Path):
    items = [
        Classification(
            trace_id="tr-aaa",
            failure_type=FailureType.SYNTAX_INVALID,
            confidence=1.0,
            evidence={"raw": "{not json", "parse_error": "json error"},
        ),
        Classification(
            trace_id="tr-bbb",
            failure_type=FailureType.NO_FAILURE,
            confidence=1.0,
            evidence={},
        ),
    ]
    out = tmp_path / "sub" / "classified.jsonl"
    write_classified_sidecar(items, out)
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert payloads[0]["trace_id"] == "tr-aaa"
    assert payloads[0]["failure_type"] == "syntax_invalid"
    assert payloads[0]["confidence"] == 1.0
    assert payloads[0]["evidence"]["raw"] == "{not json"
    assert payloads[1]["failure_type"] == "no_failure"
    assert payloads[1]["evidence"] == {}


def test_priority_ordering_syntax_beats_unknown_tool():
    """When both `syntax_invalid` and a downstream signal could fire, the
    higher-priority matcher wins. Here parse_strategy=failed should dominate
    even if a plan field is also present."""
    trace = _make_trace(
        parse_strategy="failed",
        plan=_plan("activate_warp_drive", {}),  # would otherwise be unknown_tool
        raw_output="{not json",
    )
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.SYNTAX_INVALID


# ---------------------------------------------------------------------------
# 2026-09-16 revision: matchers read raw_plan when validation failed
# ---------------------------------------------------------------------------


def _failed_trace(raw_plan: dict | None, *, error_type: str | None = "RepairExhaustedError: x",
                  raw_output: str | None = None) -> Trace:
    """A validation-failure trace: plan None, raw_plan as given."""
    if raw_output is None:
        raw_output = json.dumps(raw_plan) if raw_plan is not None else ""
    return Trace(
        case_id="case-f",
        catalog_id="iot_light_5",
        run_id="run-test",
        source="benchmark.iot",
        prompt="turn on the living room light",
        raw_output=raw_output,
        parse_strategy="json_object",
        latency_ms=10.0,
        input_tokens_total=10,
        output_tokens_total=5,
        model_id="scripted",
        timestamp="2026-05-20T00:00:00Z",
        attempts=({"attempt": 0, "content": raw_output},) if raw_output else (),
        expected_plan=None,
        plan=None,
        error_type=error_type,
        raw_plan=raw_plan,
    )


def test_raw_plan_missing_required_arg_is_not_abstention_or_syntax():
    trace = _failed_trace(_plan("set_light", {"room": "living"}))
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.MISSING_REQUIRED_ARG
    assert result.evidence["arg_name"] == "state"
    # Same verdict without gold.
    assert classify(trace, catalog=CATALOG).failure_type == FailureType.MISSING_REQUIRED_ARG


def test_raw_plan_unknown_arg_and_enum_value():
    unknown = _failed_trace(_plan("set_light", {"room": "living", "state": "on", "id": 3}))
    assert classify(unknown, catalog=CATALOG).failure_type == FailureType.UNKNOWN_ARG
    enum = _failed_trace(_plan("set_light", {"room": "living", "state": "blue"}))
    assert classify(enum, catalog=CATALOG).failure_type == FailureType.VALUE_OUT_OF_ENUM
    tool = _failed_trace(_plan("activate_warp_drive", {}))
    assert classify(tool, catalog=CATALOG).failure_type == FailureType.UNKNOWN_TOOL


def test_nothing_decodable_with_error_is_syntax_invalid():
    trace = _failed_trace(None, error_type="RepairExhaustedError: invalid JSON", raw_output="{not json")
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.SYNTAX_INVALID
    assert result.evidence["raw"] == "{not json"
    # error_type alone (no raw text) is enough.
    no_raw = _failed_trace(None, error_type="RuntimeError: boom", raw_output="")
    assert classify(no_raw, catalog=CATALOG).failure_type == FailureType.SYNTAX_INVALID


def test_decodable_raw_plan_is_never_syntax_invalid_even_with_json_error_text():
    trace = _failed_trace(
        _plan("set_light", {"room": "living"}),
        error_type="RepairExhaustedError: could not extract JSON DSL: invalid JSON",
    )
    assert classify(trace, catalog=CATALOG).failure_type == FailureType.MISSING_REQUIRED_ARG


def test_explicit_empty_calls_in_raw_plan_is_abstention():
    trace = _failed_trace({"calls": []}, error_type="RepairExhaustedError: 'calls' must not be empty")
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.ABSTENTION_MISS_SHOULD_CALL
    assert result.evidence == {"predicted_count": 0, "expected_count": 1}


def test_nothing_decodable_without_evidence_but_gold_is_abstention():
    trace = _failed_trace(None, error_type=None, raw_output="")
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    assert classify(trace, catalog=CATALOG, gold=gold).failure_type == FailureType.ABSTENTION_MISS_SHOULD_CALL


def test_validation_failed_but_unexplained_is_unclassified():
    """plan None, raw_plan structurally fine → fall-through is NO_FAILURE @ 0.0."""
    trace = _failed_trace(_plan("set_light", {"room": "living", "state": "on"}),
                          error_type="DSLValidationError: custom validator said no")
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.NO_FAILURE
    assert result.confidence == 0.0
    assert result.evidence.get("unclassified") == "validation_failed"


def test_validated_plan_still_preferred_over_raw_plan():
    """When both exist, `plan` wins (fixtures with dicts in `plan` stay valid)."""
    good = _make_trace(plan=_plan("set_light", {"room": "living", "state": "on"}))
    trace = Trace.from_dict({**good.to_dict(), "raw_plan": _plan("set_light", {"room": "living"}), "trace_id": ""})
    gold = _action_plan("set_light", {"room": "living", "state": "on"})
    result = classify(trace, catalog=CATALOG, gold=gold)
    assert result.failure_type == FailureType.NO_FAILURE
    assert result.confidence == 1.0


def test_chat_path_failed_strategy_with_decodable_raw_is_classified_structurally():
    """`lm.serve` stamps `parse_strategy="failed"` on every errored result.

    Without gating that signal on `raw_plan is None`, every chat validation
    failure was filed as `syntax_invalid` at confidence 1.0 — no structural
    matcher ran, the run histogram was useless and `synthesize_rules` proposed
    nothing. Observed live on an out-of-range brightness.
    """
    raw_plan = _plan("set_light", {"room": "living", "state": "on", "brightness": 250})
    trace = _failed_trace(raw_plan, error_type="ModelOutputError: brightness must be <= 100")
    trace = replace(trace, parse_strategy="failed")
    result = classify(trace, catalog=CATALOG)
    assert result.failure_type == FailureType.VALUE_OUT_OF_RANGE
    assert result.confidence == 1.0
    # ... and with nothing decodable it is still syntax_invalid.
    nothing = replace(
        _failed_trace(None, error_type="ModelOutputError: bad", raw_output="not json"),
        parse_strategy="failed",
    )
    assert classify(nothing, catalog=CATALOG).failure_type == FailureType.SYNTAX_INVALID
