"""End-to-end failure path across the substrate (contract §1.8, errata E14).

A scripted completer emits a DSL payload the catalog's defaults do NOT rescue
(``set_light`` with ``room`` only — ``state`` is required and no ``brightness``
/ ``color_temp`` triggers the ``defaults_when_missing`` hook). It is driven
through ``run_dsl_with_repair`` → ``run_iot`` → ``traces_from_results`` →
``classify`` → ``synthesize_rules(..., golds=)`` and must:

- surface as ``RepairExhaustedError`` whose ``.raw`` the runner preserves;
- yield a ``Trace`` with ``plan is None``, non-empty ``attempts`` and a
  decoded ``raw_plan``;
- classify as ``MISSING_REQUIRED_ARG`` (not abstention, not syntax);
- produce a ``set_default`` patch for ``set_light.state`` with default ``"on"``.

Specs: [[analyzer_trace_store]], [[analyzer_failure_taxonomy]],
[[analyzer_rule_synthesis]], [[analyzer_repair_policy]], [[benchmark_iot]].
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.repair import (
    RepairConfig,
    RepairExhaustedError,
    run_dsl_with_repair,
)
from ganglion.analyzer.rules import RuleSynthConfig, make_patch_id, synthesize_rules
from ganglion.analyzer.taxonomy import FailureType, classify, classify_traces
from ganglion.analyzer.trace import Trace, TraceStore
from ganglion.benchmarks.iot.dataset import EvalCase
from ganglion.benchmarks.iot.runner import run_iot, traces_from_results
from ganglion.contract.builtins import get_catalog
from ganglion.contract.types import ActionPlan, ToolCall

# pytest prepend mode: `tests/` has no __init__, so sibling modules import bare.
from test_repair_loop import ScriptedCompleter

CATALOG = get_catalog("iot_light_5")
PROMPT = "거실 불 켜줘"
# `state` missing, no brightness/color_temp → `set_light.state is required`
# (the defaults_when_missing hook needs brightness or color_temp to fire).
MISSING_STATE = '{"calls":[{"action":"set_light","args":{"room":"living"}}]}'
GOLD = ActionPlan(calls=(ToolCall("set_light", {"room": "living", "state": "on"}),))


class _Client:
    """`ScriptedCompleter` implements `complete(messages)`, not `invoke` —
    wrap it in the same shape `QwenJSONDSLClient` uses (errata E14)."""

    def __init__(self, completer: ScriptedCompleter, repair: RepairConfig | None = None) -> None:
        self.completer = completer
        self.repair = repair or RepairConfig()

    def invoke(self, prompt: str):
        return run_dsl_with_repair(CATALOG, prompt, self.completer, self.repair)


def _cases(n: int) -> list[EvalCase]:
    return [EvalCase(id=f"case-{i}", prompt=PROMPT, expected=GOLD) for i in range(n)]


def _run_failure_path(n: int = 5):
    completer = ScriptedCompleter([MISSING_STATE] * n)
    results = run_iot(_Client(completer), _cases(n))
    traces = traces_from_results(
        results, catalog_id="iot_light_5", run_id="failure-path", model_id="scripted",
    )
    return results, traces


def test_missing_state_is_not_rescued_by_defaults() -> None:
    """Sanity: the fixture output really fails validation."""
    with pytest.raises(RepairExhaustedError) as excinfo:
        run_dsl_with_repair(CATALOG, PROMPT, ScriptedCompleter([MISSING_STATE]), RepairConfig())
    assert "set_light.state is required" in str(excinfo.value)
    assert excinfo.value.attempts[0]["content"] == MISSING_STATE
    assert excinfo.value.raw["final_content"] == MISSING_STATE


def test_runner_preserves_raw_from_exception() -> None:
    results, _ = _run_failure_path(1)
    run = results[0].runs[0]
    assert run.plan is None
    assert run.error is not None and run.error.startswith("RepairExhaustedError")
    assert isinstance(run.raw, dict)
    assert run.raw["attempts"][0]["content"] == MISSING_STATE
    assert run.raw["attempts"][0]["error"] == "set_light.state is required"


def test_trace_has_raw_plan_and_attempts_but_no_plan() -> None:
    _, traces = _run_failure_path(1)
    assert len(traces) == 1
    tr = traces[0]
    assert tr.plan is None
    assert tr.attempts and tr.attempts[0]["content"] == MISSING_STATE
    assert tr.raw_plan == {"calls": [{"action": "set_light", "args": {"room": "living"}}]}
    assert tr.raw_output == MISSING_STATE
    assert tr.error_type is not None and tr.error_type.startswith("RepairExhaustedError")
    assert tr.expected_plan == GOLD.to_jsonable()
    assert tr.source == "benchmark.iot"
    assert tr.repeat_index == 0
    assert tr.parse_strategy == "json_object"


def test_classifies_as_missing_required_arg_not_abstention() -> None:
    _, traces = _run_failure_path(1)
    tr = traces[0]
    with_gold = classify(tr, catalog=CATALOG, gold=GOLD)
    assert with_gold.failure_type == FailureType.MISSING_REQUIRED_ARG
    assert with_gold.confidence == 1.0
    assert with_gold.evidence["arg_name"] == "state"
    assert with_gold.evidence["action"] == "set_light"
    # Also without gold: the structural matcher reads raw_plan.
    without_gold = classify(tr, catalog=CATALOG)
    assert without_gold.failure_type == FailureType.MISSING_REQUIRED_ARG
    assert without_gold.failure_type not in {
        FailureType.ABSTENTION_MISS_SHOULD_CALL,
        FailureType.SYNTAX_INVALID,
    }


def test_synthesize_rules_with_golds_proposes_set_default() -> None:
    results, traces = _run_failure_path(5)
    golds = {case.id: GOLD for case in results}
    classifs = classify_traces(traces, catalog=CATALOG, golds=golds)
    assert {c.failure_type for c in classifs} == {FailureType.MISSING_REQUIRED_ARG}

    patches = synthesize_rules(classifs, traces, CATALOG, RuleSynthConfig(), golds=golds)
    set_defaults = [p for p in patches if p.operation == "set_default"]
    assert len(set_defaults) == 1
    patch = set_defaults[0]
    assert patch.target_tool == "set_light"
    assert patch.payload["arg"] == "state"
    assert patch.payload["default"] == "on"
    assert patch.payload["predicate_hint"] == {"requires_args": ["room"]}
    assert patch.source_failure_type == FailureType.MISSING_REQUIRED_ARG
    assert patch.catalog_id == CATALOG.name
    assert patch.patch_id == make_patch_id(
        CATALOG.name, "set_default", "set_light", patch.payload,
    )
    assert patch.evidence["failure_count"] == 5
    assert set(patch.evidence["example_trace_ids"]) <= {t.trace_id for t in traces}


def test_label_gold_wins_over_expected_plan() -> None:
    """A `golds=` entry overrides the trace's dataset `expected_plan`."""
    results, traces = _run_failure_path(5)
    label_gold = ActionPlan(calls=(ToolCall("set_light", {"room": "living", "state": "off"}),))
    golds = {case.id: label_gold for case in results}
    classifs = classify_traces(traces, catalog=CATALOG, golds=golds)
    patches = synthesize_rules(classifs, traces, CATALOG, golds=golds)
    [patch] = [p for p in patches if p.operation == "set_default"]
    assert patch.payload["default"] == "off"


