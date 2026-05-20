"""Deprecated. Moved to `ganglion.analyzer.verifier`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.customer.verifier is deprecated; use ganglion.analyzer.verifier instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.analyzer.verifier import *  # noqa: E402,F401,F403
from ganglion.analyzer.verifier import VerifierFn, make_verifier  # noqa: E402,F401
