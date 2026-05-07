"""Compile a Catalog into a JSON Schema usable as a constrained-decoding grammar.

The output schema describes the full DSL envelope:

    {"calls": [ <one of the per-tool action+args objects> , ... ] }

Each per-tool branch pins ``action`` to the tool's name (via ``const``) and
nests the tool's args object (reused from ``render_openai_tools``). XGrammar,
Outlines, and most other JSON-Schema-aware constrained decoders accept this
shape directly. Phase 1 uses this only to gate teacher synthesis output;
Phase 2 will plug it into the inference path.
"""

from __future__ import annotations

from typing import Any

from ganglion.dsl.catalog import Catalog


def catalog_to_json_schema(catalog: Catalog) -> dict[str, Any]:
    """Build a JSON Schema describing the full DSL envelope for ``catalog``."""

    tool_branches: list[dict[str, Any]] = []
    for openai_tool in catalog.render_openai_tools():
        function = openai_tool["function"]
        tool_branches.append(
            {
                "type": "object",
                "properties": {
                    "action": {"const": function["name"]},
                    "args": function["parameters"],
                },
                "required": ["action", "args"],
                "additionalProperties": False,
            }
        )

    if not tool_branches:
        raise ValueError(f"catalog '{catalog.name}' has no tools")

    calls_schema: dict[str, Any] = {
        "type": "array",
        "items": (
            tool_branches[0] if len(tool_branches) == 1 else {"anyOf": tool_branches}
        ),
    }
    if not catalog.allow_empty_calls:
        calls_schema["minItems"] = 1

    return {
        "type": "object",
        "properties": {"calls": calls_schema},
        "required": ["calls"],
        "additionalProperties": False,
    }
