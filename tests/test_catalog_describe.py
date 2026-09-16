"""Tests for [[contract_describe]]: ``describe()`` and ``catalog_fingerprint()``.

Covers the success predicate of ``docs/tasks/contract_describe.md``:
stability, sensitivity (``allow_empty_calls`` toggle, alias add), every
builtin tier describing + dumping with ``sort_keys``, the iot_light_5
``set_light`` default entry, ``RawArg`` entries, and the no-circular-import
guard.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace

import pytest

from ganglion.contract import catalog_fingerprint, describe
from ganglion.contract.builtins import TIERS, get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import (
    BoolArg,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)

_FINGERPRINT_RE = re.compile(r"^cf-[0-9a-f]{12}$")


def _small_catalog(**overrides) -> Catalog:
    """A fresh, fully-featured catalog built from scratch on every call so
    equality-of-construction (not identity) is what the tests exercise."""
    room = EnumArg(
        values=("living", "bedroom"),
        aliases={"거실": "living", "침실": "bedroom", "living room": "living"},
        description="Room.",
    )
    state = EnumArg(values=("on", "off"), bool_true="on", bool_false="off")
    tools = (
        ToolSpec(
            name="set_light",
            description="Set a light.",
            args=(
                ("room", room),
                ("state", state),
                ("brightness", IntArg(min_value=0, max_value=100, required=False, allow_percent=True)),
                ("gamma", NumberArg(min_value=0.5, max_value=2.5, required=False)),
                ("label", StringArg(aliases={"거실등": "living-lamp"}, pattern=r"[a-z-]+", required=False)),
                ("force", BoolArg(required=False, description="Force.")),
                ("at", TimeArg(required=False)),
                (
                    "extra",
                    RawArg(
                        json_schema={"type": "object", "properties": {"z": {"type": "integer"}, "a": {"type": "string"}}},
                        dsl_description="object",
                        required=False,
                    ),
                ),
            ),
            defaults_when_missing=(("state", "on", lambda args: "brightness" in args),),
            strip_unknown_args=True,
            prompt_correction=lambda args, prompt: args,
        ),
        ToolSpec(name="list_devices", description="List.", dsl_args_override="{}"),
    )
    fields = dict(
        name="small_2",
        tools=tools,
        examples=(("불 켜줘", '{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}'),),
        extra_rules=("Be terse.",),
        allow_empty_calls=False,
        default_strip_unknown_args=False,
    )
    fields.update(overrides)
    return Catalog(**fields)


# ---------------------------------------------------------------------------
# stability
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_across_calls_and_equal_constructions() -> None:
    a = _small_catalog()
    b = _small_catalog()
    assert a.fingerprint() == a.fingerprint()
    assert a.fingerprint() == b.fingerprint()
    assert catalog_fingerprint(a) == a.fingerprint()
    assert _FINGERPRINT_RE.match(a.fingerprint())


def test_fingerprint_is_stable_in_a_fresh_interpreter() -> None:
    """Hashing must not depend on process state (dict order, PYTHONHASHSEED)."""
    code = (
        "from ganglion.contract.builtins import get_catalog;"
        "print(get_catalog('iot_light_5').fingerprint())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == get_catalog("iot_light_5").fingerprint()


def test_describe_matches_catalog_method_and_is_pure_json() -> None:
    catalog = _small_catalog()
    d = describe(catalog)
    assert catalog.describe() == d
    dumped = json.dumps(d, sort_keys=True, ensure_ascii=False)
    assert json.loads(dumped) == d  # no tuples / non-JSON leaves survive
    # Calling twice yields equal dicts (no shared mutable state leaking).
    assert describe(catalog) == d


# ---------------------------------------------------------------------------
# sensitivity
# ---------------------------------------------------------------------------


def test_toggling_allow_empty_calls_changes_fingerprint() -> None:
    catalog = _small_catalog()
    toggled = replace(catalog, allow_empty_calls=not catalog.allow_empty_calls)
    assert toggled.fingerprint() != catalog.fingerprint()


def test_toggling_default_strip_unknown_args_changes_fingerprint() -> None:
    catalog = _small_catalog()
    toggled = replace(catalog, default_strip_unknown_args=True)
    assert toggled.fingerprint() != catalog.fingerprint()


def test_adding_one_alias_changes_fingerprint() -> None:
    catalog = _small_catalog()
    tool = catalog.get_tool("set_light")
    assert tool is not None
    room = tool.get_arg("room")
    assert isinstance(room, EnumArg)
    new_room = replace(room, aliases={**room.aliases, "안방": "bedroom"})
    new_tool = replace(
        tool, args=tuple((n, new_room if n == "room" else s) for n, s in tool.args),
    )
    patched = replace(
        catalog, tools=tuple(new_tool if t.name == "set_light" else t for t in catalog.tools),
    )
    assert patched.fingerprint() != catalog.fingerprint()
    assert describe(patched)["tools"][0]["args"][0]["aliases"]["안방"] == "bedroom"


def test_examples_and_extra_rules_move_fingerprint() -> None:
    base = _small_catalog()
    assert replace(base, extra_rules=("Different.",)).fingerprint() != base.fingerprint()
    assert replace(base, examples=()).fingerprint() != base.fingerprint()
    assert replace(base, name="other").fingerprint() != base.fingerprint()


def test_hook_presence_moves_fingerprint_but_callable_body_does_not() -> None:
    """Documented determinism rule: callables reduce to presence booleans."""
    base = _small_catalog()
    tool = base.get_tool("set_light")
    assert tool is not None

    def other_correction(args, prompt):  # different body, same presence
        return dict(args, room="bedroom")

    same_presence = replace(
        base,
        tools=tuple(
            replace(t, prompt_correction=other_correction) if t.name == "set_light" else t
            for t in base.tools
        ),
    )
    assert same_presence.fingerprint() == base.fingerprint()

    removed = replace(
        base,
        tools=tuple(
            replace(t, prompt_correction=None) if t.name == "set_light" else t
            for t in base.tools
        ),
    )
    assert removed.fingerprint() != base.fingerprint()

    no_defaults = replace(
        base,
        tools=tuple(
            replace(t, defaults_when_missing=()) if t.name == "set_light" else t
            for t in base.tools
        ),
    )
    assert no_defaults.fingerprint() != base.fingerprint()


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def test_top_level_and_tool_shape() -> None:
    d = describe(_small_catalog())
    assert set(d) == {
        "name", "allow_empty_calls", "default_strip_unknown_args",
        "examples", "extra_rules", "tools",
    }
    assert d["name"] == "small_2"
    assert d["examples"] == [[
        "불 켜줘", '{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}',
    ]]
    assert d["extra_rules"] == ["Be terse."]
    assert [t["name"] for t in d["tools"]] == ["set_light", "list_devices"]

    set_light = d["tools"][0]
    assert set(set_light) == {
        "name", "description", "dsl_args_override", "strip_unknown_args",
        "custom_validator", "prompt_correction", "defaults_when_missing", "args",
    }
    assert set_light["dsl_args_override"] is None
    assert set_light["strip_unknown_args"] is True
    assert set_light["custom_validator"] is False
    assert set_light["prompt_correction"] is True
    assert set_light["defaults_when_missing"] == [
        {"arg": "state", "value": "on", "predicate_present": True},
    ]
    list_devices = d["tools"][1]
    assert list_devices["dsl_args_override"] == "{}"
    assert list_devices["args"] == []
    assert list_devices["defaults_when_missing"] == []


def test_per_kind_arg_keys() -> None:
    args = {a["name"]: a for a in describe(_small_catalog())["tools"][0]["args"]}
    # declared order preserved
    assert list(args) == ["room", "state", "brightness", "gamma", "label", "force", "at", "extra"]

    room = args["room"]
    assert room["kind"] == "enum" and room["required"] is True
    assert room["description"] == "Room."
    assert room["values"] == ["living", "bedroom"]  # declared order, not sorted
    assert list(room["aliases"]) == sorted(room["aliases"])  # key-sorted
    assert room["aliases"] == {"living room": "living", "거실": "living", "침실": "bedroom"}
    assert room["bool_true"] is None and room["bool_false"] is None
    assert args["state"]["bool_true"] == "on" and args["state"]["bool_false"] == "off"

    brightness = args["brightness"]
    assert brightness["kind"] == "integer" and brightness["required"] is False
    assert (brightness["min"], brightness["max"], brightness["allow_percent"]) == (0, 100, True)

    gamma = args["gamma"]
    assert gamma["kind"] == "number" and (gamma["min"], gamma["max"]) == (0.5, 2.5)
    assert "allow_percent" not in gamma

    label = args["label"]
    assert label["kind"] == "string"
    assert label["aliases"] == {"거실등": "living-lamp"} and label["pattern"] == "[a-z-]+"

    force = args["force"]
    assert set(force) == {"name", "kind", "required", "description"}
    assert force["kind"] == "boolean" and force["description"] == "Force."
    at = args["at"]
    assert set(at) == {"name", "kind", "required", "description"} and at["kind"] == "time"

    extra = args["extra"]
    assert extra["kind"] == "raw"
    assert extra["description"] == ""  # RawArg has no description field
    assert extra["dsl_description"] == "object"
    assert extra["json_schema"] == {
        "type": "object",
        "properties": {"a": {"type": "string"}, "z": {"type": "integer"}},
    }
    # canonical copy: independent of the spec's own dict
    spec = _small_catalog().get_tool("set_light").get_arg("extra")
    assert extra["json_schema"] is not spec.json_schema


def test_raw_json_schema_is_canonicalised_regardless_of_insertion_order() -> None:
    def build(schema):
        return Catalog(
            name="raw_1",
            tools=(ToolSpec(name="t", description="", args=(("x", RawArg(json_schema=schema, dsl_description="d")),)),),
        )

    a = build({"type": "object", "properties": {"z": {"type": "integer"}, "a": {"type": "string"}}})
    b = build({"properties": {"a": {"type": "string"}, "z": {"type": "integer"}}, "type": "object"})
    assert a.fingerprint() == b.fingerprint()
    assert describe(a) == describe(b)


# ---------------------------------------------------------------------------
# builtin tiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_every_builtin_tier_describes_and_dumps(tier: str) -> None:
    catalog = get_catalog(tier)
    d = catalog.describe()
    dumped = json.dumps(d, sort_keys=True, ensure_ascii=False)
    assert json.loads(dumped) == d
    assert d["name"] == catalog.name
    assert len(d["tools"]) == len(catalog.tools)
    assert _FINGERPRINT_RE.match(catalog.fingerprint())
    for tool in d["tools"]:
        for arg in tool["args"]:
            assert {"name", "kind", "required", "description"} <= set(arg)
            if arg["kind"] == "raw":
                assert "json_schema" in arg and "dsl_description" in arg


def test_builtin_tiers_have_distinct_fingerprints() -> None:
    fps = {tier: get_catalog(tier).fingerprint() for tier in TIERS}
    assert len(set(fps.values())) == len(fps)


def test_iot_light_5_set_light_default_and_raw_arg() -> None:
    d = get_catalog("iot_light_5").describe()
    tools = {t["name"]: t for t in d["tools"]}
    assert tools["set_light"]["defaults_when_missing"] == [
        {"arg": "state", "value": "on", "predicate_present": True},
    ]
    assert tools["set_light"]["prompt_correction"] is True
    assert tools["set_light"]["custom_validator"] is False
    assert tools["create_scene"]["custom_validator"] is True
    assert tools["list_devices"]["strip_unknown_args"] is True
    actions = {a["name"]: a for a in tools["create_scene"]["args"]}["actions"]
    assert actions["kind"] == "raw"
    assert actions["dsl_description"] == "array of set_light calls"
    assert actions["json_schema"]["type"] == "array"
    room = {a["name"]: a for a in tools["set_light"]["args"]}["room"]
    assert room["aliases"]["거실"] == "living"
    assert list(room["aliases"]) == sorted(room["aliases"])


# ---------------------------------------------------------------------------
# failure modes
# ---------------------------------------------------------------------------


def test_unknown_argspec_class_raises_type_error() -> None:
    class Weird:
        kind = "weird"
        required = True

    catalog = Catalog(
        name="bad", tools=(ToolSpec(name="t", description="", args=(("x", Weird()),)),),
    )
    with pytest.raises(TypeError, match="Weird"):
        describe(catalog)


def test_non_json_default_value_propagates_type_error_at_fingerprint() -> None:
    catalog = Catalog(
        name="bad_default",
        tools=(
            ToolSpec(
                name="t",
                description="",
                args=(("x", StringArg(required=False)),),
                defaults_when_missing=(("x", object(), lambda args: True),),
            ),
        ),
    )
    describe(catalog)  # the dict itself is fine
    with pytest.raises(TypeError):
        catalog_fingerprint(catalog)


# ---------------------------------------------------------------------------
# import guard
# ---------------------------------------------------------------------------


def test_package_import_has_no_circular_import() -> None:
    proc = subprocess.run(
        [
            sys.executable, "-c",
            "import ganglion.contract as c; "
            "assert c.describe and c.catalog_fingerprint and c.apply_patch and c.strip_hooks",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_contract_package_does_not_import_lm_or_analyzer() -> None:
    """The contract module is the leaf of the dependency DAG."""
    code = (
        "import sys; import ganglion.contract, ganglion.contract.describe, ganglion.contract.patch; "
        "bad = sorted(m for m in sys.modules if m.startswith(('ganglion.lm', 'ganglion.analyzer'))); "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "[]", out
