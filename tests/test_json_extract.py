from rlm_poc.dsl.json_extract import parse_json_dsl_lenient


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
