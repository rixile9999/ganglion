"""Deprecated. Moved to `ganglion.benchmarks.bfcl`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.bfcl is deprecated; use ganglion.benchmarks.bfcl instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.bfcl.grader import GraderResult, ast_match  # noqa: E402
from ganglion.benchmarks.bfcl.loader import (  # noqa: E402
    BFCLCase,
    SAMPLE_ROOT,
    load_cases,
    load_category,
)

__all__ = [
    "BFCLCase",
    "GraderResult",
    "SAMPLE_ROOT",
    "ast_match",
    "load_cases",
    "load_category",
]
