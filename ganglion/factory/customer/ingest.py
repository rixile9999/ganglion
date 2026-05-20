"""Deprecated. Moved to `ganglion.lm.synth.ingest`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.customer.ingest is deprecated; use ganglion.lm.synth.ingest instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.synth.ingest import *  # noqa: E402,F401,F403
from ganglion.lm.synth.ingest import (  # noqa: E402,F401
    SchemaInput,
    ingest_schema,
)
