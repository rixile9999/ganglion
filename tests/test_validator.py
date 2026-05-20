import pytest

from ganglion.contract.emitter import emit_tool_calls
from ganglion.contract.parse import DSLValidationError, parse_json_dsl


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


# -----------------------------------------------------------------------------
# strip_unknown_args (post-correction layer 2)
# -----------------------------------------------------------------------------


def test_strip_unknown_args_list_devices_drops_spurious_id() -> None:
    """``조명 장치 목록 보여줘 #8`` causes the model to echo ``id="8"``
    into ``list_devices`` args. With ``strip_unknown_args=True`` the call
    succeeds with empty args."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "list_devices", "args": {"id": "8"}}]}
    )
    assert plan.calls[0].action == "list_devices"
    assert plan.calls[0].args == {}


def test_strip_unknown_args_get_light_state_drops_spurious_at() -> None:
    """``복도 조명 상태 다시 확인해줘 #28`` produces ``at="28:00"`` echoed
    into get_light_state args. Stripped to keep only ``room``."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [
            {"action": "get_light_state", "args": {"room": "hallway", "at": "28:00"}}
        ]}
    )
    assert plan.calls[0].args == {"room": "hallway"}


def test_strip_unknown_args_does_not_mask_missing_required() -> None:
    """Stripping must NOT manufacture a missing required arg. If room is
    absent and we strip a spurious unknown, the validator must still
    surface ``room is required``."""
    from ganglion.contract.builtins.iot_light import CATALOG

    with pytest.raises(DSLValidationError, match="room"):
        CATALOG.parse_json_dsl(
            {"calls": [{"action": "get_light_state", "args": {"at": "28:00"}}]}
        )


# -----------------------------------------------------------------------------
# prompt-aware Korean 12h → 24h time correction (post-correction layer 3)
# -----------------------------------------------------------------------------


def test_korean_time_correction_am_morning() -> None:
    """``오전 1시에 거실 불 켜지게 예약해줘`` — model emits at="08:00"
    (the most common training-time slot). Prompt-aware correction
    overrides at="01:00"."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "living", "at": "08:00", "state": "on",
        }}]},
        prompt="오전 1시에 거실 불 켜지게 예약해줘",
    )
    assert plan.calls[0].args["at"] == "01:00"


def test_korean_time_correction_pm_afternoon() -> None:
    """오후 1시 → 13:00 (not 23:00 as the model emits)."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "living", "at": "23:00", "state": "off",
        }}]},
        prompt="오후 1시에 거실 조명 꺼줘",
    )
    assert plan.calls[0].args["at"] == "13:00"


def test_korean_time_correction_noon_and_midnight_edges() -> None:
    """12 hour edges: 오전 12시 = 00:00 (midnight), 오후 12시 = 12:00 (noon)."""
    from ganglion.contract.builtins.iot_light import CATALOG

    midnight = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "kitchen", "at": "12:00", "state": "on",
        }}]},
        prompt="오전 12시에 주방 불 켜줘",
    )
    assert midnight.calls[0].args["at"] == "00:00"

    noon = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "kitchen", "at": "00:00", "state": "on",
        }}]},
        prompt="오후 12시에 주방 불 켜줘",
    )
    assert noon.calls[0].args["at"] == "12:00"


def test_korean_time_correction_with_minutes() -> None:
    """오후 3시 30분 → 15:30."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "office", "at": "21:00", "state": "off",
        }}]},
        prompt="오후 3시 30분에 사무실 조명 꺼줘",
    )
    assert plan.calls[0].args["at"] == "15:30"


def test_korean_time_correction_no_match_leaves_at_intact() -> None:
    """Without an 오전/오후 N시 expression the model's emitted at is
    trusted and not rewritten."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "living", "at": "22:30", "state": "off",
        }}]},
        prompt="밤 10시 반에 거실 조명 꺼줘",
    )
    assert plan.calls[0].args["at"] == "22:30"


def test_korean_time_correction_ambiguous_multiple_matches() -> None:
    """Two 오전/오후 expressions in one prompt → don't correct (we can't
    pick which one is the real schedule target)."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "living", "at": "10:00", "state": "on",
        }}]},
        prompt="오전 1시 또는 오후 2시에 거실 불 켜줘",
    )
    assert plan.calls[0].args["at"] == "10:00"


def test_korean_time_correction_skipped_without_prompt() -> None:
    """Backwards compatibility: when no prompt is supplied, prompt-aware
    rules don't fire and the model's at is preserved."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "schedule_light", "args": {
            "room": "living", "at": "08:00", "state": "on",
        }}]}
    )
    assert plan.calls[0].args["at"] == "08:00"


