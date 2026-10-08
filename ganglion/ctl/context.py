"""``Context`` — catalog / model / session / run resolution for ``ganglion``.

The browser console keeps ``{catalog, model, session, run}`` in
``localStorage`` (``web/assets/api.js``); the terminal equivalent is
``.ganglion/context.json`` next to the checkout. Resolution order per field is
**flag → environment → context file → default** ([[cli_operator]] §Scope), so
a one-off ``--catalog`` never mutates saved state and ``ganglion use`` is the
only writer besides ``ganglion ask``'s implicit session.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ganglion.ctl.errors import CtlError

__all__ = [
    "CONTEXT_DIRNAME",
    "CONTEXT_BASENAME",
    "DEFAULT_RUNS_DIR",
    "FIELDS",
    "Context",
    "clear_context",
    "context_path",
    "load_context",
    "save_context",
]

CONTEXT_DIRNAME = ".ganglion"
CONTEXT_BASENAME = "context.json"
DEFAULT_RUNS_DIR = "runs/traces"

#: The four addressing fields, in the order ``ganglion use`` prints them.
FIELDS: tuple[str, ...] = ("catalog", "model", "session", "run")

_ENV = {
    "catalog": "GANGLION_CATALOG",
    # NOT `GANGLION_MODEL`: that one is already the DashScope *served model
    # name* expanded inside `configs/models.yaml` (`${GANGLION_MODEL:-qwen3.6-plus}`)
    # and read by `ganglion/lm/dashscope.py`. A registry `model_id` is a
    # different vocabulary ("qwen3.6-plus@dashscope"), so it gets its own name
    # rather than silently resolving to an id that is not in the registry.
    "model": "GANGLION_CTL_MODEL",
    "session": "GANGLION_SESSION",
    "run": "GANGLION_RUN",
    "runs_dir": "GANGLION_RUNS",
}

#: ``field → the flag that sets it`` (help text for a missing-context error).
_FLAG = {
    "catalog": "--catalog",
    "model": "--model",
    "session": "--session",
    "run": "--run",
    "runs_dir": "--runs",
}


def context_path(cwd: Path | str | None = None) -> Path:
    """``<cwd>/.ganglion/context.json`` — per-checkout operator state, gitignored."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    return base / CONTEXT_DIRNAME / CONTEXT_BASENAME


def _read(path: Path) -> dict[str, Any]:
    """The stored context, or ``{}`` for absent / unreadable / non-object files.

    A corrupt context file must not make every command fail — it is a cache of
    convenience, not a source of truth.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class Context:
    """Resolved addressing for one invocation. ``path`` is where it would persist."""

    catalog: str = ""
    model: str = ""
    session: str = ""
    run: str = ""
    runs_dir: Path = Path(DEFAULT_RUNS_DIR)
    models_path: Path | None = None
    path: Path = Path(CONTEXT_DIRNAME) / CONTEXT_BASENAME

    def require(self, field: str) -> str:
        """The field's value, or ``CtlError(400, "missing_context")`` naming how to set it."""
        value = getattr(self, field, "")
        if isinstance(value, Path):
            value = str(value)
        if not value:
            flag = _FLAG.get(field, f"--{field}")
            raise CtlError(
                400,
                "missing_context",
                f"no {field} resolved; pass {flag} <id> or set it once with "
                f"`ganglion use {flag} <id>`",
            )
        return str(value)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able projection — what ``ganglion use`` prints and persists."""
        return {
            "catalog": self.catalog,
            "model": self.model,
            "session": self.session,
            "run": self.run,
            "runs_dir": str(self.runs_dir),
            "models_path": str(self.models_path) if self.models_path else None,
            "context_path": str(self.path),
        }

    def stored(self) -> dict[str, Any]:
        """The subset written to disk (``models_path`` stays a per-invocation flag)."""
        return {
            "catalog": self.catalog,
            "model": self.model,
            "session": self.session,
            "run": self.run,
            "runs_dir": str(self.runs_dir),
        }


def load_context(
    *,
    catalog: str | None = None,
    model: str | None = None,
    session: str | None = None,
    run: str | None = None,
    runs_dir: Path | str | None = None,
    models_path: Path | str | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> Context:
    """Resolve every field by **flag → environment → file → default**."""
    environ = os.environ if env is None else env
    path = context_path(cwd)
    stored = _read(path)
    flags = {"catalog": catalog, "model": model, "session": session, "run": run}

    resolved: dict[str, str] = {}
    for field in FIELDS:
        value = flags.get(field) or environ.get(_ENV[field], "") or stored.get(field, "") or ""
        resolved[field] = str(value).strip()

    runs = (
        str(runs_dir)
        if runs_dir is not None
        else environ.get(_ENV["runs_dir"], "") or str(stored.get("runs_dir", "") or "") or DEFAULT_RUNS_DIR
    )
    return Context(
        catalog=resolved["catalog"],
        model=resolved["model"],
        session=resolved["session"],
        run=resolved["run"],
        runs_dir=Path(runs),
        # `models_path=None` lets `load_registry` do its own
        # `$GANGLION_MODELS` → `configs/models.yaml` resolution; storing it
        # here would freeze one checkout's registry into the context file.
        models_path=Path(models_path) if models_path else None,
        path=path,
    )


def save_context(ctx: Context, **patch: Any) -> Context:
    """Merge ``patch`` into ``ctx``, write the file, return the new context."""
    clean: dict[str, Any] = {}
    for field, value in patch.items():
        if value is None:
            continue
        clean[field] = Path(value) if field == "runs_dir" else str(value).strip()
    updated = replace(ctx, **clean) if clean else ctx
    _write(updated)
    return updated


def clear_context(ctx: Context) -> Context:
    """Empty the four addressing fields (``runs_dir`` survives) and persist."""
    updated = replace(ctx, catalog="", model="", session="", run="")
    _write(updated)
    return updated


def _write(ctx: Context) -> None:
    path = ctx.path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(ctx.stored(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        raise CtlError(500, "context_unwritable", f"{path}: {exc}") from exc
