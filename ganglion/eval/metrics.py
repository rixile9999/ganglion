"""Deprecated. Moved to `ganglion.analyzer.metrics`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.eval.metrics is deprecated; use ganglion.analyzer.metrics instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.analyzer.metrics import *  # noqa: E402,F401,F403
from ganglion.analyzer.metrics import (  # noqa: E402,F401
    CaseResult,
    RunResult,
    graded_score,
    summarize,
)
