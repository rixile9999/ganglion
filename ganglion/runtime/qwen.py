"""Deprecated. Moved to `ganglion.lm.dashscope` / `ganglion.lm.prompts` / `ganglion.lm.client`."""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.runtime.qwen is deprecated; use ganglion.lm.dashscope / "
    "ganglion.lm.prompts / ganglion.lm.client instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.lm.client import ModelClient, ModelResult  # noqa: E402, F401
from ganglion.lm.dashscope import (  # noqa: E402, F401
    CompletionResponse,
    QwenConfig,
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
    RepairConfig,
    _OpenAIDSLCompleter,
    run_dsl_with_repair,
)
from ganglion.lm.prompts import (  # noqa: E402, F401
    SYSTEM_PROMPT_TEMPLATE,
    _dsl_messages,
)
