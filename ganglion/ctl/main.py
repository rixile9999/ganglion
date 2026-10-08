"""``ganglion`` argv routing ([[cli_operator]] §Procedure).

Noun-verb subcommands over the [[console_operator]] route table. Every **leaf**
parser inherits :func:`common_parser` — the noun parsers deliberately do not,
because a noun-level default would shadow a value given at the leaf.

A handler is ``(argparse.Namespace, Deps) -> int``; command modules register
theirs with ``set_defaults(_handler=…)``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from ganglion import __version__
from ganglion.ctl.bridge import ApiBridge
from ganglion.ctl.commands import catalogs, chat, labels, models, patches, runs, traces, programs
from ganglion.ctl.context import FIELDS, clear_context, load_context, save_context
from ganglion.ctl.deps import Deps
from ganglion.ctl.errors import CtlError
from ganglion.ctl.render import Output, kv

__all__ = ["build_parser", "common_parser", "main"]

#: Registration order = the order nouns appear in ``ganglion --help``.
_MODULES = (catalogs, models, chat, runs, traces, labels, patches, programs)

_EPILOG = """\
addressing:
  Most commands take their catalog / model / run from the context instead of a
  flag. Set it once:

    ganglion use --catalog iot_light_5 --model rules
    ganglion run list
    ganglion ask "거실 불 켜줘"

  Resolution per field is flag > environment > .ganglion/context.json > default.

output:
  --json   the /api/* payload verbatim (the machine contract)
  --jsonl  one JSON object per row
"""


def common_parser() -> argparse.ArgumentParser:
    """The parent parser every leaf inherits: context flags + output mode."""
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group("context")
    group.add_argument("--runs", default=None, metavar="DIR",
                       help="runs dir ($GANGLION_RUNS, context, else runs/traces)")
    group.add_argument("--models", default=None, metavar="PATH",
                       help="registry yaml ($GANGLION_MODELS, else configs/models.yaml)")
    group.add_argument("--catalog", default=None, metavar="ID", help="catalog_id")
    group.add_argument("--model", default=None, metavar="ID", help="registry model_id")
    group.add_argument("--session", default=None, metavar="ID", help="chat session_id")
    group.add_argument("--run", default=None, metavar="ID", help="run_id")
    group.add_argument("--labeler", default=None, metavar="NAME",
                       help="author recorded on writes ($GANGLION_LABELER, else operator)")
    output = parser.add_argument_group("output").add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print the route payload verbatim")
    output.add_argument("--jsonl", action="store_true", help="print one JSON object per row")
    return parser


# ---------------------------------------------------------------------------
# health + use (the two commands with no noun)
# ---------------------------------------------------------------------------


def _cmd_health(args: argparse.Namespace, deps: Deps) -> int:
    payload = deps.api.get("/api/health")

    def render(body: dict) -> None:
        deps.out.write(kv([
            ("ok", body.get("ok")),
            ("version", body.get("version")),
            ("runs_dir", body.get("runs_dir")),
            ("web_dir", body.get("web_dir")),
            ("registry_path", body.get("registry_path")),
        ]))
        deps.out.blank()
        deps.out.write("context")
        deps.out.write(kv([(f, getattr(deps.ctx, f) or "-") for f in FIELDS], indent=2))
        deps.out.write(kv([("file", deps.ctx.path)], indent=2))

    return deps.out.emit(payload, render)


def _cmd_use(args: argparse.Namespace, deps: Deps) -> int:
    patch = {field: getattr(args, field, None) for field in FIELDS}
    if args.runs is not None:
        patch["runs_dir"] = args.runs
    if args.clear:
        ctx = clear_context(deps.ctx)
    elif any(value is not None for value in patch.values()) and not args.show:
        ctx = save_context(deps.ctx, **patch)
    else:
        ctx = deps.ctx

    def render(body: dict) -> None:
        deps.out.write(kv([(key, value or "-") for key, value in body.items()]))

    return deps.out.emit(ctx.to_dict(), render)


def _add_health(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    parser = sub.add_parser("health", parents=[common],
                            help="console configuration + resolved context")
    parser.set_defaults(_handler=_cmd_health)


def _add_use(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    parser = sub.add_parser("use", parents=[common],
                            help="persist catalog / model / session / run to .ganglion/context.json")
    parser.add_argument("--clear", action="store_true", help="empty the four addressing fields")
    parser.add_argument("--show", action="store_true", help="print the resolved context without writing")
    parser.set_defaults(_handler=_cmd_use)


# ---------------------------------------------------------------------------
# parser + entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """The whole ``ganglion`` surface; every leaf carries ``_handler``."""
    common = common_parser()
    parser = argparse.ArgumentParser(
        prog="ganglion",
        description="Operator CLI over the Ganglion console routes (docs/tasks/cli_operator.md)",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"ganglion {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    _add_health(sub, common)
    _add_use(sub, common)
    for module in _MODULES:
        module.add_parser(sub, common)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, resolve context, dispatch; ``CtlError`` → stderr + exit 1."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        # Defensive: every noun registers its verbs `required=True`, so argparse
        # already exited 2 for a verb-less noun. A handler-less leaf would be a
        # registration bug, and must not be reported as a successful run.
        parser.error(f"{args.command}: no subcommand handler registered")

    mode = "json" if getattr(args, "json", False) else "jsonl" if getattr(args, "jsonl", False) else "human"
    out = Output(mode)
    bridge: ApiBridge | None = None
    try:
        ctx = load_context(
            catalog=args.catalog,
            model=args.model,
            session=args.session,
            run=args.run,
            runs_dir=args.runs,
            models_path=args.models,
        )
        bridge = ApiBridge(ctx.runs_dir, models_path=ctx.models_path, labeler=args.labeler)
        return int(handler(args, Deps(api=bridge, ctx=ctx, out=out)))
    except CtlError as exc:
        print(exc.stderr_line(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":  # pragma: no cover — module entry point
    sys.exit(main())
