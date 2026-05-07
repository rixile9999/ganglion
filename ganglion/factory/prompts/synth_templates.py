"""Prompt templates for tool-anchored synthesis.

Phase 1 uses a single strategy: tool-anchored synthesis. For each tool in the
catalog, ask the teacher to produce N (intent, dsl) pairs that should call
exactly that tool. Multi-tool, adversarial, and abstain strategies are
deferred to Phase 2.

The rendered messages are passed to a chat-completion endpoint with
``response_format={"type": "json_object"}``; the teacher's response is parsed
as JSON and validated against the catalog before being kept.
"""

from __future__ import annotations

from dataclasses import replace

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import (
    BoolArg,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)


SYSTEM_PROMPT = """You generate training data for a tool-calling model.
Given a single tool specification, you produce diverse natural-language user requests
plus the exact JSON DSL call the system should produce for each.

Output JSON only. No markdown, no commentary, no explanation.

Required output shape:
{
  "pairs": [
    {
      "intent": "<natural-language user request>",
      "dsl": {"calls": [{"action": "<tool name>", "args": {...}}]}
    },
    ...
  ]
}

Hard rules (a violation makes the pair unusable):
- Every "dsl" must contain exactly ONE call, and its "action" must equal the requested tool name.
- Use only enum values listed in the spec — never invent new values.
- Stay inside any min/max bounds for integer/number args.
- Time args must be in 24-hour "HH:MM" format.
- Never invent args that are not in the spec.
- Never call a different tool than the one requested.

Soft rules (improve dataset quality):
- Vary phrasing register: terse, polite, indirect, imperative, question-form.
- Vary which optional args appear: some pairs use none, some use all, some use a subset.
- Vary the values of args: don't reuse the same room / time / number.
- Make intents sound like real users — not synthetic, not robotic.
- If the spec hints at locale-specific aliases (e.g. Korean room names), include intents in that locale.
"""


USER_TEMPLATE = """Generate {n} diverse (intent, dsl) pairs for the following tool.

Tool spec:
{tool_spec_text}

Constraints:
- The "action" field of every DSL call must be exactly: {tool_name}
- {locale_note}
- Across the {n} pairs, vary arg values, optional-arg presence, and phrasing style.
- AVOID near-duplicates: if two intents would convey the same meaning with
  similar wording, pick only one. Aim for distinct semantic angles per pair
  (e.g., for a "list devices" tool: "show me the devices" vs "what's available?"
  vs "give me a roster of installed lights" — different framings, not just
  word swaps).

Return JSON only with the shape: {{"pairs": [...]}}"""


def render_tool_anchored_prompt(
    catalog: Catalog,
    tool: ToolSpec,
    n: int = 5,
    locale_hint: str | None = None,
) -> list[dict]:
    """Build chat messages for one tool-anchored synthesis call."""
    locale_note = locale_hint or _infer_locale_hint(tool)
    tool_text = render_tool_spec(tool, catalog)
    _RAW_ARG_CTX["catalog"] = catalog
    try:
        raw_arg_hint = _raw_arg_example(tool)
    finally:
        _RAW_ARG_CTX.pop("catalog", None)
    if raw_arg_hint:
        tool_text = tool_text + "\n" + raw_arg_hint
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                n=n,
                tool_name=tool.name,
                tool_spec_text=tool_text,
                locale_note=locale_note,
            ),
        },
    ]


def render_tool_spec(tool: ToolSpec, catalog: Catalog) -> str:
    """Render a single tool's spec as compact text suitable for the prompt body.

    Reuses the catalog's DSL renderer by isolating to a single-tool catalog,
    which guarantees the format matches what the trained model will see at
    inference time. Then strips the surrounding envelope so only the tool
    line + description remains.
    """
    one_tool_catalog = replace(catalog, tools=(tool,), examples=(), extra_rules=())
    full = one_tool_catalog.render_json_dsl().strip()
    # Extract just the "- toolname args {...}" line(s)
    tool_lines = [
        line for line in full.splitlines() if line.startswith(f"- {tool.name} ")
    ]
    body = "\n".join(tool_lines)
    if tool.description:
        body += f"\n  description: {tool.description}"
    body += _arg_value_hints(tool)
    return body


def _arg_value_hints(tool: ToolSpec) -> str:
    """Add per-arg verbose hints (enum members, bounds, etc.) for the teacher."""
    hints: list[str] = []
    for arg_name, spec in tool.args:
        if isinstance(spec, EnumArg):
            values = ", ".join(f'"{v}"' for v in spec.values)
            req = "required" if spec.required else "optional"
            hints.append(f"  - {arg_name} ({req}, enum): {values}")
            if spec.aliases:
                alias_pairs = ", ".join(
                    f'"{k}"→"{v}"' for k, v in spec.aliases.items()
                )
                hints.append(f"    aliases: {alias_pairs}")
        elif isinstance(spec, IntArg):
            req = "required" if spec.required else "optional"
            bounds = []
            if spec.min_value is not None:
                bounds.append(f"min={spec.min_value}")
            if spec.max_value is not None:
                bounds.append(f"max={spec.max_value}")
            bounds_text = " (" + ", ".join(bounds) + ")" if bounds else ""
            hints.append(f"  - {arg_name} ({req}, integer{bounds_text})")
        elif isinstance(spec, NumberArg):
            req = "required" if spec.required else "optional"
            bounds = []
            if spec.min_value is not None:
                bounds.append(f"min={spec.min_value}")
            if spec.max_value is not None:
                bounds.append(f"max={spec.max_value}")
            bounds_text = " (" + ", ".join(bounds) + ")" if bounds else ""
            hints.append(f"  - {arg_name} ({req}, number{bounds_text})")
        elif isinstance(spec, StringArg):
            req = "required" if spec.required else "optional"
            hints.append(f"  - {arg_name} ({req}, string)")
        elif isinstance(spec, BoolArg):
            req = "required" if spec.required else "optional"
            hints.append(f"  - {arg_name} ({req}, boolean)")
        elif isinstance(spec, TimeArg):
            req = "required" if spec.required else "optional"
            hints.append(f'  - {arg_name} ({req}, "HH:MM" 24h time)')
        elif isinstance(spec, RawArg):
            req = "required" if spec.required else "optional"
            hints.append(f"  - {arg_name} ({req}): {spec.dsl_description}")
    if hints:
        return "\n  args detail:\n" + "\n".join(hints)
    return ""


