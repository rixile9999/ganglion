from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).with_name("dataset.jsonl")
TARGET_COUNTS = {
    "set_light": 180,
    "schedule_light": 140,
    "get_light_state": 100,
    "list_devices": 40,
    "create_scene": 40,
}

ROOMS = [
    ("living", "거실", "living room"),
    ("bedroom", "침실", "bedroom"),
    ("kitchen", "주방", "kitchen"),
    ("hallway", "복도", "hallway"),
    ("office", "서재", "office"),
]
BRIGHTNESS_VALUES = [10, 20, 35, 50, 70, 80, 100]
COLOR_TEMPS = [
    ("warm", "따뜻하게", "warm"),
    ("neutral", "중립으로", "neutral"),
    ("cool", "차갑게", "cool"),
]


def call(action: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"calls": [{"action": action, "args": args}]}


def movie_scene(room: str) -> dict[str, Any]:
    return call(
        "create_scene",
        {
            "name": "movie",
            "actions": [
                {
                    "action": "set_light",
                    "args": {
                        "room": room,
                        "state": "on",
                        "brightness": 20,
                        "color_temp": "warm",
                    },
                }
            ],
        },
    )


SEED_CASES = [
    ("거실 불 70%로 켜줘", call("set_light", {"room": "living", "state": "on", "brightness": 70})),
    ("밤 10시 반에 침실 조명 꺼줘", call("schedule_light", {"room": "bedroom", "at": "22:30", "state": "off"})),
    ("현재 주방 조명 상태 확인해줘", call("get_light_state", {"room": "kitchen"})),
    ("조명 장치 목록 보여줘", call("list_devices", {})),
    ("서재 조명을 따뜻하게 켜줘", call("set_light", {"room": "office", "state": "on", "color_temp": "warm"})),
    ("복도 불 꺼줘", call("set_light", {"room": "hallway", "state": "off"})),
    ("오전 7시에 주방 불 80%로 켜지게 예약해줘", call("schedule_light", {"room": "kitchen", "at": "07:00", "state": "on", "brightness": 80})),
    ("영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘", movie_scene("living")),
    ("침실 불 밝기 35로 켜줘", call("set_light", {"room": "bedroom", "state": "on", "brightness": 35})),
    ("오후 6시에 복도 조명 켜줘", call("schedule_light", {"room": "hallway", "at": "18:00", "state": "on"})),
    ("office light off", call("set_light", {"room": "office", "state": "off"})),
    ("Check living room light state", call("get_light_state", {"room": "living"})),
]


