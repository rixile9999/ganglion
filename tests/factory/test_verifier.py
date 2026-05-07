from __future__ import annotations

import json

import pytest

from ganglion.factory.customer.verifier import make_verifier
from ganglion.schema import get_catalog


@pytest.fixture()
def iot_verifier():
    return make_verifier(get_catalog("iot_light_5"))


def test_unparseable_returns_zero(iot_verifier) -> None:
    assert iot_verifier({}, "not json at all") == 0.0
    assert iot_verifier({}, "{") == 0.0
    assert iot_verifier({}, "{}") == 0.0  # missing 'calls'
    # Wrong action name
    bad_action = json.dumps(
        {"calls": [{"action": "definitely_not_a_tool", "args": {}}]}
    )
    assert iot_verifier({}, bad_action) == 0.0


def test_parses_without_gold_yields_partial(iot_verifier) -> None:
    valid = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    )
    assert iot_verifier({}, valid) == 0.3


def test_full_match_yields_one(iot_verifier) -> None:
    output = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    )
    assert iot_verifier({"expected": output}, output) == 1.0


def test_action_match_partial_credit(iot_verifier) -> None:
    gold = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    )
    # Same action, wrong arg value
    output = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on"}}]}
    )
    score = iot_verifier({"expected": gold}, output)
    # parse(0.3) + action_match(0.4 * 1.0) + arg_match(0.2 * 0.5) = 0.8
    assert score == pytest.approx(0.8)


def test_action_mismatch_partial_credit(iot_verifier) -> None:
    gold = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    )
    # Different valid action (get_light_state takes only `room`)
    output = json.dumps(
        {"calls": [{"action": "get_light_state", "args": {"room": "living"}}]}
    )
    score = iot_verifier({"expected": gold}, output)
    # parse(0.3) + action_match(0.4 * 0.0) + arg_match(0.2 * 0.0) = 0.3
    assert score == pytest.approx(0.3)


def test_invalid_gold_raises(iot_verifier) -> None:
    valid = json.dumps(
        {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    )
    with pytest.raises(ValueError, match="gold expected DSL is invalid"):
        iot_verifier({"expected": "not valid json"}, valid)


def test_reward_is_in_unit_interval(iot_verifier) -> None:
    # Run on a few diverse outputs; reward must always be in [0, 1]
    samples = [
        ("not json", {}),
        ("{}", {}),
        (json.dumps({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}), {}),
        (
            json.dumps({"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}),
            {"expected": json.dumps({"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on"}}]})},
        ),
    ]
    for output, prompt in samples:
        r = iot_verifier(prompt, output)
        assert 0.0 <= r <= 1.0, f"reward {r} out of range for output {output!r}"


def test_multi_catalog_works() -> None:
    """Verifier should work with home_iot_20 too."""
    verifier = make_verifier(get_catalog("home_iot_20"))
    output = json.dumps({"calls": [{"action": "definitely_not_a_tool", "args": {}}]})
    assert verifier({}, output) == 0.0
