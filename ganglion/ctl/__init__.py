"""``ganglion`` — the terminal projection of the operator console ([[cli_operator]]).

One executable over the same ``/api/*`` route table
:mod:`ganglion.console.api` serves to the browser, called **in-process**:
:class:`~ganglion.ctl.bridge.ApiBridge` is the single seam, so no module in
this package imports an ``analyzer`` / ``lm`` / ``contract`` primitive and no
command computes a verdict the route does not already return.

Public API:
    ApiBridge, Context, CtlError, Deps, Output, main.
"""

from __future__ import annotations

from ganglion.ctl.bridge import ApiBridge
from ganglion.ctl.context import Context
from ganglion.ctl.deps import Deps
from ganglion.ctl.errors import CtlError
from ganglion.ctl.render import Output

__all__ = ["ApiBridge", "Context", "CtlError", "Deps", "Output", "main"]


def main(argv: list[str] | None = None) -> int:
    """Lazy re-export of :func:`ganglion.ctl.main.main` (keeps import cheap)."""
    from ganglion.ctl.main import main as _main

    return _main(argv)
