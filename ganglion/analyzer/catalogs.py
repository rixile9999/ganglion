"""Catalog resolution for the analyzer and the console.

Companion to [[contract_catalog]] / [[contract_schema_compiler]] on the
analyzer side: turns a ``catalog_id`` string — the vocabulary
[[analyzer_trace_store]] stamps on every trace — back into a ``Catalog``.

Three id families:

* builtin tier names (``iot_light_5``, ``home_iot_20``, ``smart_home_50``,
  ``home_assistant_4``) → ``ganglion.contract.builtins.TIERS`` (errata E5/E17:
  all four tiers, not only ``SCALING_TIERS``);
* ``compiled/<sha12>`` → persisted at ``<base>/compiled/<sha12>/catalog.json``
  and recompiled through ``compile_tool_calling_schema(tools, name=catalog_id,
  allow_empty_calls=...)`` so ``catalog.name == catalog_id`` — the value
  ``synthesize_rules`` stamps into every patch (errata E5);
* ``bfcl/<category>`` → per-case catalogs; **not resolvable**
  (:class:`CatalogNotResolvable`), read-only in the console.

Public API:
    COMPILED_PREFIX, CatalogNotResolvable, resolve_catalog,
    register_compiled, list_catalogs, source_tools_for.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ganglion.contract.builtins import TIERS, get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.contract.schema_compiler import CompiledToolMapper, compile_tool_calling_schema

__all__ = [
    "COMPILED_PREFIX",
    "CatalogNotResolvable",
    "list_catalogs",
    "register_compiled",
    "resolve_catalog",
    "source_tools_for",
]

COMPILED_PREFIX = "compiled/"
_BFCL_PREFIX = "bfcl/"
_CATALOG_JSON = "catalog.json"
_SHA12_RE = re.compile(r"^[0-9a-f]{12}$")


class CatalogNotResolvable(ValueError):
    """Raised for ``bfcl/<category>`` (per-case catalogs), an unknown builtin
    tier, or a ``compiled/<sha12>`` id with no persisted ``catalog.json``."""


def _compiled_sha(catalog_id: str) -> str:
    sha = catalog_id[len(COMPILED_PREFIX):]
    if not _SHA12_RE.match(sha):
        raise CatalogNotResolvable(
            f"compiled catalog id {catalog_id!r} must be 'compiled/<12 hex chars>'"
        )
    return sha


def _compiled_path(base_dir: Path | str, sha: str) -> Path:
    return Path(base_dir) / "compiled" / sha / _CATALOG_JSON


def _read_compiled(base_dir: Path | str, catalog_id: str) -> dict[str, Any]:
    path = _compiled_path(base_dir, _compiled_sha(catalog_id))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogNotResolvable(f"no persisted catalog at {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogNotResolvable(f"unreadable catalog.json at {path}: {exc}") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tools"), list):
        raise CatalogNotResolvable(f"{path}: catalog.json must carry a 'tools' list")
    return dict(payload)


def _compile(catalog_id: str, tools: Sequence[Mapping[str, Any]], allow_empty_calls: bool) -> CompiledToolMapper:
    return compile_tool_calling_schema(
        list(tools), name=catalog_id, allow_empty_calls=bool(allow_empty_calls),
    )


def _compile_payload(catalog_id: str, payload: Mapping[str, Any]) -> CompiledToolMapper:
    """Compile a persisted ``catalog.json`` payload.

    The one path shared by :func:`resolve_catalog` and :func:`register_compiled`,
    so a catalog rebuilt from disk can never drift from the one handed back at
    registration time.
    """
    return _compile(
        catalog_id, payload["tools"], bool(payload.get("allow_empty_calls", False)),
    )


def resolve_catalog(catalog_id: str, *, base_dir: Path | str) -> Catalog:
    """``catalog_id`` → ``Catalog``.

    Builtin tiers via ``get_catalog``; ``compiled/<sha12>`` from
    ``<base>/compiled/<sha12>/catalog.json`` (recompiled with
    ``name=catalog_id``, so ``resolve_catalog(cid).name == cid``);
    ``bfcl/*`` and anything else → :class:`CatalogNotResolvable`.
    """
    if catalog_id.startswith(_BFCL_PREFIX):
        raise CatalogNotResolvable(
            f"{catalog_id!r}: BFCL catalogs are per-case and cannot be resolved by id"
        )
    if catalog_id.startswith(COMPILED_PREFIX):
        return _compile_payload(catalog_id, _read_compiled(base_dir, catalog_id)).catalog
    if catalog_id in TIERS:
        return get_catalog(catalog_id)
    raise CatalogNotResolvable(
        f"unknown catalog id {catalog_id!r}; builtin tiers: {sorted(TIERS)}"
    )


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def register_compiled(
    base_dir: Path | str,
    name: str,
    tools: list[Mapping[str, Any]],
    *,
    allow_empty_calls: bool = False,
) -> tuple[str, CompiledToolMapper]:
    """Persist an external tool list as ``compiled/<sha12>``; idempotent.

    The id is ``sha256(stable_json([name, tools, allow_empty_calls]))[:12]`` —
    hashed from a *canonical* (``sort_keys=True``) form, so two logically
    identical submissions collapse onto one id however their keys happen to be
    ordered.

    The stored document, by contrast, keeps the tool objects **in the order
    they were submitted** (pretty-printed, never ``sort_keys``). Key order is
    load-bearing here: ``compile_tool_calling_schema`` builds ``ToolSpec.args``
    in ``parameters.properties`` order, and that order is what
    ``render_json_dsl()`` puts in the model-facing prompt, what
    ``render_openai_tools()`` re-emits, and what ``describe()`` hashes into the
    ``cf-`` fingerprint. Canonicalising the document on write would hand back a
    catalog here and rebuild a *re-ordered twin* in :func:`resolve_catalog` —
    a different prompt and a different fingerprint for one id across a console
    restart, which is exactly what the identity triple exists to prevent.

    For an id that already exists the **persisted** document wins: the mapper
    is recompiled from disk rather than from the caller's list, so
    ``register_compiled`` → ``resolve_catalog`` → ``register_compiled`` all
    describe identically. A corrupt ``catalog.json`` is rewritten from the
    caller's list.

    ``catalog.json`` stores ``{"catalog_id", "name": catalog_id, "label":
    name, "tools", "allow_empty_calls"}`` (errata E5 — ``name`` is the
    catalog id the compiled ``Catalog`` carries; the human-supplied name is
    kept as ``label``). A new tool list is compiled *before* anything is
    written so a malformed schema raises ``DSLValidationError`` and leaves
    no file. Returns ``(catalog_id, mapper)``.
    """
    tools_list = [dict(t) for t in tools]
    sha = hashlib.sha256(
        _stable_json([name, tools_list, bool(allow_empty_calls)]).encode("utf-8")
    ).hexdigest()[:12]
    catalog_id = COMPILED_PREFIX + sha
    path = _compiled_path(base_dir, sha)

    if path.exists():
        try:
            persisted: dict[str, Any] | None = _read_compiled(base_dir, catalog_id)
        except CatalogNotResolvable:
            persisted = None  # corrupt or truncated → rewrite it below
        if persisted is not None:
            return catalog_id, _compile_payload(catalog_id, persisted)

    mapper = _compile(catalog_id, tools_list, allow_empty_calls)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "catalog_id": catalog_id,
        "name": catalog_id,
        "label": name,
        "tools": tools_list,
        "allow_empty_calls": bool(allow_empty_calls),
    }
    tmp = path.with_name(path.name + ".tmp")
    # Indented for review, but NOT key-sorted: see the docstring — sorting the
    # tool objects re-orders `properties`, hence `ToolSpec.args`, hence the DSL
    # prompt and the fingerprint.
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return catalog_id, mapper


def _catalog_row(catalog_id: str, catalog: Catalog, source: str) -> dict[str, Any]:
    return {
        "catalog_id": catalog_id,
        "n_tools": len(catalog.tools),
        "allow_empty_calls": bool(catalog.allow_empty_calls),
        "fingerprint": catalog.fingerprint(),
        "source": source,
    }


def list_catalogs(base_dir: Path | str) -> list[dict[str, Any]]:
    """Builtins (every tier in ``TIERS``) followed by persisted compiled catalogs.

    Rows: ``{"catalog_id", "n_tools", "allow_empty_calls", "fingerprint",
    "source": "builtin" | "compiled"}``; compiled rows also carry ``label``.
    Unreadable compiled entries are skipped.
    """
    rows = [_catalog_row(tier, catalog, "builtin") for tier, catalog in TIERS.items()]
    compiled_root = Path(base_dir) / "compiled"
    if compiled_root.is_dir():
        for entry in sorted(compiled_root.iterdir()):
            if not entry.is_dir() or not _SHA12_RE.match(entry.name):
                continue
            catalog_id = COMPILED_PREFIX + entry.name
            try:
                payload = _read_compiled(base_dir, catalog_id)
                catalog = _compile_payload(catalog_id, payload).catalog
            except Exception:  # noqa: BLE001 — a broken entry must not hide the rest
                continue
            row = _catalog_row(catalog_id, catalog, "compiled")
            row["label"] = str(payload.get("label", "") or "")
            rows.append(row)
    return rows


def source_tools_for(catalog_id: str, base_dir: Path | str) -> list[dict[str, Any]] | None:
    """The persisted source tool list of a ``compiled/<sha12>`` catalog; ``None`` otherwise."""
    if not catalog_id.startswith(COMPILED_PREFIX):
        return None
    try:
        payload = _read_compiled(base_dir, catalog_id)
    except CatalogNotResolvable:
        return None
    return [dict(t) for t in payload["tools"] if isinstance(t, Mapping)]
