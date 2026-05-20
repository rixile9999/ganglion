"""Chat-message assembly + system-prompt template for DSL clients.

The system prompt here is load-bearing: SFT training (M1-D, `lm/finetune/sft.py`)
copies `SYSTEM_PROMPT_TEMPLATE` verbatim so the trained model sees the same
prompt at inference time. Do not change wording without coordinating both sides.
"""
from __future__ import annotations

from typing import Any

from ganglion.contract import Catalog

__all__ = ["SYSTEM_PROMPT_TEMPLATE", "_dsl_messages"]


# Byte-for-byte parity invariant with SFT training. `{dsl}` is replaced by
# `Catalog.render_json_dsl()` at message-assembly time.
SYSTEM_PROMPT_TEMPLATE = (
    "You convert user requests into the JSON DSL below. "
    "The response must be valid JSON.\n\n"
    "{dsl}"
)


def _dsl_messages(catalog: Catalog, user_prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT_TEMPLATE.format(dsl=catalog.render_json_dsl()),
        },
        {"role": "user", "content": user_prompt},
    ]
