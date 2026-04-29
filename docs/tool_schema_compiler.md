# Tool Schema Compiler Process

Ganglion의 장기 목표는 임의의 tool calling schema를 입력받아 compact Action IR을
정의하고, 그 IR을 실제 tool call로 되돌리는 mapper를 자동 생성하는 것이다.

현재 프로토타입은 이 과정을 다음 파이프라인으로 구현한다.

```text
OpenAI/DashScope tools or MCP inputSchema
  -> schema compiler
  -> Ganglion Catalog
  -> model-facing JSON DSL prompt
  -> LLM output: compact Action IR
  -> Catalog validator
  -> provider-neutral tool calls
```

## Supported Inputs

`ganglion.dsl.compiler.compile_tool_calling_schema()`는 다음 입력을 받는다.

1. OpenAI-compatible function tools

```json
[
  {
    "type": "function",
    "function": {
      "name": "set_timer",
      "description": "Create a timer.",
      "parameters": {
        "type": "object",
        "properties": {
          "duration": {"type": "integer", "minimum": 1, "maximum": 120},
          "unit": {"type": "string", "enum": ["seconds", "minutes"]},
          "audible": {"type": "boolean"}
        },
        "required": ["duration", "unit", "audible"]
      }
    }
  }
]
```

2. Bare function schemas

```json
{
  "name": "search_notes",
  "description": "Search personal notes.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 20}
    },
    "required": ["query"]
  }
}
```

3. MCP-style tool schemas

```json
{
  "tools": [
    {
      "name": "search_notes",
      "description": "Search personal notes.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {"type": "string"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 20}
        },
        "required": ["query"]
      }
    }
  ]
}
```

## Usage

```python
from ganglion.dsl.compiler import compile_tool_calling_schema

mapper = compile_tool_calling_schema(openai_tools, name="timer_tools")

dsl_prompt = mapper.render_json_dsl()
print(dsl_prompt)

tool_calls = mapper.emit_tool_calls(
    {
        "calls": [
            {
                "action": "set_timer",
                "args": {
                    "duration": "15",
                    "unit": "minutes",
                    "audible": "true"
                }
            }
        ]
    }
)

assert tool_calls == [
    {
        "name": "set_timer",
        "arguments": {
            "duration": 15,
            "unit": "minutes",
            "audible": True
        }
    }
]
```

## Compilation Rules

The compiler maps JSON Schema fragments into Ganglion `ArgSpec` types:

| JSON Schema fragment | Ganglion arg |
| --- | --- |
| `{"type":"string","enum":[...]}` | `EnumArg` |
| `{"type":"integer","minimum":...,"maximum":...}` | `IntArg` |
| `{"type":"boolean"}` | `BoolArg` |
| time-like string fields such as `at`, `time`, or `format: time` | `TimeArg` |
| plain `{"type":"string"}` | `StringArg` |
| nested object, array, number, mixed enum, `oneOf`, `anyOf` | `RawArg` with schema validation |

`RawArg` is used for shapes that cannot be compactly represented as a flat
argument type. It still validates a practical subset of JSON Schema:

- `type`
- `enum`
- `const`
- `required`
- `properties`
- `additionalProperties`
- `items`
- `minItems` / `maxItems`
- `minLength` / `maxLength`
- `pattern`
- `minimum` / `maximum`
- `exclusiveMinimum` / `exclusiveMaximum`
- `allOf` / `anyOf` / `oneOf`

## Generated Contract

For the timer example above, the generated DSL prompt contains a compact action
surface similar to:

```text
Return JSON only.
JSON shape: {"calls":[{"action":"<action>","args":{...}}]}
Allowed actions:
- set_timer args {"duration": integer 1..120, "unit": "seconds"|"minutes", "audible": boolean}
Rules:
- Do not include explanations or Markdown.
```

The model emits the Action IR:

```json
{
  "calls": [
    {
      "action": "set_timer",
      "args": {
        "duration": 15,
        "unit": "minutes",
        "audible": true
      }
    }
  ]
}
```

The mapper validates and emits:

```json
[
  {
    "name": "set_timer",
    "arguments": {
      "duration": 15,
      "unit": "minutes",
      "audible": true
    }
  }
]
```

## Current Limitations

- The compiler assumes each tool's top-level parameter schema is an object.
- Complex nested fields are preserved through `RawArg` rather than rewritten
  into a more compact custom IR.
- Domain aliases, locale normalization, safety policies, and permission checks
  still need to be supplied as catalog rules or custom validators.
- Provider-specific output adapters are not yet separated. The current emitter
  returns provider-neutral `{name, arguments}` calls.

## Research Role

This module is the first step toward the main system claim:

> Tool schemas can be compiled into a compact Action IR, and that IR can become
> the unit for prompting, small-model fine-tuning, validation, regression
> testing, repair, and deployment.

Future work should extend this into MCP schema ingestion, catalog optimization,
automatic alias discovery, small-model dataset generation, and provider-specific
emission adapters.
