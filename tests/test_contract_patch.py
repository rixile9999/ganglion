"""Tests for [[contract_patch_apply]]: ``apply_patch`` and ``strip_hooks``.

Covers the success predicate of ``docs/tasks/contract_patch_apply.md``:
every operation (incl. every ``PatchNotApplicableError`` branch), purity
(the input catalog's fingerprint never moves), ``strip_hooks`` removing
defaults so a plan relying on a default fails validation while
``custom_validator`` survives, and ``retire_rule`` moving the fingerprint.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from ganglion.contract import (
    ALL_HOOK_KINDS,
    PatchNotApplicableError,
    apply_patch,
    strip_hooks,
)
from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import (
    DSLValidationError,
    EnumArg,
    IntArg,
    StringArg,
    ToolSpec,
)


def _patch(
    operation: str,
    target_tool: str,
    payload: Mapping[str, Any],
    *,
    catalog_id: str | None = "iot_light_5",
    **extra: Any,
) -> dict[str, Any]:
    """Build a ``RulePatch.to_dict()``-shaped mapping."""
    out: dict[str, Any] = {
        "patch_id": f"rs-{catalog_id}-000000000000",
        "target_tool": target_tool,
        "operation": operation,
        "payload": dict(payload),
        "evidence": {"failure_count": 3, "support_share": 0.1, "example_trace_ids": [], "confidence": 0.9},
        "source_failure_type": "no_failure",
        "created_at": "2026-09-16T00:00:00Z",
    }
    if catalog_id is not None:
        out["catalog_id"] = catalog_id
    out.update(extra)
    return out


def _small_catalog() -> Catalog:
    """A catalog with NO hooks so each operation's effect is observable."""
    return Catalog(
        name="small",
        tools=(
            ToolSpec(
                name="set_fan",
                description="Set a fan.",
                args=(
                    ("room", EnumArg(values=("living", "bedroom"), aliases={"거실": "living"})),
                    ("speed", IntArg(min_value=0, max_value=100)),
                    ("mode", StringArg(aliases={"자동": "auto"}, required=False)),
                ),
            ),
            ToolSpec(name="list_fans", description="List fans."),
        ),
    )


@pytest.fixture
def iot() -> Catalog:
    return get_catalog("iot_light_5")


@pytest.fixture(autouse=True)
def _builtins_untouched():
    """Purity guard: no test may move a builtin's fingerprint."""
    before = {tier: get_catalog(tier).fingerprint() for tier in ("iot_light_5", "home_iot_20", "home_assistant_4")}
    yield
    after = {tier: get_catalog(tier).fingerprint() for tier in before}
    assert after == before


def _set_light(args: dict[str, Any]) -> dict[str, Any]:
    return {"calls": [{"action": "set_light", "args": args}]}


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------


def test_unknown_target_tool_raises(iot: Catalog) -> None:
    with pytest.raises(PatchNotApplicableError, match="unknown target_tool"):
        apply_patch(iot, _patch("enable_strip_unknown_args", "no_such_tool", {"strip_unknown_args": True}))
    with pytest.raises(PatchNotApplicableError, match="target_tool"):
        apply_patch(iot, {"operation": "enable_strip_unknown_args", "payload": {}})


def test_catalog_id_mismatch_raises_but_absent_is_ok(iot: Catalog) -> None:
    payload = {"hook_kind": "prompt_correction", "arg": None}
    with pytest.raises(PatchNotApplicableError, match="not 'iot_light_5'"):
        apply_patch(iot, _patch("retire_rule", "set_light", payload, catalog_id="home_iot_20"))
    # absent / empty catalog_id → no check
    assert apply_patch(iot, _patch("retire_rule", "set_light", payload, catalog_id=None)).fingerprint() != iot.fingerprint()
    assert apply_patch(iot, _patch("retire_rule", "set_light", payload, catalog_id="")).fingerprint() != iot.fingerprint()


@pytest.mark.parametrize(
    "operation, payload",
    [
        ("add_prompt_correction", {"system_nudge": "When the user asks, call set_light.", "trigger_tool": "set_light"}),
        ("ESCALATE", {"arg": "brightness", "blocked_reason": "no consistent transform"}),
        ("teleport", {"arg": "room"}),
        (None, {}),
    ],
)
def test_never_applicable_and_unknown_operations_raise(iot: Catalog, operation, payload) -> None:
    with pytest.raises(PatchNotApplicableError):
        apply_patch(iot, _patch(operation, "set_light", payload))


