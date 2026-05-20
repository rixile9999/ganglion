"""Deprecated. Moved to `ganglion.benchmarks.bfcl.grader`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.bfcl.grader is deprecated; use ganglion.benchmarks.bfcl.grader instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.bfcl.grader import *  # noqa: F401,F403
from ganglion.benchmarks.bfcl.grader import (  # noqa: F401
    GraderResult,
    ast_match,
)
