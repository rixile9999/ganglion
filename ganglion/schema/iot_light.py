from __future__ import annotations

import re
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


_KOREAN_TIME_RE = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")


def _korean_time_from_prompt(prompt: str) -> str | None:
    """Return the canonical 24h ``HH:MM`` if ``prompt`` has exactly one
    Korean ``오전/오후 N시 (M분)?`` expression; ``None`` otherwise.

    Multiple matches are treated as ambiguous (don't correct). Out-of-range
    hours/minutes return ``None``.
    """
    matches = _KOREAN_TIME_RE.findall(prompt)
    if len(matches) != 1:
        return None
    period, hh, mm = matches[0]
    h = int(hh)
    m = int(mm) if mm else 0
    if h < 1 or h > 12 or m < 0 or m > 59:
        return None
    if period == "오전":
        h = 0 if h == 12 else h
    else:  # 오후
        h = 12 if h == 12 else h + 12
    return f"{h:02d}:{m:02d}"


def _correct_schedule_at(args: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Override ``args.at`` with the canonical 24h time when the prompt
    contains a single 오전/오후 expression. Targets the documented
    failure mode where 0.6B emits e.g. ``08:00`` for ``오전 1시`` —
    a systematic 12-hour-clock confusion the model can't shake off via
    SFT alone.
    """
    canon = _korean_time_from_prompt(prompt)
    if canon is None:
        return args
    out = dict(args)
    out["at"] = canon
    return out

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


# -----------------------------------------------------------------------------
# Prompt-aware post-correction helpers
# -----------------------------------------------------------------------------


def _chain(*fns):
    """Compose multiple ``(args, prompt) -> args`` correctors left-to-right."""
    def wrapped(args: dict[str, Any], prompt: str) -> dict[str, Any]:
        for fn in fns:
            args = fn(args, prompt)
        return args
    return wrapped


def _alias_match_in_prompt(
    prompt: str, aliases: Mapping[str, str], canonicals: tuple[str, ...],
) -> str | None:
    """Return the unique canonical value implied by ``prompt`` via the
    ``aliases`` map. Returns ``None`` if zero or multiple distinct canonicals
    appear (ambiguous → don't correct).

    Matches are substring-based and case-insensitive. We deliberately avoid
    word-boundary matching because ``aliases`` includes Korean tokens that
    sit inside larger noun phrases (e.g. ``서재`` inside ``서재 조명``).
    """
    text = prompt.lower()
    found: set[str] = set()
    # Sort aliases longest-first so multi-token aliases ("영화 모드") win
    # over their substrings ("영화") — deterministic.
    for alias, canonical in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            found.add(canonical)
            # Strip the matched alias to avoid double-counting overlapping
            # substrings of the same canonical.
            text = text.replace(alias, " ")
    if len(found) != 1:
        return None
    canon = next(iter(found))
    return canon if canon in canonicals else None


def _correct_room_from_prompt(args: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Override ``args.room`` with the prompt's room when the prompt names
    exactly one room. Targets ~8 cases on smart_home_50 where the model
    routes ``복도 조명 켜줘`` to ``room=office``.

    Conservative: if the prompt is ambiguous (zero or multiple distinct
    rooms), we leave ``args.room`` alone.
    """
    canon = _alias_match_in_prompt(prompt, ROOM_ALIASES, ROOMS)
    if canon is None:
        return args
    out = dict(args)
    out["room"] = canon
    return out


def _correct_set_light_color_from_prompt(
    args: dict[str, Any], prompt: str,
) -> dict[str, Any]:
    """Fill ``args.color_temp`` from a prompt color-temp alias when the
    model omits it. Targets the smart_home_50 failure mode where prompts
    like ``거실 조명을 따뜻하게 켜줘`` produce ``set_light(...)`` without
    ``color_temp="warm"``. Same pattern shows up inside
    ``create_scene.actions`` (~40 cases combined on smart_home_50).

    Conservative: only fills when prompt has exactly one color_temp alias
    AND the args don't already declare one.
    """
    if "color_temp" in args and args["color_temp"] is not None:
        return args
    canon = _alias_match_in_prompt(prompt, COLOR_TEMP_ALIASES, COLOR_TEMPS)
    if canon is None:
        return args
    out = dict(args)
    out["color_temp"] = canon
    return out


def _correct_set_light_state_color_swap(
    args: dict[str, Any], prompt: str,
) -> dict[str, Any]:
    """Fix the failure mode where ``state`` carries a color-temp value:
    ``set_light(state="neutral")`` for prompt ``"중립으로 켜줘"``. When
    state ∈ {warm, neutral, cool} and color_temp is absent, swap them and
    default state to ``"on"``.
    """
    state = args.get("state")
    if not isinstance(state, str):
        return args
    state_low = state.strip().lower()
    if state_low not in COLOR_TEMPS:
        return args
    if "color_temp" in args and args["color_temp"] is not None:
        return args
    out = dict(args)
    out["color_temp"] = state_low
    out["state"] = "on"
    return out


def _correct_create_scene_name(
    args: dict[str, Any], prompt: str,
) -> dict[str, Any]:
    """Override ``args.name`` with a scene from SCENE_ALIASES when the
    emitted name is not a canonical scene. Targets the ``영화 모드 #0``
    failure mode (19 cases on smart_home_50) where the ``#N`` suffix
    leaks into the scene name.
    """
    name = args.get("name")
    canonicals = ("movie", "relax", "focus", "sleep")
    if isinstance(name, str) and name.strip().lower() in canonicals:
        return args  # already canonical, nothing to do
    canon = _alias_match_in_prompt(prompt, SCENE_ALIASES, canonicals)
    if canon is None:
        return args
    out = dict(args)
    out["name"] = canon
    return out


def _validate_create_scene(
    args: dict[str, Any],
    catalog: Catalog,
    depth: int,
    *,
    prompt: str | None = None,
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
        nested = catalog.validate_call(raw_action, depth=depth + 1, prompt=prompt)
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
        # ``조명 장치 목록 보여줘 #8`` → small models echo the trailing
        # number into args (e.g. {"id":"8"}). list_devices accepts no
        # args, so dropping unknowns is safe and removes ~19/500 failures
        # on iot_light_5 dataset.jsonl.
        strip_unknown_args=True,
    ),
    ToolSpec(
        name="get_light_state",
        description="Get the current state of a room light.",
        args=(("room", ROOM_ARG),),
        strip_unknown_args=True,
        prompt_correction=_correct_room_from_prompt,
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
        # Small models routinely emit set_light(brightness=N) or
        # set_light(color_temp=...) without state, especially in nested
        # create_scene actions. Either signal makes "on" the only
        # consistent reading. Empirical: ~6% of dataset.jsonl failures on
        # the 0.6B+SFT path cluster on this missing-state pattern.
        defaults_when_missing=(
            (
                "state",
                "on",
                lambda args: "brightness" in args or "color_temp" in args,
            ),
        ),
        strip_unknown_args=True,
        # Three-stage chain:
        # (a) state↔color_temp swap rescues "중립으로 켜줘" → state="neutral"
        # (b) fill missing color_temp from prompt alias ("따뜻하게" → warm)
        #     — also fires for nested set_light inside create_scene.actions
        # (c) prompt-anchored room override rescues 복도→office mistakes
        prompt_correction=_chain(
            _correct_set_light_state_color_swap,
            _correct_set_light_color_from_prompt,
            _correct_room_from_prompt,
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
        strip_unknown_args=True,
        # Three-stage chain: korean time → state↔color_temp swap (rare on
        # schedule, but harmless when not applicable) → room override.
        prompt_correction=_chain(
            _correct_schedule_at,
            _correct_room_from_prompt,
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
        strip_unknown_args=True,
        # ``영화 모드 #0`` echoed as ``name="#0"`` — recover from prompt.
        prompt_correction=_correct_create_scene_name,
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
