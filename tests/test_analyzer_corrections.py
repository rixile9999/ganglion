"""Tests for ``ganglion.analyzer.corrections`` — [[analyzer_correction_attribution]].

Fixtures are produced by real clients through ``run_iot`` →
``traces_from_results``: the rules client, the degraded wrapper, and a
scripted client that returns a fixed payload (for the regression case).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ganglion.analyzer.corrections import (
    RETIRE_MAX_UPPER,
    RETIRE_MIN_ACTIVE,
    Attribution,
    attribute,
    cp95_upper_at_zero,
    hook_class,
    retire_candidates_as_patches,
    summarize_corrections,
    write_corrections,
)
from ganglion.analyzer.taxonomy import FailureType
from ganglion.analyzer.trace import Trace, attempts_from_raw
from ganglion.benchmarks.iot.dataset import EvalCase
from ganglion.benchmarks.iot.runner import run_iot, traces_from_results
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.contract.types import ActionPlan, ToolCall
from ganglion.lm.client import ModelResult

from analyzer_fixtures import CATALOG, CATALOG_ID, DegradedRulesClient, _OutputError, load_cases


class ScriptedClient:
    """Returns one fixed payload for every prompt (validated through the catalog)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def invoke(self, prompt: str) -> ModelResult:
        try:
            plan = CATALOG.parse_json_dsl(self.payload, prompt=prompt)
        except DSLValidationError as exc:
            raise _OutputError(str(exc), raw=self.payload, attempts=attempts_from_raw(self.payload)) from exc
        return ModelResult(plan=plan, raw=self.payload, latency_ms=0.1)


def _traces(client: Any, cases: list[EvalCase], run_id: str = "corr") -> list[Trace]:
    return traces_from_results(run_iot(client, cases), catalog_id=CATALOG_ID, run_id=run_id, model_id="fixture")


def _golds(cases: list[EvalCase], origin: str = "dataset") -> dict[str, tuple[ActionPlan, str]]:
    return {c.id: (c.expected, origin) for c in cases}


def _plan(*calls: tuple[str, dict[str, Any]]) -> ActionPlan:
    return ActionPlan(tuple(ToolCall(a, dict(args)) for a, args in calls))


def test_defaults_rescue_missing_state() -> None:
    case = EvalCase("c1", "거실 불 70%로 켜줘", _plan(("set_light", {"room": "living", "state": "on", "brightness": 70})))
    payload = {"calls": [{"action": "set_light", "args": {"room": "living", "brightness": 70}}]}
    [trace] = _traces(ScriptedClient(payload), [case])
    assert trace.plan is not None  # the default rescued it at run time
    att = attribute(CATALOG, trace, case.expected, gold_origin="dataset")
    assert att.f0_ok is False and att.fk_ok is True
    assert att.rescued and not att.regressed
    assert att.rescued_by == ("set_light:defaults_when_missing",)
    assert att.necessary_hooks == ("set_light:defaults_when_missing",)
    assert att.active_hooks == ("set_light:defaults_when_missing",)
    assert att.f0_plan is None and att.fk_plan == case.expected.to_jsonable()
    assert not att.unattributable
    assert Attribution.from_dict(json.loads(json.dumps(att.to_dict()))) == att
    assert hook_class(CATALOG.get_tool("set_light"), "defaults_when_missing") == "conservative"
    assert hook_class(CATALOG.get_tool("set_light"), "prompt_correction") == "rewriting"
    assert hook_class(None, "strip_unknown_args") == "conservative"


def test_strip_rescues_echoed_id_and_is_conservative() -> None:
    cases = [c for c in load_cases(70) if c.prompt.rstrip().endswith(tuple(f"#{i}" for i in range(0, 60)))][:6]
    assert len(cases) >= 3, "dataset should carry #N prompts"
    client = DegradedRulesClient(drop_state=False, echo_id=True)
    traces = _traces(client, cases)
    assert all(t.plan is not None for t in traces)  # strip_unknown_args rescued every one
    summary = summarize_corrections(CATALOG, traces, _golds(cases))
    strip_hooks = {k: v for k, v in summary["by_hook"].items() if k.endswith(":strip_unknown_args")}
    assert strip_hooks, summary["by_hook"]
    for stats in strip_hooks.values():
        assert stats["class"] == "conservative"
        assert stats["regressions"] == 0
        assert stats["rescues"] >= 1 and stats["n_active"] >= stats["rescues"]
        assert stats["verdict"] in {"keep", "insufficient_evidence"}
    assert summary["em_f0"] == 0.0 and summary["em_fk"] == 1.0
    assert summary["by_gold_origin"]["dataset"]["n"] == len(cases)
    assert summary["rescue"] == 1.0 and summary["regression"] == 0.0


