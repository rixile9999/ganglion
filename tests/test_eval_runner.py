from pathlib import Path

from ganglion.analyzer.metrics import summarize
from ganglion.cli import run_eval
from ganglion.lm.rules import RuleBasedJSONDSLClient


def test_rule_model_matches_dataset() -> None:
    results = run_eval(
        RuleBasedJSONDSLClient(),
        Path("examples/iot_light/dataset.jsonl"),
        limit=None,
    )

    summary = summarize(results)
    assert summary["syntax_valid_rate"] == 1.0
    assert summary["exact_match_rate"] == 1.0
    assert summary["failures"] == []


# ---------------------------------------------------------------------------
# 2026-09-16 revision: failure raw preservation + traces_from_results
# ---------------------------------------------------------------------------

import json  # noqa: E402

from ganglion.analyzer.trace import Trace  # noqa: E402
from ganglion.benchmarks.iot.dataset import EvalCase, load_dataset  # noqa: E402
from ganglion.benchmarks.iot.runner import _invoke_once, run_iot, traces_from_results  # noqa: E402
from ganglion.contract.types import ActionPlan, ToolCall  # noqa: E402
from ganglion.lm.client import ModelResult  # noqa: E402


class _RawCarryingError(ValueError):
    def __init__(self, raw) -> None:
        super().__init__("bad output")
        self.raw = raw


class _ScriptedClient:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)

    def invoke(self, prompt: str) -> ModelResult:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_invoke_once_records_raw_from_exception() -> None:
    raw = {"content": "{oops", "parse_strategy": "failed"}
    run = _invoke_once(_ScriptedClient([_RawCarryingError(raw)]), "x")
    assert run.plan is None
    assert run.raw is raw
    assert run.error == "_RawCarryingError: bad output"
    assert run.latency_ms is None and run.input_tokens is None and run.output_tokens is None


def test_invoke_once_without_raw_attribute_records_none() -> None:
    run = _invoke_once(_ScriptedClient([RuntimeError("boom")]), "x")
    assert run.raw is None
    assert run.error == "RuntimeError: boom"


def test_run_iot_with_rules_yields_action_plans() -> None:
    cases = load_dataset(Path("examples/iot_light/dataset.jsonl"), limit=5)
    results = run_iot(RuleBasedJSONDSLClient(), cases)
    assert len(results) == 5
    assert all(isinstance(r.runs[0].plan, ActionPlan) for r in results)


def test_traces_from_results_success_and_failure_and_repeat() -> None:
    gold = ActionPlan(calls=(ToolCall("set_light", {"room": "living", "state": "on"}),))
    cases = [
        EvalCase(id="ok", prompt="거실 불 켜줘", expected=gold),
        EvalCase(id="fail", prompt="거실 불 켜줘", expected=gold),
    ]
    ok_result = ModelResult(
        plan=gold,
        raw={"content": '{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}',
             "parse_strategy": "fenced"},
        latency_ms=12.5,
        input_tokens=None,
        output_tokens=7,
    )
    failure = _RawCarryingError({"attempts": [{"attempt": 0, "content": '{"calls":[{"action":"set_light","args":{"room":"living"}}]}',
                                               "input_tokens": 3, "output_tokens": 4, "error": "state required"}],
                                 "final_content": '{"calls":[{"action":"set_light","args":{"room":"living"}}]}'})
    client = _ScriptedClient([ok_result, ok_result, failure, RuntimeError("boom")])
    results = run_iot(client, cases, repeat=2)
    traces = traces_from_results(results, catalog_id="iot_light_5", run_id="r", model_id="scripted")
    assert len(traces) == 4
    assert [(t.case_id, t.repeat_index) for t in traces] == [("ok", 0), ("ok", 1), ("fail", 0), ("fail", 1)]
    assert len({t.trace_id for t in traces}) == 4

    ok0 = traces[0]
    assert ok0.plan == gold.to_jsonable()
    assert ok0.expected_plan == gold.to_jsonable()
    assert ok0.parse_strategy == "fenced"
    assert ok0.raw_output == ok_result.raw["content"]
    assert ok0.raw_plan == json.loads(ok_result.raw["content"])
    assert ok0.error_type is None
    assert ok0.latency_ms == 12.5
    assert (ok0.input_tokens_total, ok0.output_tokens_total) == (0, 7)
    assert ok0.source == "benchmark.iot" and ok0.model_id == "scripted" and ok0.run_id == "r"
    assert ok0.timestamp.endswith("Z")

    fail0 = traces[2]
    assert fail0.plan is None
    assert fail0.error_type == "_RawCarryingError: bad output"
    assert len(fail0.attempts) == 1 and fail0.attempts[0]["error"] == "state required"
    assert fail0.raw_plan == {"calls": [{"action": "set_light", "args": {"room": "living"}}]}
    assert fail0.parse_strategy == "json_object"
    assert fail0.latency_ms == 0.0

    fail1 = traces[3]
    assert fail1.attempts == () and fail1.raw_plan is None and fail1.raw_output == ""
    assert fail1.error_type == "RuntimeError: boom"
    # Round-trips through the on-disk shape.
    assert all(Trace.from_dict(t.to_dict()) == t for t in traces)


def test_traces_from_results_rules_client_raw_mapping() -> None:
    cases = load_dataset(Path("examples/iot_light/dataset.jsonl"), limit=3)
    results = run_iot(RuleBasedJSONDSLClient(), cases)
    traces = traces_from_results(results, catalog_id="iot_light_5", run_id="rules", model_id="rules")
    assert len(traces) == 3
    for trace, case in zip(traces, cases):
        assert trace.plan == case.expected.to_jsonable()
        # The rules client's raw is the `{"calls": [...]}` payload → one attempt.
        assert len(trace.attempts) == 1
        assert trace.raw_plan is not None and "calls" in trace.raw_plan
        assert trace.parse_strategy == "json_object"
