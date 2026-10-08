"""``ganglion trace`` — [[cli_operator]] §Scope.

Verbs: ``trace list``, ``trace show``.

Routes:
    ``GET /api/runs/{c}/{r}/traces?filter=&failure_type=&parent=&limit=&offset=``
    ``GET /api/runs/{c}/{r}/traces/{trace_id}``

Both are projections. Every selector is a **query parameter**: the route owns
what ``invalid`` / ``wrong`` / ``unlabelled`` / ``changed`` mean, which label
wins the gold join ([[analyzer_label_store]] ``resolve_gold``) and where
``direction`` comes from (``compare-<parent>.json``). Filtering the rows here
instead would make the CLI a second opinion on a trace's status.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from ganglion.ctl.deps import Deps
from ganglion.ctl.render import Column, fmt, kv, plan_lines, table

__all__ = ["add_parser"]

#: Route-side filters; ``all`` is the route's own default.
FILTERS: tuple[str, ...] = ("all", "invalid", "wrong", "unlabelled", "changed")

#: Order of the ``counts`` line — the route returns exactly these keys.
_COUNT_KEYS: tuple[str, ...] = ("all", "invalid", "wrong", "unlabelled", "changed")

_PROMPT_WIDTH = 34

_LIST_COLUMNS = (
    Column("trace_id", "trace_id"),
    Column("case_id", "case_id"),
    Column("status", "status"),
    Column("failure_type", "failure_type"),
    Column("dir", "direction"),
    Column("prompt", "prompt", max_width=_PROMPT_WIDTH),
)


def _cmd_trace_list(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    run_id = args.run_id or deps.ctx.require("run")
    payload = deps.api.get(
        f"/api/runs/{catalog}/{run_id}/traces",
        filter=args.filter,
        failure_type=args.failure_type,
        parent=args.parent,
        limit=args.limit,
        offset=args.offset,
    )

    def render(body: Any) -> None:
        counts = body.get("counts") or {}
        deps.out.write("  ".join(f"{key} {fmt(counts.get(key))}" for key in _COUNT_KEYS))
        parent = body.get("parent")
        if parent:
            deps.out.write(f"direction vs {parent}")
        deps.out.blank()
        rows = list(body.get("traces") or [])
        deps.out.write(table(rows, _LIST_COLUMNS, empty="(no traces match)"))
        total = int(body.get("total") or 0)
        if len(rows) != total:
            deps.out.blank()
            deps.out.write(
                f"showing {len(rows)} of {total} (filter {args.filter}, "
                f"limit {fmt(body.get('limit'))}, offset {fmt(body.get('offset'))})"
            )

    return deps.out.emit(payload, render, rows_key="traces")


def _cmd_trace_show(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    run_id = deps.ctx.require("run")  # `trace show` addresses the run by flag / context only
    payload = deps.api.get(f"/api/runs/{catalog}/{run_id}/traces/{args.trace_id}")

    def render(body: Any) -> None:
        trace = body.get("trace") or {}
        deps.out.write(kv([
            ("trace_id", trace.get("trace_id")),
            ("case_id", trace.get("case_id")),
            ("repeat_index", trace.get("repeat_index")),
            ("run", f"{trace.get('catalog_id')}/{trace.get('run_id')}"),
            ("source", trace.get("source")),
            ("model_id", trace.get("model_id")),
            ("timestamp", trace.get("timestamp")),
            ("latency_ms", trace.get("latency_ms")),
            ("attempts", len(trace.get("attempts") or ())),
            ("parse_strategy", trace.get("parse_strategy")),
            ("status", body.get("status")),
            ("gold_origin", body.get("gold_origin")),
        ]))

        deps.out.blank()
        deps.out.write("prompt")
        deps.out.write(f"  {trace.get('prompt')}")

        deps.out.blank()
        deps.out.write("plan (validated)")
        for line in plan_lines(trace.get("plan")):
            deps.out.write(line)
        deps.out.write("expected")
        for line in plan_lines(trace.get("expected_plan")):
            deps.out.write(line)

        # The decoded-but-rejected output is the whole point of a failed
        # trace, so it is printed whenever there is no validated plan.
        if trace.get("plan") is None or trace.get("error_type"):
            deps.out.blank()
            deps.out.write(kv([("error_type", trace.get("error_type"))]))
            deps.out.write("raw_plan (decoded, unvalidated)")
            for line in plan_lines(trace.get("raw_plan")):
                deps.out.write(line)

        classification = body.get("classification")
        deps.out.blank()
        deps.out.write("classification")
        if isinstance(classification, Mapping):
            deps.out.write(kv([
                ("failure_type", classification.get("failure_type")),
                ("confidence", classification.get("confidence")),
                ("evidence", classification.get("evidence")),
            ], indent=2))
        else:
            deps.out.write("  (none — `ganglion run analyze`)")

        label = body.get("label")
        deps.out.blank()
        deps.out.write("label")
        if isinstance(label, Mapping):
            deps.out.write(kv([
                ("label_id", label.get("label_id")),
                ("verdict", label.get("verdict")),
                ("zone", label.get("zone")),
                ("family_id", label.get("family_id")),
                ("author", label.get("author")),
                ("note", label.get("note")),
                ("failure_hint", label.get("failure_hint")),
            ], indent=2))
            expected = label.get("expected_plan")
            if expected is not None:
                deps.out.write("  expected_plan")
                for line in plan_lines(expected, indent=4):
                    deps.out.write(line)
        else:
            deps.out.write("  (unlabelled — `ganglion label add`)")

        attribution = body.get("attribution")
        deps.out.blank()
        deps.out.write("attribution (F⁰ = model alone, Fᴷ = catalog hooks applied)")
        if isinstance(attribution, Mapping):
            deps.out.write(kv([
                ("f0_ok", attribution.get("f0_ok")),
                ("fk_ok", attribution.get("fk_ok")),
                ("rescued", attribution.get("rescued")),
                ("regressed", attribution.get("regressed")),
                ("rescued_by", attribution.get("rescued_by")),
                ("regressed_by", attribution.get("regressed_by")),
                ("necessary_hooks", attribution.get("necessary_hooks")),
                ("active_hooks", attribution.get("active_hooks")),
                ("unattributable", attribution.get("unattributable")),
            ], indent=2))
        else:
            deps.out.write("  (none — `ganglion run analyze`)")

    return deps.out.emit(payload, render)


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register the ``trace`` noun."""
    trace = sub.add_parser(
        "trace",
        help="list | show",
        description="Traces of one run bundle (GET /api/runs/{catalog}/{run}/traces…).",
    )
    verbs = trace.add_subparsers(dest="verb", metavar="<verb>", required=True)

    listing = verbs.add_parser("list", parents=[common], help="filterable trace table + the counts line")
    listing.add_argument("run_id", nargs="?", default=None, help="default: the context run (`ganglion use --run`)")
    listing.add_argument("--filter", choices=FILTERS, default="all", help="route-side row filter (default all)")
    listing.add_argument("--failure-type", default=None, metavar="T",
                         help="keep one classified failure_type")
    listing.add_argument("--parent", default=None, metavar="RUN",
                         help="compare baseline the `direction` column reads (default: the manifest's parent_run_id)")
    listing.add_argument("--limit", type=int, default=None, metavar="N", help="page size (route default 200)")
    listing.add_argument("--offset", type=int, default=None, metavar="N", help="page offset (default 0)")
    listing.set_defaults(_handler=_cmd_trace_list)

    show = verbs.add_parser("show", parents=[common], help="one trace: plans, classification, label, attribution")
    show.add_argument("trace_id", help="a trace_id from `ganglion trace list`")
    show.set_defaults(_handler=_cmd_trace_show)

