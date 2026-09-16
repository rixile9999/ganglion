"""Tests for ``ganglion.analyzer.catalogs`` — catalog-id resolution (errata E5 / E17)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.catalogs import (
    COMPILED_PREFIX,
    CatalogNotResolvable,
    list_catalogs,
    register_compiled,
    resolve_catalog,
    source_tools_for,
)
from ganglion.contract.builtins import TIERS, get_catalog
from ganglion.contract.tool_spec import DSLValidationError

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["c", "f"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "parameters": {"type": "object", "properties": {"at": {"type": "string"}}, "required": ["at"]},
        },
    },
]


# Properties deliberately NOT in alphabetical order. ``compile_tool_calling_schema``
# builds ``ToolSpec.args`` in ``parameters.properties`` order, so this is the shape
# that catches a key-sorting persistence layer (the DSL prompt, the OpenAI
# re-render and the ``cf-`` fingerprint all move with it).
UNSORTED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_light",
            "description": "Control a lamp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "brightness": {"type": "integer"},
                    "action": {"type": "string", "enum": ["on", "off"]},
                },
                "required": ["room", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "parameters": {
                "type": "object",
                "properties": {"minutes": {"type": "integer"}, "label": {"type": "string"}},
                "required": ["minutes"],
            },
        },
    },
]
_UNSORTED_ARG_ORDER = ["room", "brightness", "action"]


def _compiled_json(base_dir: Path, catalog_id: str) -> dict:
    path = base_dir / "compiled" / catalog_id.split("/")[1] / "catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_builtin_tiers_resolve_including_home_assistant(tmp_path: Path) -> None:
    for tier in TIERS:
        assert resolve_catalog(tier, base_dir=tmp_path) is get_catalog(tier)
    assert resolve_catalog("home_assistant_4", base_dir=tmp_path).name == "home_assistant_4"


def test_bfcl_and_unknown_are_not_resolvable(tmp_path: Path) -> None:
    with pytest.raises(CatalogNotResolvable):
        resolve_catalog("bfcl/simple_python", base_dir=tmp_path)
    with pytest.raises(CatalogNotResolvable):
        resolve_catalog("no_such_tier", base_dir=tmp_path)
    with pytest.raises(CatalogNotResolvable):
        resolve_catalog("compiled/000000000000", base_dir=tmp_path)
    with pytest.raises(CatalogNotResolvable):
        resolve_catalog("compiled/../etc", base_dir=tmp_path)
    assert issubclass(CatalogNotResolvable, ValueError)


def test_register_compiled_round_trip_and_idempotency(tmp_path: Path) -> None:
    catalog_id, mapper = register_compiled(tmp_path, "weather", TOOLS)
    assert catalog_id.startswith(COMPILED_PREFIX) and len(catalog_id) == len(COMPILED_PREFIX) + 12
    assert mapper.catalog.name == catalog_id  # E5: synthesize_rules stamps catalog.name
    path = tmp_path / "compiled" / catalog_id.split("/")[1] / "catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["catalog_id"] == catalog_id and payload["name"] == catalog_id
    assert payload["label"] == "weather" and payload["allow_empty_calls"] is False
    assert payload["tools"] == TOOLS
    mtime = path.stat().st_mtime_ns
    again, _ = register_compiled(tmp_path, "weather", TOOLS)
    assert again == catalog_id and path.stat().st_mtime_ns == mtime
    resolved = resolve_catalog(catalog_id, base_dir=tmp_path)
    assert resolved.name == catalog_id
    assert [t.name for t in resolved.tools] == ["get_weather", "set_alarm"]
    assert resolved.fingerprint() == mapper.catalog.fingerprint()
    plan = resolved.parse_json_dsl({"calls": [{"action": "get_weather", "args": {"city": "Seoul"}}]})
    assert plan.calls[0].action == "get_weather"
    # Different name / allow_empty_calls → different id.
    other_id, other = register_compiled(tmp_path, "weather", TOOLS, allow_empty_calls=True)
    assert other_id != catalog_id and other.catalog.allow_empty_calls is True
    assert resolve_catalog(other_id, base_dir=tmp_path).allow_empty_calls is True
    assert source_tools_for(catalog_id, tmp_path) == TOOLS
    assert source_tools_for("iot_light_5", tmp_path) is None
    assert source_tools_for("compiled/ffffffffffff", tmp_path) is None


def test_register_compiled_rejects_bad_schema_without_writing(tmp_path: Path) -> None:
    with pytest.raises(DSLValidationError):
        register_compiled(tmp_path, "bad", [{"type": "function", "function": {"name": ""}}])
    assert not (tmp_path / "compiled").exists()


def test_list_catalogs(tmp_path: Path) -> None:
    rows = list_catalogs(tmp_path)
    assert [r["catalog_id"] for r in rows] == list(TIERS)
    assert all(r["source"] == "builtin" for r in rows)
    iot = next(r for r in rows if r["catalog_id"] == "iot_light_5")
    assert iot["n_tools"] == 5 and iot["allow_empty_calls"] is False
    assert iot["fingerprint"] == get_catalog("iot_light_5").fingerprint()
    catalog_id, _ = register_compiled(tmp_path, "weather", TOOLS)
    (tmp_path / "compiled" / "notasha").mkdir()  # ignored
    rows = list_catalogs(tmp_path)
    compiled = [r for r in rows if r["source"] == "compiled"]
    assert [r["catalog_id"] for r in compiled] == [catalog_id]
    assert compiled[0]["n_tools"] == 2 and compiled[0]["label"] == "weather"
    assert set(compiled[0]) >= {"catalog_id", "n_tools", "allow_empty_calls", "fingerprint", "source"}


def test_fingerprint_is_stable_across_persistence(tmp_path: Path) -> None:
    """compile → disk → compile again must yield ONE fingerprint.

    Regression: ``catalog.json`` used to be written with ``sort_keys=True``,
    which re-ordered every tool's ``properties``; the catalog
    ``resolve_catalog`` rebuilt after a console restart therefore carried a
    different ``ToolSpec.args`` order and a different ``cf-`` fingerprint than
    the one handed back at registration — the instability the identity triple
    exists to prevent.
    """
    catalog_id, mapper = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)
    from_disk = resolve_catalog(catalog_id, base_dir=tmp_path)
    again_id, again = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)

    assert again_id == catalog_id
    fingerprint = mapper.catalog.fingerprint()
    assert fingerprint.startswith("cf-")
    assert from_disk.fingerprint() == fingerprint
    assert again.catalog.fingerprint() == fingerprint
    # E5: the catalog name is the compiled id on every path (synthesize_rules
    # stamps catalog.name into patch ids).
    assert mapper.catalog.name == catalog_id
    assert from_disk.name == catalog_id
    assert again.catalog.name == catalog_id


def test_persisted_tools_keep_property_order(tmp_path: Path) -> None:
    catalog_id, mapper = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)

    payload = _compiled_json(tmp_path, catalog_id)
    props = payload["tools"][0]["function"]["parameters"]["properties"]
    assert list(props) == _UNSORTED_ARG_ORDER
    assert list(props) != sorted(props)  # sorting these is what broke the fingerprint
    assert list(payload["tools"][0]["function"]) == ["name", "description", "parameters"]

    from_disk = resolve_catalog(catalog_id, base_dir=tmp_path)
    for catalog in (mapper.catalog, from_disk):
        assert [arg for arg, _spec in catalog.tools[0].args] == _UNSORTED_ARG_ORDER
    # Arg order is not a hash detail: it is the order the model sees.
    assert from_disk.render_json_dsl() == mapper.catalog.render_json_dsl()
    rendered = from_disk.render_openai_tools()[0]["function"]["parameters"]["properties"]
    assert list(rendered) == _UNSORTED_ARG_ORDER
    assert source_tools_for(catalog_id, tmp_path) == UNSORTED_TOOLS

    compiled_rows = [r for r in list_catalogs(tmp_path) if r["source"] == "compiled"]
    assert [r["fingerprint"] for r in compiled_rows] == [mapper.catalog.fingerprint()]


def test_reordered_submission_shares_id_and_persisted_order(tmp_path: Path) -> None:
    """The id is hashed from a canonical form, so a key-reordered but logically
    identical submission lands on the same ``compiled/<sha12>`` — and must then
    describe like the document already on disk, not like a re-ordered twin."""
    catalog_id, mapper = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)

    shuffled = json.loads(json.dumps(UNSORTED_TOOLS))
    props = shuffled[0]["function"]["parameters"]["properties"]
    shuffled[0]["function"]["parameters"]["properties"] = {
        key: props[key] for key in ("action", "room", "brightness")
    }
    again_id, again = register_compiled(tmp_path, "lights", shuffled)

    assert again_id == catalog_id
    assert again.catalog.fingerprint() == mapper.catalog.fingerprint()
    assert [arg for arg, _spec in again.catalog.tools[0].args] == _UNSORTED_ARG_ORDER
    stored = _compiled_json(tmp_path, catalog_id)["tools"]
    assert list(stored[0]["function"]["parameters"]["properties"]) == _UNSORTED_ARG_ORDER


def test_corrupt_catalog_json_is_rewritten(tmp_path: Path) -> None:
    catalog_id, mapper = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)
    path = tmp_path / "compiled" / catalog_id.split("/")[1] / "catalog.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(CatalogNotResolvable):
        resolve_catalog(catalog_id, base_dir=tmp_path)

    again_id, again = register_compiled(tmp_path, "lights", UNSORTED_TOOLS)
    assert again_id == catalog_id
    assert again.catalog.fingerprint() == mapper.catalog.fingerprint()
    assert resolve_catalog(catalog_id, base_dir=tmp_path).fingerprint() == mapper.catalog.fingerprint()
