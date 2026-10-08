"""``ganglion run`` + ``ganglion compare`` — [[cli_operator]] §Scope.

Verbs: ``run list``, ``run show``, ``run analyze``, ``run events``, and the
top-level ``compare``.

Routes:
    ``GET  /api/runs?catalog_id=``          run list
    ``GET  /api/runs/{c}/{r}``              run show
    ``POST /api/runs/{c}/{r}/analyze``      run analyze      (W)
    ``GET  /api/runs/{c}/{r}/events``       run events
    ``GET  /api/compare``                   compare          (W†)

``compare`` is registered here because it is the second half of the same
join: it addresses two run ids of one catalog and its artifact
(``compare-<run_a>.json``) lives in the run bundle ``run show`` lists under
``compares``. It is *W†*, not a projection — the route persists that file
through the writer.

Nothing below decides anything: the histogram, the corrections summary, the
precision figures, the transition matrix and the CI all arrive computed by
the primitive behind the route ([[analyzer_analyze]], [[analyzer_compare]],
[[analyzer_patch_decision]]).
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from ganglion.ctl.deps import Deps
from ganglion.ctl.render import Column, bar, fmt, kv, table, truncate

__all__ = ["add_parser"]

#: Manifest fields worth a line of a terminal, in reading order. Anything the
#: manifest gained since is appended (non-empty only) so a new field is
#: visible without editing this list.
_MANIFEST_KEYS: tuple[str, ...] = (
    "run_id",
    "catalog_id",
    "catalog_fingerprint",
    "model_id",
    "model_fingerprint",
    "benchmark",
    "metric_kind",
    "client_kind",
    "n_cases",
    "limit",
    "iteration",
    "parent_run_id",
    "decoding",
    "dataset_path",
    "dataset_sha256",
    "base_model",
    "adapter_dir",
    "git_head",
    "python",
    "accelerator",
    "started_at",
    "finished_at",
)

#: Long hex identities are clipped — a 64-char sha owns a whole terminal line.
_CLIP_KEYS = frozenset({"catalog_fingerprint", "dataset_sha256", "adapter_sha256", "git_head"})

#: ``has_*`` sidecar flags as one column: present → the letter, absent → ``.``.
_SIDECARS: tuple[tuple[str, str], ...] = (
    ("has_classified", "c"),
    ("has_patches", "p"),
    ("has_corrections", "x"),
)

_SIDECAR_LEGEND = "c=classified  p=patches  x=corrections  +N=compares"
_MARK_LEGEND = "*=context run"

#: ``run show`` prints the tail of a tail; the route already caps at 20.
_EVENTS_IN_DETAIL = 8

_EVENT_PAYLOAD_WIDTH = 52


# ---------------------------------------------------------------------------
# shared cell helpers
# ---------------------------------------------------------------------------


def _headline(row: Any) -> Any:
    """The run's headline metric out of its persisted ``summary.json``."""
    summary = row.get("summary") if isinstance(row, Mapping) else None
    if not isinstance(summary, Mapping):
        return None
    return summary.get("exact_match_rate")


def _sidecar_cell(row: Any) -> str:
    if not isinstance(row, Mapping):
        return ""
    letters = "".join(letter if row.get(key) else "." for key, letter in _SIDECARS)
    compares = row.get("has_compare") or ()
    return f"{letters}+{len(compares)}" if compares else letters


def _event_payload(row: Any) -> str:
    """``k=v k=v`` over an event's payload — the column, not the event."""
    payload = row.get("payload") if isinstance(row, Mapping) else None
    if not isinstance(payload, Mapping) or not payload:
        return ""
    return " ".join(f"{key}={fmt(payload[key])}" for key in sorted(payload))


_EVENT_COLUMNS = (
    Column("ts", "ts"),
    Column("name", "name"),
    Column("producer", "producer"),
    Column("payload", _event_payload, max_width=_EVENT_PAYLOAD_WIDTH),
)


