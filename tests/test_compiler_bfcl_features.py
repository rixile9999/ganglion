"""Compiler coverage for BFCL v4 schema features."""
from __future__ import annotations

import pytest

from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.dsl.tool_spec import (
    BoolArg,
    DSLValidationError,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
)


def _bfcl_tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": "test tool",
        "parameters": {
            "type": "dict",
            "properties": properties,
            "required": required or [],
        },
    }


def test_dict_type_treated_as_object() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "calc",
            {"x": {"type": "integer", "description": "x"}},
            required=["x"],
        )
    )
    plan = mapper.parse_json_dsl(
        {"calls": [{"action": "calc", "args": {"x": 5}}]}
    )
    assert plan.calls[0].args == {"x": 5}


def test_float_type_compiles_to_number_arg() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "scale",
            {"factor": {"type": "float", "description": "scaling factor"}},
            required=["factor"],
        )
    )
    spec = mapper.catalog.tools[0].get_arg("factor")
    assert isinstance(spec, NumberArg)
    plan = mapper.parse_json_dsl(
        {"calls": [{"action": "scale", "args": {"factor": 1.5}}]}
    )
    assert plan.calls[0].args == {"factor": 1.5}


def test_optional_flag_overrides_required() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "do_thing",
            {
                "needed": {"type": "string", "description": "always needed"},
                "maybe": {
                    "type": "boolean",
                    "description": "optional flag",
                    "optional": True,
                    "default": True,
                },
            },
            required=["needed", "maybe"],
        )
    )
    needed = mapper.catalog.tools[0].get_arg("needed")
    maybe = mapper.catalog.tools[0].get_arg("maybe")
    assert isinstance(needed, StringArg) and needed.required is True
    assert isinstance(maybe, BoolArg) and maybe.required is False


def test_any_type_compiles_to_permissive_raw_arg() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "log",
            {"payload": {"type": "any", "description": "anything"}},
            required=["payload"],
        )
    )
    spec = mapper.catalog.tools[0].get_arg("payload")
    assert isinstance(spec, RawArg)
    for raw in [42, "string", {"a": 1}, [1, 2], True]:
        plan = mapper.parse_json_dsl(
            {"calls": [{"action": "log", "args": {"payload": raw}}]}
        )
        assert plan.calls[0].args == {"payload": raw}


def test_tuple_type_normalised_to_array() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "pair",
            {
                "values": {
                    "type": "tuple",
                    "items": {"type": "integer"},
                    "description": "two ints",
                }
            },
            required=["values"],
        )
    )
    plan = mapper.parse_json_dsl(
        {"calls": [{"action": "pair", "args": {"values": [1, 2]}}]}
    )
    assert plan.calls[0].args == {"values": [1, 2]}


def test_nested_dict_property_is_normalised() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "wrap",
            {
                "meta": {
                    "type": "dict",
                    "properties": {
                        "score": {"type": "float"},
                    },
                    "required": ["score"],
                }
            },
            required=["meta"],
        )
    )
    plan = mapper.parse_json_dsl(
        {"calls": [{"action": "wrap", "args": {"meta": {"score": 0.75}}}]}
    )
    assert plan.calls[0].args == {"meta": {"score": 0.75}}


def test_number_bounds_are_enforced() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "ratio",
            {
                "value": {
                    "type": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                }
            },
            required=["value"],
        )
    )
    spec = mapper.catalog.tools[0].get_arg("value")
    assert isinstance(spec, NumberArg)
    assert spec.min_value == 0.0 and spec.max_value == 1.0
    with pytest.raises(DSLValidationError, match="<="):
        mapper.parse_json_dsl(
            {"calls": [{"action": "ratio", "args": {"value": 1.5}}]}
        )


def test_default_field_does_not_break_compilation() -> None:
    mapper = compile_tool_calling_schema(
        _bfcl_tool(
            "round_to",
            {
                "value": {"type": "float"},
                "decimal_places": {
                    "type": "integer",
                    "default": 2,
                    "description": "places",
                },
            },
            required=["value"],
        )
    )
    plan = mapper.parse_json_dsl(
        {"calls": [{"action": "round_to", "args": {"value": 1.234}}]}
    )
    assert plan.calls[0].args == {"value": 1.234}
