"""``Deps`` — what every command handler receives besides its parsed args.

Its own module so a command module can annotate the handler signature without
importing :mod:`ganglion.ctl.main` (which imports the command modules).
"""

from __future__ import annotations

from dataclasses import dataclass

from ganglion.ctl.bridge import ApiBridge
from ganglion.ctl.context import Context
from ganglion.ctl.render import Output

__all__ = ["Deps"]


@dataclass(frozen=True)
class Deps:
    """``api`` = the route seam, ``ctx`` = resolved context, ``out`` = stdout."""

    api: ApiBridge
    ctx: Context
    out: Output
