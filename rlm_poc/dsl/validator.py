from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from rlm_poc.dsl.types import ActionPlan, ToolCall
from rlm_poc.schema.iot_light import COLOR_TEMPS, ROOM_ALIASES, ROOMS, STATES

VALID_ACTIONS = {
    "list_devices",
    "get_light_state",
    "set_light",
    "schedule_light",
    "create_scene",
}

SCENE_ALIASES = {
    "movie": "movie",
    "movie mode": "movie",
    "movie scene": "movie",
    "영화": "movie",
    "영화 모드": "movie",
    "영화 감상": "movie",
    "영화 감상 모드": "movie",
    "영화 보기": "movie",
}


class DSLValidationError(ValueError):
    """Raised when model JSON cannot be converted into safe tool calls."""


def parse_json_dsl(raw: str | Mapping[str, Any]) -> ActionPlan:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DSLValidationError(f"invalid JSON: {exc.msg}") from exc
    else:
        payload = dict(raw)
    return validate_json_dsl(payload)


def validate_json_dsl(payload: Mapping[str, Any]) -> ActionPlan:
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

    calls = tuple(_validate_call(raw_call, depth=0) for raw_call in raw_calls)
    return ActionPlan(calls=calls)


def _validate_call(raw_call: Any, depth: int) -> ToolCall:
    if not isinstance(raw_call, Mapping):
        raise DSLValidationError("each call must be an object")

    action = raw_call.get("action")
    if not isinstance(action, str):
        raise DSLValidationError("call.action must be a string")
    action = action.strip()
    if action not in VALID_ACTIONS:
        raise DSLValidationError(f"unsupported action: {action}")

    args = raw_call.get("args", {})
    if not isinstance(args, Mapping):
        raise DSLValidationError("call.args must be an object")

    if depth > 0 and action != "set_light":
        raise DSLValidationError("scene actions may only contain set_light")

    validator = {
        "list_devices": _validate_list_devices,
        "get_light_state": _validate_get_light_state,
        "set_light": _validate_set_light,
        "schedule_light": _validate_schedule_light,
        "create_scene": _validate_create_scene,
    }[action]
    return ToolCall(action=action, args=validator(dict(args), depth))


def _validate_list_devices(args: dict[str, Any], _depth: int) -> dict[str, Any]:
    if args:
        raise DSLValidationError("list_devices does not accept args")
    return {}


def _validate_get_light_state(args: dict[str, Any], _depth: int) -> dict[str, Any]:
    return {"room": _required_room(args)}


def _validate_set_light(args: dict[str, Any], _depth: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "room": _required_room(args),
        "state": _required_state(args),
    }
    if "brightness" in args and args["brightness"] is not None:
        normalized["brightness"] = _brightness(args["brightness"])
    if "color_temp" in args and args["color_temp"] is not None:
        normalized["color_temp"] = _color_temp(args["color_temp"])
    return normalized


def _validate_schedule_light(args: dict[str, Any], _depth: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "room": _required_room(args),
        "at": _required_time(args),
        "state": _required_state(args),
    }
    if "brightness" in args and args["brightness"] is not None:
        normalized["brightness"] = _brightness(args["brightness"])
    return normalized


def _validate_create_scene(args: dict[str, Any], depth: int) -> dict[str, Any]:
    if depth > 0:
        raise DSLValidationError("nested scenes are not supported")

    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DSLValidationError("create_scene.name must be a non-empty string")

    raw_actions = args.get("actions")
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes)):
        raise DSLValidationError("create_scene.actions must be an array")
    if not raw_actions:
        raise DSLValidationError("create_scene.actions must not be empty")

    actions = [_validate_call(action, depth=depth + 1) for action in raw_actions]
    normalized_name = SCENE_ALIASES.get(name.strip().lower(), name.strip())
    return {
        "name": normalized_name,
        "actions": [
            {"action": action.action, "args": action.args}
            for action in actions
        ],
    }


def _required_room(args: Mapping[str, Any]) -> str:
    raw = args.get("room")
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError("room is required")
    normalized = ROOM_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if normalized not in ROOMS:
        raise DSLValidationError(f"unsupported room: {raw}")
    return normalized


def _required_state(args: Mapping[str, Any]) -> str:
    raw = args.get("state")
    if isinstance(raw, bool):
        return "on" if raw else "off"
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError("state is required")
    normalized = raw.strip().lower()
    if normalized in {"켜", "켜기", "켜줘", "on"}:
        normalized = "on"
    if normalized in {"꺼", "끄기", "꺼줘", "off"}:
        normalized = "off"
    if normalized not in STATES:
        raise DSLValidationError(f"unsupported state: {raw}")
    return normalized


def _required_time(args: Mapping[str, Any]) -> str:
    raw = args.get("at")
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError("at is required")
    value = raw.strip()
    if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value):
        raise DSLValidationError("at must be HH:MM")
    hour = int(value[:2])
    if hour > 23:
        raise DSLValidationError("at hour must be 00..23")
    return value


def _brightness(raw: Any) -> int:
    if isinstance(raw, str):
        raw = raw.strip().removesuffix("%")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise DSLValidationError("brightness must be an integer") from exc
    if not 0 <= value <= 100:
        raise DSLValidationError("brightness must be 0..100")
    return value


def _color_temp(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DSLValidationError("color_temp must be a string")
    value = raw.strip().lower()
    aliases = {
        "따뜻": "warm",
        "따뜻하게": "warm",
        "전구색": "warm",
        "중립": "neutral",
        "차갑": "cool",
        "차갑게": "cool",
        "주광색": "cool",
    }
    value = aliases.get(value, value)
    if value not in COLOR_TEMPS:
        raise DSLValidationError(f"unsupported color_temp: {raw}")
    return value
