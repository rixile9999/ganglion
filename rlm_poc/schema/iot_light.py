from __future__ import annotations

from typing import Any

ROOMS = ("living", "bedroom", "kitchen", "hallway", "office")
STATES = ("on", "off")
COLOR_TEMPS = ("warm", "neutral", "cool")

ROOM_ALIASES = {
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

JSON_DSL_CATALOG = """Return JSON only.
JSON shape: {"calls":[{"action":"<action>","args":{...}}]}
Allowed actions:
- list_devices args {}
- get_light_state args {"room": one of living, bedroom, kitchen, hallway, office}
- set_light args {"room": room, "state": "on"|"off", "brightness": optional integer 0..100, "color_temp": optional "warm"|"neutral"|"cool"}
- schedule_light args {"room": room, "at": "HH:MM" 24h time, "state": "on"|"off", "brightness": optional integer 0..100}
- create_scene args {"name": string, "actions": array of set_light calls}
Rules:
- Use canonical English room names.
- Use 24-hour HH:MM for schedules.
- Do not include explanations or Markdown.
Examples:
User: 거실 불 70%로 켜줘
JSON: {"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":70}}]}
User: 밤 10시 반에 침실 조명 꺼줘
JSON: {"calls":[{"action":"schedule_light","args":{"room":"bedroom","at":"22:30","state":"off"}}]}
User: 현재 주방 조명 상태 확인해줘
JSON: {"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}
User: 영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘
JSON: {"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"living","state":"on","brightness":20,"color_temp":"warm"}}]}}]}
"""

OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_devices",
            "description": "List controllable light devices.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_light_state",
            "description": "Get the current state of a room light.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "enum": list(ROOMS)},
                },
                "required": ["room"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_light",
            "description": "Turn a room light on or off and optionally set brightness or color temperature.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "enum": list(ROOMS)},
                    "state": {"type": "string", "enum": list(STATES)},
                    "brightness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "color_temp": {"type": "string", "enum": list(COLOR_TEMPS)},
                },
                "required": ["room", "state"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_light",
            "description": "Schedule a room light state change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "enum": list(ROOMS)},
                    "at": {
                        "type": "string",
                        "description": "24-hour HH:MM local time.",
                    },
                    "state": {"type": "string", "enum": list(STATES)},
                    "brightness": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["room", "at", "state"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_scene",
            "description": "Create a named scene from multiple light actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "actions": {
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
                },
                "required": ["name", "actions"],
                "additionalProperties": False,
            },
        },
    },
]
