"""``python -m ganglion.ctl`` — same entry point as the ``ganglion`` script."""

from __future__ import annotations

import sys

from ganglion.ctl.main import main

if __name__ == "__main__":  # pragma: no cover — module entry point
    sys.exit(main())
