"""``ApiBridge`` — the single seam between ``ganglion`` and the console routes.

Every command talks to :class:`ganglion.console.api.ConsoleAPI` through
``get`` / ``post`` here and nowhere else, so the CLI and the browser share one
route table, one ``Writer`` discipline and one set of verdicts
([[cli_operator]] §Scope, [[console_operator]] §on violation).

The ``ConsoleAPI`` is built on the **first** call: ``ganglion --help``, an
argparse usage error and ``ganglion use`` must not start a writer thread or
import ``openai`` / ``yaml``. ``close()`` is idempotent and unconditional in
``main``'s ``finally`` — a leaked writer thread hangs the process at exit.
"""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from ganglion.ctl.errors import CtlError

__all__ = ["ApiBridge"]


def _query(pairs: dict[str, Any]) -> dict[str, list[str]]:
    """Scalars → ``ConsoleAPI.handle``'s ``{name: [value]}``; ``None`` / ``""`` dropped."""
    out: dict[str, list[str]] = {}
    for name, value in pairs.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            out[name] = ["true" if value else "false"]
        else:
            out[name] = [str(value)]
    return out


class ApiBridge:
    """Lazy in-process ``ConsoleAPI`` holder. ``get``/``post`` raise ``CtlError``."""

    def __init__(
        self,
        runs_dir: Path | str = "runs/traces",
        *,
        models_path: Path | str | None = None,
        labeler: str | None = None,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.models_path = models_path
        self.labeler = labeler
        self._api: Any = None
        self.endpoint = os.environ.get("GANGLION_CONSOLE_URL", "").rstrip("/")

    # -- lifecycle ---------------------------------------------------------

    @property
    def api(self) -> Any:
        """The ``ConsoleAPI``, built on first use (imports are deferred with it)."""
        if self._api is None:
            from ganglion.console.api import ConsoleAPI
            from ganglion.console.server import default_web_dir

            self._api = ConsoleAPI(
                base_dir=self.runs_dir,
                web_dir=default_web_dir(),
                models_path=self.models_path,
                labeler=self.labeler,
            )
        return self._api

    @property
    def started(self) -> bool:
        """``True`` once a route has been called (i.e. a writer thread exists)."""
        return self._api is not None

    def close(self) -> None:
        """Stop the writer thread if one was ever started. Idempotent."""
        api, self._api = self._api, None
        if api is not None:
            api.close()

    def __enter__(self) -> "ApiBridge":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- routes ------------------------------------------------------------

    def request(self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None) -> Any:
        """One route call; returns the payload or raises ``CtlError`` on non-2xx."""
        if self.endpoint:
            import json
            from urllib.parse import urlencode, urlsplit
            from urllib.request import Request, urlopen
            from urllib.error import HTTPError, URLError
            parsed = urlsplit(self.endpoint)
            if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.path or parsed.username:
                raise CtlError(400, "invalid_endpoint", "GANGLION_CONSOLE_URL must be a loopback HTTP URL")
            url = self.endpoint + path
            if query:
                url += "?" + urlencode({k: v for k, v in query.items() if v is not None and v != ""})
            request = Request(url, method=method, data=json.dumps(body or {}).encode() if method == "POST" else None,
                              headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=120) as response:
                    return json.load(response)
            except HTTPError as exc:
                raise CtlError.from_payload(exc.code, json.load(exc)) from None
            except URLError:
                raise CtlError(503, "console_unavailable", "cannot connect to the configured loopback console") from None
        status, payload = self.api.handle(method, path, _query(query or {}), body)
        if not 200 <= int(status) < 300:
            raise CtlError.from_payload(int(status), payload)
        return payload

    def get(self, path: str, **query: Any) -> Any:
        """``GET path?<query>`` — keyword arguments become the query string."""
        return self.request("GET", path, query=query)

    def post(self, path: str, body: Any = None) -> Any:
        """``POST path`` with a JSON body (``None`` → empty object semantics)."""
        return self.request("POST", path, body=body)
