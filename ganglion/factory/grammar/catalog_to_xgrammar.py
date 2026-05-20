"""Deprecated. Moved to `ganglion.lm.grammar`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.grammar.catalog_to_xgrammar is deprecated; "
    "use ganglion.lm.grammar instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.grammar import catalog_to_json_schema  # noqa: E402, F401