def _raw_arg_example(tool: ToolSpec) -> str:
    """For tools whose args include a RawArg, append a concrete example.

    The generic renderer only emits a one-liner like 'array of set_light calls'
    which leaves the teacher guessing the actual shape. We probe the JSON
    Schema and synthesize a plausible example so the teacher can pattern-match.
    """
    raw_args = [(name, spec) for name, spec in tool.args if isinstance(spec, RawArg)]
    if not raw_args:
        return ""

    # Need access to the surrounding catalog to resolve nested tool refs
    from ganglion.dsl.catalog import Catalog as _Catalog  # noqa: F401 (type only)
    catalog = _RAW_ARG_CTX.get("catalog")

    lines = ["", "  Concrete example for the complex args:"]
    example_args: dict = {}
    for name, spec in raw_args:
        nested_tool = (
            _find_referenced_tool(spec.dsl_description, catalog) if catalog else None
        )
        if nested_tool is not None:
            example_args[name] = [_example_call_for_tool(nested_tool)]
        else:
            example_args[name] = _example_from_schema(spec.json_schema)
    # Also include simple required args so the example is parseable
    for name, spec in tool.args:
        if isinstance(spec, RawArg):
            continue
        if spec.required:
            example_args[name] = _example_from_arg_spec(spec)

    import json as _json
    full_call = {"action": tool.name, "args": example_args}
    full_dsl = {"calls": [full_call]}
    lines.append("  " + _json.dumps(full_dsl, ensure_ascii=False))
    lines.append(
        "  ↑ Note the nested action+args shape for any complex array-of-call args."
    )
    return "\n".join(lines)


# Module-level context to pass catalog into _raw_arg_example without changing
# its signature (it's called from inside render_tool_anchored_prompt).
_RAW_ARG_CTX: dict = {}


def _find_referenced_tool(description: str, catalog) -> ToolSpec | None:
    """Detect a 'XXX calls' pattern in the description and return that tool."""
    if not description or catalog is None:
        return None
    lowered = description.lower()
    for tool in catalog.tools:
        if f"{tool.name} call" in lowered or f"{tool.name}s call" in lowered:
            return tool
    return None


def _example_call_for_tool(tool: ToolSpec) -> dict:
    """Build a valid {"action": ..., "args": ...} for a tool."""
    args: dict = {}
    for arg_name, spec in tool.args:
        if not spec.required:
            continue
        args[arg_name] = _example_from_arg_spec(spec)
    return {"action": tool.name, "args": args}


def _example_from_schema(schema: dict) -> object:
    """Generate a minimal valid example from a JSON Schema fragment."""
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items", {})
        return [_example_from_schema(items)]
    if schema_type == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required", list(props.keys())[:2])
        out: dict = {}
        for prop_name in required:
            if prop_name in props:
                out[prop_name] = _example_from_schema(props[prop_name])
        return out
    if schema_type == "string":
        enum = schema.get("enum")
        if enum:
            return enum[0]
        return "set_light"  # specific to scene actions; generic placeholder
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    return {}


def _example_from_arg_spec(spec) -> object:
    """Pick a minimal example value for a non-RawArg ArgSpec."""
    if isinstance(spec, EnumArg):
        return spec.values[0]
    if isinstance(spec, IntArg):
        return spec.min_value if spec.min_value is not None else 0
    if isinstance(spec, NumberArg):
        return float(spec.min_value) if spec.min_value is not None else 0.0
    if isinstance(spec, BoolArg):
        return True
    if isinstance(spec, TimeArg):
        return "09:00"
    if isinstance(spec, StringArg):
        return "example"
    return None


def _infer_locale_hint(tool: ToolSpec) -> str:
    """If any enum arg has Korean-character aliases, request a Korean intent mix."""
    has_korean_alias = False
    for _, spec in tool.args:
        aliases = getattr(spec, "aliases", None) or {}
        if any(_is_korean(s) for s in aliases.keys()):
            has_korean_alias = True
            break
    if has_korean_alias:
        return "Mix English (60%) and Korean (40%) intents."
    return "Use natural English intents."


def _is_korean(text: str) -> bool:
    """Cheap check: any character in Hangul Syllables / Jamo blocks."""
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:  # Hangul Syllables
            return True
        if 0x1100 <= code <= 0x11FF:  # Hangul Jamo
            return True
    return False