def test_traces_persist_and_reload_with_raw_plan(tmp_path: Path) -> None:
    _, traces = _run_failure_path(2)
    store = TraceStore(tmp_path)
    ids = {store.append(tr) for tr in traces}
    assert len(ids) == 2  # distinct case_ids → distinct trace_ids
    shard = tmp_path / "iot_light_5" / "failure-path" / "traces.jsonl"
    rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines()]
    assert all(row["plan"] is None for row in rows)
    assert all(row["raw_plan"]["calls"][0]["action"] == "set_light" for row in rows)
    reloaded = list(store.iter(catalog_id="iot_light_5", run_id="failure-path"))
    assert reloaded == traces
    assert all(isinstance(tr, Trace) and tr.raw_plan is not None for tr in reloaded)


def test_repair_enabled_still_preserves_full_chain() -> None:
    """With repair on and two bad answers, both attempts survive into the trace."""
    completer = ScriptedCompleter([MISSING_STATE, MISSING_STATE])
    results = run_iot(
        _Client(completer, RepairConfig(enabled=True, max_attempts=1)), _cases(1),
    )
    [trace] = traces_from_results(
        results, catalog_id="iot_light_5", run_id="repair-on", model_id="scripted",
    )
    assert trace.plan is None
    assert len(trace.attempts) == 2
    assert [a["attempt"] for a in trace.attempts] == [0, 1]
    assert trace.raw_plan is not None
    assert classify(trace, catalog=CATALOG).failure_type == FailureType.MISSING_REQUIRED_ARG