def test_non_mapping_payload_raises(iot: Catalog) -> None:
    patch = _patch("add_alias", "set_light", {})
    patch["payload"] = ["not", "a", "mapping"]
    with pytest.raises(PatchNotApplicableError, match="payload"):
        apply_patch(iot, patch)


# ---------------------------------------------------------------------------
# add_alias
# ---------------------------------------------------------------------------


def test_add_alias_enum_then_korean_alias_parses(iot: Catalog) -> None:
    patch = _patch("add_alias", "set_light", {"arg": "room", "aliases": {"안방": "bedroom"}, "kind": "enum"})
    patched = apply_patch(iot, patch)

    plan = patched.parse_json_dsl(_set_light({"room": "안방", "state": "on"}))
    assert plan.calls[0].args["room"] == "bedroom"
    with pytest.raises(DSLValidationError, match="unsupported room"):
        iot.parse_json_dsl(_set_light({"room": "안방", "state": "on"}))
    assert patched.fingerprint() != iot.fingerprint()
    # existing aliases are preserved
    assert patched.parse_json_dsl(_set_light({"room": "거실", "state": "on"})).calls[0].args["room"] == "living"
    # untouched tools keep identity
    assert patched.get_tool("list_devices") is iot.get_tool("list_devices")


def test_add_alias_string_arg() -> None:
    cat = _small_catalog()
    patched = apply_patch(
        cat,
        _patch("add_alias", "set_fan", {"arg": "mode", "aliases": {"수동": "manual"}, "kind": "string"}, catalog_id="small"),
    )
    plan = patched.parse_json_dsl({"calls": [{"action": "set_fan", "args": {"room": "living", "speed": 3, "mode": "수동"}}]})
    assert plan.calls[0].args["mode"] == "manual"
    assert cat.parse_json_dsl({"calls": [{"action": "set_fan", "args": {"room": "living", "speed": 3, "mode": "수동"}}]}).calls[0].args["mode"] == "수동"


def test_add_alias_keys_are_normalised_like_the_validator_lookup(iot: Catalog) -> None:
    patched = apply_patch(iot, _patch("add_alias", "set_light", {"arg": "room", "aliases": {"  Master Bedroom ": "bedroom"}, "kind": "enum"}))
    assert patched.parse_json_dsl(_set_light({"room": "master bedroom", "state": "on"})).calls[0].args["room"] == "bedroom"


def test_add_alias_identical_mapping_is_a_no_op(iot: Catalog) -> None:
    patched = apply_patch(iot, _patch("add_alias", "set_light", {"arg": "room", "aliases": {"거실": "living"}, "kind": "enum"}))
    assert patched.fingerprint() == iot.fingerprint()
    assert patched == iot


@pytest.mark.parametrize(
    "payload, match",
    [
        ({"arg": "room", "aliases": {"거실": "bedroom"}, "kind": "enum"}, "already maps"),
        ({"arg": "room", "aliases": {"안방": "attic"}, "kind": "enum"}, "not one of"),
        ({"arg": "room", "aliases": {"안방": "bedroom"}, "kind": "string"}, "does not match"),
        ({"arg": "brightness", "aliases": {"밝게": "80"}, "kind": "enum"}, "enum/string"),
        ({"arg": "nope", "aliases": {"x": "y"}, "kind": "enum"}, "no arg"),
        ({"arg": "room", "aliases": {}, "kind": "enum"}, "non-empty"),
        ({"arg": "room", "aliases": "안방", "kind": "enum"}, "non-empty"),
        ({"arg": "room", "aliases": {"안방": 7}, "kind": "enum"}, "str"),
        ({"arg": "room", "aliases": {"  ": "bedroom"}, "kind": "enum"}, "empty alias key"),
        ({"aliases": {"안방": "bedroom"}, "kind": "enum"}, "'arg'"),
    ],
)
def test_add_alias_rejections(iot: Catalog, payload, match) -> None:
    with pytest.raises(PatchNotApplicableError, match=match):
        apply_patch(iot, _patch("add_alias", "set_light", payload))


# ---------------------------------------------------------------------------
# set_default
# ---------------------------------------------------------------------------


