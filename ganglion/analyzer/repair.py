"""DSL repair-loop core.

Module 2 (analyzer) owns the repair *policy* — the decision of whether to retry
a failed `Catalog.parse_json_dsl()` and with what corrective message. Module 1
(`lm/`) only executes that policy against a live completer.

For Batch 3 this module preserves the existing fixed-retry behaviour previously
hosted at `runtime/qwen.py` / `lm/dashscope.py`. The `RepairPolicy` protocol
described in `docs/tasks/analyzer_repair_policy.md` is a follow-up; today's
`RepairConfig` is the byte-equal stand-in for `FixedRetryPolicy(max_attempts=1)`.

Directed import graph: `lm.dashscope → analyzer.repair → contract`. `analyzer.repair`
must not import from `lm/*` to keep the dependency one-way.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from ganglion.contract import Catalog, DSLValidationError
from ganglion.lm.client import ModelResult
from ganglion.lm.prompts import _dsl_messages

__all__ = [
    "CompletionResponse",
    "Completer",
    "RepairConfig",
    "run_dsl_with_repair",
]


@dataclass(frozen=True)
class RepairConfig:
    enabled: bool = False
    max_attempts: int = 1


@dataclass(frozen=True)
class CompletionResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


class Completer(Protocol):
    def complete(self, messages: list[dict[str, Any]]) -> CompletionResponse: ...


def run_dsl_with_repair(
    catalog: Catalog,
    user_prompt: str,
    completer: Completer,
    repair: RepairConfig,
) -> ModelResult:
    messages = _dsl_messages(catalog, user_prompt)
    attempts: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    started = time.perf_counter()
    last_error: DSLValidationError | None = None

    for attempt in range(repair.max_attempts + 1):
        response = completer.complete(messages)
        total_input += response.input_tokens
        total_output += response.output_tokens
        attempts.append(
            {
                "attempt": attempt,
                "content": response.content,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            }
        )

        try:
            plan = catalog.parse_json_dsl(response.content, prompt=user_prompt)
            latency_ms = (time.perf_counter() - started) * 1000
            return ModelResult(
                plan=plan,
                raw={"attempts": attempts, "final_content": response.content},
                latency_ms=latency_ms,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        except DSLValidationError as exc:
            last_error = exc
            attempts[-1]["error"] = str(exc)
            if not repair.enabled or attempt >= repair.max_attempts:
                raise
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON failed validation: "
                        f"{exc}. Return only valid JSON that matches the DSL."
                    ),
                },
            ]

    raise RuntimeError(f"repair loop exited without returning; last_error={last_error}")
