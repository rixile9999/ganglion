"""``ganglion label`` — the human-review write path ([[cli_operator]] §Scope).

Verbs: ``add`` → ``POST /api/labels``; ``export`` → ``GET /api/export/labels``.

A label is the only place a human verdict enters a run bundle, so nothing here
computes one: the ``zone``, the ``family_id`` and the ``graded_score`` all come
back from the primitive behind the route ([[analyzer_label_store]]).
``export`` is the documented *W†* GET — it persists an idempotent artifact
under ``<runs>/../labels/<catalog_id>/`` and is therefore not a projection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ganglion.ctl.deps import Deps
from ganglion.ctl.errors import CtlError
from ganglion.ctl.render import kv, plan_lines

__all__ = ["add_parser"]

#: Mirrors ``VERDICTS`` in ``ganglion/analyzer/labels.py`` — the source of
#: truth. Hard-coded rather than imported because a command module may not
#: reach into a primitive ([[cli_operator]] §Observation
#: ``primitive_imports_in_ctl``); grep that name if the two ever diverge. The
#: route rejects an unknown verdict with ``400 bad_request`` regardless, so
#: this list is an ergonomic (``--help``) copy, never a second gate.
VERDICTS: tuple[str, ...] = ("correct", "incorrect", "should_abstain", "unsure")


def _load_expected(source: str | None) -> dict[str, Any] | None:
    """``--expected PATH|-`` → the parsed Action IR object, or ``None``.

    Unreadable or non-JSON is *our* error (``400 bad_input``) and must fail
    before the route call; whether the object is a plan the catalog accepts is
    the route's verdict (``422 invalid_expected_plan``), never patched here.
    """
    if source is None:
        return None
    if source == "-" and sys.stdin is None:
        raise CtlError(400, "bad_input", "--expected -: no stdin to read")
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # `UnicodeDecodeError` is a `ValueError`, *not* an `OSError`: a binary
        # file is unreadable too, and must be the same `bad_input` as a missing
        # one rather than a traceback out of `main`'s `CtlError` boundary.
        raise CtlError(400, "bad_input", f"--expected {source}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CtlError(400, "bad_input", f"--expected {source}: not JSON — {exc}") from exc
    if payload is None:
        # The one local check beyond the failure table's row: a JSON `null`
        # names no plan, and passing it on would be indistinguishable from
        # omitting `--expected` — a `correct` verdict would then be recorded
        # with no gold, silently. Any other JSON value is the route's call.
        raise CtlError(400, "bad_input", f"--expected {source}: JSON null is not a plan")
    return payload


def _cmd_add(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = deps.ctx.require("catalog")
    run_id = deps.ctx.require("run")
    expected = _load_expected(args.expected)

    body: dict[str, Any] = {
        "catalog_id": catalog_id,
        "run_id": run_id,
        "trace_id": args.trace_id,
        "verdict": args.verdict,
    }
    if expected is not None:
        body["expected_plan"] = expected
    if args.endorsed is not None:  # tri-state: an absent flag is not `false`
        body["endorsed"] = args.endorsed
    if args.saw_f0:
        body["saw_f0"] = True
    if args.note:
        body["note"] = args.note
    if args.failure_hint:
        body["failure_hint"] = args.failure_hint
    if args.unordered:
        body["order_sensitive"] = False
    if args.supersedes:
        body["supersedes"] = args.supersedes

    payload = deps.api.post("/api/labels", body)

    def render(body_out: dict) -> None:
        deps.out.write(f"label recorded on {args.trace_id} ({catalog_id}/{run_id})")
        deps.out.write(kv([
            ("verdict", args.verdict),
            ("label_id", body_out.get("label_id")),
            ("zone", body_out.get("zone")),
            ("family_id", body_out.get("family_id")),
            ("graded_score", body_out.get("graded_score")),
            ("events", len(body_out.get("event_ids") or [])),
        ], indent=2))
        if body_out.get("graded_score") is None:
            deps.out.write("  (graded_score is null: no gold to score this trace against)")
        if expected is not None and isinstance(expected, dict):
            deps.out.blank()
            deps.out.write("gold")
            for line in plan_lines(expected):
                deps.out.write(line)

    return deps.out.emit(payload, render)


def _cmd_export(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = args.catalog_id or deps.ctx.require("catalog")
    payload = deps.api.get("/api/export/labels", catalog_id=catalog_id, zones=args.zones)

    def render(body: dict) -> None:
        deps.out.write(kv([
            ("catalog_id", catalog_id),
            ("zones", ", ".join(str(z) for z in body.get("zones") or [])),
            ("n_labels", body.get("n_labels")),
            ("n_sft", body.get("n_sft")),
            ("n_hard", body.get("n_hard")),
        ]))
        deps.out.blank()
        deps.out.write("written")
        paths = body.get("paths") or {}
        deps.out.write(kv([(name, paths[name]) for name in sorted(paths)], indent=2))

    return deps.out.emit(payload, render)


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register ``label`` and its two verbs; only the leaves inherit ``common``."""
    parser = sub.add_parser("label", help="add | export")
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>", required=True)

    add = verbs.add_parser(
        "add",
        parents=[common],
        help="record a human verdict on one trace (POST /api/labels)",
        description="Write one label. The catalog and run come from the context "
                    "(`ganglion use --catalog … --run …`) unless --catalog / --run override them.",
    )
    add.add_argument("trace_id", help="trace to label, as `ganglion trace list` prints it")
    add.add_argument("--verdict", required=True, choices=VERDICTS,
                     help="the human verdict (ganglion/analyzer/labels.py VERDICTS)")
    add.add_argument("--expected", default=None, metavar="PATH",
                     help='gold Action IR plan file {"calls":[{"action":…,"args":{…}}]}, or - for stdin; '
                          "required by verdict incorrect")
    endorse = add.add_mutually_exclusive_group()
    endorse.add_argument("--endorsed", dest="endorsed", action="store_const", const=True, default=None,
                         help="the model's own plan is endorsed as gold")
    endorse.add_argument("--not-endorsed", dest="endorsed", action="store_const", const=False,
                         help="explicitly not endorsed (absent = the key is omitted)")
    add.add_argument("--saw-f0", action="store_true",
                     help="record that the labeller saw the F⁰ (hooks-stripped) plan")
    add.add_argument("--note", default=None, metavar="TEXT", help="free-text note stored on the label")
    add.add_argument("--failure-hint", default=None, metavar="TYPE",
                     help="operator's guess at the failure type, for the taxonomy")
    add.add_argument("--unordered", action="store_true",
                     help="call order does not matter (sends order_sensitive=false)")
    add.add_argument("--supersedes", default=None, metavar="ID",
                     help="label_id this one replaces")
    add.set_defaults(_handler=_cmd_add)

    export = verbs.add_parser(
        "export",
        parents=[common],
        help="write the SFT / hard-pool export for a catalog (GET /api/export/labels)",
        description="Persists <runs>/../labels/<catalog_id>/{human_sft,hard_pool,zones}.jsonl. "
                    "Idempotent, but a write: this GET is not a projection.",
    )
    export.add_argument("catalog_id", nargs="?", default=None,
                        help="catalog to export (default: the context catalog)")
    export.add_argument("--zones", default=None, metavar="LIST",
                        help="comma-separated zones for the SFT split (route default: train)")
    export.set_defaults(_handler=_cmd_export)
