from __future__ import annotations

import re
import time
from typing import Any

from ganglion.dsl.validator import parse_json_dsl
from ganglion.runtime.types import ModelResult
from ganglion.schema.iot_light import ROOM_ALIASES


class RuleBasedJSONDSLClient:
    """Deterministic offline stand-in for the LLM."""

    def invoke(self, user_prompt: str) -> ModelResult:
        started = time.perf_counter()
        payload = self._to_payload(user_prompt)
        plan = parse_json_dsl(payload)
        return ModelResult(
            plan=plan,
            raw=payload,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=None,
            output_tokens=None,
        )

    def _to_payload(self, prompt: str) -> dict[str, Any]:
        text = prompt.strip().lower()
        if any(word in text for word in ["목록", "장치", "디바이스", "devices"]):
            return {"calls": [{"action": "list_devices", "args": {}}]}

        room = _room(text)
        if any(word in text for word in ["영화", "movie"]):
            return {
                "calls": [
                    {
                        "action": "create_scene",
                        "args": {
                            "name": "movie",
                            "actions": [
                                {
                                    "action": "set_light",
                                    "args": {
                                        "room": room or "living",
                                        "state": "on",
                                        "brightness": 20,
                                        "color_temp": "warm",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }

        if any(word in text for word in ["상태", "확인", "state"]):
            return {
                "calls": [
                    {
                        "action": "get_light_state",
                        "args": {"room": room or "living"},
                    }
                ]
            }

        state = _state(text)
        brightness = _brightness(text)
        scheduled_at = _time(text)
        args: dict[str, Any] = {"room": room or "living", "state": state}
        if brightness is not None:
            args["brightness"] = brightness
        color_temp = _color_temp(text)
        if color_temp is not None and scheduled_at is None:
            args["color_temp"] = color_temp

        if scheduled_at is not None:
            args["at"] = scheduled_at
            return {"calls": [{"action": "schedule_light", "args": args}]}
        return {"calls": [{"action": "set_light", "args": args}]}


def _room(text: str) -> str | None:
    aliases = sorted(
        ROOM_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, room in aliases:
        if alias in text:
            return room
    return None


def _brightness(text: str) -> int | None:
    match = re.search(r"(\d{1,3})\s*%", text)
    if not match:
        match = re.search(r"밝기\s*(\d{1,3})", text)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def _state(text: str) -> str:
    if "꺼" in text or "끄" in text or re.search(r"\boff\b", text):
        return "off"
    return "on"


def _time(text: str) -> str | None:
    clock_match = re.search(r"\b([0-2]?\d):([0-5]\d)\b", text)
    if clock_match:
        hour = int(clock_match.group(1))
        minute = int(clock_match.group(2))
        if hour <= 23:
            return f"{hour:02d}:{minute:02d}"

    match = re.search(r"(\d{1,2})\s*시\s*(반)?", text)
    if not match and not any(word in text for word in ["예약", "schedule"]):
        return None
    if not match:
        return "22:00"

    hour = int(match.group(1))
    minute = 30 if match.group(2) else 0
    if any(word in text for word in ["밤", "오후", "저녁", "pm"]) and hour < 12:
        hour += 12
    if any(word in text for word in ["새벽", "오전", "아침", "am"]) and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _color_temp(text: str) -> str | None:
    if any(word in text for word in ["따뜻", "전구색", "warm"]):
        return "warm"
    if any(word in text for word in ["차갑", "주광색", "cool"]):
        return "cool"
    if any(word in text for word in ["중립", "neutral"]):
        return "neutral"
    return None
