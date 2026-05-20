"""Tests for the graded_score reward function used by S3 (DPO).

The function maps (predicted, expected) ActionPlan pairs onto [0.0, 1.0]
in five interpretable bands:

    0.0   parse fail / no prediction
    0.25  parse OK, action sequence wrong
    0.5+  action right, args partially right (linear up to 1.0)
    1.0   exact match
"""

from __future__ import annotations

import pytest

from ganglion.contract.types import ActionPlan, ToolCall
from ganglion.analyzer.metrics import graded_score


def _plan(*calls: tuple[str, dict]) -> ActionPlan:
    return ActionPlan(calls=tuple(ToolCall(action=a, args=args) for a, args in calls))


def test_score_none_is_zero() -> None:
    """Parse failure floors the reward to 0."""
    expected = _plan(("set_light", {"room": "living", "state": "on"}))
    assert graded_score(None, expected) == 0.0


def test_score_exact_match_is_one() -> None:
    expected = _plan(("set_light", {"room": "living", "state": "on"}))
    predicted = _plan(("set_light", {"room": "living", "state": "on"}))
    assert graded_score(predicted, expected) == 1.0


def test_score_wrong_action_is_quarter() -> None:
    """Parse OK but wrong tool — reward floored above zero, below the
    "right tool, wrong args" band."""
    expected = _plan(("set_light", {"room": "living", "state": "on"}))
    predicted = _plan(("get_light_state", {"room": "living"}))
    assert graded_score(predicted, expected) == 0.25


def test_score_right_action_partial_args() -> None:
    """Right tool, half args match → 0.5 + 0.5 * 0.5 = 0.75 (Jaccard 0.5)."""
    expected = _plan((
        "set_light",
        {"room": "living", "state": "on", "brightness": 70},
    ))
    predicted = _plan((
        "set_light",
        {"room": "living", "state": "on", "brightness": 50},  # 1 of 3 wrong
    ))
    score = graded_score(predicted, expected)
    # union = {(room,living), (state,on), (brightness,70), (brightness,50)} → 4
    # intersection = {(room,living), (state,on)} → 2
    # Jaccard = 2/4 = 0.5  →  0.5 + 0.5*0.5 = 0.75
    assert score == pytest.approx(0.75, abs=1e-9)


def test_score_right_action_no_arg_overlap() -> None:
    """Right tool, every arg differs → 0.5 (the floor for the right-action band)."""
    expected = _plan(("set_light", {"room": "living", "state": "on"}))
    predicted = _plan(("set_light", {"room": "bedroom", "state": "off"}))
    # union = 4 distinct (key,value) pairs, intersection empty → 0.0 ratio
    assert graded_score(predicted, expected) == pytest.approx(0.5, abs=1e-9)


def test_score_multi_call_average() -> None:
    """Multi-call plan: per-call scores average."""
    expected = _plan(
        ("set_light", {"room": "living", "state": "on"}),
        ("set_light", {"room": "bedroom", "state": "on"}),
    )
    predicted = _plan(
        ("set_light", {"room": "living", "state": "on"}),       # 1.0
        ("set_light", {"room": "bedroom", "state": "off"}),     # 0.5 + 0.5*1/3 ≈ 0.667
    )
    # call 2: union = 3 (room,bed), (state,on), (state,off) → 3
    #         intersection = {(room,bedroom)} → 1
    #         ratio 1/3 → 0.5 + 0.5*(1/3) ≈ 0.6667
    expected_score = (1.0 + (0.5 + 0.5 * (1 / 3))) / 2
    assert graded_score(predicted, expected) == pytest.approx(expected_score, abs=1e-9)


def test_score_handles_nested_list_args() -> None:
    """create_scene.actions is a nested list — must be comparable, not crash."""
    expected = _plan((
        "create_scene",
        {
            "name": "movie",
            "actions": [{"action": "set_light", "args": {"room": "living", "state": "on"}}],
        },
    ))
    predicted_same = _plan((
        "create_scene",
        {
            "name": "movie",
            "actions": [{"action": "set_light", "args": {"room": "living", "state": "on"}}],
        },
    ))
    assert graded_score(predicted_same, expected) == 1.0
    # Different nested args → Jaccard < 1
    predicted_diff = _plan((
        "create_scene",
        {
            "name": "movie",
            "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on"}}],
        },
    ))
    score = graded_score(predicted_diff, expected)
    assert 0.5 <= score < 1.0


def test_score_different_call_count_is_capped() -> None:
    """Different call counts → action sequence mismatch → 0.25 floor."""
    expected = _plan(
        ("set_light", {"room": "living", "state": "on"}),
        ("set_light", {"room": "bedroom", "state": "on"}),
    )
    predicted = _plan(("set_light", {"room": "living", "state": "on"}))
    # Action sequence mismatch (length differs)
    assert graded_score(predicted, expected) == 0.25


def test_score_is_monotonic_under_arg_correction() -> None:
    """Adding correct args strictly improves score."""
    expected = _plan((
        "set_light",
        {"room": "living", "state": "on", "brightness": 70, "color_temp": "warm"},
    ))
    no_args = _plan(("set_light", {}))
    one_arg = _plan(("set_light", {"room": "living"}))
    two_args = _plan(("set_light", {"room": "living", "state": "on"}))
    three_args = _plan(("set_light", {"room": "living", "state": "on", "brightness": 70}))

    s0 = graded_score(no_args, expected)
    s1 = graded_score(one_arg, expected)
    s2 = graded_score(two_args, expected)
    s3 = graded_score(three_args, expected)
    s4 = graded_score(expected, expected)

    assert 0.5 <= s0 < s1 < s2 < s3 < s4 == 1.0
