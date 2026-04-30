"""Coverage for the BFCL runner — uses a fake in-memory client (no API)."""
from __future__ import annotations

from typing import Any

from ganglion.bfcl.loader import BFCLCase
from ganglion.dsl.catalog import Catalog
from ganglion.dsl.types import ActionPlan, ToolCall
from ganglion.eval.bfcl_runner import (
    BFCLCaseResult,
    build_case_catalog,
    run_bfcl,
    summarize_bfcl,
)
from ganglion.runtime.types import ModelResult


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


def test_run_bfcl_irrelevance_fails_when_model_calls() -> None:
    case = _case("0", None)
    plan = ActionPlan(calls=(ToolCall("calc", {"x": 5}),))
    client = _FakeClient({"run calc": plan})

    results = run_bfcl(lambda _catalog: client, [case])
    assert not results[0].grade.valid
    assert results[0].grade.error_type == "irrelevance:unexpected_call"
