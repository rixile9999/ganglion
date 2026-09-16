"""Operator console — static ``web/`` pages plus a ``/api/*`` JSON surface.

Implements [[console_operator]] (docs/tasks/console_operator.md): one
standard-library HTTP process over the run-directory tree
(``runs/traces/<catalog_id>/<run_id>/``). Every GET is a projection of files
the primitives already wrote; every POST calls exactly one primitive through
a single writer thread and records that primitive's declared event.

    python -m ganglion.console seed --runs runs/traces --limit 100
    python -m ganglion.console serve --port 8766

Modules:
    :mod:`ganglion.console.api`     — the route table.
    :mod:`ganglion.console.server`  — ``ThreadingHTTPServer`` + static files.
    :mod:`ganglion.console.writer`  — the single writer thread.
    :mod:`ganglion.console.seed`    — offline seed runs (errata E8).
"""

from __future__ import annotations

from ganglion.console.api import ApiError, ConsoleAPI
from ganglion.console.seed import DegradedRulesClient, seed_runs
from ganglion.console.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ConsoleHTTPServer,
    console_port,
    create_server,
    default_web_dir,
    serve,
)
from ganglion.console.writer import Writer, WriterDeadError

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ApiError",
    "ConsoleAPI",
    "ConsoleHTTPServer",
    "DegradedRulesClient",
    "Writer",
    "WriterDeadError",
    "console_port",
    "create_server",
    "default_web_dir",
    "seed_runs",
    "serve",
]