def test_korean_time_correction_does_not_apply_to_set_light() -> None:
    """Only schedule_light has prompt_correction wired. set_light without
    a schedule semantic should not have args.at materialized from the
    prompt — and set_light declares no ``at`` arg, so the strip-unknown
    path keeps it absent."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "living", "state": "on", "brightness": 50,
        }}]},
        prompt="오전 1시에 거실 불 50%로 켜줘",
    )
    assert "at" not in plan.calls[0].args


# -----------------------------------------------------------------------------
# Scene name correction (post-correction layer 4)
# -----------------------------------------------------------------------------


def test_scene_name_correction_recovers_hash_suffix_leak() -> None:
    """``영화 모드 #0`` causes the model to emit ``name="#0"``. The prompt
    contains a SCENE_ALIAS so the corrector recovers ``name="movie"``."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "create_scene", "args": {
            "name": "#0",
            "actions": [{"action": "set_light", "args": {
                "room": "living", "state": "on", "brightness": 20, "color_temp": "warm",
            }}],
        }}]},
        prompt="영화 모드 #0 만들어줘. 거실 조명은 20% 따뜻하게 켜줘",
    )
    assert plan.calls[0].args["name"] == "movie"


def test_scene_name_correction_canonical_name_preserved() -> None:
    """Already-canonical name short-circuits — no prompt scan, no rewrite."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "create_scene", "args": {
            "name": "relax",
            "actions": [{"action": "set_light", "args": {
                "room": "living", "state": "on",
            }}],
        }}]},
        prompt="movie 만들어줘 (typo'd intent)",
    )
    assert plan.calls[0].args["name"] == "relax"


# -----------------------------------------------------------------------------
# State ↔ color_temp swap (post-correction layer 5)
# -----------------------------------------------------------------------------


def test_state_color_temp_swap_neutral() -> None:
    """``중립으로 켜줘`` makes the model emit ``state="neutral"`` (a
    color-temp value in the wrong slot). Swap puts it back."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "bedroom", "state": "neutral",
        }}]},
        prompt="침실 조명을 중립으로 켜줘",
    )
    args = plan.calls[0].args
    assert args["state"] == "on"
    assert args["color_temp"] == "neutral"


def test_state_color_temp_swap_skipped_when_color_temp_present() -> None:
    """If color_temp is already populated, swap must NOT clobber it."""
    from ganglion.contract.builtins.iot_light import CATALOG

    # state="on" stays, color_temp="warm" stays.
    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "kitchen", "state": "on", "color_temp": "warm",
        }}]},
        prompt="주방 따뜻하게 켜줘",
    )
    args = plan.calls[0].args
    assert args["state"] == "on"
    assert args["color_temp"] == "warm"


# -----------------------------------------------------------------------------
# Color-temp fill from prompt (post-correction layer 6)
# -----------------------------------------------------------------------------


def test_color_temp_fill_from_prompt_set_light_warm() -> None:
    """``따뜻하게 켜줘`` with no color_temp in args → fill ``warm`` from prompt."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "living", "state": "on", "brightness": 20,
        }}]},
        prompt="거실 조명을 따뜻하게 켜줘",
    )
    args = plan.calls[0].args
    assert args["color_temp"] == "warm"


def test_color_temp_fill_propagates_into_create_scene_actions() -> None:
    """Nested set_light inside create_scene.actions also benefits — a
    create_scene rooted in a 따뜻하게 prompt rescues the missing
    color_temp on the inner set_light."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "create_scene", "args": {
            "name": "movie",
            "actions": [{"action": "set_light", "args": {
                "room": "living", "state": "on", "brightness": 20,
            }}],
        }}]},
        prompt="영화 모드 만들어줘. 거실 조명은 20% 따뜻하게 켜줘",
    )
    nested = plan.calls[0].args["actions"][0]
    assert nested["args"]["color_temp"] == "warm"


def test_color_temp_fill_skipped_on_ambiguous_prompt() -> None:
    """Multiple distinct color_temp aliases in one prompt → don't fill."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "living", "state": "on",
        }}]},
        prompt="따뜻하게 또는 차갑게 골라서 켜줘",
    )
    assert "color_temp" not in plan.calls[0].args


# -----------------------------------------------------------------------------
# Room override from prompt (post-correction layer 7)
# -----------------------------------------------------------------------------


def test_room_override_from_prompt_korean_alias() -> None:
    """The model sends ``room="office"`` for ``복도 조명 켜줘`` (alias
    miss). Prompt scan unambiguously points to ``hallway``."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "office", "state": "on",
        }}]},
        prompt="복도 조명 켜줘",
    )
    assert plan.calls[0].args["room"] == "hallway"


def test_room_override_skipped_on_multi_room_prompt() -> None:
    """Two distinct rooms in prompt → ambiguous → leave args.room alone."""
    from ganglion.contract.builtins.iot_light import CATALOG

    plan = CATALOG.parse_json_dsl(
        {"calls": [{"action": "set_light", "args": {
            "room": "living", "state": "on",
        }}]},
        prompt="거실하고 침실 조명 둘 다 켜줘",
    )
    assert plan.calls[0].args["room"] == "living"
