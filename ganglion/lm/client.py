"""Protocol surface for Module 1 language-model clients.

Defines the public `ModelClient` Protocol that all DSL/native clients
implement, the `ModelResult` frozen dataclass returned by `invoke()`, and
`ModelOutputError` — the one failure shape a client raises when it cannot
turn the model's output into a validated `ActionPlan`.

See `docs/factory_design.md` §2.2 and [[lm_client]] (`docs/tasks/lm_client.md`).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ganglion.contract import ActionPlan
from ganglion.contract.tool_spec import DSLValidationError

__all__ = ["ModelClient", "ModelOutputError", "ModelResult"]


@dataclass(frozen=True)
class ModelResult:
    plan: ActionPlan
    raw: Any
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelOutputError(DSLValidationError):
    """The model produced output, but no valid plan could be built from it.

    Subclasses ``DSLValidationError`` so every existing ``except
    DSLValidationError`` keeps working, and carries what the model actually
    said so the run owner can still record a full trace
    ([[analyzer_trace_store]] failure raw preservation):

    - ``raw``: the client's raw output in one of the shapes
      ``ganglion.analyzer.trace.attempts_from_raw`` accepts — a ``str``
      (freeform / local text), ``{"attempts": [...], "final_content"}``,
      ``{"content", "parse_strategy"}``, a native ``list`` of tool calls, or
      the rules client's ``{"calls": [...]}`` payload.
    - ``attempts``: ``attempts_from_raw(raw)``. Callers normally pass it
      explicitly (lazy-importing ``attempts_from_raw`` at the raise site so
      the module-level graph stays ``lm.dashscope → analyzer.repair →
      contract``); when omitted it is derived here with the same lazy import.

    Raised by the rules, freeform, native and local-HF clients (errata E9 /
    E12); the json-dsl client's terminal failure is
    ``ganglion.analyzer.repair.RepairExhaustedError``, which carries the same
    two attributes.
    """

    def __init__(
        self,
        message: str = "model output could not be validated",
        *,
        raw: Any = None,
        attempts: Sequence[Mapping[str, Any]] | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.raw: Any = raw
        if attempts is None:
            # Lazy: analyzer.trace is a leaf over the contract, never over lm.
            from ganglion.analyzer.trace import attempts_from_raw

            attempts = attempts_from_raw(raw)
        self.attempts: tuple[dict[str, Any], ...] = tuple(dict(att) for att in attempts)
        # Token accounting for a generation that *happened* but did not
        # validate — without these, a failed local/freeform call is invisible
        # in cost reporting (`ganglion.lm.serve` reads them off the exception).
        self.input_tokens: int | None = input_tokens
        self.output_tokens: int | None = output_tokens


class ModelClient(Protocol):
    """A language-model client that converts a user prompt to an `ActionPlan`.

    Concrete implementations live in `ganglion.lm.dashscope` (Qwen /
    OpenAI-compatible), `ganglion.lm.rules` (offline rule-based stand-in) and
    `ganglion.lm.local_hf` (in-process transformers + PEFT).
    """

    def invoke(self, prompt: str) -> ModelResult: ...
