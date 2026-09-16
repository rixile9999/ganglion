"""Standard-library HTTP front end for the console ([[console_operator]]).

``ThreadingHTTPServer`` bound to ``127.0.0.1`` (loopback only — auth, TLS and
remote binding are explicitly out of scope in docs/tasks/console_operator.md).
Two surfaces:

* ``/api/*`` → :class:`ganglion.console.api.ConsoleAPI`, JSON in / JSON out,
  request bodies capped at 1 MiB, errors as ``{"error", "detail"}``.
* everything else → static files from the ``--web`` directory: ``/`` →
  ``index.html``, any root-level file, and the ``assets/`` and ``mockups/``
  subtrees. Path traversal is refused by resolving the candidate and checking
  it is inside the web root; directories never produce a listing.

No dependency beyond the standard library, by contract.

Public API:
    ConsoleHTTPServer, ConsoleRequestHandler, DEFAULT_HOST, DEFAULT_PORT,
    console_port, create_server, serve.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ganglion import __version__
from ganglion.console.api import ConsoleAPI

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ConsoleHTTPServer",
    "ConsoleRequestHandler",
    "console_port",
    "create_server",
    "serve",
]

_LOG = logging.getLogger("ganglion.console")

DEFAULT_HOST = "127.0.0.1"
#: 8765 is the doc-graph viewer; the console sits next to it.
DEFAULT_PORT = 8766
PORT_ENV = "GANGLION_CONSOLE_PORT"

MAX_BODY_BYTES = 1 << 20  # 1 MiB

#: Explicit types first — `mimetypes` disagrees across platforms on .js.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}

#: Subtrees served below the web root (plus any single root-level file).
_STATIC_DIRS = ("assets", "mockups")


def console_port(port: int | None = None) -> int:
    """``port`` → ``$GANGLION_CONSOLE_PORT`` → :data:`DEFAULT_PORT`."""
    if port is not None:
        return int(port)
    raw = os.environ.get(PORT_ENV, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            _LOG.warning("ignoring non-numeric %s=%r", PORT_ENV, raw)
    return DEFAULT_PORT


def content_type_for(path: Path) -> str:
    """Content type for a static file; ``application/octet-stream`` fallback."""
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class ConsoleHTTPServer(ThreadingHTTPServer):
    """Threaded loopback server holding the shared :class:`ConsoleAPI`."""

    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET

    def __init__(self, server_address: tuple[str, int], api: ConsoleAPI) -> None:
        self.api = api
        super().__init__(server_address, ConsoleRequestHandler)

    @property
    def port(self) -> int:
        """The bound port (meaningful after binding to port 0)."""
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"

    def server_close(self) -> None:  # noqa: D102 — stop the writer with the server
        try:
            super().server_close()
        finally:
            self.api.close()


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    """One request: ``/api/*`` JSON, everything else a static file."""

    server_version = f"GanglionConsole/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    @property
    def api(self) -> ConsoleAPI:
        return self.server.api  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102 — quiet by default
        _LOG.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, *, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, error: str, detail: str = "") -> None:
        self._send_json(status, {"error": error, "detail": detail})

    def _read_body(self) -> Any:
        """Parsed JSON body (``None`` when empty); raises nothing — errors are sent."""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise _BadBody(400, "bad_request", "invalid Content-Length") from None
        if length < 0:
            raise _BadBody(400, "bad_request", "invalid Content-Length")
        if length > MAX_BODY_BYTES:
            # The body is not drained: close rather than desync the connection.
            self.close_connection = True
            raise _BadBody(400, "body_too_large", f"{length} bytes > {MAX_BODY_BYTES} limit")
        if length == 0:
            return None
        raw = self.rfile.read(length)
        if not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _BadBody(400, "bad_request", f"invalid JSON body: {exc}") from None

    # -- verbs -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        split = urlsplit(self.path)
        path = unquote(split.path)
        if path.startswith("/api/") or path == "/api":
            query = parse_qs(split.query, keep_blank_values=True)
            status, payload = self.api.handle("GET", path, query, None)
            self._send_json(status, payload)
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        split = urlsplit(self.path)
        path = unquote(split.path)
        if not (path.startswith("/api/") or path == "/api"):
            self._error(404, "not_found", path)
            return
        try:
            body = self._read_body()
        except _BadBody as exc:
            self._error(exc.status, exc.error, exc.detail)
            return
        query = parse_qs(split.query, keep_blank_values=True)
        status, payload = self.api.handle("POST", path, query, body)
        self._send_json(status, payload)

    # -- static ------------------------------------------------------------

    def _static_target(self, path: str) -> Path | None:
        """Resolve ``path`` inside the web root, or ``None`` when out of bounds."""
        web_dir = self.api.web_dir
        try:
            root = web_dir.resolve()
        except OSError:
            return None
        rel = path.lstrip("/")
        if not rel or rel.endswith("/"):
            rel += "index.html"
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return None
        if not parts:
            return None
        if len(parts) > 1 and parts[0] not in _STATIC_DIRS:
            return None
        candidate = root.joinpath(*parts)
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved != root and root not in resolved.parents:
            return None
        return resolved

    def _serve_static(self, path: str) -> None:
        target = self._static_target(path)
        if target is None or not target.is_file():
            self._error(404, "not_found", path)
            return
        try:
            body = target.read_bytes()
        except OSError as exc:
            self._error(404, "not_found", f"{path}: {exc}")
            return
        self._send(200, body, content_type_for(target))


class _BadBody(Exception):
    """Internal: a request body that never reaches the API layer."""

    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.error = error
        self.detail = detail


def create_server(
    *,
    base_dir: Path | str = "runs/traces",
    web_dir: Path | str | None = None,
    models_path: Path | str | None = None,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    api: ConsoleAPI | None = None,
) -> ConsoleHTTPServer:
    """Bind a console server (``port=0`` picks a free port; the API is started)."""
    if api is None:
        api = ConsoleAPI(
            base_dir=base_dir,
            web_dir=web_dir if web_dir is not None else default_web_dir(),
            models_path=models_path,
        )
    return ConsoleHTTPServer((host, console_port(port)), api)


def default_web_dir() -> Path:
    """``<repo>/web`` — the package's parent directory."""
    return Path(__file__).resolve().parents[2] / "web"


def serve(
    *,
    base_dir: Path | str = "runs/traces",
    web_dir: Path | str | None = None,
    models_path: Path | str | None = None,
    host: str = DEFAULT_HOST,
    port: int | None = None,
) -> None:
    """Bind and serve forever; Ctrl-C shuts down cleanly."""
    server = create_server(
        base_dir=base_dir, web_dir=web_dir, models_path=models_path, host=host, port=port,
    )
    api = server.api
    print(
        f"[console] {server.url}  runs={api.base_dir}  web={api.web_dir}  "
        f"models={api.registry.path or '(built-in rules only)'}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[console] shutting down")
    finally:
        server.server_close()