def test_set_default_with_requires_args_predicate(iot: Catalog) -> None:
    # schedule_light has no default rules; add "state → off when brightness present".
    patch = _patch("set_default", "schedule_light", {"arg": "state", "default": "off", "predicate_hint": {"requires_args": ["brightness"]}})
    patched = apply_patch(iot, patch)
    sched = lambda args: {"calls": [{"action": "schedule_light", "args": args}]}  # noqa: E731

    plan = patched.parse_json_dsl(sched({"room": "living", "at": "22:30", "brightness": 40}))
    assert plan.calls[0].args["state"] == "off"
    with pytest.raises(DSLValidationError, match="state is required"):
        iot.parse_json_dsl(sched({"room": "living", "at": "22:30", "brightness": 40}))
    # predicate gate: no brightness → the default does not fire
    with pytest.raises(DSLValidationError, match="state is required"):
        patched.parse_json_dsl(sched({"room": "living", "at": "22:30"}))
    # an explicit state is never overridden
    assert patched.parse_json_dsl(sched({"room": "living", "at": "22:30", "brightness": 40, "state": "on"})).calls[0].args["state"] == "on"
    assert patched.fingerprint() != iot.fingerprint()
    assert patched.describe()["tools"][3]["defaults_when_missing"] == [{"arg": "state", "value": "off", "predicate_present": True}]


def test_set_default_empty_requires_args_is_always_true(iot: Catalog) -> None:
    for payload in (
        {"arg": "state", "default": "on", "predicate_hint": {"requires_args": []}},
        {"arg": "state", "default": "on"},
    ):
        patched = apply_patch(iot, _patch("set_default", "schedule_light", payload))
        plan = patched.parse_json_dsl({"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00"}}]})
        assert plan.calls[0].args["state"] == "on"


def test_set_default_equal_patches_yield_equal_behaviour(iot: Catalog) -> None:
    payload = {"arg": "state", "default": "on", "predicate_hint": {"requires_args": ["brightness"]}}
    a = apply_patch(iot, _patch("set_default", "schedule_light", payload))
    b = apply_patch(iot, _patch("set_default", "schedule_light", payload))
    assert a.fingerprint() == b.fingerprint()
    raw = {"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00", "brightness": 10}}]}
    assert a.parse_json_dsl(raw) == b.parse_json_dsl(raw)


@pytest.mark.parametrize(
    "tool, payload, match",
    [
        ("set_light", {"arg": "state", "default": "on", "predicate_hint": {"requires_args": ["brightness"]}}, "already has"),
        ("schedule_light", {"arg": "colour", "default": "warm"}, "no arg"),
        ("schedule_light", {"arg": "state", "predicate_hint": {"requires_args": []}}, "no 'default'"),
        ("list_devices", {"arg": "id", "default": "1"}, "no arg"),
        ("schedule_light", {"arg": "state", "default": "on", "predicate_hint": "brightness"}, "predicate_hint"),
        ("schedule_light", {"arg": "state", "default": "on", "predicate_hint": {"requires_args": "brightness"}}, "requires_args"),
        ("schedule_light", {"default": "on"}, "'arg'"),
    ],
)
def test_set_default_rejections(iot: Catalog, tool, payload, match) -> None:
    with pytest.raises(PatchNotApplicableError, match=match):
        apply_patch(iot, _patch("set_default", tool, payload))


# ---------------------------------------------------------------------------
# enable_strip_unknown_args
# ---------------------------------------------------------------------------


def test_enable_strip_unknown_args() -> None:
    cat = _small_catalog()
    raw = {"calls": [{"action": "list_fans", "args": {"id": "8"}}]}
    with pytest.raises(DSLValidationError, match="does not accept args"):
        cat.parse_json_dsl(raw)
    patched = apply_patch(cat, _patch("enable_strip_unknown_args", "list_fans", {"strip_unknown_args": True}, catalog_id="small"))
    assert patched.parse_json_dsl(raw).calls[0].args == {}
    assert patched.fingerprint() != cat.fingerprint()
    # a tool with declared args: unknown key dropped, declared ones kept
    patched2 = apply_patch(cat, _patch("enable_strip_unknown_args", "set_fan", {"strip_unknown_args": True}, catalog_id="small"))
    plan = patched2.parse_json_dsl({"calls": [{"action": "set_fan", "args": {"room": "living", "speed": 5, "id": "8"}}]})
    assert plan.calls[0].args == {"room": "living", "speed": 5}


