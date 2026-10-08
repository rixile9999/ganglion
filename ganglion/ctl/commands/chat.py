"""``ganglion ask`` + ``ganglion session new`` — [[cli_operator]] §Scope.

Verbs: ``session new``, ``ask``.
Routes: ``POST /api/sessions``, ``POST /api/chat``.

``ask`` is the **one documented two-call command** in the CLI: it creates a
session when none is resolvable (exactly what ``web/chat.html``'s
``ensureSession()`` does) and then serves the prompt. Any further composition
belongs in a route, not here ([[cli_operator]] §on violation).

The route never fails for a model failure — ``Server.serve`` returns ``200``
with ``error`` set and ``raw`` / ``attempts`` / ``raw_plan`` preserved, because
a failed inference must still be a recordable trace ([[lm_request_serve]]). So
``ask`` reports the error and exits 1 while still printing the trace id: the
trace is on disk either way.
"""

from __future__ import annotations

import argparse
from typing import Any

from ganglion.ctl.context import load_context, save_context
from ganglion.ctl.deps import Deps
from ganglion.ctl.render import fmt, kv, plan_lines

__all__ = ["add_parser"]

#: Body indent for plan / hooks / raw blocks — matches ``plan_lines``' default.
_INDENT = "  "


def _add_repair_flags(parser: argparse.ArgumentParser) -> None:
    """The two decoding flags ``POST /api/sessions`` and ``POST /api/chat`` share."""
    parser.add_argument("--repair", action="store_true",
                        help="enable the M4 repair loop for this session / prompt")
    parser.add_argument("--repair-max-attempts", type=int, default=1, metavar="N",
                        help="repair attempts when --repair is set (default 1)")


def _session_body(args: argparse.Namespace, deps: Deps) -> dict[str, Any]:
    """``POST /api/sessions`` body — catalog + model come from the context."""
    return {
        "catalog_id": deps.ctx.require("catalog"),
        "model_id": deps.ctx.require("model"),
        "repair": bool(args.repair),
        "repair_max_attempts": int(args.repair_max_attempts),
    }


def _persist_session(deps: Deps, payload: dict[str, Any]) -> None:
    """Merge ``session`` / ``run`` into the context file — and nothing else.

    ``save_context`` persists every field of the context it is handed, so
    passing the *resolved* one would promote a one-off ``--catalog`` /
    ``--model`` / ``--runs`` (or a ``GANGLION_*`` variable) into saved state.
    A flag outranks the file for **this invocation only** ([[cli_operator]]
    §Scope), so the stored file is re-read with neither flags nor environment
    and only the two fields this session just produced are written back —
    the same two ``web/chat.html``'s ``ensureSession()`` saves.
    """
    stored = load_context(cwd=deps.ctx.path.parent.parent, env={})
    save_context(stored, session=payload.get("session_id"), run=payload.get("run_id"))


def _open_session(args: argparse.Namespace, deps: Deps) -> dict[str, Any]:
    """Create a session and persist ``session`` / ``run`` to the context file.

    Persisting here is what makes the *next* ``ask`` (and ``trace list``,
    ``label add``) continue the same run bundle without a flag.
    """
    payload = deps.api.post("/api/sessions", _session_body(args, deps))
    _persist_session(deps, payload)
    return payload


# ---------------------------------------------------------------------------
# session new
# ---------------------------------------------------------------------------


def _cmd_session_new(args: argparse.Namespace, deps: Deps) -> int:
    payload = _open_session(args, deps)

    def render(body: dict) -> None:
        deps.out.write(kv([
            ("session_id", body.get("session_id")),
            ("run_id", body.get("run_id")),
            ("manifest_path", body.get("manifest_path")),
        ]))
        deps.out.write(f"saved to {deps.ctx.path} — `ganglion ask …` continues this run")

    return deps.out.emit(payload, render)


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


def _header(payload: dict) -> str:
    attempts = payload.get("attempts")
    return kv([
        ("model", payload.get("model_id")),
        ("catalog", payload.get("catalog_id")),
        ("fingerprint", payload.get("catalog_fingerprint")),
        ("latency_ms", payload.get("latency_ms")),
        ("input_tokens", payload.get("input_tokens")),
        ("output_tokens", payload.get("output_tokens")),
        ("parse_strategy", payload.get("parse_strategy")),
        ("attempts", len(attempts) if isinstance(attempts, (list, tuple)) else None),
    ])


