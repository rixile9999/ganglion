"""Auto-derive a continuous reward function from a Catalog.

The verifier is the factory's central asset: it gates synthesis output and (in
Phase 2+) supplies the RL reward. We deliberately avoid binary 0/1 rewards;
GRPO / DPO need partial-credit signal to learn anything when most rollouts
fail outright.

Reward levels (continuous in [0.0, 1.0]):

    0.00  — output is not parseable as DSL JSON
    0.30  — parses + schema-valid, but no gold to compare against
    0.30 + 0.40 * action_match_ratio + 0.20 * arg_match_ratio
          — parses, gold present, partial structural match
    1.00  — full structural equality with gold

For synth gating we typically threshold at >= 0.95 (full match). For RL we
use the raw continuous score.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.dsl.types import ActionPlan, ToolCall

VerifierFn = Callable[[Mapping[str, Any], str], float]


def make_verifier(catalog: Catalog) -> VerifierFn:
    """Return a continuous reward function bound to ``catalog``.

    The returned callable accepts an input mapping (with optional ``expected``
    key holding a gold DSL string) and a raw model output string, and returns
    a reward in ``[0.0, 1.0]``.
    """

    def verify(prompt: Mapping[str, Any], output: str) -> float:
        try:
            plan = catalog.parse_json_dsl(output)
        except DSLValidationError:
            return 0.0
        except Exception:  # malformed JSON, etc.
            return 0.0

        gold_raw = prompt.get("expected")
        if gold_raw is None:
            return 0.3  # parse + schema OK, no semantic ground truth

        try:
            gold_plan = catalog.parse_json_dsl(gold_raw)
        except DSLValidationError as exc:
            raise ValueError(f"gold expected DSL is invalid: {exc}") from exc

        if plan == gold_plan:
            return 1.0

        return 0.3 + 0.4 * _action_match_ratio(plan, gold_plan) + 0.2 * _arg_match_ratio(
            plan, gold_plan
        )

    return verify


def _action_match_ratio(plan: ActionPlan, gold: ActionPlan) -> float:
    """Fraction of positions where action names agree.

    Order-sensitive: position i in plan vs position i in gold. If plan has
    more calls than gold, extras count as misses against gold's length.
    """
    if not gold.calls:
        return 1.0 if not plan.calls else 0.0
    matches = 0
    n = max(len(plan.calls), len(gold.calls))
    for i in range(n):
        a = plan.calls[i].action if i < len(plan.calls) else None
        b = gold.calls[i].action if i < len(gold.calls) else None
        if a == b and a is not None:
            matches += 1
    return matches / max(len(gold.calls), 1)


def _arg_match_ratio(plan: ActionPlan, gold: ActionPlan) -> float:
    """Average per-call arg-match ratio, but only for calls whose action matches.

    For each gold call whose action matches the plan's call at the same
    position, compute (matching args) / (gold's arg count). Average over gold
    calls. Calls with no gold args contribute 1.0 if the plan also emits no
    args, otherwise 0.0.
    """
    if not gold.calls:
        return 1.0
    ratios: list[float] = []
    for i, gold_call in enumerate(gold.calls):
        if i >= len(plan.calls):
            ratios.append(0.0)
            continue
        plan_call = plan.calls[i]
        if plan_call.action != gold_call.action:
            ratios.append(0.0)
            continue
        ratios.append(_single_call_arg_match(plan_call, gold_call))
    return sum(ratios) / len(ratios)


def _single_call_arg_match(plan_call: ToolCall, gold_call: ToolCall) -> float:
    if not gold_call.args:
        return 1.0 if not plan_call.args else 0.5
    matches = sum(
        1 for k, v in gold_call.args.items() if plan_call.args.get(k) == v
    )
    return matches / len(gold_call.args)
