import json
from collections import Counter

import pytest

from examples.home_assistant.generate_dataset import DATASET_PATH, derive_rows
from ganglion.benchmarks.iot.dataset import DEFAULT_DATASET, default_dataset_for, load_dataset
from ganglion.contract.builtins import get_catalog
from ganglion.contract.builtins.home_assistant import (
    COLOR_TEMP_KELVIN,
    TOOL_NAME_PREFIX,
    translate_plan,
)
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.contract.types import ActionPlan, ToolCall

IOT = get_catalog("iot_light_5")
HA = get_catalog("home_assistant_4")


def _iot(payload: dict) -> ActionPlan:
    return IOT.parse_json_dsl(payload)


def test_tier_registered_with_four_assist_tools() -> None:
    assert [tool.name for tool in HA.tools] == [
        "HassTurnOn", "HassTurnOff", "HassLightSet", "GetLiveContext",
    ]
    assert set(TOOL_NAME_PREFIX) == {tool.name for tool in HA.tools}


def test_translate_set_light_variants() -> None:
    on = _iot({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]})
    assert translate_plan(on) == ActionPlan(
        calls=(ToolCall("HassTurnOn", {"area": "living", "domain": "light"}),)
    )

    off = _iot({"calls": [{"action": "set_light", "args": {"room": "복도", "state": "off"}}]})
    assert translate_plan(off) == ActionPlan(
        calls=(ToolCall("HassTurnOff", {"area": "hallway", "domain": "light"}),)
    )

    dim = _iot({"calls": [{"action": "set_light", "args": {
        "room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]})
    assert translate_plan(dim) == ActionPlan(
        calls=(ToolCall("HassLightSet", {
            "area": "office", "domain": "light", "brightness": 20,
            "temperature": COLOR_TEMP_KELVIN["warm"]}),)
    )


def test_translate_state_queries_collapse_to_live_context() -> None:
    state = _iot({"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]})
    devices = _iot({"calls": [{"action": "list_devices", "args": {}}]})
    expected = ActionPlan(calls=(ToolCall("GetLiveContext", {}),))
    assert translate_plan(state) == expected
    assert translate_plan(devices) == expected


def test_translate_unsupported_intents_return_none() -> None:
    schedule = _iot({"calls": [{"action": "schedule_light", "args": {
        "room": "bedroom", "at": "22:30", "state": "off"}}]})
    scene = _iot({"calls": [{"action": "create_scene", "args": {
        "name": "movie", "actions": [{"action": "set_light", "args": {
            "room": "living", "state": "on", "brightness": 20}}]}}]})
    assert translate_plan(schedule) is None
    assert translate_plan(scene) is None


def test_domain_defaulted_and_aliases_canonicalised() -> None:
    plan = HA.parse_json_dsl({"calls": [{"action": "HassTurnOn", "args": {"area": "거실"}}]})
    assert plan.calls[0].args == {"area": "living", "domain": "light"}

    explicit = HA.parse_json_dsl(
        {"calls": [{"action": "HassTurnOn", "args": {"area": "living room", "domain": "light"}}]}
    )
    assert explicit == plan


def test_untargeted_intent_rejected() -> None:
    for name in ("HassTurnOn", "HassTurnOff", "HassLightSet"):
        with pytest.raises(DSLValidationError):
            HA.parse_json_dsl({"calls": [{"action": name, "args": {}}]})
        with pytest.raises(DSLValidationError):
            HA.parse_json_dsl({"calls": [{"action": name, "args": {"domain": "light"}}]})


def test_light_set_bounds_and_percent() -> None:
    plan = HA.parse_json_dsl({"calls": [{"action": "HassLightSet", "args": {
        "area": "bedroom", "brightness": "35%", "temperature": 4000}}]})
    assert plan.calls[0].args == {
        "area": "bedroom", "domain": "light", "brightness": 35, "temperature": 4000}
    with pytest.raises(DSLValidationError):
        HA.parse_json_dsl({"calls": [{"action": "HassLightSet", "args": {
            "area": "bedroom", "temperature": 9000}}]})


def test_live_context_strips_echoed_args() -> None:
    plan = HA.parse_json_dsl({"calls": [{"action": "GetLiveContext", "args": {"id": "8"}}]})
    assert plan.calls[0].args == {}


def test_iot_light_actions_are_unknown_in_tier() -> None:
    with pytest.raises(DSLValidationError):
        HA.parse_json_dsl({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]})


def test_dsl_render_shorter_than_native() -> None:
    dsl = len(HA.render_json_dsl())
    native = len(json.dumps(HA.render_openai_tools()))
    assert dsl < native
    assert "HassLightSet" in HA.render_json_dsl()


def test_derived_dataset_matches_generator_and_source() -> None:
    rows, skipped = derive_rows()
    on_disk = [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines() if line]
    assert on_disk == rows, "run python examples/home_assistant/generate_dataset.py"

    source = load_dataset(DEFAULT_DATASET)
    assert len(rows) + sum(skipped.values()) == len(source)
    assert set(skipped) == {"schedule_light", "create_scene"}
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["source_id"] for row in rows}) == len(rows)

    by_action = Counter(row["expected"]["calls"][0]["action"] for row in rows)
    assert set(by_action) <= {tool.name for tool in HA.tools}
    assert by_action["GetLiveContext"] == 140  # get_light_state 100 + list_devices 40


def test_derived_dataset_round_trips_through_tier() -> None:
    cases = load_dataset(DATASET_PATH, catalog=HA)
    assert len(cases) == 320
    for case in cases:
        assert case.expected == HA.parse_json_dsl(case.expected.to_jsonable())


def test_derived_dataset_needs_its_own_catalog() -> None:
    with pytest.raises(DSLValidationError):
        load_dataset(DATASET_PATH)


def test_tier_default_dataset_lookup() -> None:
    assert default_dataset_for("home_assistant_4") == DATASET_PATH.relative_to(DATASET_PATH.parents[2])
    assert default_dataset_for("iot_light_5") == DEFAULT_DATASET
