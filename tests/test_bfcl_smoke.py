"""Phase B offline smoke — full BFCL pipeline against real sample data.

Uses a ground-truth-replay fake client (no API calls) so this stays in CI:
    1. Pull one case from each of the 5 BFCL categories.
    2. Compile its tool list into a per-case Catalog.
    3. Replay the BFCL ground_truth back as predicted ToolCalls.
    4. Confirm the grader reports 100% AST match across all 5 cases.

This validates the loader → compiler → catalog → grader chain on real
BFCL v4 schema variants (dict / float / tuple / optional / nested).
"""
from __future__ import annotations

from typing import Any

from ganglion.bfcl.loader import CATEGORIES, BFCLCase, load_category
from ganglion.dsl.types import ActionPlan, ToolCall
from ganglion.eval.bfcl_runner import run_bfcl, summarize_bfcl
from ganglion.runtime.types import ModelResult


def _replay_plan(case: BFCLCase) -> ActionPlan:
    """Build an ActionPlan that exactly satisfies the BFCL ground truth.

    For each ground-truth function entry, pick the *first* accepted value of
    every required parameter (skipping optionals where "" is allowed). The
    grader accepts any value present in the accepted list, so this always
    grades valid for non-irrelevance cases.
    """
    if case.ground_truth is None:
        return ActionPlan(calls=())

    func_descriptions = {tool["name"]: tool for tool in case.tools}
    calls: list[ToolCall] = []
    for entry in case.ground_truth:
        func_name = next(iter(entry.keys()))
        accepted = entry[func_name]
        required = func_descriptions[func_name]["parameters"].get("required", [])
        param_details = func_descriptions[func_name]["parameters"]["properties"]

        args: dict[str, Any] = {}
        for param, options in accepted.items():
            non_empty = [o for o in options if o != ""]
            if not non_empty:
                continue
            if param not in required and "" in options:
                continue
            value = non_empty[0]
            if (
                param_details.get(param, {}).get("type") == "float"
                and isinstance(value, int)
            ):
                value = float(value)
            args[param] = value
        calls.append(ToolCall(action=func_name, args=args))

    return ActionPlan(calls=tuple(calls))


class _ReplayClient:
    def __init__(self, plan_by_message: dict[str, ActionPlan]) -> None:
        self._plans = plan_by_message

    def invoke(self, user_prompt: str) -> ModelResult:
        return ModelResult(
            plan=self._plans[user_prompt],
            raw=None,
            latency_ms=1.0,
            input_tokens=10,
            output_tokens=5,
        )


def test_offline_smoke_one_case_per_category() -> None:
    cases: list[BFCLCase] = []
    for category in CATEGORIES:
        cases.append(load_category(category)[0])
    assert len(cases) == 5

    plans = {case.user_message: _replay_plan(case) for case in cases}
    client = _ReplayClient(plans)

    results = run_bfcl(lambda _catalog: client, cases)
    summary = summarize_bfcl(results)

    assert summary["total"] == 5
    assert summary["ast_match_rate"] == 1.0
    assert summary["syntax_valid_rate"] == 1.0
    assert summary["failures"] == []

    by_cat = summary["by_category"]
    for category in CATEGORIES:
        assert by_cat[category]["total"] == 1
        assert by_cat[category]["ast_match_rate"] == 1.0


def test_offline_smoke_five_cases_per_category() -> None:
    """Exercise 25 cases across the 5 categories — broader schema coverage."""
    cases: list[BFCLCase] = []
    for category in CATEGORIES:
        cases.extend(load_category(category)[:5])
    assert len(cases) == 25

    # User messages can repeat across cases; build a per-case plan map keyed
    # on case id and resolve via a tiny adapter client.
    plans_by_id = {case.id: _replay_plan(case) for case in cases}

    class _ReplayByIdClient:
        def __init__(self, ordered_cases: list[BFCLCase]) -> None:
            self._iter = iter(ordered_cases)

        def invoke(self, user_prompt: str) -> ModelResult:
            case = next(self._iter)
            assert case.user_message == user_prompt
            return ModelResult(
                plan=plans_by_id[case.id],
                raw=None,
                latency_ms=1.0,
                input_tokens=10,
                output_tokens=5,
            )

    client = _ReplayByIdClient(list(cases))
    results = run_bfcl(lambda _catalog: client, cases)
    summary = summarize_bfcl(results)

    assert summary["total"] == 25
    assert summary["ast_match_rate"] == 1.0
    assert summary["failures"] == []
    for category in CATEGORIES:
        assert summary["by_category"][category]["total"] == 5
        assert summary["by_category"][category]["ast_match_rate"] == 1.0