def _kv_pairs(body: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Scalar rows of a flat-ish dict; nested containers become a count."""
    pairs: list[tuple[str, Any]] = []
    for key in sorted(body):
        value = body[key]
        if isinstance(value, Mapping):
            pairs.append((f"{key}[n]", len(value)))
        elif isinstance(value, (list, tuple)):
            pairs.append((f"{key}[n]", len(value)))
        else:
            pairs.append((key, value))
    return pairs


def _histogram_rows(hist: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Non-zero histogram rows, biggest first, taxonomy order breaking ties."""
    rows = [(name, int(count or 0)) for name, count in hist.items() if int(count or 0) > 0]
    rows.sort(key=lambda row: -row[1])
    return rows


# ---------------------------------------------------------------------------
# run list
# ---------------------------------------------------------------------------


def _cmd_run_list(args: argparse.Namespace, deps: Deps) -> int:
    # No catalog in context is not an error here: the route then lists every
    # catalog's runs, and the rendering grows a catalog column.
    catalog = deps.ctx.catalog
    payload = deps.api.get("/api/runs", catalog_id=catalog)

    def render(body: Any) -> None:
        rows = body.get("runs") or []
        columns: list[Column] = []
        if deps.ctx.run:  # an empty marker column would indent every row for nothing
            columns.append(Column("", lambda row: "*" if row.get("run_id") == deps.ctx.run else ""))
        if not catalog:
            columns.append(Column("catalog", "catalog_id"))
        columns += [
            Column("run_id", "run_id"),
            Column("it", "iteration", align=">"),
            Column("model_id", "model_id"),
            Column("traces", "n_traces", align=">"),
            Column("labels", "n_labels", align=">"),
            Column("em", _headline, align=">"),
            Column("cpx", _sidecar_cell),
        ]
        deps.out.write(table(rows, columns, empty="(no runs — `python -m ganglion.console seed --runs <dir>`)"))
        if rows:
            deps.out.blank()
            legend = [_SIDECAR_LEGEND] + ([_MARK_LEGEND] if deps.ctx.run else [])
            deps.out.write("  ".join(legend))

    return deps.out.emit(payload, render, rows_key="runs")


# ---------------------------------------------------------------------------
# run show
# ---------------------------------------------------------------------------


def _cmd_run_show(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    run_id = args.run_id or deps.ctx.require("run")
    payload = deps.api.get(f"/api/runs/{catalog}/{run_id}")

    def render(body: Any) -> None:
        manifest = body.get("manifest") or {}
        pairs: list[tuple[str, Any]] = []
        for key in _MANIFEST_KEYS:
            if key not in manifest:
                continue
            value = manifest[key]
            pairs.append((key, truncate(fmt(value), 16) if key in _CLIP_KEYS else value))
        extra = [
            (key, manifest[key])
            for key in sorted(set(manifest) - set(_MANIFEST_KEYS))
            if manifest[key] not in (None, "", {}, [])
        ]
        deps.out.write("manifest")
        deps.out.write(kv(pairs + extra, indent=2))

        summary = body.get("summary")
        deps.out.blank()
        deps.out.write("summary")
        if isinstance(summary, Mapping):
            deps.out.write(kv(_kv_pairs(summary), indent=2))
        else:
            deps.out.write(kv([("summary", None), ("n_traces", body.get("n_traces")),
                               ("n_labels", body.get("n_labels"))], indent=2))

        hist = body.get("histogram") or {}
        rows = _histogram_rows(hist)
        total = sum(int(count or 0) for count in hist.values())
        deps.out.blank()
        deps.out.write(f"failures  ({total} classified of {body.get('n_traces')} traces)")
        if rows:
            deps.out.write(table(
                rows,
                (
                    Column("failure_type", lambda row: row[0]),
                    Column("n", lambda row: row[1], align=">"),
                    Column("", lambda row: bar(row[1], total)),
                ),
            ))
        else:
            deps.out.write("  (no classification — `ganglion run analyze`)")

        corrections = body.get("corrections")
        deps.out.blank()
        deps.out.write("corrections (F⁰/Fᴷ)")
        if isinstance(corrections, Mapping):
            deps.out.write(kv(_kv_pairs(corrections), indent=2))
        else:
            deps.out.write("  (none — `ganglion run analyze`)")

        precision = body.get("precision")
        deps.out.blank()
        deps.out.write("patch precision")
        if isinstance(precision, Mapping):
            deps.out.write(kv(_kv_pairs(precision), indent=2))
        else:
            deps.out.write("  (none)")

        compares = body.get("compares") or []
        deps.out.blank()
        deps.out.write(kv([("labels", body.get("n_labels")), ("compares", ", ".join(map(str, compares)) or None)]))

        events = body.get("events_tail") or []
        shown = events[-_EVENTS_IN_DETAIL:]
        deps.out.blank()
        deps.out.write(f"events  (last {len(shown)} of {len(events)} in the tail)")
        deps.out.write(table(shown, _EVENT_COLUMNS, empty="  (none)"))

    return deps.out.emit(payload, render)


# ---------------------------------------------------------------------------
# run analyze
# ---------------------------------------------------------------------------


def _cmd_run_analyze(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    run_id = args.run_id or deps.ctx.require("run")
    payload = deps.api.post(f"/api/runs/{catalog}/{run_id}/analyze")

    def render(body: Any) -> None:
        deps.out.write(f"analyzed  {catalog}/{run_id}")
        deps.out.blank()
        deps.out.write("classify")
        deps.out.write(kv([
            ("traces", body.get("n_traces")),
            ("classified", body.get("n_classified")),
        ], indent=2))
        hist = body.get("histogram") or {}
        rows = _histogram_rows(hist)
        if rows:
            deps.out.write(kv([(name, count) for name, count in rows], indent=4))

        corrections = body.get("corrections") or {}
        deps.out.blank()
        deps.out.write("attribute")
        deps.out.write(kv([
            (key, corrections.get(key))
            for key in ("n", "em_f0", "em_fk", "n_rescued", "n_regressed",
                        "unattributable", "n_retire_candidates")
        ], indent=2))

        deps.out.blank()
        deps.out.write("synthesise")
        deps.out.write(kv([("patches", body.get("n_patches"))], indent=2))

        paths = body.get("paths") or {}
        if paths:
            deps.out.blank()
            deps.out.write("wrote")
            deps.out.write(kv(sorted(paths.items()), indent=2))

    return deps.out.emit(payload, render)


# ---------------------------------------------------------------------------
# run events
# ---------------------------------------------------------------------------


def _cmd_run_events(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    run_id = args.run_id or deps.ctx.require("run")
    payload = deps.api.get(f"/api/runs/{catalog}/{run_id}/events")

    def render(body: Any) -> None:
        events = list(body.get("events") or [])
        total = len(events)
        if args.name:
            events = [row for row in events if row.get("name") == args.name]
        matched = len(events)
        events = events[-args.tail:] if args.tail > 0 else events
        deps.out.write(table(events, _EVENT_COLUMNS, empty="(no events)"))
        if matched != total or len(events) != matched:
            deps.out.blank()
            deps.out.write(f"showing {len(events)} of {matched} matched ({total} in the ledger)")

    return deps.out.emit(payload, render, rows_key="events")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


_PER_CASE_COLUMNS = (
    Column("case_id", "case_id"),
    Column("a", "status_a"),
    Column("b", "status_b"),
    Column("direction", "direction"),
    Column("failure_a", "failure_type_a"),
    Column("failure_b", "failure_type_b"),
)


def _cmd_compare(args: argparse.Namespace, deps: Deps) -> int:
    catalog = deps.ctx.require("catalog")
    payload = deps.api.get(
        "/api/compare",
        catalog_id=catalog,
        run_a=args.run_a,
        run_b=args.run_b,
        allow_diff=args.allow_diff,
    )

    def render(body: Any) -> None:
        deps.out.write(kv([
            ("catalog", body.get("catalog_id")),
            ("run_a", body.get("run_a")),
            ("run_b", body.get("run_b")),
            ("cases", body.get("n")),
            ("graded", body.get("n_graded")),
        ]))

        counts = body.get("counts") or {}
        joined = sum(int(count or 0) for count in counts.values())
        deps.out.blank()
        deps.out.write("transitions")
        deps.out.write(table(
            list(counts.items()),
            (
                Column("direction", lambda row: row[0]),
                Column("n", lambda row: row[1], align=">"),
                Column("", lambda row: bar(row[1], joined)),
            ),
            empty="  (none)",
        ))

        ci = body.get("ci95")
        ci_text = f"95% CI [{fmt(ci[0])}, {fmt(ci[1])}]" if isinstance(ci, (list, tuple)) and len(ci) == 2 else "95% CI -"
        deps.out.blank()
        deps.out.write(
            f"ΔEM {fmt(body.get('em_delta'))}  ({fmt(body.get('em_a'))} → {fmt(body.get('em_b'))})  "
            f"{ci_text}  bootstrap {fmt(body.get('bootstrap'))} seed {fmt(body.get('seed'))}"
        )

        diff = body.get("manifest_diff") or {}
        deps.out.blank()
        deps.out.write("manifest_diff")
        if diff:
            deps.out.write(kv(
                [(key, f"a={fmt(value.get('a'))}  b={fmt(value.get('b'))}"
                  if isinstance(value, Mapping) else value) for key, value in sorted(diff.items())],
                indent=2,
            ))
        else:
            deps.out.write("  (identical on the compared fields)")

        warnings = body.get("warnings") or []
        deps.out.blank()
        deps.out.write("warnings")
        deps.out.write(kv([(f"[{i}]", text) for i, text in enumerate(warnings)], indent=2) or "  (none)")

        if args.per_case:
            deps.out.blank()
            deps.out.write("per case")
            deps.out.write(table(list(body.get("per_case") or []), _PER_CASE_COLUMNS, empty="  (none)"))

        deps.out.blank()
        deps.out.write(kv([("wrote", body.get("path"))]))

    return deps.out.emit(payload, render, rows_key="per_case")


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register the ``run`` noun and the top-level ``compare`` command."""
    run = sub.add_parser(
        "run",
        help="list | show | analyze | events",
        description="Run bundles under the runs dir (GET /api/runs…).",
    )
    verbs = run.add_subparsers(dest="verb", metavar="<verb>", required=True)

    listing = verbs.add_parser("list", parents=[common], help="runs of the context catalog, or of all")
    listing.set_defaults(_handler=_cmd_run_list)

    show = verbs.add_parser("show", parents=[common], help="manifest, summary, failure histogram, sidecars")
    show.add_argument("run_id", nargs="?", default=None, help="default: the context run (`ganglion use --run`)")
    show.set_defaults(_handler=_cmd_run_show)

    analyze = verbs.add_parser("analyze", parents=[common],
                               help="classify + attribute + synthesise (writes the sidecars)")
    analyze.add_argument("run_id", nargs="?", default=None, help="default: the context run")
    analyze.set_defaults(_handler=_cmd_run_analyze)

    events = verbs.add_parser("events", parents=[common], help="the run's ledger rows")
    events.add_argument("run_id", nargs="?", default=None, help="default: the context run")
    events.add_argument("--tail", type=int, default=20, metavar="N",
                        help="last N rows (human output only; --json prints every event)")
    events.add_argument("--name", default=None, metavar="EVENT",
                        help="keep one event name (human output only; --json prints every event)")
    events.set_defaults(_handler=_cmd_run_events)

    compare = sub.add_parser(
        "compare",
        parents=[common],
        help="join two runs of one catalog on case_id",
        description="GET /api/compare — persists compare-<run_a>.json in run_b's bundle.",
    )
    compare.add_argument("run_a", help="the baseline run")
    compare.add_argument("run_b", help="the run being judged")
    compare.add_argument("--allow-diff", action="store_true",
                         help="compare anyway when dataset_sha256 / metric_kind / decoding differ")
    compare.add_argument("--per-case", action="store_true",
                         help="also print the per-case table (human output only; --json always carries it)")
    compare.set_defaults(_handler=_cmd_compare)
