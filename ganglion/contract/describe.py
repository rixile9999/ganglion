"""Deterministic ``Catalog`` description and content fingerprint.

Spec: [[contract_describe]] (``docs/tasks/contract_describe.md``).

``Catalog`` already renders two artifacts — ``render_json_dsl()`` and
``render_openai_tools()`` — but neither exposes aliases, post-correction
hooks, argument descriptions or ``bool_true`` / ``bool_false``. Two
catalogs that differ only in an alias therefore render byte-identical
DSL. ``describe()`` is the third renderer: one JSON-able dict covering
every field the validator reads, in declared order, with mappings emitted
key-sorted and callables reduced to presence booleans. ``catalog_fingerprint``
hashes it into the ``cf-`` value that is the first element of the identity
triple (``catalog_fingerprint``, ``model_id``, ``dataset_sha256``).

Determinism rule, stated once: **changing the body of a callable
(``custom_validator``, ``prompt_correction``, a ``DefaultRule`` predicate)
does not move the fingerprint.** Hook bodies are reviewed at source level
and measured empirically by the analyzer, never hashed here.

Import discipline: this module imports ``tool_spec`` at top level and only
type-imports ``Catalog`` (``ganglion/contract/__init__.py`` imports
``catalog`` first; ``Catalog.describe()`` lazy-imports this module).
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from ganglion.contract.tool_spec import (
    ArgSpec,
    BoolArg,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, see [[contract_describe]]
    from ganglion.contract.catalog import Catalog

__all__ = ["catalog_fingerprint", "describe", "describe_arg", "describe_tool"]

_FINGERPRINT_PREFIX = "cf-"
_FINGERPRINT_HEX_LEN = 12


def describe(catalog: Catalog) -> dict[str, Any]:
    """Return the deterministic JSON-able description of ``catalog``.

    Shape (top level → tool → arg)::

        {"name", "allow_empty_calls", "default_strip_unknown_args",
         "examples": [[prompt, response], ...], "extra_rules": [...],
         "tools": [describe_tool(tool), ...]}

    Sequences keep declared order (tools, args, enum values, examples,
    extra rules); mappings are key-sorted; callables become presence
    booleans. ``json.dumps(describe(c), sort_keys=True, ensure_ascii=False)``
    always succeeds for a well-declared catalog; a non-JSON default value
    surfaces as ``TypeError`` at dump time (the builtin is mis-declared).

    Raises ``TypeError`` for an ``ArgSpec`` of an unknown class — there is
    deliberately no silent ``"raw"`` fallback.
    """
    return {
        "name": catalog.name,
        "allow_empty_calls": bool(catalog.allow_empty_calls),
        "default_strip_unknown_args": bool(catalog.default_strip_unknown_args),
        "examples": [list(pair) for pair in catalog.examples],
        "extra_rules": list(catalog.extra_rules),
        "tools": [describe_tool(tool) for tool in catalog.tools],
    }


def describe_tool(tool: ToolSpec) -> dict[str, Any]:
    """Describe one ``ToolSpec`` — see :func:`describe` for the rules."""
    return {
        "name": tool.name,
        "description": tool.description,
        "dsl_args_override": tool.dsl_args_override,
        "strip_unknown_args": bool(tool.strip_unknown_args),
        "custom_validator": tool.custom_validator is not None,
        "prompt_correction": tool.prompt_correction is not None,
        "defaults_when_missing": [
            {"arg": arg_name, "value": default_value, "predicate_present": True}
            for arg_name, default_value, _predicate in tool.defaults_when_missing
        ],
        "args": [describe_arg(name, spec) for name, spec in tool.args],
    }


def describe_arg(name: str, spec: ArgSpec) -> dict[str, Any]:
    """Describe one ``(name, ArgSpec)`` pair.

    Every entry carries ``name`` / ``kind`` / ``required`` / ``description``
    (``RawArg`` has no ``description`` field → ``""``), plus per-kind keys:

    ========= ==========================================
    kind      extra keys
    ========= ==========================================
    enum      values, aliases (sorted), bool_true, bool_false
    integer   min, max, allow_percent
    number    min, max
    string    aliases (sorted), pattern
    boolean   —
    time      —
    raw       json_schema (canonical JSON copy), dsl_description
    ========= ==========================================
    """
    entry: dict[str, Any] = {
        "name": name,
        "kind": spec.kind,
        "required": bool(spec.required),
        "description": getattr(spec, "description", ""),
    }
    if isinstance(spec, EnumArg):
        entry["values"] = list(spec.values)
        entry["aliases"] = _sorted_aliases(spec.aliases)
        entry["bool_true"] = spec.bool_true
        entry["bool_false"] = spec.bool_false
    elif isinstance(spec, IntArg):
        entry["min"] = spec.min_value
        entry["max"] = spec.max_value
        entry["allow_percent"] = bool(spec.allow_percent)
    elif isinstance(spec, NumberArg):
        entry["min"] = spec.min_value
        entry["max"] = spec.max_value
    elif isinstance(spec, StringArg):
        entry["aliases"] = _sorted_aliases(spec.aliases)
        entry["pattern"] = spec.pattern
    elif isinstance(spec, (BoolArg, TimeArg)):
        pass
    elif isinstance(spec, RawArg):
        # Round-trip through JSON so the returned object is a pure, canonical
        # copy (tuples → lists, key order fixed) independent of the spec's
        # own mutable dict. Non-serialisable content fails loud here.
        entry["json_schema"] = json.loads(
            json.dumps(spec.json_schema, sort_keys=True, ensure_ascii=False)
        )
        entry["dsl_description"] = spec.dsl_description
    else:
        raise TypeError(
            f"describe: unknown ArgSpec class {type(spec).__name__!r} for arg {name!r}"
        )
    return entry


def catalog_fingerprint(catalog: Catalog) -> str:
    """Return ``"cf-" + sha256(canonical describe JSON)[:12]``.

    Stable across processes and re-computations; moves iff a field that
    :func:`describe` exposes changes (an alias, a default, a hook toggle,
    ``allow_empty_calls`` …). Matches ``^cf-[0-9a-f]{12}$``.
    """
    blob = json.dumps(describe(catalog), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return _FINGERPRINT_PREFIX + digest[:_FINGERPRINT_HEX_LEN]


def _sorted_aliases(aliases: Any) -> dict[str, str]:
    return {str(key): str(value) for key, value in sorted(dict(aliases).items())}