def test_enable_strip_unknown_args_no_op_and_contradiction(iot: Catalog) -> None:
    patched = apply_patch(iot, _patch("enable_strip_unknown_args", "list_devices", {"strip_unknown_args": True}))
    assert patched.fingerprint() == iot.fingerprint()
    with pytest.raises(PatchNotApplicableError, match="false"):
        apply_patch(iot, _patch("enable_strip_unknown_args", "list_devices", {"strip_unknown_args": False}))


# ---------------------------------------------------------------------------
# extend_argspec
# ---------------------------------------------------------------------------


def test_extend_argspec_percent_on_int_arg_sets_allow_percent() -> None:
    cat = _small_catalog()
    raw = {"calls": [{"action": "set_fan", "args": {"room": "living", "speed": "50%"}}]}
    with pytest.raises(DSLValidationError, match="speed must be an integer"):
        cat.parse_json_dsl(raw)
    patch = _patch("extend_argspec", "set_fan", {"arg": "speed", "spec_hint": "IntArg(allow_percent=True)", "transform": "percent"}, catalog_id="small")
    patched = apply_patch(cat, patch)
    assert patched.parse_json_dsl(raw).calls[0].args["speed"] == 50
    assert patched.get_tool("set_fan").get_arg("speed").allow_percent is True
    assert patched.fingerprint() != cat.fingerprint()
    # idempotent when already allowed (iot_light_5 brightness)
    ha = get_catalog("home_assistant_4")
    again = apply_patch(ha, _patch("extend_argspec", "HassLightSet", {"arg": "brightness", "spec_hint": "IntArg(allow_percent=True)", "transform": "percent"}, catalog_id="home_assistant_4"))
    assert again.fingerprint() == ha.fingerprint()


@pytest.mark.parametrize(
    "payload, match",
    [
        ({"arg": "id", "observed_types": {"str": 12}, "spec_hint": "RawArg"}, "only transform='percent'"),
        ({"arg": "speed", "spec_hint": "IntArg", "transform": "int_from_string"}, "only transform='percent'"),
        ({"arg": "speed", "spec_hint": "IntArg", "transform": "strip_unit"}, "only transform='percent'"),
        ({"arg": "speed", "spec_hint": "RawArg", "transform": None}, "only transform='percent'"),
        ({"arg": "room", "spec_hint": "IntArg(allow_percent=True)", "transform": "percent"}, "not an IntArg"),
        ({"arg": "nope", "spec_hint": "IntArg(allow_percent=True)", "transform": "percent"}, "not an IntArg"),
        ({"spec_hint": "IntArg(allow_percent=True)", "transform": "percent"}, "'arg'"),
    ],
)
def test_extend_argspec_rejections(payload, match) -> None:
    with pytest.raises(PatchNotApplicableError, match=match):
        apply_patch(_small_catalog(), _patch("extend_argspec", "set_fan", payload, catalog_id="small"))


# ---------------------------------------------------------------------------
# retire_rule
# ---------------------------------------------------------------------------


def test_retire_rule_defaults_when_missing_by_arg(iot: Catalog) -> None:
    patched = apply_patch(iot, _patch("retire_rule", "set_light", {"hook_kind": "defaults_when_missing", "arg": "state"}))
    assert patched.fingerprint() != iot.fingerprint()
    raw = _set_light({"room": "living", "brightness": 70})
    assert iot.parse_json_dsl(raw).calls[0].args["state"] == "on"
    with pytest.raises(DSLValidationError, match="state is required"):
        patched.parse_json_dsl(raw)
    assert patched.get_tool("set_light").defaults_when_missing == ()
    # other hooks on the tool survive
    assert patched.get_tool("set_light").strip_unknown_args is True
    assert patched.get_tool("set_light").prompt_correction is not None