def _render_ask(payload: dict, deps: Deps, *, raw: bool, created: dict | None) -> None:
    """The F⁰/Fᴷ pair: what the catalog accepted next to what the model said."""
    if created is not None:
        deps.out.write(f"session {created.get('session_id')} created → {created.get('run_id')}")
        deps.out.blank()
    deps.out.write(_header(payload))
    if payload.get("model_load_seconds") is not None:
        deps.out.write(kv([("model_load_seconds", payload.get("model_load_seconds"))]))

    if payload.get("error"):
        deps.out.blank()
        deps.out.write("error (no valid plan — the trace below is still recorded)")
        deps.out.write(f"{_INDENT}{payload['error']}")

    deps.out.blank()
    deps.out.write("Fᴷ (catalog accepted)")
    for line in plan_lines(payload.get("plan")):
        deps.out.write(line)

    deps.out.blank()
    deps.out.write("F⁰ (model alone)")
    for line in plan_lines(payload.get("f0_plan")):
        deps.out.write(line)
    if payload.get("f0_plan") is None and payload.get("f0_error"):
        deps.out.write(f"{_INDENT}{payload['f0_error']}")

    deps.out.blank()
    deps.out.write("hooks")
    diff = payload.get("hooks_diff")
    # Already human-readable (`ganglion.lm.serve.hooks_diff`) — printed verbatim.
    if isinstance(diff, (list, tuple)) and diff:
        for line in diff:
            deps.out.write(f"{_INDENT}{line}")
    else:
        deps.out.write(f"{_INDENT}(no hook rewrote the model output)")

    if raw:
        deps.out.blank()
        deps.out.write("raw")
        deps.out.write(f"{_INDENT}{fmt(payload.get('raw'))}")
        attempts = payload.get("attempts")
        for index, attempt in enumerate(attempts if isinstance(attempts, (list, tuple)) else []):
            number = attempt.get("attempt", index) if isinstance(attempt, dict) else index
            content = attempt.get("content") if isinstance(attempt, dict) else attempt
            deps.out.write(f"{_INDENT}attempt {fmt(number)}  {fmt(content)}")

    deps.out.blank()
    deps.out.write(kv([
        ("trace_id", payload.get("trace_id")),
        ("run_id", payload.get("run_id")),
        ("session_id", payload.get("session_id")),
        ("repeat_index", payload.get("repeat_index")),
    ]))
    deps.out.write(f"next  ganglion label add {fmt(payload.get('trace_id'))} --verdict <verdict>")


def _cmd_ask(args: argparse.Namespace, deps: Deps) -> int:
    session = (args.session or deps.ctx.session or "").strip()
    created: dict | None = None
    if args.new_session or not session:
        created = _open_session(args, deps)
        session = str(created.get("session_id") or "")

    payload = deps.api.post("/api/chat", {
        "session_id": session,
        "catalog_id": deps.ctx.require("catalog"),
        "model_id": deps.ctx.require("model"),
        "prompt": " ".join(args.prompt),
        "repair": bool(args.repair),
        "repair_max_attempts": int(args.repair_max_attempts),
    })

    def render(body: dict) -> None:
        _render_ask(body, deps, raw=bool(args.raw), created=created)

    status = deps.out.emit(payload, render)
    # A model failure is a recorded trace, not a route error: exit 1 so a
    # script notices, with the payload already printed in full.
    return 1 if isinstance(payload, dict) and payload.get("error") else status


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    session = sub.add_parser("session", help="chat sessions (one run bundle per session)")
    verbs = session.add_subparsers(dest="verb", metavar="<verb>", required=True)

    new = verbs.add_parser(
        "new", parents=[common],
        help="POST /api/sessions — open a run bundle and save it to the context",
        description="Create a chat session for the context's catalog + model. The "
                    "session_id and run_id are written to .ganglion/context.json, so "
                    "the next `ganglion ask` and `ganglion trace list` address this run "
                    "with no flags.",
    )
    _add_repair_flags(new)
    new.set_defaults(_handler=_cmd_session_new)

    ask = sub.add_parser(
        "ask", parents=[common],
        help="POST /api/sessions (if needed) then /api/chat — one prompt, F⁰/Fᴷ pair",
        description="Serve one prompt and print the F⁰/Fᴷ pair: the plan the catalog "
                    "accepted next to the plan the model produced alone. Exits 1 when "
                    "the model produced no valid plan — the trace is still recorded, so "
                    "the printed trace_id is labellable either way. This is the only "
                    "command that calls two routes: a session is created first when none "
                    "is resolvable from --session / the context, or with --new-session.",
    )
    ask.add_argument("prompt", nargs="+", metavar="PROMPT",
                     help="user text; every word is joined with a single space, so "
                          "`ganglion ask 거실 불 켜줘` needs no quoting")
    ask.add_argument("--new-session", action="store_true",
                     help="open a fresh session even when one is already resolved")
    _add_repair_flags(ask)
    ask.add_argument("--raw", action="store_true",
                     help="also print the raw model output and every attempt (human "
                          "mode only; --json always carries them)")
    ask.set_defaults(_handler=_cmd_ask)
