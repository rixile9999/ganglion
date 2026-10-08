"""``ganglion model`` — list | health | load | unload ([[cli_operator]] §Scope).

One route per verb, and nothing else:

    ``GET  /api/models?catalog_id=``      model list
    ``GET  /api/models/{id}/health``      model health
    ``POST /api/models/{id}/load``        model load     (W)
    ``POST /api/models/{id}/unload``      model unload   (W)

The rows are the registry's own ([[lm_model_registry]] ``spec_to_row``) plus the
two verdicts only the route can join: ``model_fingerprint`` and
``fingerprint_match`` — whether the model's ``trained_on`` fingerprint is the
context catalog's. ``fingerprint_match`` is ``null`` for a model that declares no
``trained_on``; that is "not applicable", rendered ``-``, never a mismatch.

``load`` / ``unload`` exist for ``kind: local_hf`` only; every other kind is the
route's ``404 not_local_model``, surfaced unchanged.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from ganglion.ctl.deps import Deps
from ganglion.ctl.render import Column, kv, table

__all__ = ["add_parser"]

#: Status keys worth a line first, in reading order; anything else the route
#: returns (``device`` / ``vram_mb`` for a local model) is appended sorted, so a
#: new key is visible without editing this list.
_STATUS_KEYS: tuple[str, ...] = ("status", "detail", "seconds", "unloaded", "device", "vram_mb")

_DETAIL_WIDTH = 34


def _status_block(model_id: str, body: Any) -> str:
    """``model_id`` + every key the health / load / unload payload carries."""
    if not isinstance(body, Mapping):
        return kv([("model_id", model_id), ("payload", body)])
    keys = [key for key in _STATUS_KEYS if key in body]
    keys += [key for key in sorted(body) if key not in keys]
    return kv([("model_id", model_id), *((key, body[key]) for key in keys)])


# ---------------------------------------------------------------------------
# model list
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace, deps: Deps) -> int:
    # A catalog is optional here, unlike everywhere else: with no context the
    # route is called without the query and the registry lists every entry.
    catalog = deps.ctx.catalog
    payload = deps.api.get("/api/models", catalog_id=catalog)

    def render(body: Any) -> None:
        rows = [row for row in (body.get("models") or ()) if isinstance(row, Mapping)]
        columns: list[Column] = []
        if deps.ctx.model:  # an all-empty marker column would indent every row for nothing
            columns.append(Column("", lambda row: "*" if row.get("model_id") == deps.ctx.model else ""))
        columns += [
            Column("model_id", "model_id"),
            Column("kind", "kind"),
            Column("client", "client"),
            Column("provider", "provider"),
            Column("avail", "available"),
            Column("match", "fingerprint_match"),
            Column("detail", "available_detail", max_width=_DETAIL_WIDTH),
        ]
        deps.out.write(table(rows, columns, empty="(no models — check the registry path in `ganglion health`)"))
        deps.out.blank()
        note = (
            f"match = trained_on fingerprint == {catalog}" if catalog
            else "match = - for every row: no context catalog to compare trained_on against"
        )
        deps.out.write(note + ("  *=context model" if deps.ctx.model else ""))

    return deps.out.emit(payload, render, rows_key="models")


# ---------------------------------------------------------------------------
# model health / load / unload
# ---------------------------------------------------------------------------


def _cmd_health(args: argparse.Namespace, deps: Deps) -> int:
    model_id = args.model_id or deps.ctx.require("model")
    payload = deps.api.get(f"/api/models/{model_id}/health")
    return deps.out.emit(payload, lambda body: deps.out.write(_status_block(model_id, body)))


def _cmd_load(args: argparse.Namespace, deps: Deps) -> int:
    model_id = args.model_id or deps.ctx.require("model")
    payload = deps.api.post(f"/api/models/{model_id}/load")
    return deps.out.emit(payload, lambda body: deps.out.write(_status_block(model_id, body)))


def _cmd_unload(args: argparse.Namespace, deps: Deps) -> int:
    model_id = args.model_id or deps.ctx.require("model")
    payload = deps.api.post(f"/api/models/{model_id}/unload")
    return deps.out.emit(payload, lambda body: deps.out.write(_status_block(model_id, body)))


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register the ``model`` noun. Only the leaves inherit ``common``."""
    model = sub.add_parser(
        "model",
        help="list | health | load | unload",
        description="Registry entries and their liveness (GET /api/models…).",
    )
    verbs = model.add_subparsers(dest="verb", metavar="<verb>", required=True)

    listing = verbs.add_parser("list", parents=[common],
                               help="registry entries, narrowed to the context catalog when set")
    listing.set_defaults(_handler=_cmd_list)

    health = verbs.add_parser("health", parents=[common], help="probe one model without invoking it")
    health.add_argument("model_id", nargs="?", default=None,
                        help="default: the context model (`ganglion use --model`)")
    health.set_defaults(_handler=_cmd_health)

    load = verbs.add_parser("load", parents=[common], help="load a local_hf model into this process")
    load.add_argument("model_id", nargs="?", default=None, help="default: the context model")
    load.set_defaults(_handler=_cmd_load)

    unload = verbs.add_parser("unload", parents=[common], help="drop the in-process local_hf model")
    unload.add_argument("model_id", nargs="?", default=None, help="default: the context model")
    unload.set_defaults(_handler=_cmd_unload)