def test_retire_rule_defaults_when_missing_null_arg_removes_all() -> None:
    ha = get_catalog("home_assistant_4")
    patched = apply_patch(ha, _patch("retire_rule", "HassTurnOn", {"hook_kind": "defaults_when_missing", "arg": None}, catalog_id="home_assistant_4"))
    assert patched.get_tool("HassTurnOn").defaults_when_missing == ()
    assert patched.get_tool("HassTurnOff").defaults_when_missing == ha.get_tool("HassTurnOff").defaults_when_missing
    raw = {"calls": [{"action": "HassTurnOn", "args": {"area": "living"}}]}
    assert ha.parse_json_dsl(raw).calls[0].args.get("domain") == "light"
    assert "domain" not in patched.parse_json_dsl(raw).calls[0].args


def test_retire_rule_strip_unknown_args_and_prompt_correction(iot: Catalog) -> None:
    stripped = apply_patch(iot, _patch("retire_rule", "list_devices", {"hook_kind": "strip_unknown_args", "arg": None}))
    assert stripped.get_tool("list_devices").strip_unknown_args is False
    with pytest.raises(DSLValidationError, match="does not accept args"):
        stripped.parse_json_dsl({"calls": [{"action": "list_devices", "args": {"id": "8"}}]})
    assert stripped.fingerprint() != iot.fingerprint()

    no_prompt = apply_patch(iot, _patch("retire_rule", "schedule_light", {"hook_kind": "prompt_correction", "arg": None}))
    assert no_prompt.get_tool("schedule_light").prompt_correction is None
    raw = {"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}
    prompt = "오전 1시에 거실 불 켜줘"
    assert iot.parse_json_dsl(raw, prompt=prompt).calls[0].args["at"] == "01:00"
    assert no_prompt.parse_json_dsl(raw, prompt=prompt).calls[0].args["at"] == "08:00"
    assert no_prompt.fingerprint() != iot.fingerprint()


@pytest.mark.parametrize(
    "tool, payload, match",
    [
        ("schedule_light", {"hook_kind": "defaults_when_missing", "arg": "state"}, "no defaults_when_missing rule for 'state'"),
        ("set_light", {"hook_kind": "defaults_when_missing", "arg": "room"}, "no defaults_when_missing rule for 'room'"),
        ("schedule_light", {"hook_kind": "defaults_when_missing", "arg": None}, "no defaults_when_missing rule"),
        ("list_devices", {"hook_kind": "prompt_correction", "arg": None}, "no prompt_correction"),
        ("set_light", {"hook_kind": "custom_validator", "arg": None}, "unknown hook_kind"),
        ("set_light", {"arg": "state"}, "unknown hook_kind"),
    ],
)
def test_retire_rule_rejections(iot: Catalog, tool, payload, match) -> None:
    with pytest.raises(PatchNotApplicableError, match=match):
        apply_patch(iot, _patch("retire_rule", tool, payload))


def test_retire_rule_strip_unknown_args_already_false_raises() -> None:
    with pytest.raises(PatchNotApplicableError, match="already False"):
        apply_patch(_small_catalog(), _patch("retire_rule", "list_fans", {"hook_kind": "strip_unknown_args", "arg": None}, catalog_id="small"))


# ---------------------------------------------------------------------------
# strip_hooks
# ---------------------------------------------------------------------------


def test_strip_hooks_all_kinds_removes_defaults_so_plan_relying_on_default_fails(iot: Catalog) -> None:
    f0 = strip_hooks(iot, ALL_HOOK_KINDS)
    raw = _set_light({"room": "living", "brightness": 70})
    assert iot.parse_json_dsl(raw).calls[0].args["state"] == "on"
    with pytest.raises(DSLValidationError, match="state is required"):
        f0.parse_json_dsl(raw)
    for tool in f0.tools:
        assert tool.defaults_when_missing == ()
        assert tool.strip_unknown_args is False
        assert tool.prompt_correction is None
    assert f0.default_strip_unknown_args is False
    assert f0.fingerprint() != iot.fingerprint()
    assert f0.name == iot.name and f0.examples == iot.examples and f0.extra_rules == iot.extra_rules


def test_strip_hooks_keeps_custom_validator(iot: Catalog) -> None:
    f0 = strip_hooks(iot, ALL_HOOK_KINDS)
    assert f0.get_tool("create_scene").custom_validator is iot.get_tool("create_scene").custom_validator
    scene = {
        "calls": [{
            "action": "create_scene",
            "args": {"name": "영화 모드", "actions": [
                {"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}},
            ]},
        }],
    }
    # the validator still normalises the scene name and validates nested calls
    plan = f0.parse_json_dsl(scene)
    assert plan.calls[0].args["name"] == "movie"
    with pytest.raises(DSLValidationError, match="unsupported scene name"):
        f0.parse_json_dsl({"calls": [{"action": "create_scene", "args": {"name": "party", "actions": scene["calls"][0]["args"]["actions"]}}]})
    # nested set_light relying on the (now stripped) default fails through the validator
    with pytest.raises(DSLValidationError, match="state is required"):
        f0.parse_json_dsl({"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [
            {"action": "set_light", "args": {"room": "living", "brightness": 20}},
        ]}}]})

    ha = get_catalog("home_assistant_4")
    ha0 = strip_hooks(ha, ALL_HOOK_KINDS)
    assert ha0.get_tool("HassTurnOn").custom_validator is ha.get_tool("HassTurnOn").custom_validator
    with pytest.raises(DSLValidationError, match="requires at least one of"):
        ha0.parse_json_dsl({"calls": [{"action": "HassTurnOn", "args": {}}]})


