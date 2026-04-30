from __future__ import annotations

import pytest

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError, NumberArg, ToolSpec


def _catalog(arg: NumberArg) -> Catalog:
    return Catalog(
        name="numbers",
        tools=(
            ToolSpec(
                name="set_value",
                description="Set a numeric value.",
                args=(("value", arg),),
            ),
        ),
    )


def test_number_arg_accepts_int() -> None:
    plan = _catalog(NumberArg()).parse_json_dsl(
        {"calls": [{"action": "set_value", "args": {"value": 7}}]}
    )
    assert plan.calls[0].args["value"] == 7
    assert isinstance(plan.calls[0].args["value"], int)


def test_number_arg_accepts_float() -> None:
    plan = _catalog(NumberArg()).parse_json_dsl(
        {"calls": [{"action": "set_value", "args": {"value": 3.14}}]}
    )
    assert plan.calls[0].args["value"] == 3.14


def test_number_arg_parses_string() -> None:
    plan = _catalog(NumberArg()).parse_json_dsl(
        {"calls": [{"action": "set_value", "args": {"value": "2.5"}}]}
    )
    assert plan.calls[0].args["value"] == 2.5


def test_number_arg_string_int_preserves_int_type() -> None:
    plan = _catalog(NumberArg()).parse_json_dsl(
        {"calls": [{"action": "set_value", "args": {"value": "42"}}]}
    )
    value = plan.calls[0].args["value"]
    assert value == 42 and isinstance(value, int)


def test_number_arg_rejects_bool() -> None:
    with pytest.raises(DSLValidationError, match="must be a number"):
        _catalog(NumberArg()).parse_json_dsl(
            {"calls": [{"action": "set_value", "args": {"value": True}}]}
        )


def test_number_arg_enforces_min() -> None:
    with pytest.raises(DSLValidationError, match=">="):
        _catalog(NumberArg(min_value=0.0)).parse_json_dsl(
            {"calls": [{"action": "set_value", "args": {"value": -0.5}}]}
        )


def test_number_arg_enforces_max() -> None:
    with pytest.raises(DSLValidationError, match="<="):
        _catalog(NumberArg(max_value=1.0)).parse_json_dsl(
            {"calls": [{"action": "set_value", "args": {"value": 1.5}}]}
        )


def test_number_arg_dsl_rendering_with_bounds() -> None:
    catalog = _catalog(NumberArg(min_value=0.0, max_value=1.0))
    dsl = catalog.render_json_dsl()
    assert '"value": number 0..1' in dsl


def test_number_arg_dsl_rendering_unbounded() -> None:
    catalog = _catalog(NumberArg())
    dsl = catalog.render_json_dsl()
    assert '"value": number' in dsl


def test_number_arg_openai_rendering() -> None:
    catalog = _catalog(NumberArg(min_value=-1.5, max_value=1.5))
    tools = catalog.render_openai_tools()
    schema = tools[0]["function"]["parameters"]["properties"]["value"]
    assert schema == {"type": "number", "minimum": -1.5, "maximum": 1.5}


def test_number_arg_optional_can_be_omitted() -> None:
    catalog = _catalog(NumberArg(required=False))
    plan = catalog.parse_json_dsl({"calls": [{"action": "set_value", "args": {}}]})
    assert plan.calls[0].args == {}
