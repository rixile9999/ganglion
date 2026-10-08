"""``ganglion patch`` — the rule-lifecycle review path ([[cli_operator]] §Scope).

Verbs: ``list`` → ``GET /api/runs/{c}/{r}/patches``; ``decide`` →
``POST /api/patches/{id}/decision``; ``ported`` →
``POST /api/patches/{id}/ported``.

The proposals, the preview counts and the precision figures are all computed
behind the route ([[analyzer_rule_synthesis]], [[contract_patch_apply]],
[[analyzer_patch_decision]]); this module chooses columns and nothing else.
A decision is a *record*, not an effect: accepting a patch mutates no catalog,
and ``ported`` is how a human code edit gets attached to the proposal it came
from.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable, Mapping

from ganglion.ctl.deps import Deps
from ganglion.ctl.render import Column, bar, kv, table

__all__ = ["add_parser"]

#: Mirror of ``STAGES`` / ``DECISIONS`` in ``ganglion/analyzer/decisions.py``,
#: the source of truth. Hard-coded because a command module may not import a
#: primitive ([[cli_operator]] §Observation ``primitive_imports_in_ctl``); grep
#: those two names if the vocabularies ever diverge. The route re-checks both,
#: so this copy only shapes ``--help`` and the stage ordering below.
STAGES: tuple[str, ...] = ("blind", "after_preview", "ported")
DECISIONS: tuple[str, ...] = ("accept", "hold", "reject", "retire", "ported")


def _evidence(row: Mapping[str, Any], key: str) -> Any:
    evidence = row.get("evidence")
    return evidence.get(key) if isinstance(evidence, Mapping) else None


def _decisions_cell(row: Mapping[str, Any]) -> Any:
    """``blind:accept after_preview:hold`` — the latest decision per stage."""
    stages = row.get("decisions")
    if not isinstance(stages, Mapping) or not stages:
        return None
    ordered = [s for s in STAGES if s in stages] + [s for s in sorted(stages) if s not in STAGES]
    parts = []
    for stage in ordered:
        record = stages.get(stage)
        decision = record.get("decision") if isinstance(record, Mapping) else None
        parts.append(f"{stage}:{decision or '?'}")
    return " ".join(parts)


def _preview_cell(row: Mapping[str, Any]) -> Any:
    """``+2/-0 of 40`` — populated by the route only once a ``blind`` decision exists."""
    preview = row.get("preview")
    if not isinstance(preview, Mapping):
        return None
    if not preview.get("applicable", False):
        return "not applicable"
    return f"+{preview.get('rescues')}/-{preview.get('regressions')} of {preview.get('n_considered')}"


_PATCH_COLUMNS = (
    Column("patch_id", "patch_id"),
    Column("tool", "target_tool"),
    Column("operation", "operation"),
    Column("payload", "payload", max_width=38),
    Column("from", "source_failure_type"),
    Column("fails", lambda r: _evidence(r, "failure_count"), align=">"),
    Column("support", lambda r: _evidence(r, "support_share"), align=">"),
    Column("conf", lambda r: _evidence(r, "confidence"), align=">"),
    Column("decisions", _decisions_cell),
    Column("preview", _preview_cell),
)

_RETIRE_COLUMNS = (
    Column("hook", "hook"),
    Column("arg", "arg"),
    Column("class", "class"),
    Column("active", "n_active", align=">"),
    Column("necessary", "necessary", align=">"),
    Column("rescues", "rescues", align=">"),
    Column("regressions", "regressions", align=">"),
    Column("cp95_upper", "cp95_upper", align=">"),
    Column("verdict", "verdict"),
)


def _cmd_list(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = deps.ctx.require("catalog")
    run_id = args.run_id or deps.ctx.require("run")
    payload = deps.api.get(f"/api/runs/{catalog_id}/{run_id}/patches")

    def render(body: dict) -> None:
        patches = body.get("patches") or []
        deps.out.write(f"{catalog_id}/{run_id} — {len(patches)} proposed patch(es)")
        deps.out.write(table(patches, _PATCH_COLUMNS, empty="(no proposals — run `ganglion run analyze` first)"))

        precision = body.get("precision") or {}
        if precision:
            deps.out.blank()
            deps.out.write("precision")
            deps.out.write(kv([
                ("n_proposed", precision.get("n_proposed")),
                ("accepted_blind", precision.get("accepted_blind")),
                ("accepted_after_preview", precision.get("accepted_after_preview")),
                ("held", precision.get("held")),
                ("rejected", precision.get("rejected")),
                ("retired", precision.get("retired")),
                ("ported", precision.get("ported")),
                ("patch_acceptance_rate", precision.get("patch_acceptance_rate")),
                ("precision_at_conf", precision.get("precision_at_conf")),
                ("conf_threshold", precision.get("conf_threshold")),
                ("n_conf_ge_threshold", precision.get("n_conf_ge_threshold")),
            ], indent=2))
            accepted, proposed = precision.get("accepted_blind"), precision.get("n_proposed")
            meter = bar(accepted, proposed)
            if meter:
                deps.out.write(f"  accepted blind  {meter} {accepted}/{proposed}")
            by_operation = precision.get("by_operation") or {}
            if by_operation:
                deps.out.write(kv(
                    [(op, f"{by_operation[op].get('accepted')}/{by_operation[op].get('proposed')} accepted")
                     for op in sorted(by_operation)],
                    indent=2,
                ))

        deps.out.blank()
        deps.out.write("retire candidates")
        deps.out.write(table(body.get("retire_candidates") or [], _RETIRE_COLUMNS,
                             empty="  (none — no hook rescued nothing over a large enough sample)"))

    # `patches` is the payload's primary list, so `--jsonl` streams proposals.
    return deps.out.emit(payload, render, rows_key="patches")


def _decision_render(
    deps: Deps, heading: str, rows: list[tuple[str, Any]]
) -> Callable[[dict], None]:
    def render(body: dict) -> None:
        deps.out.write(heading)
        deps.out.write(kv(rows + [
            ("decision_id", body.get("decision_id")),
            ("events", len(body.get("event_ids") or [])),
        ], indent=2))

    return render


def _cmd_decide(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = deps.ctx.require("catalog")
    run_id = deps.ctx.require("run")
    body: dict[str, Any] = {
        "catalog_id": catalog_id,
        "run_id": run_id,
        "stage": args.stage,
        "decision": args.decision,
    }
    if args.reason:
        body["reason"] = args.reason
    payload = deps.api.post(f"/api/patches/{args.patch_id}/decision", body)
    return deps.out.emit(payload, _decision_render(
        deps,
        f"decision recorded on {args.patch_id} ({catalog_id}/{run_id})",
        [("stage", args.stage), ("decision", args.decision), ("reason", args.reason)],
    ))


def _cmd_ported(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = deps.ctx.require("catalog")
    run_id = deps.ctx.require("run")
    payload = deps.api.post(
        f"/api/patches/{args.patch_id}/ported",
        {"catalog_id": catalog_id, "run_id": run_id, "commit_sha": args.commit},
    )
    return deps.out.emit(payload, _decision_render(
        deps,
        f"port recorded on {args.patch_id} ({catalog_id}/{run_id})",
        [("stage", "ported"), ("decision", "ported"), ("commit_sha", args.commit)],
    ))


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register ``patch`` and its three verbs; only the leaves inherit ``common``."""
    parser = sub.add_parser("patch", help="list | decide | ported")
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>", required=True)

    listing = verbs.add_parser(
        "list",
        parents=[common],
        help="proposed patches, precision and retire candidates for one run",
        description="A patch's `preview` (rescues / regressions under the patched catalog) "
                    "is filled in by the route only after that patch has a `blind` decision.",
    )
    listing.add_argument("run_id", nargs="?", default=None,
                         help="run to read (default: the context run)")
    listing.set_defaults(_handler=_cmd_list)

    decide = verbs.add_parser(
        "decide",
        parents=[common],
        help="record a review decision on one proposal (POST /api/patches/<id>/decision)",
        description="Accepting a patch changes no catalog — the decision ledger is the only effect. "
                    "Decide `blind` before looking at the preview; `after_preview` once you have.",
    )
    decide.add_argument("patch_id", help="patch to decide, as `ganglion patch list` prints it")
    decide.add_argument("--stage", required=True, choices=STAGES,
                        help="review stage (ganglion/analyzer/decisions.py STAGES)")
    decide.add_argument("--decision", required=True, choices=DECISIONS,
                        help="the verdict (ganglion/analyzer/decisions.py DECISIONS)")
    decide.add_argument("--reason", default=None, metavar="TEXT",
                        help="free-text rationale stored with the decision")
    decide.set_defaults(_handler=_cmd_decide)

    ported = verbs.add_parser(
        "ported",
        parents=[common],
        help="record that a proposal was ported into the catalog by hand",
        description="Accepting a patch changes nothing; porting it is a reviewed human code edit, "
                    "recorded here with the commit that made it.",
    )
    ported.add_argument("patch_id", help="patch that was ported")
    ported.add_argument("--commit", required=True, metavar="SHA",
                        help="commit sha of the code edit that ported this patch")
    ported.set_defaults(_handler=_cmd_ported)