def test_strip_hooks_single_kind_leaves_others(iot: Catalog) -> None:
    only_defaults = strip_hooks(iot, ("defaults_when_missing",))
    sl = only_defaults.get_tool("set_light")
    assert sl.defaults_when_missing == () and sl.strip_unknown_args is True and sl.prompt_correction is not None

    only_strip = strip_hooks(iot, ["strip_unknown_args"])
    assert only_strip.get_tool("set_light").defaults_when_missing == iot.get_tool("set_light").defaults_when_missing
    assert all(not t.strip_unknown_args for t in only_strip.tools)
    with pytest.raises(DSLValidationError, match="unknown arg 'id'"):
        only_strip.parse_json_dsl(_set_light({"room": "living", "state": "on", "id": "8"}))

    only_prompt = strip_hooks(iot, "prompt_correction")  # a bare str is one kind
    assert all(t.prompt_correction is None for t in only_prompt.tools)
    assert only_prompt.get_tool("set_light").strip_unknown_args is True

    assert strip_hooks(iot, ()).fingerprint() == iot.fingerprint()


def test_strip_hooks_clears_catalog_level_default_strip() -> None:
    home = get_catalog("home_iot_20")
    assert home.default_strip_unknown_args is True
    assert strip_hooks(home, ("strip_unknown_args",)).default_strip_unknown_args is False
    assert strip_hooks(home, ("defaults_when_missing",)).default_strip_unknown_args is True


def test_strip_hooks_unknown_kind_raises_value_error(iot: Catalog) -> None:
    with pytest.raises(ValueError, match="custom_validator"):
        strip_hooks(iot, ("custom_validator",))
    with pytest.raises(ValueError):
        strip_hooks(iot, ALL_HOOK_KINDS + ("aliases",))


def test_all_hook_kinds_constant() -> None:
    assert ALL_HOOK_KINDS == ("defaults_when_missing", "strip_unknown_args", "prompt_correction")


# ---------------------------------------------------------------------------
# integration with the real RulePatch envelope (analyzer is owned elsewhere;
# skip cleanly if its import is mid-edit)
# ---------------------------------------------------------------------------


def test_real_rulepatch_to_dict_round_trips_into_apply_patch(iot: Catalog) -> None:
    try:
        from ganglion.analyzer.rules import RulePatch
        from ganglion.analyzer.taxonomy import FailureType
    except Exception as exc:  # pragma: no cover - other agent's file mid-edit
        pytest.skip(f"analyzer.rules not importable right now: {exc}")
    patch = RulePatch(
        patch_id="rs-iot_light_5-abcdefabcdef",
        catalog_id=iot.name,
        target_tool="set_light",
        operation="add_alias",
        payload={"arg": "room", "aliases": {"안방": "bedroom"}, "kind": "enum"},
        evidence={"failure_count": 3, "support_share": 0.2, "example_trace_ids": [], "confidence": 0.8},
        source_failure_type=FailureType.VALUE_OUT_OF_ENUM,
        created_at="2026-09-16T00:00:00Z",
    )
    patched = apply_patch(iot, patch.to_dict())
    assert patched.parse_json_dsl(_set_light({"room": "안방", "state": "on"})).calls[0].args["room"] == "bedroom"
