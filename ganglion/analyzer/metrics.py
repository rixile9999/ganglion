from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Any

from ganglion.contract.types import ActionPlan


@dataclass(frozen=True)
class RunResult:
    plan: ActionPlan | None
    raw: Any
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.plan is not None and self.error is None


@dataclass(frozen=True)
class CaseResult:
    id: str
    prompt: str
    expected: ActionPlan
    runs: tuple[RunResult, ...] = field(default_factory=tuple)

    @property
    def predicted(self) -> ActionPlan | None:
        if not self.runs:
            return None
        return self.runs[0].plan

    @property
    def raw(self) -> Any:
        if not self.runs:
            return None
        return self.runs[0].raw

    @property
    def latency_ms(self) -> float | None:
        if not self.runs:
            return None
        return self.runs[0].latency_ms

    @property
    def input_tokens(self) -> int | None:
        if not self.runs:
            return None
        return self.runs[0].input_tokens

    @property
    def output_tokens(self) -> int | None:
        if not self.runs:
            return None
        return self.runs[0].output_tokens

    @property
    def error(self) -> str | None:
        if not self.runs:
            return "no runs"
        return self.runs[0].error

    @property
    def valid(self) -> bool:
        if not self.runs:
            return False
        return all(run.valid for run in self.runs)

    @property
    def exact_match(self) -> bool:
        if not self.runs:
            return False
        return all(run.valid and run.plan == self.expected for run in self.runs)

    @property
    def action_match(self) -> bool:
        if not self.runs:
            return False
        expected_actions = [call.action for call in self.expected.calls]
        for run in self.runs:
            if not run.valid or run.plan is None:
                return False
            predicted_actions = [call.action for call in run.plan.calls]
            if predicted_actions != expected_actions:
                return False
        return True


def graded_score(
    predicted: ActionPlan | None, expected: ActionPlan
) -> float:
    """Score a single prediction against gold on a 0.0-1.0 gradation.

    Designed as a reward signal for graded-preference RL (DPO/GRPO):
    a smooth-ish gradient gives the trainer more information than a
    binary right/wrong, especially in the args-level error regime that
    dominates after S2c.

    Step structure:
        0.0   parse failure or no prediction
        0.25  parse OK, action sequence wrong
        0.5   action sequence right, args mostly wrong
              (linearly interpolates up to 0.75 with arg overlap)
        0.75  action sequence right, most args right
        1.0   exact match (action + args)

    For multi-call plans, the per-call scores are averaged. Different
    call counts cap the score below 1.0 (alignment loss is real).
    """
    if predicted is None:
        return 0.0
    if predicted == expected:
        return 1.0

    pred_calls = predicted.calls
    exp_calls = expected.calls

    # Action-sequence alignment is required for partial credit above 0.25.
    pred_actions = [c.action for c in pred_calls]
    exp_actions = [c.action for c in exp_calls]
    if pred_actions != exp_actions:
        # Wrong tool sequence — the validator parsed but the model picked
        # the wrong action(s). Floor reward, but >0 to distinguish from
        # parse-fail.
        return 0.25

    # Same action sequence; score per-call args overlap and average.
    per_call_scores: list[float] = []
    for pc, ec in zip(pred_calls, exp_calls):
        # Arg-set overlap as a Jaccard-like ratio over (key, value) pairs.
        # Each correct (key, value) is worth one point; missing or wrong
        # keys cost points symmetrically.
        pred_items = set(_hashable_items(pc.args))
        exp_items = set(_hashable_items(ec.args))
        if not exp_items and not pred_items:
            per_call_scores.append(1.0)
            continue
        union = pred_items | exp_items
        intersection = pred_items & exp_items
        ratio = len(intersection) / len(union) if union else 1.0
        # Map ratio into [0.5, 1.0]: action right with no arg overlap → 0.5,
        # action right with all args matching → 1.0 (caught by ==-fast-path
        # already, but the math works).
        per_call_scores.append(0.5 + 0.5 * ratio)

    return sum(per_call_scores) / len(per_call_scores)


def _hashable_items(args: dict[str, Any]) -> list[tuple[str, Any]]:
    """Convert args dict to hashable (key, value) pairs for set comparison.

    Nested values (lists, dicts) are JSON-serialized so we can compare them
    structurally — important for create_scene whose actions are nested lists.
    """
    import json

    out: list[tuple[str, Any]] = []
    for k, v in args.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, sort_keys=True, ensure_ascii=False)
        out.append((k, v))
    return out


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    valid = sum(result.valid for result in results)
    exact = sum(result.exact_match for result in results)
    action = sum(result.action_match for result in results)

    latencies: list[float] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    parse_strategies: Counter[str] = Counter()
    reasoning_chars = 0
    repair_attempts = 0
    repair_successes = 0
    runs_total = 0

    for result in results:
        for run in result.runs:
            runs_total += 1
            if run.latency_ms is not None:
                latencies.append(run.latency_ms)
            if run.input_tokens is not None:
                input_tokens.append(run.input_tokens)
            if run.output_tokens is not None:
                output_tokens.append(run.output_tokens)
            if isinstance(run.raw, dict):
                strategy = run.raw.get("parse_strategy")
                if isinstance(strategy, str):
                    parse_strategies[strategy] += 1
                chars = run.raw.get("reasoning_chars")
                if isinstance(chars, int):
                    reasoning_chars += chars
                attempts = run.raw.get("attempts")
                if isinstance(attempts, list):
                    repair_attempts += max(0, len(attempts) - 1)
                    if len(attempts) > 1 and run.error is None:
                        repair_successes += 1

    return {
        "total": total,
        "runs_per_case": runs_total // total if total else 0,
        "syntax_valid_rate": _rate(valid, total),
        "exact_match_rate": _rate(exact, total),
        "action_match_rate": _rate(action, total),
        "latency_ms_mean": _mean(latencies),
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "latency_ms_stddev": _stddev(latencies),
        "input_tokens_total": sum(input_tokens) if input_tokens else None,
        "output_tokens_total": sum(output_tokens) if output_tokens else None,
        "parse_strategy_counts": dict(parse_strategies) if parse_strategies else None,
        "reasoning_chars_total": reasoning_chars or None,
        "repair_attempts_total": repair_attempts or None,
        "repair_successes_total": repair_successes or None,
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


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pstdev(values), 2)
