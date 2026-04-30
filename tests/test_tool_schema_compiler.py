import pytest

from ganglion.dsl.compiler import compile_openai_tools, compile_tool_calling_schema
from ganglion.dsl.emitter import emit_tool_calls
from ganglion.dsl.tool_spec import DSLValidationError


OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Create a timer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                    },
                    "unit": {"type": "string", "enum": ["seconds", "minutes"]},
                    "label": {"type": "string"},
                    "audible": {"type": "boolean"},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "priority": {
                                "type": "string",
                                "enum": ["low", "normal", "high"],
                            }
                        },
                        "required": ["priority"],
                        "additionalProperties": False,
                    },
                },
                "required": ["duration", "unit", "audible"],
                "additionalProperties": False,
            },
        },
    }
]


def test_compile_openai_tools_to_catalog_and_mapper() -> None:
    catalog = compile_openai_tools(OPENAI_TOOLS, name="timers")
    dsl = catalog.render_json_dsl()

    assert "set_timer" in dsl
    assert '"duration": integer 1..120' in dsl
    assert '"unit": "seconds"|"minutes"' in dsl
    assert '"audible": boolean' in dsl

    payload = {
        "calls": [
            {
                "action": "set_timer",
                "args": {
                    "duration": "15",
                    "unit": "minutes",
                    "audible": "true",
                    "metadata": {"priority": "high"},
                },
            }
        ]
    }
    calls = emit_tool_calls(payload, catalog=catalog)

    assert calls == [
        {
            "name": "set_timer",
            "arguments": {
                "duration": 15,
                "unit": "minutes",
                "audible": True,
                "metadata": {"priority": "high"},
            },
        }
    ]


def test_compiled_mapper_rejects_nested_raw_arg_schema_violations() -> None:
    mapper = compile_tool_calling_schema(OPENAI_TOOLS, name="timers")

    with pytest.raises(DSLValidationError, match="unknown property"):
        mapper.emit_tool_calls(
            {
                "calls": [
                    {
                        "action": "set_timer",
                        "args": {
                            "duration": 15,
                            "unit": "minutes",
                            "audible": True,
                            "metadata": {"priority": "high", "extra": "nope"},
                        },
                    }
                ]
            }
        )


def test_compile_mcp_style_tool_schema() -> None:
    mapper = compile_tool_calling_schema(
        {
            "tools": [
                {
                    "name": "search_notes",
                    "description": "Search personal notes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "minLength": 1},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                    },
                }
            ]
        },
        name="notes",
    )

    assert mapper.emit_tool_calls(
        {"calls": [{"action": "search_notes", "args": {"query": "paper", "limit": 5}}]}
    ) == [{"name": "search_notes", "arguments": {"query": "paper", "limit": 5}}]


def test_compiler_can_enable_empty_call_plans() -> None:
    mapper = compile_tool_calling_schema(
        OPENAI_TOOLS,
        name="timers",
        allow_empty_calls=True,
    )

    assert mapper.parse_json_dsl({"calls": []}).calls == ()
    assert mapper.emit_tool_calls({"calls": []}) == []
    assert '{"calls":[]}' in mapper.render_json_dsl()
