"""Deprecated. `ModelResult` moved to `ganglion.lm.client`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.runtime.types is deprecated; use ganglion.lm.client instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.client import ModelResult  # noqa: E402, F401