def test_no_hook_involvement_gives_f0_equals_fk() -> None:
    [case] = [c for c in load_cases(10) if c.id == "iot-008"]  # create_scene movie
    from ganglion.lm.rules import RuleBasedJSONDSLClient

    [trace] = _traces(RuleBasedJSONDSLClient(), [case])
    att = attribute(CATALOG, trace, case.expected, gold_origin="dataset")
    assert att.f0_ok and att.fk_ok
    assert att.f0_plan == att.fk_plan == case.expected.to_jsonable()
    assert att.necessary_hooks == () and att.active_hooks == ()
    assert not att.rescued and not att.regressed


def test_empty_attempts_is_unattributable_and_does_not_move_em() -> None:
    case = EvalCase("c1", "거실 불 켜줘", _plan(("set_light", {"room": "living", "state": "on"})))
    [good] = _traces(ScriptedClient({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}), [case])
    empty = Trace.from_dict({**good.to_dict(), "attempts": [], "raw_output": "", "raw_plan": None, "plan": None,
                     "trace_id": "", "case_id": "c2"})
    assert empty.attempts == ()
    att = attribute(CATALOG, empty, case.expected, gold_origin="dataset")
    assert att.unattributable and att.f0_ok is None and att.fk_ok is None
    assert attribute(CATALOG, good, None, gold_origin="none").unattributable
    golds = {"c1": (case.expected, "dataset"), "c2": (case.expected, "dataset")}
    summary = summarize_corrections(CATALOG, [good, empty], golds)
    assert summary["n"] == 2 and summary["unattributable"] == 1
    assert summary["em_f0"] == 1.0 and summary["em_fk"] == 1.0
    assert summary["by_gold_origin"]["dataset"]["n"] == 1


def test_non_json_first_attempt_is_graded_fail_on_both_sides() -> None:
    case = EvalCase("c1", "거실 불 켜줘", _plan(("set_light", {"room": "living", "state": "on"})))
    [good] = _traces(ScriptedClient({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}), [case])
    garbage = Trace.from_dict({**good.to_dict(), "attempts": [{"attempt": 0, "content": "not json at all"}],
                       "raw_plan": None, "plan": None, "trace_id": ""})
    att = attribute(CATALOG, garbage, case.expected, gold_origin="dataset")
    assert att.f0_ok is False and att.fk_ok is False and not att.unattributable
    assert att.rescued_by == () and att.regressed_by == ()


def test_prompt_correction_regression_under_label_gold() -> None:
    # Raw says bedroom; the label insists bedroom is right; the prompt names
    # 거실 so `_correct_room_from_prompt` rewrites it → F⁰ passes, Fᴷ fails.
    case = EvalCase("c1", "거실 불 켜줘", _plan(("set_light", {"room": "bedroom", "state": "on"})))
    payload = {"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on"}}]}
    [trace] = _traces(ScriptedClient(payload), [case])
    att = attribute(CATALOG, trace, case.expected, gold_origin="human:operator")
    assert att.f0_ok is True and att.fk_ok is False
    assert att.regressed and not att.rescued
    assert att.regressed_by == ("set_light:prompt_correction",)
    assert "set_light:prompt_correction" in att.active_hooks
    summary = summarize_corrections(CATALOG, [trace], {"c1": (case.expected, "human:operator")})
    stats = summary["by_hook"]["set_light:prompt_correction"]
    assert stats["class"] == "rewriting" and stats["regressions"] == 1 and stats["rescues"] == 0
    assert stats["verdict"] == "insufficient_evidence"  # n_active 1 < 30
    origin = summary["by_gold_origin"]["human:operator"]
    assert origin["em_f0"] == 1.0 and origin["em_fk"] == 0.0 and origin["regression"] == 1.0
    assert summary["em_fk"] - summary["em_f0"] == pytest.approx(summary["rescue"] - summary["regression"])


