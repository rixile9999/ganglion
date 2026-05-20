"""Deprecated. Moved to `ganglion.lm.local_hf`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.customer.eval is deprecated; "
    "use ganglion.lm.local_hf instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.local_hf import *  # noqa: E402,F401,F403
from ganglion.lm.local_hf import (  # noqa: E402,F401
    EvalConfig,
    evaluate_lora,
    split_train_eval,
    write_report,
    write_split_jsonls,
)
