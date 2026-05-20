"""Deprecated. Moved to `ganglion.benchmarks.iot.dataset`."""

from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.eval.dataset is deprecated; use ganglion.benchmarks.iot.dataset instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.iot.dataset import *  # noqa: E402,F401,F403
from ganglion.benchmarks.iot.dataset import (  # noqa: E402,F401
    ADVERSARIAL_DATASET,
    DEFAULT_DATASET,
    EvalCase,
    load_dataset,
)