def _echo_cases(n: int) -> list[EvalCase]:
    return [
        EvalCase(f"echo-{i}", f"거실 불 켜줘 #{i}", _plan(("set_light", {"room": "living", "state": "on"})))
        for i in range(n)
    ]


def test_retire_candidate_gate_at_40_and_20(tmp_path: Path) -> None:
    # drop_state + echo_id: `state` is missing (no default fires: no brightness)
    # and `id` is echoed, so strip_unknown_args fires on every trace but can
    # never rescue — 0 rescues over n_active traces.
    client = DegradedRulesClient(drop_state=True, echo_id=True)
    for n, expected_verdict in ((40, "retire_candidate"), (20, "insufficient_evidence")):
        cases = _echo_cases(n)
        traces = _traces(client, cases, run_id=f"retire-{n}")
        assert all(t.plan is None and t.raw_plan is not None for t in traces)
        attributions: list[Attribution] = []
        summary = summarize_corrections(CATALOG, traces, _golds(cases), attributions_out=attributions)
        assert len(attributions) == n
        stats = summary["by_hook"]["set_light:strip_unknown_args"]
        assert stats["n_active"] == n and stats["rescues"] == 0
        assert stats["necessary"] == 0  # removing strip changes nothing: both sides fail
        assert stats["cp95_upper"] == pytest.approx(cp95_upper_at_zero(n), abs=1e-4)
        assert stats["verdict"] == expected_verdict
        assert "set_light:defaults_when_missing" not in summary["by_hook"]
        patches = retire_candidates_as_patches(CATALOG_ID, summary)
        if expected_verdict == "retire_candidate":
            assert stats["cp95_upper"] < RETIRE_MAX_UPPER and n >= RETIRE_MIN_ACTIVE
            assert summary["n_retire_candidates"] == 1
            [patch] = patches
            assert patch.operation == "retire_rule"
            assert patch.patch_id.startswith("rs-iot_light_5-")
            assert patch.target_tool == "set_light"
            assert patch.payload == {"hook_kind": "strip_unknown_args", "arg": None}
            assert patch.source_failure_type == FailureType.NO_FAILURE
            assert patch.evidence["failure_count"] == n and patch.evidence["support_share"] == 1.0
            assert patch.evidence["confidence"] == pytest.approx(1 - stats["cp95_upper"], abs=1e-4)
            assert len(patch.evidence["example_trace_ids"]) == 5
            assert set(patch.evidence["example_trace_ids"]) <= {t.trace_id for t in traces}
            rows_path, summary_path = write_corrections(tmp_path, CATALOG_ID, f"retire-{n}", attributions, summary)
            assert rows_path.name == "corrections.jsonl" and summary_path.name == "corrections.summary.json"
            rows = [json.loads(l) for l in rows_path.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == n and rows[0]["active_hooks"] == ["set_light:strip_unknown_args"]
            assert json.loads(summary_path.read_text(encoding="utf-8"))["by_hook"] == summary["by_hook"]
        else:
            assert stats["cp95_upper"] >= RETIRE_MAX_UPPER
            assert patches == [] and summary["n_retire_candidates"] == 0


def test_defaults_arg_is_recorded_for_retire_payload() -> None:
    case = EvalCase("c1", "거실 불 70%로 켜줘", _plan(("set_light", {"room": "living", "state": "on", "brightness": 70})))
    payload = {"calls": [{"action": "set_light", "args": {"room": "living", "brightness": 70}}]}
    traces = _traces(ScriptedClient(payload), [case])
    summary = summarize_corrections(CATALOG, traces, _golds([case]))
    assert summary["by_hook"]["set_light:defaults_when_missing"]["arg"] == "state"
    assert summary["by_hook"]["set_light:defaults_when_missing"]["verdict"] == "insufficient_evidence"
    assert summary["shared"] == 0
    assert summary["by_hook"]["set_light:defaults_when_missing"]["cp95_upper"] is None
