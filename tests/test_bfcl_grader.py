"""Coverage for the local BFCL v4 AST grader."""
from __future__ import annotations

from typing import Any

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import BFCLCase
from ganglion.dsl.types import ToolCall


def _case(
    category: str,
    tools: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]] | None,
    case_id: str = "x_0",
) -> BFCLCase:
    return BFCLCase(
        id=case_id,
        category=category,
        user_message="ignored",
        tools=tuple(tools),
        ground_truth=tuple(ground_truth) if ground_truth is not None else None,
    )


_INT_TOOL = {
    "name": "calc",
    "description": "test",
    "parameters": {
        "type": "dict",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    },
}


def test_simple_match_basic() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("calc", {"x": 5})], case)
    assert result.valid


def test_simple_match_wrong_function_name() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("other", {"x": 5})], case)
    assert not result.valid
    assert result.error_type == "simple_function_checker:wrong_func_name"


def test_simple_match_value_mismatch() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("calc", {"x": 6})], case)
    assert not result.valid
    assert result.error_type == "value_error:others"


def test_simple_match_missing_required() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("calc", {})], case)
    assert not result.valid
    assert result.error_type == "simple_function_checker:missing_required"


def test_simple_match_unexpected_param() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("calc", {"x": 5, "y": 1})], case)
    assert not result.valid
    assert result.error_type == "simple_function_checker:unexpected_param"


def test_simple_match_wrong_count() -> None:
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match(
        [ToolCall("calc", {"x": 5}), ToolCall("calc", {"x": 5})], case
    )
    assert not result.valid
    assert result.error_type == "simple_function_checker:wrong_count"


def test_string_check_is_case_insensitive_and_punctuation_tolerant() -> None:
    tool = {
        "name": "say",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    }
    case = _case("simple_python", [tool], [{"say": {"msg": ["April 1, 2024"]}}])
    result = ast_match([ToolCall("say", {"msg": "april 1 2024"})], case)
    assert result.valid


def test_float_int_auto_promotion() -> None:
    tool = {
        "name": "scale",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {"factor": {"type": "float"}},
            "required": ["factor"],
        },
    }
    case = _case("simple_python", [tool], [{"scale": {"factor": [1.0]}}])
    result = ast_match([ToolCall("scale", {"factor": 1})], case)
    assert result.valid


def test_optional_param_missing_with_empty_string_in_accepted() -> None:
    tool = {
        "name": "round_to",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {
                "value": {"type": "float"},
                "places": {"type": "integer"},
            },
            "required": ["value"],
        },
    }
    # "" in accepted means the param can be omitted.
    case = _case(
        "simple_python",
        [tool],
        [{"round_to": {"value": [1.5], "places": [2, ""]}}],
    )
    result = ast_match([ToolCall("round_to", {"value": 1.5})], case)
    assert result.valid


def test_optional_param_required_when_no_empty_string() -> None:
    tool = {
        "name": "round_to",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {
                "value": {"type": "float"},
                "places": {"type": "integer"},
            },
            "required": ["value"],
        },
    }
    case = _case(
        "simple_python",
        [tool],
        [{"round_to": {"value": [1.5], "places": [2]}}],
    )
    result = ast_match([ToolCall("round_to", {"value": 1.5})], case)
    assert not result.valid
    assert result.error_type == "simple_function_checker:missing_optional"


def test_array_value_match() -> None:
    tool = {
        "name": "pick",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {
                "items": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["items"],
        },
    }
    case = _case("simple_python", [tool], [{"pick": {"items": [[1, 2, 3]]}}])
    result = ast_match([ToolCall("pick", {"items": [1, 2, 3]})], case)
    assert result.valid


def test_dict_value_match() -> None:
    tool = {
        "name": "wrap",
        "description": "test",
        "parameters": {
            "type": "dict",
            "properties": {
                "meta": {
                    "type": "dict",
                    "properties": {"score": {"type": "float"}},
                },
            },
            "required": ["meta"],
        },
    }
    case = _case(
        "simple_python", [tool], [{"wrap": {"meta": [{"score": [0.75]}]}}]
    )
    result = ast_match([ToolCall("wrap", {"meta": {"score": 0.75}})], case)
    assert result.valid


