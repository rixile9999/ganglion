from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ganglion.dsl.tool_spec import (
    ArgSpec,
    BoolArg,
    DSLValidationError,
    EnumArg,
    IntArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)
from ganglion.dsl.types import ActionPlan, ToolCall

_TIME_PATTERN = re.compile(r"[0-2][0-9]:[0-5][0-9]")


@dataclass(frozen=True)
class Catalog:
    name: str
    tools: tuple[ToolSpec, ...]
    examples: tuple[tuple[str, str], ...] = ()
    extra_rules: tuple[str, ...] = ()

    def get_tool(self, name: str) -> ToolSpec | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def render_json_dsl(self) -> str:
        lines: list[str] = [
            "Return JSON only.",
            'JSON shape: {"calls":[{"action":"<action>","args":{...}}]}',
            "Allowed actions:",
        ]
        for tool in self.tools:
            args_text = tool.dsl_args_override or _render_dsl_args(tool.args)
            lines.append(f"- {tool.name} args {args_text}")
        rules = list(self.extra_rules) + [
            "Do not include explanations or Markdown.",
        ]
        lines.append("Rules:")
        lines.extend(f"- {rule}" for rule in rules)
        if self.examples:
            lines.append("Examples:")
            for prompt, response in self.examples:
                lines.append(f"User: {prompt}")
                lines.append(f"JSON: {response}")
        return "\n".join(lines) + "\n"

    def render_openai_tools(self) -> list[dict[str, Any]]:
        return [_render_openai_tool(tool) for tool in self.tools]

    def validate(self, payload: Mapping[str, Any]) -> ActionPlan:
        if "calls" in payload:
            raw_calls = payload["calls"]
        elif "action" in payload:
            raw_calls = [payload]
        else:
            raise DSLValidationError("expected 'calls' array or top-level 'action'")
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
            raise DSLValidationError("'calls' must be an array")
        if not raw_calls:
            raise DSLValidationError("'calls' must not be empty")
        calls = tuple(self.validate_call(raw_call, depth=0) for raw_call in raw_calls)
        return ActionPlan(calls=calls)

    def parse_json_dsl(self, raw: str | Mapping[str, Any]) -> ActionPlan:
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DSLValidationError(f"invalid JSON: {exc.msg}") from exc
        else:
            payload = dict(raw)
        return self.validate(payload)

    def validate_call(self, raw_call: Any, depth: int) -> ToolCall:
        if not isinstance(raw_call, Mapping):
            raise DSLValidationError("each call must be an object")
        action = raw_call.get("action")
        if not isinstance(action, str):
            raise DSLValidationError("call.action must be a string")
        action = action.strip()
        tool = self.get_tool(action)
        if tool is None:
            raise DSLValidationError(f"unsupported action: {action}")
        args = raw_call.get("args", {})
        if not isinstance(args, Mapping):
            raise DSLValidationError("call.args must be an object")
        if tool.custom_validator is not None:
            normalized = tool.custom_validator(dict(args), self, depth)
        else:
            normalized = _validate_flat_args(tool, dict(args))
        return ToolCall(action=action, args=normalized)


def _render_dsl_args(args: tuple[tuple[str, ArgSpec], ...]) -> str:
    if not args:
        return "{}"
    parts: list[str] = []
    for name, spec in args:
        parts.append(f'"{name}": {_render_dsl_arg_value(spec)}')
    return "{" + ", ".join(parts) + "}"


def _render_dsl_arg_value(spec: ArgSpec) -> str:
    optional = "" if spec.required else "optional "
    if isinstance(spec, EnumArg):
        if len(spec.values) <= 3:
            inner = "|".join(f'"{v}"' for v in spec.values)
        else:
            inner = "one of " + ", ".join(spec.values)
        return f"{optional}{inner}"
    if isinstance(spec, IntArg):
        bounds = _int_bounds(spec)
        return f"{optional}integer{bounds}"
    if isinstance(spec, StringArg):
        return f"{optional}string"
    if isinstance(spec, BoolArg):
        return f"{optional}boolean"
    if isinstance(spec, TimeArg):
        return f'{optional}"HH:MM" 24h time'
    if isinstance(spec, RawArg):
        return f"{optional}{spec.dsl_description}"
    raise DSLValidationError(f"unknown ArgSpec: {spec}")


def _int_bounds(spec: IntArg) -> str:
    if spec.min_value is not None and spec.max_value is not None:
        return f" {spec.min_value}..{spec.max_value}"
    if spec.min_value is not None:
        return f" >={spec.min_value}"
    if spec.max_value is not None:
        return f" <={spec.max_value}"
    return ""


