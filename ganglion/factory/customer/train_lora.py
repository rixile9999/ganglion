"""Deprecated. Moved to `ganglion.lm.finetune.sft`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.factory.customer.train_lora is deprecated; "
    "use ganglion.lm.finetune.sft instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.finetune.sft import *  # noqa: E402,F401,F403
from ganglion.lm.finetune.sft import (  # noqa: E402,F401
    SYSTEM_PROMPT_TEMPLATE,
    TrainConfig,
    build_messages,
    generate_dsl,
    load_base_for_inference,
    load_lora_for_inference,
    train_lora,
)
