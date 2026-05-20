"""Deprecated. Moved to `ganglion.benchmarks.bfcl.runner` + `ganglion.benchmarks.bfcl.case_catalog`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.eval.bfcl_runner is deprecated; use ganglion.benchmarks.bfcl.runner instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.bfcl.runner import *  # noqa: F401,F403,E402
from ganglion.benchmarks.bfcl.runner import (  # noqa: F401,E402
    BFCLCaseResult,
    BFCLRunResult,
    ClientFactory,
    ModelClient,
    run_bfcl,
    summarize_bfcl,
)
from ganglion.benchmarks.bfcl.case_catalog import build_case_catalog  # noqa: F401,E402