def _render_openai_tool(tool: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in tool.args:
        properties[name] = _render_openai_arg(spec)
        if spec.required:
            required.append(name)
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    }


def _render_openai_arg(spec: ArgSpec) -> dict[str, Any]:
    if isinstance(spec, EnumArg):
        return {"type": "string", "enum": list(spec.values)}
    if isinstance(spec, IntArg):
        schema: dict[str, Any] = {"type": "integer"}
        if spec.min_value is not None:
            schema["minimum"] = spec.min_value
        if spec.max_value is not None:
            schema["maximum"] = spec.max_value
        return schema
    if isinstance(spec, StringArg):
        schema = {"type": "string"}
        if spec.pattern is not None:
            schema["pattern"] = spec.pattern
        return schema
    if isinstance(spec, BoolArg):
        return {"type": "boolean"}
    if isinstance(spec, TimeArg):
        return {
            "type": "string",
            "description": "24-hour HH:MM local time.",
            "pattern": r"^[0-2][0-9]:[0-5][0-9]$",
        }
    if isinstance(spec, RawArg):
        return dict(spec.json_schema)
    raise DSLValidationError(f"unknown ArgSpec: {spec}")


def _validate_flat_args(tool: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    if not tool.args:
        if args:
            raise DSLValidationError(f"{tool.name} does not accept args")
        return {}

    declared = {name for name, _ in tool.args}
    for key in args:
        if key not in declared:
            raise DSLValidationError(f"{tool.name}: unknown arg '{key}'")

    normalized: dict[str, Any] = {}
    for name, spec in tool.args:
        present = name in args and args[name] is not None
        if not present:
            if spec.required:
                raise DSLValidationError(f"{tool.name}.{name} is required")
            continue
        normalized[name] = _normalize_value(name, spec, args[name])
    return normalized


def _normalize_value(name: str, spec: ArgSpec, raw: Any) -> Any:
    if isinstance(spec, EnumArg):
        return _normalize_enum(name, spec, raw)
    if isinstance(spec, IntArg):
        return _normalize_int(name, spec, raw)
    if isinstance(spec, StringArg):
        return _normalize_string(name, spec, raw)
    if isinstance(spec, BoolArg):
        return _normalize_bool(name, raw)
    if isinstance(spec, TimeArg):
        return _normalize_time(name, raw)
    if isinstance(spec, RawArg):
        _validate_schema_fragment(name, spec.json_schema, raw)
        return raw
    raise DSLValidationError(f"unknown ArgSpec for {name}")


def _normalize_enum(name: str, spec: EnumArg, raw: Any) -> str:
    if isinstance(raw, bool):
        if spec.bool_true is not None and raw:
            return spec.bool_true
        if spec.bool_false is not None and not raw:
            return spec.bool_false
        raise DSLValidationError(f"{name}: boolean not accepted here")
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError(f"{name} is required")
    value = raw.strip().lower()
    value = spec.aliases.get(value, value)
    if value not in spec.values:
        raise DSLValidationError(f"unsupported {name}: {raw}")
    return value


def _normalize_int(name: str, spec: IntArg, raw: Any) -> int:
    if isinstance(raw, bool):
        raise DSLValidationError(f"{name} must be an integer")
    if isinstance(raw, str):
        cleaned = raw.strip()
        if spec.allow_percent and cleaned.endswith("%"):
            cleaned = cleaned[:-1].strip()
        try:
            value = int(cleaned)
        except (TypeError, ValueError) as exc:
            raise DSLValidationError(f"{name} must be an integer") from exc
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise DSLValidationError(f"{name} must be an integer") from exc
    if spec.min_value is not None and value < spec.min_value:
        raise DSLValidationError(
            f"{name} must be >= {spec.min_value}"
        )
    if spec.max_value is not None and value > spec.max_value:
        raise DSLValidationError(
            f"{name} must be <= {spec.max_value}"
        )
    return value


def _normalize_string(name: str, spec: StringArg, raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError(f"{name} must be a non-empty string")
    value = raw.strip()
    lowered = value.lower()
    if lowered in spec.aliases:
        return spec.aliases[lowered]
    if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
        raise DSLValidationError(f"{name} does not match pattern")
    return value


def _normalize_bool(name: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"true", "yes", "1", "on"}:
            return True
        if value in {"false", "no", "0", "off"}:
            return False
    raise DSLValidationError(f"{name} must be a boolean")


def _normalize_time(name: str, raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError(f"{name} is required")
    value = raw.strip()
    if not _TIME_PATTERN.fullmatch(value):
        raise DSLValidationError(f"{name} must be HH:MM")
    if int(value[:2]) > 23:
        raise DSLValidationError(f"{name} hour must be 00..23")
    return value


def _validate_schema_fragment(path: str, schema: Mapping[str, Any], value: Any) -> None:
    """Validate the useful subset of JSON Schema needed for compiled RawArg values."""
    if "const" in schema and value != schema["const"]:
        raise DSLValidationError(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise DSLValidationError(f"{path} must be one of {schema['enum']!r}")

    if "allOf" in schema:
        for index, subschema in enumerate(schema["allOf"]):
            if isinstance(subschema, Mapping):
                _validate_schema_fragment(f"{path}.allOf[{index}]", subschema, value)
    if "anyOf" in schema:
        _validate_any_schema(path, schema["anyOf"], value)
    if "oneOf" in schema:
        _validate_one_schema(path, schema["oneOf"], value)

    schema_types = _schema_types(schema)
    if not schema_types:
        return
    if not any(_matches_json_type(schema_type, value) for schema_type in schema_types):
        expected = "|".join(schema_types)
        raise DSLValidationError(f"{path} must be {expected}")

    if "object" in schema_types:
        _validate_object_schema(path, schema, value)
    if "array" in schema_types:
        _validate_array_schema(path, schema, value)
    if "string" in schema_types:
        _validate_string_schema(path, schema, value)
    if "integer" in schema_types or "number" in schema_types:
        _validate_number_schema(path, schema, value)


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


def _matches_json_type(schema_type: str, value: Any) -> bool:
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def _validate_object_schema(path: str, schema: Mapping[str, Any], value: Any) -> None:
    if not isinstance(value, Mapping):
        return
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        required = []
    for key in required:
        if isinstance(key, str) and key not in value:
            raise DSLValidationError(f"{path}.{key} is required")
    for key, raw_value in value.items():
        if key in properties and isinstance(properties[key], Mapping):
            _validate_schema_fragment(f"{path}.{key}", properties[key], raw_value)
            continue
        additional = schema.get("additionalProperties", True)
        if additional is False:
            raise DSLValidationError(f"{path}: unknown property '{key}'")
        if isinstance(additional, Mapping):
            _validate_schema_fragment(f"{path}.{key}", additional, raw_value)


def _validate_array_schema(path: str, schema: Mapping[str, Any], value: Any) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if isinstance(min_items, int) and len(value) < min_items:
        raise DSLValidationError(f"{path} must contain at least {min_items} items")
    if isinstance(max_items, int) and len(value) > max_items:
        raise DSLValidationError(f"{path} must contain at most {max_items} items")
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            _validate_schema_fragment(f"{path}[{index}]", items, item)


def _validate_string_schema(path: str, schema: Mapping[str, Any], value: Any) -> None:
    if not isinstance(value, str):
        return
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and not re.fullmatch(pattern, value):
        raise DSLValidationError(f"{path} does not match pattern")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if isinstance(min_length, int) and len(value) < min_length:
        raise DSLValidationError(f"{path} must have length >= {min_length}")
    if isinstance(max_length, int) and len(value) > max_length:
        raise DSLValidationError(f"{path} must have length <= {max_length}")


def _validate_number_schema(path: str, schema: Mapping[str, Any], value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(minimum, (int, float)) and value < minimum:
        raise DSLValidationError(f"{path} must be >= {minimum}")
    if isinstance(maximum, (int, float)) and value > maximum:
        raise DSLValidationError(f"{path} must be <= {maximum}")
    if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
        raise DSLValidationError(f"{path} must be > {exclusive_minimum}")
    if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
        raise DSLValidationError(f"{path} must be < {exclusive_maximum}")


def _validate_any_schema(path: str, schemas: Any, value: Any) -> None:
    if not isinstance(schemas, Sequence) or isinstance(schemas, (str, bytes)):
        return
    errors = []
    for subschema in schemas:
        if not isinstance(subschema, Mapping):
            continue
        try:
            _validate_schema_fragment(path, subschema, value)
            return
        except DSLValidationError as exc:
            errors.append(str(exc))
    raise DSLValidationError(f"{path} did not match any allowed schema: {errors}")


def _validate_one_schema(path: str, schemas: Any, value: Any) -> None:
    if not isinstance(schemas, Sequence) or isinstance(schemas, (str, bytes)):
        return
    matches = 0
    for subschema in schemas:
        if not isinstance(subschema, Mapping):
            continue
        try:
            _validate_schema_fragment(path, subschema, value)
        except DSLValidationError:
            continue
        matches += 1
    if matches != 1:
        raise DSLValidationError(f"{path} must match exactly one schema")
