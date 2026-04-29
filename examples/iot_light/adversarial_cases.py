"""Adversarial test cases for M4 repair loop validation.

These cases are designed to trigger validation failures that the repair loop
can attempt to recover from. The adversarial cases fall into two categories:

1. **Hard for LLM**: Prompts that may cause the LLM to generate invalid output
   (e.g., explicit invalid values, malformed requests).

2. **Rule-based client fails**: Prompts that the deterministic client can't
   handle but the LLM should manage (useful for comparing DSL vs native).

Categories:
  - invalid_room_explicit: Prompt contains explicit invalid room name
  - malformed_json_hint: Prompt hints at malformed output
  - multi_call: Multiple tool calls required
  - out_of_range_explicit: Prompt explicitly asks for out-of-range values
  - unknown_action_hint: Prompt asks for action that doesn't exist

Note: Qwen structured output is very robust, so many of these may still pass
on the first attempt. The repair loop serves as a safety net for edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).with_name("adversarial_cases.jsonl")


def call(action: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"calls": [{"action": action, "args": args}]}


def multi_call(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"calls": list(calls)}


# ---------------------------------------------------------------------------
# Adversarial cases – each tuple is (prompt, expected, category)
# ---------------------------------------------------------------------------

ADVERSARIAL_CASES: list[tuple[str, dict[str, Any], str]] = [
    # === Multi-call (5 cases) ===
    # These require the LLM to generate multiple tool calls.
    # The rules-based client fails on these.
    (
        "거실과 주방 불 모두 켜줘",
        multi_call(
            call("set_light", {"room": "living", "state": "on"})["calls"][0],
            call("set_light", {"room": "kitchen", "state": "on"})["calls"][0],
        ),
        "multi_call",
    ),
    (
        "7시에 켜고 10시에 꺼줘",
        multi_call(
            call("schedule_light", {"room": "living", "at": "07:00", "state": "on"})["calls"][0],
            call("schedule_light", {"room": "living", "at": "22:00", "state": "off"})["calls"][0],
        ),
        "multi_call",
    ),
    (
        "침실 불 켜고 상태 확인해줘",
        multi_call(
            call("set_light", {"room": "bedroom", "state": "on"})["calls"][0],
            call("get_light_state", {"room": "bedroom"})["calls"][0],
        ),
        "multi_call",
    ),
    (
        "거실 불 8시에 켜고 11시에 꺼줘",
        multi_call(
            call("schedule_light", {"room": "living", "at": "08:00", "state": "on"})["calls"][0],
            call("schedule_light", {"room": "living", "at": "23:00", "state": "off"})["calls"][0],
        ),
        "multi_call",
    ),
    (
        "서재와 침실 불 모두 70%로 켜줘",
        multi_call(
            call("set_light", {"room": "office", "state": "on", "brightness": 70})["calls"][0],
            call("set_light", {"room": "bedroom", "state": "on", "brightness": 70})["calls"][0],
        ),
        "multi_call",
    ),

    # === Invalid room explicit (5 cases) ===
    # Prompt explicitly mentions a room not in the schema.
    # The LLM should either infer a valid room or reject the request.
    (
        "안방 불 켜줘",
        call("set_light", {"room": "bedroom", "state": "on"}),
        "invalid_room_explicit",
    ),
    (
        "마당 조명 꺼줘",
        call("set_light", {"room": "hallway", "state": "off"}),
        "invalid_room_explicit",
    ),
    (
        "지하불 켜줘",
        call("set_light", {"room": "living", "state": "on"}),
        "invalid_room_explicit",
    ),
    (
        "게임방 불 70%로 켜줘",
        call("set_light", {"room": "office", "state": "on", "brightness": 70}),
        "invalid_room_explicit",
    ),
    (
        "화장실 불 꺼줘",
        call("set_light", {"room": "hallway", "state": "off"}),
        "invalid_room_explicit",
    ),

    # === Out-of-range explicit (3 cases) ===
    # Prompt asks for brightness > 100 or < 0.
    # The LLM should clamp to valid range.
    (
        "거실 불 150%로 켜줘",
        call("set_light", {"room": "living", "state": "on", "brightness": 100}),
        "out_of_range_explicit",
    ),
    (
        "침실 불 -10%로 해줘",
        call("set_light", {"room": "bedroom", "state": "on", "brightness": 0}),
        "out_of_range_explicit",
    ),
    (
        "주방 불 200%로 최대로 켜줘",
        call("set_light", {"room": "kitchen", "state": "on", "brightness": 100}),
        "out_of_range_explicit",
    ),

    # === Unknown action hints (5 cases) ===
    # Prompts that ask for actions not in the tool set.
    # The LLM should either map to closest valid action or fail.
    (
        "거실 불 색깔 바꿔줘",
        call("set_light", {"room": "living", "state": "on", "color_temp": "warm"}),
        "unknown_action_hint",
    ),
    (
        "주방 온도 조절해줘",
        call("get_light_state", {"room": "kitchen"}),
        "unknown_action_hint",
    ),
    (
        "침실 알람 설정해줘",
        call("schedule_light", {"room": "bedroom", "at": "07:00", "state": "on"}),
        "unknown_action_hint",
    ),
    (
        "서재 환기 시켜줘",
        call("get_light_state", {"room": "office"}),
        "unknown_action_hint",
    ),
    (
        "복도 보안등 켜줘",
        call("set_light", {"room": "hallway", "state": "on"}),
        "unknown_action_hint",
    ),

    # === Vague brightness (5 cases) ===
    # Non-numeric brightness that requires interpretation.
    (
        "거실 불 밝게 켜줘",
        call("set_light", {"room": "living", "state": "on", "brightness": 80}),
        "vague_brightness",
    ),
    (
        "침실 불 어둡게 해줘",
        call("set_light", {"room": "bedroom", "state": "on", "brightness": 20}),
        "vague_brightness",
    ),
    (
        "주방 불 중간 밝기로 켜줘",
        call("set_light", {"room": "kitchen", "state": "on", "brightness": 50}),
        "vague_brightness",
    ),
    (
        "서재 불 반짝하게 켜줘",
        call("set_light", {"room": "office", "state": "on", "brightness": 100}),
        "vague_brightness",
    ),
    (
        "복도 불 은은하게 켜줘",
        call("set_light", {"room": "hallway", "state": "on", "brightness": 30}),
        "vague_brightness",
    ),

    # === Ambiguous scene name (5 cases) ===
    # Scene names that require normalization.
    (
        "영화 보기 좋은 분위기로 만들어줘. 거실 조명은 20% 따뜻하게 켜줘",
        call("create_scene", {
            "name": "movie",
            "actions": [{
                "action": "set_light",
                "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"},
            }],
        }),
        "ambiguous_scene",
    ),
    (
        "cinema mode로 바꿔줘. 침실 불 30% 중립으로",
        call("create_scene", {
            "name": "movie",
            "actions": [{
                "action": "set_light",
                "args": {"room": "bedroom", "state": "on", "brightness": 30, "color_temp": "neutral"},
            }],
        }),
        "ambiguous_scene",
    ),
    (
        "독서 모드 scene 만들어줘. 서재 불 80% 차갑게",
        call("create_scene", {
            "name": "focus",
            "actions": [{
                "action": "set_light",
                "args": {"room": "office", "state": "on", "brightness": 80, "color_temp": "cool"},
            }],
        }),
        "ambiguous_scene",
    ),
    (
        "relax scene 만들어줘. 주방 불 10% 따뜻하게",
        call("create_scene", {
            "name": "relax",
            "actions": [{
                "action": "set_light",
                "args": {"room": "kitchen", "state": "on", "brightness": 10, "color_temp": "warm"},
            }],
        }),
        "ambiguous_scene",
    ),
    (
        "수면 모드 만들어줘. 침실 불 꺼줘",
        call("create_scene", {
            "name": "sleep",
            "actions": [{
                "action": "set_light",
                "args": {"room": "bedroom", "state": "off"},
            }],
        }),
        "ambiguous_scene",
    ),
]


def main() -> None:
    """Generate adversarial_cases.jsonl."""
    with DATASET_PATH.open("w", encoding="utf-8") as file:
        for index, (prompt, expected, category) in enumerate(ADVERSARIAL_CASES, start=1):
            row = {
                "id": f"adversarial-{index:03d}",
                "prompt": prompt,
                "expected": expected,
                "category": category,
            }
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(f"Generated {len(ADVERSARIAL_CASES)} adversarial cases to {DATASET_PATH}")

    # Print category distribution
    from collections import Counter
    counts = Counter(cat for _prompt, _expected, cat in ADVERSARIAL_CASES)
    print("\nCategory distribution:")
    for cat, count in sorted(counts.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