def main() -> None:
    buckets: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    seen_prompts: set[str] = set()

    for prompt, expected in SEED_CASES:
        action = expected["calls"][0]["action"]
        add_case(buckets, seen_prompts, action, prompt, expected, force=True)

    generate_set_light(buckets, seen_prompts)
    generate_schedule_light(buckets, seen_prompts)
    generate_get_light_state(buckets, seen_prompts)
    generate_list_devices(buckets, seen_prompts)
    generate_create_scene(buckets, seen_prompts)

    cases: list[tuple[str, dict[str, Any]]] = []
    seed_prompts = {prompt for prompt, _expected in SEED_CASES}
    cases.extend(SEED_CASES)
    counts = Counter(expected["calls"][0]["action"] for _prompt, expected in cases)
    remaining = {
        action: [
            (prompt, expected)
            for prompt, expected in buckets[action]
            if prompt not in seed_prompts
        ]
        for action in TARGET_COUNTS
    }
    offsets = Counter()
    while len(cases) < sum(TARGET_COUNTS.values()):
        advanced = False
        for action, target in TARGET_COUNTS.items():
            if counts[action] >= target:
                continue
            offset = offsets[action]
            if offset >= len(remaining[action]):
                continue
            prompt, expected = remaining[action][offset]
            offsets[action] += 1
            cases.append((prompt, expected))
            counts[action] += 1
            advanced = True
        if not advanced:
            break

    if sum(counts.values()) != sum(TARGET_COUNTS.values()):
        raise RuntimeError(f"expected {sum(TARGET_COUNTS.values())} cases, got {sum(counts.values())}")
    for action, target in TARGET_COUNTS.items():
        if counts[action] != target:
            raise RuntimeError(f"expected {target} {action} cases, got {counts[action]}")

    with DATASET_PATH.open("w", encoding="utf-8") as file:
        for index, (prompt, expected) in enumerate(cases, start=1):
            row = {"id": f"iot-{index:03d}", "prompt": prompt, "expected": expected}
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def add_case(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
    action: str,
    prompt: str,
    expected: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    if not force and len(buckets[action]) >= TARGET_COUNTS[action]:
        return
    if prompt in seen_prompts:
        return
    seen_prompts.add(prompt)
    buckets[action].append((prompt, expected))


def generate_set_light(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
) -> None:
    for room, ko, en in ROOMS:
        add_case(buckets, seen_prompts, "set_light", f"{ko} 불 켜줘", call("set_light", {"room": room, "state": "on"}))
        add_case(buckets, seen_prompts, "set_light", f"{ko} 조명 켜줘", call("set_light", {"room": room, "state": "on"}))
        add_case(buckets, seen_prompts, "set_light", f"{ko} 불 꺼줘", call("set_light", {"room": room, "state": "off"}))
        add_case(buckets, seen_prompts, "set_light", f"turn {en} light on", call("set_light", {"room": room, "state": "on"}))
        add_case(buckets, seen_prompts, "set_light", f"turn {en} light off", call("set_light", {"room": room, "state": "off"}))
        for brightness in BRIGHTNESS_VALUES:
            add_case(
                buckets,
                seen_prompts,
                "set_light",
                f"{ko} 불 {brightness}%로 켜줘",
                call("set_light", {"room": room, "state": "on", "brightness": brightness}),
            )
            add_case(
                buckets,
                seen_prompts,
                "set_light",
                f"{ko} 조명 밝기 {brightness}로 켜줘",
                call("set_light", {"room": room, "state": "on", "brightness": brightness}),
            )
            add_case(
                buckets,
                seen_prompts,
                "set_light",
                f"set {en} light to {brightness}%",
                call("set_light", {"room": room, "state": "on", "brightness": brightness}),
            )
        for color_temp, ko_color, en_color in COLOR_TEMPS:
            add_case(
                buckets,
                seen_prompts,
                "set_light",
                f"{ko} 조명을 {ko_color} 켜줘",
                call("set_light", {"room": room, "state": "on", "color_temp": color_temp}),
            )
            add_case(
                buckets,
                seen_prompts,
                "set_light",
                f"make {en} light {en_color}",
                call("set_light", {"room": room, "state": "on", "color_temp": color_temp}),
            )
    index = 0
    while len(buckets["set_light"]) < TARGET_COUNTS["set_light"]:
        room, ko, _en = ROOMS[index % len(ROOMS)]
        brightness = BRIGHTNESS_VALUES[index % len(BRIGHTNESS_VALUES)]
        add_case(
            buckets,
            seen_prompts,
            "set_light",
            f"{ko} 불 {brightness}%로 다시 켜줘 #{index}",
            call("set_light", {"room": room, "state": "on", "brightness": brightness}),
        )
        add_case(
            buckets,
            seen_prompts,
            "set_light",
            f"{ko} 불 다시 꺼줘 #{index}",
            call("set_light", {"room": room, "state": "off"}),
        )
        index += 1


def generate_schedule_light(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
) -> None:
    for room, ko, en in ROOMS:
        for hour in range(1, 12):
            at = f"{hour:02d}:00"
            add_case(
                buckets,
                seen_prompts,
                "schedule_light",
                f"오전 {hour}시에 {ko} 불 켜지게 예약해줘",
                call("schedule_light", {"room": room, "at": at, "state": "on"}),
            )
            at_pm = f"{hour + 12:02d}:00"
            add_case(
                buckets,
                seen_prompts,
                "schedule_light",
                f"오후 {hour}시에 {ko} 조명 꺼줘",
                call("schedule_light", {"room": room, "at": at_pm, "state": "off"}),
            )
        for brightness in [20, 50, 80]:
            add_case(
                buckets,
                seen_prompts,
                "schedule_light",
                f"오전 7시에 {ko} 불 {brightness}%로 켜지게 예약해줘",
                call("schedule_light", {"room": room, "at": "07:00", "state": "on", "brightness": brightness}),
            )
        for time_value in ["06:30", "18:00", "22:30"]:
            add_case(
                buckets,
                seen_prompts,
                "schedule_light",
                f"schedule {en} light on at {time_value}",
                call("schedule_light", {"room": room, "at": time_value, "state": "on"}),
            )
            add_case(
                buckets,
                seen_prompts,
                "schedule_light",
                f"at {time_value} turn {en} light off",
                call("schedule_light", {"room": room, "at": time_value, "state": "off"}),
            )


def generate_get_light_state(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
) -> None:
    variants = [
        ("현재 {ko} 조명 상태 확인해줘", "ko"),
        ("{ko} 불 상태 알려줘", "ko"),
        ("{ko} 조명 켜져 있는지 확인해줘", "ko"),
        ("Check {en} light state", "en"),
        ("what is the {en} light state", "en"),
        ("get {en} light state", "en"),
    ]
    for room, ko, en in ROOMS:
        for template, _language in variants:
            add_case(
                buckets,
                seen_prompts,
                "get_light_state",
                template.format(ko=ko, en=en),
                call("get_light_state", {"room": room}),
            )
    index = 0
    while len(buckets["get_light_state"]) < TARGET_COUNTS["get_light_state"]:
        room, ko, en = ROOMS[index % len(ROOMS)]
        add_case(
            buckets,
            seen_prompts,
            "get_light_state",
            f"{ko} 조명 상태 다시 확인해줘 #{index}",
            call("get_light_state", {"room": room}),
        )
        add_case(
            buckets,
            seen_prompts,
            "get_light_state",
            f"check {en} light state again #{index}",
            call("get_light_state", {"room": room}),
        )
        index += 1


def generate_list_devices(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
) -> None:
    variants = [
        "조명 장치 목록 보여줘",
        "사용 가능한 조명 장치 목록 알려줘",
        "등록된 조명 디바이스 목록 보여줘",
        "집 안 조명 장치 목록 확인해줘",
        "list devices",
        "list light devices",
        "show available devices",
        "what light devices are available",
    ]
    index = 0
    while len(buckets["list_devices"]) < TARGET_COUNTS["list_devices"]:
        prompt = variants[index % len(variants)]
        if index >= len(variants):
            prompt = f"{prompt} #{index}"
        add_case(buckets, seen_prompts, "list_devices", prompt, call("list_devices", {}))
        index += 1


def generate_create_scene(
    buckets: dict[str, list[tuple[str, dict[str, Any]]]],
    seen_prompts: set[str],
) -> None:
    for room, ko, en in ROOMS:
        add_case(buckets, seen_prompts, "create_scene", f"영화 모드 scene을 만들어줘. {ko} 조명은 20% 따뜻하게 켜줘", movie_scene(room))
        add_case(buckets, seen_prompts, "create_scene", f"영화 볼 때 쓸 scene 만들어줘. {ko} 조명 20% 따뜻하게 켜줘", movie_scene(room))
        add_case(buckets, seen_prompts, "create_scene", f"create movie mode scene. set {en} light to 20% warm", movie_scene(room))
        add_case(buckets, seen_prompts, "create_scene", f"movie scene for {en} light, 20% warm", movie_scene(room))
    index = 0
    while len(buckets["create_scene"]) < TARGET_COUNTS["create_scene"]:
        room, ko, en = ROOMS[index % len(ROOMS)]
        add_case(
            buckets,
            seen_prompts,
            "create_scene",
            f"영화 모드 #{index} 만들어줘. {ko} 조명은 20% 따뜻하게 켜줘",
            movie_scene(room),
        )
        add_case(
            buckets,
            seen_prompts,
            "create_scene",
            f"create movie mode #{index}. set {en} light to 20% warm",
            movie_scene(room),
        )
        index += 1


if __name__ == "__main__":
    main()
