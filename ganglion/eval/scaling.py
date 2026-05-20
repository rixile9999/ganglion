"""Deprecated. Moved to `ganglion.benchmarks.iot.scaling`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.eval.scaling is deprecated; use ganglion.benchmarks.iot.scaling instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.benchmarks.iot.scaling import *  # noqa: E402,F401,F403
from ganglion.benchmarks.iot.scaling import (  # noqa: E402,F401
    main,
    measure,
)


if __name__ == "__main__":
    main()
