"""Deprecated. Moved to `ganglion.lm.synth.strategies`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.prompts.synth_templates is deprecated; use ganglion.lm.synth.strategies instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.synth.strategies import *  # noqa: E402,F401,F403
from ganglion.lm.synth.strategies import (  # noqa: E402,F401
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    render_tool_anchored_prompt,
    render_tool_spec,
)
