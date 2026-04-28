from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    action: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ActionPlan:
    calls: tuple[ToolCall, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "calls": [
                {"action": call.action, "args": call.args}
                for call in self.calls
            ]
        }
