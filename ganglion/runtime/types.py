from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ganglion.dsl.types import ActionPlan


@dataclass(frozen=True)
class ModelResult:
    plan: ActionPlan
    raw: Any
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
