"""``ganglion catalog`` — list | show | compile ([[cli_operator]] §Scope).

One route per verb, and nothing else:

    ``GET  /api/catalogs``                catalog list
    ``GET  /api/catalogs/{catalog_id}``   catalog show
    ``POST /api/catalogs/compile``        catalog compile   (W)

``show`` prints the route's six artifacts — the ``describe`` snapshot the
fingerprint is taken over, the DSL block, the OpenAI tool schema, the assembled
system prompt, the grammar schema and a compiled catalog's source tools
([[contract_describe]], [[analyzer_catalogs]]). ``--part`` only chooses which of
them a *terminal* gets; the payload is one object and ``--json`` prints all of
it, so a script never binds to this module's projection.

Nothing here validates or compiles anything: ``compile`` hands the parsed
document to the route, whose primitive
(``ganglion.contract.schema_compiler``) is the only thing that judges it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from ganglion.ctl.deps import Deps
from ganglion.ctl.errors import CtlError
from ganglion.ctl.render import Column, kv, table

__all__ = ["add_parser"]

#: ``show --part`` choices in help order; ``all`` is the operator default.
PARTS: tuple[str, ...] = ("all", "describe", "dsl", "tools", "prompt", "schema", "source")

#: ``--name`` default when the tool list arrives on stdin (the route's own default).
_STDIN_LABEL = "compiled_tools"

#: ``describe_tool`` key → the letter-ish label of the post-correction hook it
#: reports. ``defaults_when_missing`` is a list, the other three are booleans
#: ([[contract_describe]]); an empty list is "no hook" for both.
_HOOK_LABELS: tuple[tuple[str, str], ...] = (
    ("defaults_when_missing", "defaults"),
    ("strip_unknown_args", "strip"),
    ("prompt_correction", "prompt"),
    ("custom_validator", "validator"),
)

_ARGS_WIDTH = 52
_DESCRIPTION_WIDTH = 38


# ---------------------------------------------------------------------------
# shared cell helpers
# ---------------------------------------------------------------------------


def _json_block(value: Any) -> str:
    """A payload fragment, formatted exactly as ``--json`` formats the whole."""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _arg_label(entry: Any) -> str:
    """``room:enum`` for a required arg, ``brightness:integer?`` for an optional one."""
    if not isinstance(entry, Mapping):
        return "?"
    label = f"{entry.get('name', '?')}:{entry.get('kind', '?')}"
    return label if entry.get("required") else label + "?"


def _args_cell(tool: Any) -> str | None:
    if not isinstance(tool, Mapping):
        return None
    args = tool.get("args") or ()
    return ", ".join(_arg_label(arg) for arg in args) or None


def _hooks_cell(tool: Any) -> str | None:
    if not isinstance(tool, Mapping):
        return None
    return " ".join(label for key, label in _HOOK_LABELS if tool.get(key)) or None


_TOOL_COLUMNS = (
    Column("tool", "name"),
    Column("args", _args_cell, max_width=_ARGS_WIDTH),
    Column("hooks", _hooks_cell),
    Column("description", "description", max_width=_DESCRIPTION_WIDTH),
)


def _tools_of(describe: Any) -> list[Any]:
    tools = describe.get("tools") if isinstance(describe, Mapping) else None
    return list(tools) if isinstance(tools, (list, tuple)) else []


# ---------------------------------------------------------------------------
# catalog list
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace, deps: Deps) -> int:
    payload = deps.api.get("/api/catalogs")

    def render(body: Any) -> None:
        rows = [row for row in (body.get("catalogs") or ()) if isinstance(row, Mapping)]
        columns: list[Column] = []
        if deps.ctx.catalog:  # an all-empty marker column would indent every row for nothing
            columns.append(Column("", lambda row: "*" if row.get("catalog_id") == deps.ctx.catalog else ""))
        columns += [
            Column("catalog_id", "catalog_id"),
            Column("tools", "n_tools", align=">"),
            Column("empty_ok", "allow_empty_calls"),
            Column("fingerprint", "fingerprint"),
            Column("source", "source"),
        ]
        if any(row.get("label") for row in rows):  # only compiled rows carry a label
            columns.append(Column("label", lambda row: row.get("label") or None, max_width=24))
        deps.out.write(table(rows, columns, empty="(no catalogs)"))
        if deps.ctx.catalog:
            deps.out.blank()
            deps.out.write("*=context catalog")

    return deps.out.emit(payload, render, rows_key="catalogs")


# ---------------------------------------------------------------------------
# catalog show
# ---------------------------------------------------------------------------


def _render_part(part: str, body: Mapping[str, Any], deps: Deps) -> bool:
    """Print one single-artifact ``--part``; ``False`` when ``part`` is ``all``."""
    if part == "describe":
        deps.out.write(_json_block(body.get("describe")))
    elif part == "dsl":
        deps.out.write(body.get("json_dsl") or "")
    elif part == "tools":
        deps.out.write(_json_block(body.get("openai_tools")))
    elif part == "prompt":
        deps.out.write(body.get("system_prompt") or "")
    elif part == "schema":
        schema = body.get("json_schema")
        deps.out.write(
            _json_block(schema) if schema is not None
            else "(no json_schema — the grammar compiler is unavailable here)"
        )
    elif part == "source":
        tools = body.get("source_tools")
        deps.out.write(
            _json_block(tools) if tools is not None
            else "(no source_tools — a builtin tier is declared in code, not compiled from a schema)"
        )
    else:
        return False
    return True


def _cmd_show(args: argparse.Namespace, deps: Deps) -> int:
    catalog_id = args.catalog_id or deps.ctx.require("catalog")
    # `compiled/<sha12>` and `bfcl/<category>` carry one slash: `ConsoleAPI.handle`
    # splits the path itself and knows both two-segment prefixes, so the id goes
    # in raw — percent-encoding it would hide the second segment from the router.
    payload = deps.api.get(f"/api/catalogs/{catalog_id}")

    def render(body: Any) -> None:
        if _render_part(args.part, body, deps):
            return
        describe = body.get("describe") or {}
        deps.out.write(kv([
            ("catalog_id", body.get("catalog_id")),
            ("fingerprint", body.get("fingerprint")),
            ("source", body.get("source")),
            ("name", describe.get("name")),
            ("tools", len(_tools_of(describe))),
            ("allow_empty_calls", describe.get("allow_empty_calls")),
        ]))
        deps.out.blank()
        deps.out.write(table(_tools_of(describe), _TOOL_COLUMNS, empty="(no tools)"))
        deps.out.blank()
        deps.out.write("json_dsl (appended to the system prompt)")
        deps.out.write(body.get("json_dsl") or "")

    return deps.out.emit(payload, render, rows_key="openai_tools")


# ---------------------------------------------------------------------------
# catalog compile
# ---------------------------------------------------------------------------


def _read_json_document(path: str) -> Any:
    """The parsed document at ``path`` (``-`` = stdin); local problems are ``bad_input``.

    An unreadable file or a syntax error is this side of the seam and must never
    reach the route ([[cli_operator]] §Contract.failure); a tool list the
    compiler rejects is the route's own ``422 invalid_tools``.
    """
    stdin = path == "-"
    where = "<stdin>" if stdin else path
    if stdin and sys.stdin is None:  # the process was started with fd 0 closed
        raise CtlError(400, "bad_input", f"{where}: no stdin to read")
    try:
        raw = sys.stdin.read() if stdin else Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # `ValueError` catches a non-UTF-8 byte (`UnicodeDecodeError`) and a
        # closed stream. Both are "unreadable", and letting either escape would
        # put a traceback on stderr where only `error: <slug>` belongs.
        raise CtlError(400, "bad_input", f"{where}: unreadable — {exc}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise CtlError(400, "bad_input", f"{where}: not JSON — {exc}") from exc


def _cmd_compile(args: argparse.Namespace, deps: Deps) -> int:
    document = _read_json_document(args.tools_file)
    name = args.name or (_STDIN_LABEL if args.tools_file == "-" else Path(args.tools_file).stem)
    payload = deps.api.post("/api/catalogs/compile", {
        "name": name,
        # The route accepts a bare list or a `{"tools": [...]}` wrapper, so the
        # document goes through as parsed; unwrapping it here would fork the
        # contract and silently accept shapes the browser console rejects.
        "tools": document,
        "allow_empty_calls": bool(args.allow_empty_calls),
    })

    def render(body: Any) -> None:
        catalog_id = body.get("catalog_id")
        deps.out.write(kv([
            ("catalog_id", catalog_id),
            ("fingerprint", body.get("fingerprint")),
            ("n_tools", body.get("n_tools")),
        ]))
        deps.out.blank()
        deps.out.write(f"ganglion use --catalog {catalog_id}")

    return deps.out.emit(payload, render)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    """Register the ``catalog`` noun. Only the leaves inherit ``common``."""
    catalog = sub.add_parser(
        "catalog",
        help="list | show | compile",
        description="Catalogs resolvable by id, and the artifacts they render (GET /api/catalogs…).",
    )
    verbs = catalog.add_subparsers(dest="verb", metavar="<verb>", required=True)

    listing = verbs.add_parser("list", parents=[common], help="builtin tiers + compiled catalogs")
    listing.set_defaults(_handler=_cmd_list)

    show = verbs.add_parser("show", parents=[common], help="fingerprint, tools, DSL, prompt, schema")
    show.add_argument("catalog_id", nargs="?", default=None,
                      help="default: the context catalog (`ganglion use --catalog`)")
    show.add_argument("--part", choices=PARTS, default="all",
                      help="which artifact to print (human output only; --json prints the whole payload)")
    show.set_defaults(_handler=_cmd_show)

    compile_ = verbs.add_parser("compile", parents=[common],
                                help="compile an external tool schema into compiled/<sha12>")
    compile_.add_argument("tools_file", metavar="TOOLS.json",
                          help="OpenAI / MCP / bare function schema list, or '-' for stdin")
    compile_.add_argument("--name", default=None, metavar="NAME",
                          help="label stored with the catalog (default: the file stem)")
    compile_.add_argument("--allow-empty-calls", action="store_true",
                          help='accept {"calls": []} as a valid plan (the abstention contract)')
    compile_.set_defaults(_handler=_cmd_compile)
