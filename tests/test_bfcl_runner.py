"""Coverage for the BFCL runner — uses a fake in-memory client (no API)."""
from __future__ import annotations

from typing import Any

from ganglion.benchmarks.bfcl.loader import BFCLCase
from ganglion.contract.catalog import Catalog
from ganglion.contract.types import ActionPlan, ToolCall
from ganglion.benchmarks.bfcl.runner import (
    BFCLCaseResult,
    build_case_catalog,
    run_bfcl,
    summarize_bfcl,
)
from ganglion.lm.client import ModelResult


_TOOL = {
    "name": "calc",
    "description": "test",
    "parameters": {
        "type": "dict",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    },
}


def _case(case_id: str, ground_truth: list[dict[str, Any]] | None) -> BFCLCase:
    category = "irrelevance" if ground_truth is None else "simple_python"
    return BFCLCase(
        id=f"{category}_{case_id}",
        category=category,
        user_message="run calc",
        tools=(_TOOL,),
        ground_truth=tuple(ground_truth) if ground_truth is not None else None,
    )


class _FakeClient:
    def __init__(self, plans: dict[str, ActionPlan | Exception]) -> None:
        self.plans = plans
        self.invocations: list[str] = []

    def invoke(self, user_prompt: str) -> ModelResult:
        self.invocations.append(user_prompt)
        outcome = self.plans[user_prompt]
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResult(
            plan=outcome,
            raw=None,
            latency_ms=10.0,
            input_tokens=5,
            output_tokens=3,
        )


def test_build_case_catalog_compiles_per_case_tools() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    catalog = build_case_catalog(case)
    assert isinstance(catalog, Catalog)
    assert catalog.tools[0].name == "calc"
    assert "calc" in catalog.render_json_dsl()


def test_build_case_catalog_can_allow_empty_calls() -> None:
    case = _case("0", None)
    catalog = build_case_catalog(case, allow_empty_calls=True)
    assert catalog.allow_empty_calls is True
    assert catalog.validate({"calls": []}).calls == ()


def test_run_bfcl_marks_correct_predictions_valid() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    client = _FakeClient({"run calc": plan})

    results = run_bfcl(lambda _catalog: client, [case])
    assert len(results) == 1
    assert results[0].grade.valid
    assert results[0].predicted == plan
    assert results[0].dsl_chars > 0
    assert results[0].native_chars > 0


def test_run_bfcl_records_grader_failure() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 6}),))
    client = _FakeClient({"run calc": plan})

    results = run_bfcl(lambda _catalog: client, [case])
    assert not results[0].grade.valid
    assert results[0].grade.error_type == "value_error:others"


def test_run_bfcl_captures_client_exception() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    client = _FakeClient({"run calc": RuntimeError("boom")})

    results = run_bfcl(lambda _catalog: client, [case])
    assert results[0].runs[0].error == "RuntimeError: boom"
    # Grader sees an empty plan, which is invalid for a non-irrelevance case.
    assert not results[0].grade.valid


def test_summarize_bfcl_aggregates_categories() -> None:
    case_a = _case("0", [{"calc": {"x": [5]}}])
    case_b = _case("1", [{"calc": {"x": [6]}}])
    plan_a = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    plan_b = ActionPlan(calls=(ToolCall("calc", {"x": 99}),))
    client = _FakeClient({"run calc": plan_a})

    results = run_bfcl(lambda _catalog: client, [case_a])
    # Build a second hand-rolled result to exercise aggregation across pass/fail.
    client_b = _FakeClient({"run calc": plan_b})
    results.extend(run_bfcl(lambda _catalog: client_b, [case_b]))

    summary = summarize_bfcl(results)
    assert summary["total"] == 2
    assert summary["ast_match_rate"] == 0.5
    assert summary["syntax_valid_rate"] == 1.0
    assert summary["by_category"]["simple_python"] == {
        "total": 2,
        "ast_match_rate": 0.5,
        "syntax_valid_rate": 1.0,
    }
    assert summary["error_type_counts"] == {"value_error:others": 1}
    assert summary["latency_ms_mean"] is not None
    assert summary["input_tokens_total"] == 10
    assert summary["output_tokens_total"] == 6
    assert len(summary["failures"]) == 1
    assert summary["failures"][0]["id"] == case_b.id


