"""Protocol surface for Module 1 language-model clients.

Defines the public `ModelClient` Protocol that all DSL/native clients
implement, plus the `ModelResult` frozen dataclass returned by `invoke()`.

See `docs/factory_design.md` §2.2 and `docs/tasks/lm_client.md`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ganglion.contract import ActionPlan

__all__ = ["ModelClient", "ModelResult"]


@dataclass(frozen=True)
class ModelResult:
    plan: ActionPlan
    raw: Any
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelClient(Protocol):
    """A language-model client that converts a user prompt to an `ActionPlan`.

    Concrete implementations live in `ganglion.lm.dashscope` (Qwen/DashScope)
    and `ganglion.runtime.rules` (offline rule-based stand-in).
    """

    def invoke(self, prompt: str) -> ModelResult: ...
