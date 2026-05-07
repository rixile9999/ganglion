from __future__ import annotations

import pytest

from ganglion.factory.grammar import catalog_to_json_schema
from ganglion.schema import get_catalog


def test_envelope_shape_iot_light_5() -> None:
    schema = catalog_to_json_schema(get_catalog("iot_light_5"))
    assert schema["type"] == "object"
    assert schema["required"] == ["calls"]
    assert schema["additionalProperties"] is False
    calls = schema["properties"]["calls"]
    assert calls["type"] == "array"
    assert calls["minItems"] == 1
    items = calls["items"]
    # iot_light_5 has more than one tool, so items uses anyOf
    assert "anyOf" in items
    assert len(items["anyOf"]) == len(get_catalog("iot_light_5").tools)


def test_per_tool_branch_pins_action() -> None:
    catalog = get_catalog("iot_light_5")
    schema = catalog_to_json_schema(catalog)
    branches = schema["properties"]["calls"]["items"]["anyOf"]
    expected_actions = {tool.name for tool in catalog.tools}
    actions_in_schema = {
        branch["properties"]["action"]["const"] for branch in branches
    }
    assert actions_in_schema == expected_actions
    for branch in branches:
        assert branch["required"] == ["action", "args"]
        assert branch["additionalProperties"] is False
        # args must be an object schema
        args_schema = branch["properties"]["args"]
        assert args_schema["type"] == "object"


def test_args_schema_matches_render_openai_tools() -> None:
    catalog = get_catalog("home_iot_20")
    schema = catalog_to_json_schema(catalog)
    branches = {
        branch["properties"]["action"]["const"]: branch["properties"]["args"]
        for branch in schema["properties"]["calls"]["items"]["anyOf"]
    }
    for openai_tool in catalog.render_openai_tools():
        name = openai_tool["function"]["name"]
        assert branches[name] == openai_tool["function"]["parameters"]


def test_allow_empty_calls_drops_min_items() -> None:
    from dataclasses import replace

    catalog = get_catalog("iot_light_5")
    permissive = replace(catalog, allow_empty_calls=True)
    schema = catalog_to_json_schema(permissive)
    assert "minItems" not in schema["properties"]["calls"]


def test_empty_catalog_raises() -> None:
    from dataclasses import replace

    catalog = replace(get_catalog("iot_light_5"), tools=())
    with pytest.raises(ValueError, match="no tools"):
        catalog_to_json_schema(catalog)


def test_single_tool_uses_branch_directly() -> None:
    from dataclasses import replace

    catalog = get_catalog("iot_light_5")
    one = replace(catalog, tools=(catalog.tools[0],))
    schema = catalog_to_json_schema(one)
    items = schema["properties"]["calls"]["items"]
    # Single-tool catalogs should not wrap in anyOf
    assert "anyOf" not in items
    assert items["properties"]["action"]["const"] == catalog.tools[0].name
