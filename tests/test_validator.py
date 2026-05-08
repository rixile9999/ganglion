import pytest

from ganglion.dsl.emitter import emit_tool_calls
from ganglion.dsl.validator import DSLValidationError, parse_json_dsl


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


# --- Post-correction defaults_when_missing rules ---

def test_default_state_filled_when_brightness_present() -> None:
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "set_light",
                    "args": {"room": "living", "brightness": 70},
                }
            ]
        }
    )
    assert plan.calls[0].args["state"] == "on"
    assert plan.calls[0].args["brightness"] == 70


def test_default_state_filled_when_color_temp_present() -> None:
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "set_light",
                    "args": {"room": "study", "color_temp": "warm"},
                }
            ]
        }
    )
    assert plan.calls[0].args["state"] == "on"


def test_default_does_not_override_explicit_state() -> None:
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "set_light",
                    "args": {"room": "living", "state": "off", "brightness": 50},
                }
            ]
        }
    )
    assert plan.calls[0].args["state"] == "off"


def test_default_not_filled_without_brightness_or_color_temp() -> None:
    """If brightness AND color_temp both absent, the call is genuinely
    underspecified — let validation fail rather than guess."""
    with pytest.raises(DSLValidationError):
        parse_json_dsl(
            {
                "calls": [
                    {
                        "action": "set_light",
                        "args": {"room": "living"},
                    }
                ]
            }
        )


def test_default_applies_to_nested_create_scene_actions() -> None:
    """The 30/500 failure pattern from 0.6B+SFT: nested set_light without
    state inside create_scene.actions. Must rescue via the same rule."""
    plan = parse_json_dsl(
        {
            "calls": [
                {
                    "action": "create_scene",
                    "args": {
                        "name": "movie",
                        "actions": [
                            {
                                "action": "set_light",
                                "args": {
                                    "room": "living",
                                    "brightness": 20,
                                    "color_temp": "warm",
                                },
                            }
                        ],
                    },
                }
            ]
        }
    )
    nested = plan.calls[0].args["actions"][0]
    assert nested["args"]["state"] == "on"
