from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.factory.customer.ingest import ingest_schema


def test_ingest_builtin_tier_name() -> None:
    catalog = ingest_schema("iot_light_5")
    assert catalog.name == "iot_light_5"
    assert len(catalog.tools) == 5


def test_ingest_unknown_tier_raises() -> None:
    with pytest.raises(FileNotFoundError):
        ingest_schema("not_a_tier_or_path")


def test_ingest_openai_tools_list() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_user",
                "description": "Look up a user by id.",
                "parameters": {
                    "type": "object",
                    "properties": {"user_id": {"type": "integer"}},
                    "required": ["user_id"],
                },
            },
        }
    ]
    catalog = ingest_schema(tools, name="custom")
    assert catalog.name == "custom"
    assert len(catalog.tools) == 1
    assert catalog.tools[0].name == "lookup_user"


def test_ingest_mcp_wrapper() -> None:
    schema = {
        "tools": [
            {
                "name": "echo",
                "description": "Return the input.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
    }
    catalog = ingest_schema(schema, name="mcp_test")
    assert len(catalog.tools) == 1
    assert catalog.tools[0].name == "echo"


def test_ingest_json_file(tmp_path: Path) -> None:
    schema = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "ping",
                    "description": "Ping.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    }
    path = tmp_path / "my_schema.json"
    path.write_text(json.dumps(schema))
    catalog = ingest_schema(path)
    assert catalog.name == "my_schema"
    assert len(catalog.tools) == 1


def test_ingest_yaml_file(tmp_path: Path) -> None:
    yaml_content = """
tools:
  - type: function
    function:
      name: hello
      description: Say hello.
      parameters:
        type: object
        properties:
          who:
            type: string
        required:
          - who
"""
    path = tmp_path / "hello.yaml"
    path.write_text(yaml_content)
    catalog = ingest_schema(path)
    assert catalog.name == "hello"
    assert catalog.tools[0].name == "hello"
