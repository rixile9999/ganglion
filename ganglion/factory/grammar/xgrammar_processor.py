"""Deprecated. Moved to `ganglion.lm.grammar`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.grammar.xgrammar_processor is deprecated; "
    "use ganglion.lm.grammar instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.grammar import (  # noqa: E402, F401
    compile_catalog_grammar,
    make_logits_processor,
)