def test_multiple_picks_one_function() -> None:
    tool_a = {
        "name": "alpha",
        "description": "a",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    tool_b = {
        "name": "beta",
        "description": "b",
        "parameters": {
            "type": "dict",
            "properties": {"y": {"type": "integer"}},
            "required": ["y"],
        },
    }
    case = _case("multiple", [tool_a, tool_b], [{"beta": {"y": [3]}}])
    result = ast_match([ToolCall("beta", {"y": 3})], case)
    assert result.valid


def test_multiple_rejects_wrong_pick() -> None:
    tool_a = {
        "name": "alpha",
        "description": "a",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    tool_b = {
        "name": "beta",
        "description": "b",
        "parameters": {
            "type": "dict",
            "properties": {"y": {"type": "integer"}},
            "required": ["y"],
        },
    }
    case = _case("multiple", [tool_a, tool_b], [{"beta": {"y": [3]}}])
    result = ast_match([ToolCall("alpha", {"x": 3})], case)
    assert not result.valid
    assert result.error_type == "simple_function_checker:wrong_func_name"


def test_parallel_no_order_matches_in_any_order() -> None:
    tool = {
        "name": "calc",
        "description": "t",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    case = _case(
        "parallel",
        [tool],
        [{"calc": {"x": [1]}}, {"calc": {"x": [2]}}],
    )
    # reverse order should still match
    result = ast_match(
        [ToolCall("calc", {"x": 2}), ToolCall("calc", {"x": 1})], case
    )
    assert result.valid


def test_parallel_no_order_wrong_count() -> None:
    tool = {
        "name": "calc",
        "description": "t",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    case = _case(
        "parallel",
        [tool],
        [{"calc": {"x": [1]}}, {"calc": {"x": [2]}}],
    )
    result = ast_match([ToolCall("calc", {"x": 1})], case)
    assert not result.valid
    assert result.error_type == "parallel_function_checker_no_order:wrong_count"


def test_parallel_multiple_uses_parallel_path() -> None:
    tool_a = {
        "name": "alpha",
        "description": "a",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    tool_b = {
        "name": "beta",
        "description": "b",
        "parameters": {
            "type": "dict",
            "properties": {"y": {"type": "integer"}},
            "required": ["y"],
        },
    }
    case = _case(
        "parallel_multiple",
        [tool_a, tool_b],
        [{"alpha": {"x": [1]}}, {"beta": {"y": [2]}}],
    )
    result = ast_match(
        [ToolCall("beta", {"y": 2}), ToolCall("alpha", {"x": 1})], case
    )
    assert result.valid


def test_irrelevance_no_calls_is_valid() -> None:
    tool = {
        "name": "calc",
        "description": "t",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    case = _case("irrelevance", [tool], None)
    result = ast_match([], case)
    assert result.valid


def test_irrelevance_with_call_is_invalid() -> None:
    tool = {
        "name": "calc",
        "description": "t",
        "parameters": {
            "type": "dict",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    }
    case = _case("irrelevance", [tool], None)
    result = ast_match([ToolCall("calc", {"x": 1})], case)
    assert not result.valid
    assert result.error_type == "irrelevance:unexpected_call"


def test_bool_is_not_int() -> None:
    """type(True) is int returns True in Python via isinstance, but BFCL
    uses `type() ==` so booleans should NOT satisfy integer typing."""
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": [5]}}])
    result = ast_match([ToolCall("calc", {"x": True})], case)
    assert not result.valid
    assert result.error_type == "type_error:simple"


def test_variable_substitution_string_for_int() -> None:
    """When ground_truth lists a string placeholder for an int parameter,
    BFCL accepts model output that returns a string in that slot."""
    case = _case("simple_python", [_INT_TOOL], [{"calc": {"x": ["$ref"]}}])
    result = ast_match([ToolCall("calc", {"x": "$ref"})], case)
    assert result.valid
