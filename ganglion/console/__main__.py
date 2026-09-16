"""``python -m ganglion.console <subcommand>`` ([[console_operator]] §Scope).

Subcommands:

``serve``          bind the console on ``127.0.0.1`` (default port 8766 /
                   ``$GANGLION_CONSOLE_PORT``).
``seed``           write ``rules-seed`` + ``rules-degraded-seed`` and analyse
                   both, so every page has real data offline (errata E8).
``analyze``        ``analyze_run`` on one ``<catalog_id> <run_id>``.
``export-labels``  corrected-SFT / hard-pool export for one catalog.
``compare``        ``compare_runs`` on two runs of one catalog.

Every subcommand prints JSON to stdout (``serve`` prints a banner to stdout
and then blocks), so the same surface is scriptable without the HTTP layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ganglion.analyzer.analyze import analyze_run
from ganglion.analyzer.compare import compare_runs
from ganglion.console.api import ConsoleAPI
from ganglion.console.seed import seed_runs
from ganglion.console.server import DEFAULT_HOST, default_web_dir, serve

DEFAULT_RUNS = "runs/traces"


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ganglion.console",
        description="Ganglion operator console (docs/tasks/console_operator.md)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_cmd = sub.add_parser("serve", help="serve web/ + /api/* on 127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=None, help="default: $GANGLION_CONSOLE_PORT or 8766")
    serve_cmd.add_argument("--host", default=DEFAULT_HOST, help="default: 127.0.0.1 (loopback only)")
    serve_cmd.add_argument("--runs", default=DEFAULT_RUNS)
    serve_cmd.add_argument("--models", default=None, help="registry yaml (default: configs/models.yaml)")
    serve_cmd.add_argument("--web", default=None, help="static dir (default: <repo>/web)")

    seed_cmd = sub.add_parser("seed", help="write the two offline seed runs and analyse them")
    seed_cmd.add_argument("--runs", default=DEFAULT_RUNS)
    seed_cmd.add_argument("--catalog", default="iot_light_5")
    seed_cmd.add_argument("--limit", type=int, default=100, help="dataset rows per file (default 100)")
    seed_cmd.add_argument("--no-analyze", action="store_true", help="write the runs without analysing")
    seed_cmd.add_argument("--no-compare", action="store_true", help="skip the seed-vs-degraded compare")

    analyze_cmd = sub.add_parser("analyze", help="classify + attribute + synthesise one run")
    analyze_cmd.add_argument("catalog_id")
    analyze_cmd.add_argument("run_id")
    analyze_cmd.add_argument("--runs", default=DEFAULT_RUNS)

    export_cmd = sub.add_parser("export-labels", help="human_sft.jsonl / hard_pool.jsonl for a catalog")
    export_cmd.add_argument("catalog_id")
    export_cmd.add_argument("--runs", default=DEFAULT_RUNS)
    export_cmd.add_argument("--zones", default="train")

    compare_cmd = sub.add_parser("compare", help="join two runs of one catalog on case_id")
    compare_cmd.add_argument("catalog_id")
    compare_cmd.add_argument("run_a")
    compare_cmd.add_argument("run_b")
    compare_cmd.add_argument("--runs", default=DEFAULT_RUNS)
    compare_cmd.add_argument("--allow-diff", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        serve(
            base_dir=args.runs,
            web_dir=Path(args.web) if args.web else default_web_dir(),
            models_path=args.models,
            host=args.host,
            port=args.port,
        )
        return 0

    if args.command == "seed":
        result = seed_runs(
            args.runs,
            catalog_id=args.catalog,
            limit=args.limit,
            analyze=not args.no_analyze,
            compare=not args.no_compare,
        )
        # The compare per-case table is large and not useful on a terminal.
        summary = dict(result)
        if summary.get("compare"):
            compare = dict(summary["compare"])
            compare.pop("per_case", None)
            summary["compare"] = compare
        _print(summary)
        return 0

    if args.command == "analyze":
        _print(analyze_run(args.runs, args.catalog_id, args.run_id))
        return 0

    if args.command == "export-labels":
        api = ConsoleAPI(base_dir=args.runs, web_dir=default_web_dir())
        try:
            status, payload = api.handle(
                "GET",
                "/api/export/labels",
                {"catalog_id": [args.catalog_id], "zones": [args.zones]},
                None,
            )
        finally:
            api.close()
        _print(payload)
        return 0 if status == 200 else 1

    if args.command == "compare":
        try:
            result = compare_runs(
                args.runs, args.catalog_id, args.run_a, args.run_b, allow_diff=args.allow_diff,
            )
        except ValueError as exc:
            _print({"error": "compare_refused", "detail": str(exc)})
            return 1
        payload = result.to_dict()
        payload.pop("per_case", None)
        _print(payload)
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover — module entry point
    sys.exit(main())
