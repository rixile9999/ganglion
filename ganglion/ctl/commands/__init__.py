"""One module per noun of the ``ganglion`` surface ([[cli_operator]] §Scope).

Each module exposes ``add_parser(sub, common) -> None``. It registers its
top-level parser(s) on ``sub`` and calls ``set_defaults(_handler=…)`` on every
**leaf**; only leaves take ``parents=[common]``, so a noun-level default can
never shadow a flag given at the leaf. A handler is
``(argparse.Namespace, Deps) -> int`` and reaches the console routes through
``deps.api`` alone.
"""

from __future__ import annotations

__all__ = ["catalogs", "chat", "labels", "models", "patches", "runs", "traces"]
