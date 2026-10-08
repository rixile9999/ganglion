"""``CtlError`` — the one exception ``ganglion`` turns into an exit status.

A non-2xx ``/api/*`` response and a locally detected input problem are the
same thing to a caller: a status, a machine-readable ``error`` slug and a
human ``detail``. ``main`` prints ``error: <error> — <detail>`` to stderr and
exits 1; the route's own wording is never reworded ([[cli_operator]]
§Contract.failure).
"""

from __future__ import annotations

__all__ = ["CtlError"]


class CtlError(Exception):
    """One failed command: ``status`` (HTTP-shaped), ``error`` slug, ``detail``."""

    def __init__(self, status: int, error: str, detail: str = "") -> None:
        super().__init__(f"{error} — {detail}" if detail else error)
        self.status = int(status)
        self.error = str(error)
        self.detail = str(detail)

    @classmethod
    def from_payload(cls, status: int, payload: object) -> "CtlError":
        """Build from a route's ``{"error", "detail"}`` body (any shape survives)."""
        if isinstance(payload, dict):
            error = str(payload.get("error") or f"http_{status}")
            detail = str(payload.get("detail") or "")
        else:
            error = f"http_{status}"
            detail = "" if payload is None else str(payload)
        return cls(status, error, detail)

    def stderr_line(self) -> str:
        return f"error: {self.error} — {self.detail}" if self.detail else f"error: {self.error}"
