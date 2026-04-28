from __future__ import annotations

from typing import Any

from rlm_poc.dsl.types import ActionPlan
from rlm_poc.dsl.validator import parse_json_dsl


def emit_tool_calls(raw: str | dict[str, Any] | ActionPlan) -> list[dict[str, Any]]:
    plan = raw if isinstance(raw, ActionPlan) else parse_json_dsl(raw)
    return [
        {
            "name": call.action,
            "arguments": call.args,
        }
        for call in plan.calls
    ]