def test_run_bfcl_irrelevance_passes_with_empty_plan() -> None:
    case = _case("0", None)
    plan = ActionPlan(calls=())
    client = _FakeClient({"run calc": plan})

    results = run_bfcl(lambda _catalog: client, [case])
    assert results[0].grade.valid
    assert results[0].case.category == "irrelevance"


def test_run_bfcl_passes_allow_empty_calls_to_catalog() -> None:
    case = _case("0", None)
    seen: list[bool] = []

    class _CatalogAwareClient:
        def __init__(self, catalog: Catalog) -> None:
            seen.append(catalog.allow_empty_calls)

        def invoke(self, user_prompt: str) -> ModelResult:
            return ModelResult(
                plan=ActionPlan(calls=()),
                raw=None,
                latency_ms=10.0,
                input_tokens=5,
                output_tokens=3,
            )

    results = run_bfcl(
        lambda catalog: _CatalogAwareClient(catalog),
        [case],
        allow_empty_calls=True,
    )
    assert seen == [True]
    assert results[0].grade.valid


def test_run_bfcl_irrelevance_fails_when_model_calls() -> None:
    case = _case("0", None)
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    client = _FakeClient({"run calc": plan})

    results = run_bfcl(lambda _catalog: client, [case])
    assert not results[0].grade.valid
    assert results[0].grade.error_type == "irrelevance:unexpected_call"


# ---------------------------------------------------------------------------
# 2026-09-16 revision: failure raw preservation + traces_from_bfcl_results
# ---------------------------------------------------------------------------

from ganglion.analyzer.trace import Trace  # noqa: E402
from ganglion.benchmarks.bfcl.runner import traces_from_bfcl_results  # noqa: E402


class _RawError(ValueError):
    def __init__(self, raw) -> None:
        super().__init__("no plan")
        self.raw = raw


def test_run_bfcl_preserves_exception_raw() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    raw = [{"name": "calc", "arguments": {"x": "five"}}]
    client = _FakeClient({"run calc": _RawError(raw)})
    results = run_bfcl(lambda _catalog: client, [case])
    run = results[0].runs[0]
    assert run.plan is None
    assert run.raw is raw
    assert run.error == "_RawError: no plan"
    assert run.latency_ms is not None


def test_traces_from_bfcl_results_shape() -> None:
    ok_case = _case("0", [{"calc": {"x": [5]}}])
    bad_case = _case("1", [{"calc": {"x": [6]}}])
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    ok_client = _FakeClient({"run calc": plan})
    bad_client = _FakeClient({"run calc": _RawError([{"name": "calc", "arguments": {"x": "five"}}])})
    results = run_bfcl(lambda _c: ok_client, [ok_case]) + run_bfcl(lambda _c: bad_client, [bad_case])

    traces = traces_from_bfcl_results(results, category="simple_python", run_id="t", model_id="stub")
    assert len(traces) == 2
    ok, bad = traces
    assert ok.catalog_id == bad.catalog_id == "bfcl/simple_python"
    assert ok.source == bad.source == "benchmark.bfcl"
    assert ok.case_id == ok_case.id and bad.case_id == bad_case.id
    assert ok.prompt == "run calc"
    # Errata E6: ground truth is not a plan; never put it in expected_plan.
    assert ok.expected_plan is None and bad.expected_plan is None
    assert ok.plan == plan.to_jsonable()
    assert ok.error_type is None
    # _FakeClient returns raw=None on success → no attempts, no raw_plan.
    assert ok.attempts == () and ok.raw_plan is None and ok.raw_output == ""
    assert bad.plan is None
    assert bad.error_type == "_RawError: no plan"
    assert bad.raw_plan == {"calls": [{"action": "calc", "args": {"x": "five"}}]}
    assert len(bad.attempts) == 1
    assert bad.repeat_index == 0 and ok.repeat_index == 0
    assert ok.trace_id != bad.trace_id
    assert all(Trace.from_dict(t.to_dict()) == t for t in traces)


def test_traces_from_bfcl_results_repeat_index() -> None:
    case = _case("0", [{"calc": {"x": [5]}}])
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    client = _FakeClient({"run calc": plan})
    results = run_bfcl(lambda _c: client, [case], repeat=3)
    traces = traces_from_bfcl_results(results, category="simple_python", run_id="t", model_id="stub")
    assert [t.repeat_index for t in traces] == [0, 1, 2]
    assert len({t.trace_id for t in traces}) == 3
