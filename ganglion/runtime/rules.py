"""Deprecated. Moved to `ganglion.lm.rules`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.runtime.rules is deprecated; use ganglion.lm.rules instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.rules import RuleBasedJSONDSLClient  # noqa: F401,E402
