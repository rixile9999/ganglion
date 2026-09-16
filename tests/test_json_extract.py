import pytest

from ganglion.contract.parse import parse_json_dsl_lenient
from ganglion.contract.tool_spec import DSLValidationError


def test_parse_json_dsl_lenient_extracts_fenced_json() -> None:
    raw = """
Here is the result:

```json
{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}
```
"""

    plan, strategy = parse_json_dsl_lenient(raw)

    assert strategy == "fenced"
    assert plan.to_jsonable() == {
        "calls": [
            {
                "action": "set_light",
                "args": {"room": "living", "state": "on"},
            }
        ]
    }


def test_parse_json_dsl_lenient_extracts_embedded_json() -> None:
    raw = 'Result: {"calls":[{"action":"get_light_state","args":{"room":"주방"}}]}'

    plan, strategy = parse_json_dsl_lenient(raw)

    assert strategy == "embedded"
    assert plan.calls[0].args["room"] == "kitchen"


# ---------------------------------------------------------------------------
# Error reporting: the salvage loop must not bury the real diagnosis.
# ---------------------------------------------------------------------------


def test_lenient_reports_the_plan_shaped_candidates_error_not_the_last() -> None:
    """A decodable-but-invalid plan surfaces its *validation* error.

    The loop walks every `{` in the text, so the inner `args` object is always
    tried last; its generic "expected 'calls' array" complaint used to
    overwrite `last_error` and reach the operator (and the failure taxonomy)
    instead of `brightness must be <= 100`.
    """
    raw = '{"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":250}}]}'
    with pytest.raises(DSLValidationError) as excinfo:
        parse_json_dsl_lenient(raw)
    assert str(excinfo.value) == "brightness must be <= 100"


def test_lenient_reports_the_embedded_plans_error_when_prose_wraps_it() -> None:
    raw = 'Sure! {"calls":[{"action":"set_light","args":{"room":"living","brightness":250}}]} done'
    with pytest.raises(DSLValidationError) as excinfo:
        parse_json_dsl_lenient(raw)
    assert "could not extract JSON DSL" not in str(excinfo.value)
    assert "brightness" in str(excinfo.value)


def test_lenient_still_says_could_not_extract_when_nothing_is_plan_shaped() -> None:
    with pytest.raises(DSLValidationError, match="could not extract JSON DSL"):
        parse_json_dsl_lenient("I'm sorry, I cannot help with that.")
    with pytest.raises(DSLValidationError, match="could not extract JSON DSL"):
        parse_json_dsl_lenient('{"nope": 1}')
