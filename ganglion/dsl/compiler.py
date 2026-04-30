from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import (
    ArgSpec,
    BoolArg,
    DSLValidationError,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)

# BFCL v4 uses non-standard JSON Schema type names; normalise them up front
# so the rest of the compiler and validator only see standard types.
_TYPE_ALIASES = {
    "dict": "object",
    "float": "number",
    "tuple": "array",
}
from ganglion.dsl.types import ActionPlan

_TIME_PATTERN_HINTS = (
    r"^[0-2][0-9]:[0-5][0-9]$",
    r"^\d{2}:\d{2}$",
)


@dataclass(frozen=True)
class CompiledToolMapper:
    """Runtime bundle produced from external tool schemas.

    The mapper owns the generated Catalog. The Catalog renders the model-facing
    DSL prompt, validates model output, and emits provider-neutral tool calls.
    """

    catalog: Catalog
    source_tools: tuple[dict[str, Any], ...]

    def render_json_dsl(self) -> str:
        return self.catalog.render_json_dsl()

    def render_openai_tools(self) -> list[dict[str, Any]]:
        return self.catalog.render_openai_tools()

    def parse_json_dsl(self, raw: str | Mapping[str, Any]) -> ActionPlan:
        return self.catalog.parse_json_dsl(raw)

    def emit_tool_calls(
        self,
        raw: str | Mapping[str, Any] | ActionPlan,
    ) -> list[dict[str, Any]]:
        plan = raw if isinstance(raw, ActionPlan) else self.catalog.parse_json_dsl(raw)
        return [
            {
                "name": call.action,
                "arguments": call.args,
            }
            for call in plan.calls
        ]


def compile_tool_calling_schema(
    schema: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    name: str = "compiled_tools",
    examples: Sequence[tuple[str, str]] = (),
    extra_rules: Sequence[str] = (),
    allow_empty_calls: bool = False,
) -> CompiledToolMapper:
    """Compile OpenAI/DashScope or MCP-style tool schemas into a DSL mapper.

    Supported inputs:
    - OpenAI-compatible tools: {"type":"function","function":{...}}
    - Bare function schemas: {"name":..., "description":..., "parameters":...}
    - MCP-style tools: {"name":..., "description":..., "inputSchema":...}
    - A wrapper object with a top-level "tools" array.
    """

    tools = _coerce_tool_list(schema)
    compiled = tuple(_compile_tool(tool) for tool in tools)
    catalog = Catalog(
        name=name,
        tools=compiled,
        examples=tuple(examples),
        extra_rules=tuple(extra_rules),
        allow_empty_calls=allow_empty_calls,
    )
    return CompiledToolMapper(
        catalog=catalog,
        source_tools=tuple(deepcopy(dict(tool)) for tool in tools),
    )


def compile_openai_tools(
    tools: Sequence[Mapping[str, Any]],
    *,
    name: str = "compiled_tools",
    examples: Sequence[tuple[str, str]] = (),
    extra_rules: Sequence[str] = (),
    allow_empty_calls: bool = False,
) -> Catalog:
    """Compile OpenAI-compatible tools into a Catalog."""

    return compile_tool_calling_schema(
        tools,
        name=name,
        examples=examples,
        extra_rules=extra_rules,
        allow_empty_calls=allow_empty_calls,
    ).catalog


