from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import (
    DSLValidationError,
    EnumArg,
    IntArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)

ROOMS = ("living", "bedroom", "kitchen", "hallway", "office")
STATES = ("on", "off")
COLOR_TEMPS = ("warm", "neutral", "cool")

ROOM_ALIASES: dict[str, str] = {
    "living room": "living",
    "living": "living",
    "lounge": "living",
    "거실": "living",
    "bedroom": "bedroom",
    "bed room": "bedroom",
    "침실": "bedroom",
    "방": "bedroom",
    "kitchen": "kitchen",
    "주방": "kitchen",
    "부엌": "kitchen",
    "hallway": "hallway",
    "hall": "hallway",
    "복도": "hallway",
    "office": "office",
    "study": "office",
    "서재": "office",
    "사무실": "office",
}

STATE_ALIASES: dict[str, str] = {
    "켜": "on",
    "켜기": "on",
    "켜줘": "on",
    "on": "on",
    "꺼": "off",
    "끄기": "off",
    "꺼줘": "off",
    "off": "off",
}

COLOR_TEMP_ALIASES: dict[str, str] = {
    "따뜻": "warm",
    "따뜻하게": "warm",
    "전구색": "warm",
    "warm": "warm",
    "중립": "neutral",
    "neutral": "neutral",
    "차갑": "cool",
    "차갑게": "cool",
    "주광색": "cool",
    "cool": "cool",
}

SCENE_ALIASES: dict[str, str] = {
    "movie": "movie",
    "movie mode": "movie",
    "movie scene": "movie",
    "영화": "movie",
    "영화 모드": "movie",
    "영화 감상": "movie",
    "영화 감상 모드": "movie",
    "영화 보기": "movie",
    "cinema": "movie",
    "cinema mode": "movie",
    "relax": "relax",
    "relax mode": "relax",
    "휴식": "relax",
    "focus": "focus",
    "focus mode": "focus",
    "집중": "focus",
    "독서": "focus",
    "reading": "focus",
    "sleep": "sleep",
    "sleep mode": "sleep",
    "수면": "sleep",
    "수면 모드": "sleep",
}

ROOM_ARG = EnumArg(values=ROOMS, aliases=ROOM_ALIASES)
STATE_ARG = EnumArg(values=STATES, aliases=STATE_ALIASES, bool_true="on", bool_false="off")
BRIGHTNESS_ARG = IntArg(min_value=0, max_value=100, required=False, allow_percent=True)
COLOR_TEMP_ARG = EnumArg(values=COLOR_TEMPS, aliases=COLOR_TEMP_ALIASES, required=False)
SCENE_NAME_ARG = EnumArg(values=("movie", "relax", "focus", "sleep"), aliases=SCENE_ALIASES)


def _validate_create_scene(
    args: dict[str, Any],
    catalog: Catalog,
    depth: int,
) -> dict[str, Any]:
    if depth > 0:
        raise DSLValidationError("nested scenes are not supported")

    name_raw = args.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise DSLValidationError("create_scene.name must be a non-empty string")
    raw_actions = args.get("actions")
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes)):
        raise DSLValidationError("create_scene.actions must be an array")
    if not raw_actions:
        raise DSLValidationError("create_scene.actions must not be empty")

    actions = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, Mapping):
            raise DSLValidationError("each scene action must be an object")
        nested_action = raw_action.get("action")
        if nested_action != "set_light":
            raise DSLValidationError("scene actions may only contain set_light")
        nested = catalog.validate_call(raw_action, depth=depth + 1)
        actions.append({"action": nested.action, "args": nested.args})

    # Normalize scene name using SCENE_ALIASES
    name_clean = name_raw.strip().lower()
    normalized_name = SCENE_ALIASES.get(name_clean, name_clean)
    if normalized_name not in ("movie", "relax", "focus", "sleep"):
        raise DSLValidationError(f"create_scene.name: unsupported scene name '{name_raw}'")
    return {"name": normalized_name, "actions": actions}


SCENE_ACTIONS_RAW_ARG = RawArg(
    json_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set_light"]},
                "args": {"type": "object"},
            },
            "required": ["action", "args"],
        },
    },
    dsl_description="array of set_light calls",
)


IOT_LIGHT_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_devices",
        description="List controllable light devices.",
    ),
    ToolSpec(
        name="get_light_state",
        description="Get the current state of a room light.",
        args=(("room", ROOM_ARG),),
    ),
    ToolSpec(
        name="set_light",
        description="Turn a room light on or off and optionally set brightness or color temperature.",
        args=(
            ("room", ROOM_ARG),
            ("state", STATE_ARG),
            ("brightness", BRIGHTNESS_ARG),
            ("color_temp", COLOR_TEMP_ARG),
        ),
    ),
    ToolSpec(
        name="schedule_light",
        description="Schedule a room light state change.",
        args=(
            ("room", ROOM_ARG),
            ("at", TimeArg()),
            ("state", STATE_ARG),
            ("brightness", BRIGHTNESS_ARG),
        ),
    ),
    ToolSpec(
        name="create_scene",
        description="Create a named scene from multiple light actions.",
        args=(
            ("name", SCENE_NAME_ARG),
            ("actions", SCENE_ACTIONS_RAW_ARG),
        ),
        custom_validator=_validate_create_scene,
    ),
)


IOT_LIGHT_EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        "거실 불 70%로 켜줘",
        '{"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":70}}]}',
    ),
    (
        "밤 10시 반에 침실 조명 꺼줘",
        '{"calls":[{"action":"schedule_light","args":{"room":"bedroom","at":"22:30","state":"off"}}]}',
    ),
    (
        "현재 주방 조명 상태 확인해줘",
        '{"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}',
    ),
    (
        "영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘",
        '{"calls":[{"action":"create_scene","args":{"name":"movie","actions":'
        '[{"action":"set_light","args":{"room":"living","state":"on","brightness":20,"color_temp":"warm"}}]}}]}',
    ),
)


IOT_LIGHT_RULES: tuple[str, ...] = (
    "Use canonical English room names.",
    "Use 24-hour HH:MM for schedules.",
)


CATALOG = Catalog(
    name="iot_light_5",
    tools=IOT_LIGHT_TOOLS,
    examples=IOT_LIGHT_EXAMPLES,
    extra_rules=IOT_LIGHT_RULES,
)


JSON_DSL_CATALOG = CATALOG.render_json_dsl()
OPENAI_TOOLS: list[dict[str, Any]] = CATALOG.render_openai_tools()
