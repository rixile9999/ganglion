"""``home_assistant_4`` tier — the ``iot_light_5`` intents projected onto Home
Assistant's LLM-facing Assist API (Open Home Foundation).

The four tools mirror the slot schemas of ``HassTurnOn`` / ``HassTurnOff`` /
``HassLightSet`` / ``GetLiveContext`` as exposed by ``homeassistant.helpers.llm``
(verified against ``home-assistant/core`` ``dev`` on 2026-09-09). Home
Assistant serves exactly these tools over its MCP Server integration
(``/api/mcp/assist``), so a catalog in this shape can later be run against a
physical Home Assistant instance without changing the contract.

This module also owns ``translate_plan`` — the deterministic projection from
an ``iot_light_5`` ``ActionPlan`` to an Assist ``ActionPlan`` — which the
derived dataset generator (``examples/home_assistant/generate_dataset.py``)
uses. Intents Assist cannot express (``schedule_light``, ``create_scene``)
translate to ``None`` and are excluded, never approximated.

See ``docs/tasks/contract_tier_home_assistant.md``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ganglion.contract.catalog import Catalog, _validate_flat_args
from ganglion.contract.tool_spec import (
    DSLValidationError,
    EnumArg,
    IntArg,
    StringArg,
    ToolSpec,
)
from ganglion.contract.builtins.iot_light import (
    CATALOG as IOT_LIGHT_CATALOG,
    ROOM_ALIASES,
    ROOMS,
)
from ganglion.contract.types import ActionPlan, ToolCall

__all__ = [
    "CATALOG",
    "COLOR_TEMP_KELVIN",
    "HOME_ASSISTANT_TOOLS",
    "TOOL_NAME_PREFIX",
    "TARGETING_SLOTS",
    "translate_plan",
]

# ``iot_light`` colour-temperature enum → Kelvin. 6500 is the upper bound of
# Home Assistant's ``color_temp`` selector (2000..6500).
COLOR_TEMP_KELVIN: dict[str, int] = {"warm": 2700, "neutral": 4000, "cool": 6500}

# Home Assistant 2026.9 prefixes LLM tool names with the providing domain
# (``intent__HassTurnOn``, ``homeassistant__GetLiveContext``). The tier keeps
# the unprefixed intent names canonical; an MCP adapter applies this at the
# boundary. Data only — nothing in this module reads it.
TOOL_NAME_PREFIX: dict[str, str] = {
    "HassTurnOn": "intent",
    "HassTurnOff": "intent",
    "HassLightSet": "intent",
    "GetLiveContext": "homeassistant",
}

# HassTurnOn/Off/LightSet targeting slots. HA marks all of them optional and
# rejects an untargeted intent only at execution time ("no entity matched");
# the tier rejects it at parse time so exact-match cannot reward empty calls.
TARGETING_SLOTS: tuple[str, ...] = ("name", "area", "floor")

AREA_ARG = EnumArg(
    values=ROOMS,
    aliases=ROOM_ALIASES,
    required=False,
    description="Home Assistant area name.",
)
NAME_ARG = StringArg(required=False, description="Entity or device name.")
FLOOR_ARG = StringArg(required=False, description="Floor name.")
DOMAIN_ARG = EnumArg(values=("light",), required=False, description="Entity domain.")
DEVICE_CLASS_ARG = StringArg(required=False, description="Entity device class.")
COLOR_ARG = StringArg(required=False, description="CSS colour name.")
TEMPERATURE_ARG = IntArg(
    min_value=2000, max_value=6500, required=False, description="Colour temperature in Kelvin."
)
BRIGHTNESS_ARG = IntArg(
    min_value=0, max_value=100, required=False, allow_percent=True,
    description="Brightness percentage, 0 is off and 100 is fully lit.",
)

TARGETING_ARGS: tuple[tuple[str, Any], ...] = (
    ("name", NAME_ARG),
    ("area", AREA_ARG),
    ("floor", FLOOR_ARG),
    ("domain", DOMAIN_ARG),
    ("device_class", DEVICE_CLASS_ARG),
)


def _has_target(args: Mapping[str, Any]) -> bool:
    return any(args.get(slot) not in (None, "") for slot in TARGETING_SLOTS)


# The catalog carries a single domain, so filling ``domain="light"`` when a
# target is named is unambiguous. Both ``{"area":"living"}`` and
# ``{"area":"living","domain":"light"}`` normalise to the same ToolCall.
DOMAIN_DEFAULT = ("domain", "light", _has_target)


def _make_targeted_validator(tool_name: str):
    def _validate(
        args: dict[str, Any],
        catalog: Catalog,
        depth: int,
        *,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        if depth > 0:
            raise DSLValidationError(f"{tool_name} cannot be nested")
        if not _has_target(args):
            raise DSLValidationError(
                f"{tool_name} requires at least one of {', '.join(TARGETING_SLOTS)}"
            )
        tool = catalog.get_tool(tool_name)
        assert tool is not None
        return _validate_flat_args(tool, args)

    return _validate


HOME_ASSISTANT_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="HassTurnOn",
        description="Turns on/opens a device or entity",
        args=TARGETING_ARGS,
        defaults_when_missing=(DOMAIN_DEFAULT,),
        strip_unknown_args=True,
        custom_validator=_make_targeted_validator("HassTurnOn"),
    ),
    ToolSpec(
        name="HassTurnOff",
        description="Turns off/closes a device or entity",
        args=TARGETING_ARGS,
        defaults_when_missing=(DOMAIN_DEFAULT,),
        strip_unknown_args=True,
        custom_validator=_make_targeted_validator("HassTurnOff"),
    ),
    ToolSpec(
        name="HassLightSet",
        description="Sets the brightness percentage or color of a light",
        args=TARGETING_ARGS
        + (
            ("color", COLOR_ARG),
            ("temperature", TEMPERATURE_ARG),
            ("brightness", BRIGHTNESS_ARG),
        ),
        defaults_when_missing=(DOMAIN_DEFAULT,),
        strip_unknown_args=True,
        custom_validator=_make_targeted_validator("HassLightSet"),
    ),
    ToolSpec(
        name="GetLiveContext",
        description=(
            "Provides real-time information about the current state, value, "
            "or mode of devices, sensors, entities, or areas."
        ),
        strip_unknown_args=True,
    ),
)


# -----------------------------------------------------------------------------
# iot_light_5 → Assist projection
# -----------------------------------------------------------------------------


def _translate_call(call: ToolCall) -> ToolCall | None:
    args = call.args
    if call.action == "set_light":
        target = {"area": args["room"], "domain": "light"}
        if args.get("state") == "off":
            return ToolCall(action="HassTurnOff", args=target)
        light_args = dict(target)
        if args.get("brightness") is not None:
            light_args["brightness"] = int(args["brightness"])
        if args.get("color_temp") is not None:
            light_args["temperature"] = COLOR_TEMP_KELVIN[args["color_temp"]]
        if len(light_args) == len(target):
            return ToolCall(action="HassTurnOn", args=target)
        return ToolCall(action="HassLightSet", args=light_args)
    if call.action in ("get_light_state", "list_devices"):
        # Assist has no per-area state query for LLMs; the room is dropped.
        return ToolCall(action="GetLiveContext", args={})
    # schedule_light (clock-time; HassStartTimer is duration-based) and
    # create_scene (no LLM-facing scene intent) have no Assist counterpart.
    return None


def translate_plan(plan: ActionPlan) -> ActionPlan | None:
    """Project an ``iot_light_5`` plan onto Assist tools.

    Returns ``None`` when any call has no Home Assistant counterpart. The
    projection is total on the supported subset and never invents a tool.
    """
    calls: list[ToolCall] = []
    for call in plan.calls:
        translated = _translate_call(call)
        if translated is None:
            return None
        calls.append(translated)
    return ActionPlan(calls=tuple(calls))


def _translate_example(prompt: str, iot_json: str) -> tuple[str, str] | None:
    plan = translate_plan(IOT_LIGHT_CATALOG.parse_json_dsl(iot_json))
    if plan is None:
        return None
    import json

    return prompt, json.dumps(plan.to_jsonable(), ensure_ascii=False, separators=(",", ":"))


_EXAMPLE_SOURCES: tuple[tuple[str, str], ...] = (
    (
        "거실 불 70%로 켜줘",
        '{"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":70}}]}',
    ),
    (
        "복도 불 꺼줘",
        '{"calls":[{"action":"set_light","args":{"room":"hallway","state":"off"}}]}',
    ),
    (
        "서재 조명을 따뜻하게 켜줘",
        '{"calls":[{"action":"set_light","args":{"room":"office","state":"on","color_temp":"warm"}}]}',
    ),
    (
        "현재 주방 조명 상태 확인해줘",
        '{"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}',
    ),
)

HOME_ASSISTANT_EXAMPLES: tuple[tuple[str, str], ...] = tuple(
    example
    for example in (_translate_example(p, j) for p, j in _EXAMPLE_SOURCES)
    if example is not None
)

HOME_ASSISTANT_RULES: tuple[str, ...] = (
    "Use canonical English area names.",
    "Target lights by area and domain; use name only for a specific device.",
    "Colour temperature is in Kelvin: warm 2700, neutral 4000, cool 6500.",
)

CATALOG = Catalog(
    name="home_assistant_4",
    tools=HOME_ASSISTANT_TOOLS,
    examples=HOME_ASSISTANT_EXAMPLES,
    extra_rules=HOME_ASSISTANT_RULES,
)