def _coerce_tool_list(
    schema: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(schema, Mapping):
        raw_tools = schema.get("tools")
        if isinstance(raw_tools, Sequence) and not isinstance(raw_tools, (str, bytes)):
            tools = raw_tools
        else:
            tools = (schema,)
    elif isinstance(schema, Sequence) and not isinstance(schema, (str, bytes)):
        tools = schema
    else:
        raise DSLValidationError("tool schema must be a mapping or a sequence")

    result: list[Mapping[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            raise DSLValidationError(f"tool[{index}] must be an object")
        result.append(tool)
    if not result:
        raise DSLValidationError("tool schema must contain at least one tool")
    return tuple(result)


def _compile_tool(tool: Mapping[str, Any]) -> ToolSpec:
    function = _extract_function(tool)
    name = _required_string(function, "name")
    description = _optional_string(function.get("description"))
    parameters = _normalize_schema(_extract_parameters(function))
    args = _compile_parameters(name, parameters)
    return ToolSpec(name=name, description=description, args=args)


def _normalize_schema(schema: Any) -> Any:
    """Recursively rewrite BFCL-specific type aliases to standard JSON Schema."""
    if not isinstance(schema, Mapping):
        return schema
    result = dict(schema)
    raw_type = result.get("type")
    if isinstance(raw_type, str):
        result["type"] = _TYPE_ALIASES.get(raw_type, raw_type)
    elif isinstance(raw_type, Sequence) and not isinstance(raw_type, (str, bytes)):
        result["type"] = [
            _TYPE_ALIASES.get(item, item) if isinstance(item, str) else item
            for item in raw_type
        ]
    properties = result.get("properties")
    if isinstance(properties, Mapping):
        result["properties"] = {key: _normalize_schema(value) for key, value in properties.items()}
    items = result.get("items")
    if isinstance(items, Mapping):
        result["items"] = _normalize_schema(items)
    elif isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        result["items"] = [_normalize_schema(item) for item in items]
    additional = result.get("additionalProperties")
    if isinstance(additional, Mapping):
        result["additionalProperties"] = _normalize_schema(additional)
    for combinator in ("allOf", "anyOf", "oneOf"):
        sub = result.get(combinator)
        if isinstance(sub, Sequence) and not isinstance(sub, (str, bytes)):
            result[combinator] = [_normalize_schema(item) for item in sub]
    return result


def _extract_function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    if tool.get("type") == "function":
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise DSLValidationError("OpenAI tool.function must be an object")
        return function
    if "function" in tool and isinstance(tool["function"], Mapping):
        return tool["function"]
    return tool


def _extract_parameters(function: Mapping[str, Any]) -> Mapping[str, Any]:
    if "inputSchema" in function:
        parameters = function["inputSchema"]
    else:
        parameters = function.get("parameters", {"type": "object", "properties": {}})
    if parameters is None:
        return {"type": "object", "properties": {}}
    if not isinstance(parameters, Mapping):
        raise DSLValidationError("tool parameters/inputSchema must be an object")
    schema_types = tuple(
        _TYPE_ALIASES.get(item, item) for item in _schema_types(parameters)
    )
    if schema_types and "object" not in schema_types:
        raise DSLValidationError("tool parameters/inputSchema must be an object schema")
    return parameters


def _compile_parameters(
    tool_name: str,
    parameters: Mapping[str, Any],
) -> tuple[tuple[str, ArgSpec], ...]:
    properties = parameters.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, Mapping):
        raise DSLValidationError(f"{tool_name}.parameters.properties must be an object")
    required = parameters.get("required", [])
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        raise DSLValidationError(f"{tool_name}.parameters.required must be an array")
    required_names = {item for item in required if isinstance(item, str)}

    args: list[tuple[str, ArgSpec]] = []
    for arg_name, arg_schema in properties.items():
        if not isinstance(arg_name, str):
            raise DSLValidationError(f"{tool_name}: argument names must be strings")
        if not isinstance(arg_schema, Mapping):
            raise DSLValidationError(f"{tool_name}.{arg_name} schema must be an object")
        args.append(
            (
                arg_name,
                _compile_arg(arg_name, arg_schema, required=arg_name in required_names),
            )
        )
    return tuple(args)


def _compile_arg(
    name: str,
    schema: Mapping[str, Any],
    *,
    required: bool,
) -> ArgSpec:
    if schema.get("optional") is True:
        required = False
    description = _optional_string(schema.get("description"))
    schema_types = _schema_types(schema)
    enum_values = schema.get("enum")

    if _is_string_enum(enum_values):
        return EnumArg(
            values=tuple(enum_values),
            required=required,
            description=description,
        )

    if "boolean" in schema_types:
        return BoolArg(required=required, description=description)

    if "integer" in schema_types:
        return IntArg(
            min_value=_int_bound(schema.get("minimum")),
            max_value=_int_bound(schema.get("maximum")),
            required=required,
            description=description,
        )

    if "number" in schema_types:
        return NumberArg(
            min_value=_number_bound(schema.get("minimum")),
            max_value=_number_bound(schema.get("maximum")),
            required=required,
            description=description,
        )

    if "string" in schema_types:
        pattern = schema.get("pattern")
        if _looks_like_time(name, schema):
            return TimeArg(required=required, description=description)
        return StringArg(
            pattern=pattern if isinstance(pattern, str) else None,
            required=required,
            description=description,
        )

    if "any" in schema_types:
        return RawArg(
            json_schema={},
            dsl_description="any JSON value",
            required=required,
        )

    return RawArg(
        json_schema=deepcopy(dict(schema)),
        dsl_description=_describe_schema(schema),
        required=required,
    )


def _required_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DSLValidationError(f"tool.{key} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _schema_types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return (schema_type,)
    if isinstance(schema_type, Sequence) and not isinstance(schema_type, (str, bytes)):
        return tuple(item for item in schema_type if isinstance(item, str))
    if "properties" in schema:
        return ("object",)
    if "items" in schema:
        return ("array",)
    return ()


def _is_string_enum(values: Any) -> bool:
    return (
        isinstance(values, Sequence)
        and not isinstance(values, (str, bytes))
        and bool(values)
        and all(isinstance(value, str) for value in values)
    )


def _int_bound(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _number_bound(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _looks_like_time(name: str, schema: Mapping[str, Any]) -> bool:
    if name in {"at", "time", "start_time", "end_time"}:
        return True
    if schema.get("format") in {"time", "partial-time"}:
        return True
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        normalized = pattern.replace("\\\\d", r"\d")
        if normalized in _TIME_PATTERN_HINTS:
            return True
        if re.search(r"\[0-2\].*:\[0-5\]", normalized):
            return True
    description = schema.get("description")
    return isinstance(description, str) and "HH:MM" in description.upper()


def _describe_schema(schema: Mapping[str, Any]) -> str:
    enum_values = schema.get("enum")
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, (str, bytes)):
        return "one of " + ", ".join(json.dumps(value) for value in enum_values)

    schema_types = _schema_types(schema)
    if "array" in schema_types:
        items = schema.get("items")
        if isinstance(items, Mapping):
            return "array of " + _describe_schema(items)
        return "array"
    if "object" in schema_types:
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping) and properties:
            return "object{" + ", ".join(str(key) for key in properties) + "}"
        return "object"
    if "number" in schema_types:
        return "number"
    if "integer" in schema_types:
        return "integer"
    if "boolean" in schema_types:
        return "boolean"
    if "string" in schema_types:
        return "string"
    return "JSON value"
