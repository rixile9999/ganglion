from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from statistics import median
from typing import Any

from rlm_poc.dsl.types import ActionPlan


@dataclass(frozen=True)
class CaseResult:
    id: str
    prompt: str
    expected: ActionPlan
    predicted: ActionPlan | None
    raw: Any
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.predicted is not None and self.error is None

    @property
    def exact_match(self) -> bool:
        return self.valid and self.predicted == self.expected

    @property
    def action_match(self) -> bool:
        if not self.valid or self.predicted is None:
            return False
        expected_actions = [call.action for call in self.expected.calls]
        predicted_actions = [call.action for call in self.predicted.calls]
        return expected_actions == predicted_actions


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    valid = sum(result.valid for result in results)
    exact = sum(result.exact_match for result in results)
    action = sum(result.action_match for result in results)
    latencies = [
        result.latency_ms
        for result in results
        if result.latency_ms is not None
    ]
    input_tokens = [
        result.input_tokens
        for result in results
        if result.input_tokens is not None
    ]
    output_tokens = [
        result.output_tokens
        for result in results
        if result.output_tokens is not None
    ]
    parse_strategies: Counter[str] = Counter()
    reasoning_chars = 0
    for result in results:
        if isinstance(result.raw, dict):
            strategy = result.raw.get("parse_strategy")
            if isinstance(strategy, str):
                parse_strategies[strategy] += 1
            chars = result.raw.get("reasoning_chars")
            if isinstance(chars, int):
                reasoning_chars += chars
    return {
        "total": total,
        "syntax_valid_rate": _rate(valid, total),
        "exact_match_rate": _rate(exact, total),
        "action_match_rate": _rate(action, total),
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "input_tokens_total": sum(input_tokens) if input_tokens else None,
        "output_tokens_total": sum(output_tokens) if output_tokens else None,
        "parse_strategy_counts": dict(parse_strategies) if parse_strategies else None,
        "reasoning_chars_total": reasoning_chars or None,
        "failures": [
            {
                "id": result.id,
                "prompt": result.prompt,
                "expected": result.expected.to_jsonable(),
                "predicted": result.predicted.to_jsonable()
                if result.predicted is not None
                else None,
                "error": result.error,
                "raw": result.raw,
            }
            for result in results
            if not result.exact_match
        ],
    }


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    if percentile == 50:
        return round(median(values), 2)
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile / 100))
    return round(ordered[index], 2)
