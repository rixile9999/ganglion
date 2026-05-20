"""Deprecated. Moved to `ganglion.benchmarks.bfcl.loader`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.bfcl.loader is deprecated; use ganglion.benchmarks.bfcl.loader instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.bfcl.loader import *  # noqa: F401,F403
from ganglion.benchmarks.bfcl.loader import (  # noqa: F401
    BFCLCase,
    CATEGORIES,
    SAMPLE_ROOT,
    all_categories,
    load_cases,
    load_category,
)
