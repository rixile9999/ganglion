from __future__ import annotations

import pytest

from ganglion.bfcl import BFCLCase, load_category
from ganglion.bfcl.loader import CATEGORIES


@pytest.mark.parametrize("category", CATEGORIES)
def test_each_sample_has_100_cases(category: str) -> None:
    cases = load_category(category)
    assert len(cases) == 100
    assert all(isinstance(case, BFCLCase) for case in cases)


def test_simple_python_case_shape() -> None:
    cases = load_category("simple_python")
    case = cases[0]
    assert case.category == "simple_python"
    assert case.id.startswith("simple_python_")
    assert isinstance(case.user_message, str) and case.user_message
    assert len(case.tools) == 1
    assert case.expects_call is True
    assert case.ground_truth is not None
    func_name = case.tools[0]["name"]
    answer = case.ground_truth[0]
    assert func_name in answer


def test_multiple_case_has_multiple_tools() -> None:
    cases = load_category("multiple")
    has_multi = any(len(case.tools) > 1 for case in cases)
    assert has_multi, "multiple category should have at least one case with >1 tools"


def test_parallel_multiple_has_multi_call_ground_truth() -> None:
    cases = load_category("parallel_multiple")
    has_multi = any(case.ground_truth and len(case.ground_truth) > 1 for case in cases)
    assert has_multi, "parallel_multiple should have at least one case with >1 expected calls"


def test_irrelevance_has_no_ground_truth() -> None:
    cases = load_category("irrelevance")
    assert all(case.ground_truth is None for case in cases)
    assert all(case.expects_call is False for case in cases)


def test_user_message_extraction() -> None:
    cases = load_category("simple_python")
    for case in cases[:5]:
        assert case.user_message.strip() != ""
