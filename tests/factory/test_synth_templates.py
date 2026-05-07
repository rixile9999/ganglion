from __future__ import annotations

from ganglion.factory.prompts.synth_templates import (
    SYSTEM_PROMPT,
    render_tool_anchored_prompt,
    render_tool_spec,
)
from ganglion.schema import get_catalog


def test_messages_have_system_and_user() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "set_light")
    msgs = render_tool_anchored_prompt(catalog, tool, n=5)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[0]["content"] == SYSTEM_PROMPT


def test_user_message_pins_tool_name() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "set_light")
    msgs = render_tool_anchored_prompt(catalog, tool, n=7)
    user = msgs[1]["content"]
    # Must mention the requested tool name
    assert "set_light" in user
    # Must mention the requested batch size
    assert "7" in user
    # Must mention the JSON output shape
    assert "pairs" in user


def test_tool_spec_contains_args_and_enum_values() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "set_light")
    text = render_tool_spec(tool, catalog)
    # Tool line must be present
    assert "- set_light args" in text
    # Enum values must appear
    assert '"living"' in text
    assert '"on"' in text and '"off"' in text


def test_korean_alias_triggers_locale_hint() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "set_light")
    msgs = render_tool_anchored_prompt(catalog, tool, n=5)
    user = msgs[1]["content"]
    # iot_light_5 has Korean aliases like "거실"
    assert "Korean" in user


def test_no_korean_alias_no_korean_hint() -> None:
    """Synthetic catalog without Korean aliases should default to English-only hint."""
    from dataclasses import replace
    from ganglion.dsl.tool_spec import EnumArg, ToolSpec

    bare_tool = ToolSpec(
        name="ping",
        description="Ping a host.",
        args=(("host", EnumArg(values=("a", "b"), required=True)),),
    )
    catalog = replace(get_catalog("iot_light_5"), tools=(bare_tool,))
    msgs = render_tool_anchored_prompt(catalog, bare_tool, n=3)
    user = msgs[1]["content"]
    assert "Korean" not in user
    assert "English" in user


def test_int_arg_bounds_in_hints() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "set_light")
    text = render_tool_spec(tool, catalog)
    # set_light has brightness with min=0 max=100
    assert "min=0" in text and "max=100" in text


def test_time_arg_appears_in_hints() -> None:
    catalog = get_catalog("iot_light_5")
    tool = next(t for t in catalog.tools if t.name == "schedule_light")
    text = render_tool_spec(tool, catalog)
    assert "HH:MM" in text
