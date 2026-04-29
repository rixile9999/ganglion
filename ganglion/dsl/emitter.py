from __future__ import annotations

from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.types import ActionPlan
from ganglion.dsl.validator import parse_json_dsl


def emit_tool_calls(
    raw: str | dict[str, Any] | ActionPlan,
    catalog: Catalog | None = None,
) -> list[dict[str, Any]]:
    if isinstance(raw, ActionPlan):
        plan = raw
    elif catalog is not None:
        plan = catalog.parse_json_dsl(raw)
    else:
        plan = parse_json_dsl(raw)
    return [
        {
            "name": call.action,
            "arguments": call.args,
        }
        for call in plan.calls
    ]
