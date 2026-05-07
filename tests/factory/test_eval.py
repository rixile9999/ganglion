"""Offline tests for eval split logic. No model loading."""

from __future__ import annotations

from collections import Counter

from ganglion.factory.customer.eval import split_train_eval
from ganglion.factory.customer.synth import SynthExample


def _make(intent: str, strategy: str) -> SynthExample:
    return SynthExample(
        intent=intent,
        expected_dsl='{"calls":[{"action":"x","args":{}}]}',
        strategy=strategy,
    )


def test_split_is_stratified() -> None:
    examples = [_make(f"a-{i}", "tool_anchored:set_light") for i in range(20)] + [
        _make(f"b-{i}", "tool_anchored:get_light_state") for i in range(20)
    ]
    train, holdout = split_train_eval(examples, holdout_ratio=0.2, seed=42)
    train_strat = Counter(e.strategy for e in train)
    holdout_strat = Counter(e.strategy for e in holdout)
    # both strategies present in both splits
    assert set(train_strat) == set(holdout_strat) == {
        "tool_anchored:set_light",
        "tool_anchored:get_light_state",
    }
    # ~20% per strategy in holdout
    for s in train_strat:
        assert holdout_strat[s] == 4  # 20% of 20
        assert train_strat[s] == 16


def test_split_handles_small_strategy() -> None:
    """Even a strategy with only 2 examples should contribute >= 1 to holdout."""
    examples = [_make(f"a-{i}", "strat_a") for i in range(2)] + [
        _make(f"b-{i}", "strat_b") for i in range(20)
    ]
    train, holdout = split_train_eval(examples, holdout_ratio=0.2, seed=42)
    holdout_strat = Counter(e.strategy for e in holdout)
    assert holdout_strat["strat_a"] >= 1
    assert holdout_strat["strat_b"] >= 1


def test_split_deterministic_under_same_seed() -> None:
    examples = [_make(f"a-{i}", "tool_anchored:set_light") for i in range(50)]
    t1, h1 = split_train_eval(examples, holdout_ratio=0.2, seed=7)
    t2, h2 = split_train_eval(examples, holdout_ratio=0.2, seed=7)
    assert [e.intent for e in t1] == [e.intent for e in t2]
    assert [e.intent for e in h1] == [e.intent for e in h2]


def test_split_no_overlap() -> None:
    examples = [_make(f"a-{i}", "tool_anchored:set_light") for i in range(30)]
    train, holdout = split_train_eval(examples, holdout_ratio=0.3, seed=42)
    train_intents = {e.intent for e in train}
    holdout_intents = {e.intent for e in holdout}
    assert train_intents.isdisjoint(holdout_intents)
    assert len(train) + len(holdout) == 30
