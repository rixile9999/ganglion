import pytest

from rlm_poc.dsl.emitter import emit_tool_calls
from rlm_poc.dsl.validator import DSLValidationError, parse_json_dsl


def test_parse_and_normalize_korean_room_alias() -> None:
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "set_light",
                    "args": {"room": "거실", "state": "on", "brightness": "70%"},
                }
            ]
        }
    )

    assert plan.to_jsonable() == {
        "calls": [
            {
                "action": "set_light",
                "args": {"room": "living", "state": "on", "brightness": 70},
            }
        ]
    }


def test_rejects_invalid_brightness() -> None:
    with pytest.raises(DSLValidationError, match="brightness"):
        parse_json_dsl(
            {
                "calls": [
                    {
                        "action": "set_light",
                        "args": {"room": "living", "state": "on", "brightness": 120},
                    }
                ]
            }
        )


def test_emits_tool_calls() -> None:
    calls = emit_tool_calls(
        {"calls": [{"action": "get_light_state", "args": {"room": "주방"}}]}
    )

    assert calls == [
        {"name": "get_light_state", "arguments": {"room": "kitchen"}}
    ]


def test_normalizes_scene_name_alias() -> None:
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "create_scene",
                    "args": {
                        "name": "영화 모드",
                        "actions": [
                            {
                                "action": "set_light",
                                "args": {
                                    "room": "living",
                                    "state": "on",
                                    "brightness": 20,
                                },
                            }
                        ],
                    },
                }
            ]
        }
    )

    assert plan.calls[0].args["name"] == "movie"
