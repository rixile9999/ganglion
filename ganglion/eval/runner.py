"""Deprecated. CLI moved to `ganglion.cli`; IoT runner to `ganglion.benchmarks.iot.runner`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.eval.runner is deprecated; use `python -m ganglion.cli` instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.cli import build_client, main, run_eval  # noqa: E402,F401

if __name__ == "__main__":
    main()
