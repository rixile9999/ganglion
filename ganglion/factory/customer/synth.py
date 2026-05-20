"""Deprecated. Moved to `ganglion.lm.synth.pipeline`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.customer.synth is deprecated; use ganglion.lm.synth.pipeline instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.synth.pipeline import *  # noqa: E402,F401,F403
from ganglion.lm.synth.pipeline import (  # noqa: E402,F401
    DashScopeTeacher,
    SynthConfig,
    SynthExample,
    SynthStats,
    TeacherClient,
    estimate_cost,
    read_jsonl,
    synth_gate,
    synthesize,
    write_jsonl,
)
