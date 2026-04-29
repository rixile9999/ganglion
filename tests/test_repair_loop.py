import pytest

from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.runtime.qwen import (
    CompletionResponse,
    RepairConfig,
    run_dsl_with_repair,
)
from ganglion.schema import get_catalog


class ScriptedCompleter:
    """Returns canned responses in sequence, recording each prompt."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> CompletionResponse:
        self.calls.append(list(messages))
        if not self.responses:
            raise RuntimeError("no more scripted responses")
        content = self.responses.pop(0)
        return CompletionResponse(content=content, input_tokens=10, output_tokens=5)


def test_repair_recovers_on_second_attempt() -> None:
    catalog = get_catalog("iot_light_5")
    bad = '{"calls": [{"action": "set_light", "args": {"room": "moon", "state": "on"}}]}'
    good = '{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}'
    completer = ScriptedCompleter([bad, good])

    result = run_dsl_with_repair(
        catalog,
        "거실 불 켜줘",
        completer,
        RepairConfig(enabled=True, max_attempts=1),
    )

    assert result.plan is not None
    assert result.plan.calls[0].args["room"] == "living"
    attempts = result.raw["attempts"]
    assert len(attempts) == 2
    assert "error" in attempts[0]
    assert "moon" in attempts[0]["error"] or "room" in attempts[0]["error"]
    repair_user_msg = completer.calls[1][-1]
    assert repair_user_msg["role"] == "user"
    assert "previous JSON failed" in repair_user_msg["content"]


def test_repair_disabled_propagates_error() -> None:
    catalog = get_catalog("iot_light_5")
    bad = '{"calls": [{"action": "set_light", "args": {"room": "moon", "state": "on"}}]}'
    completer = ScriptedCompleter([bad])

    with pytest.raises(DSLValidationError):
        run_dsl_with_repair(
            catalog,
            "거실 불 켜줘",
            completer,
            RepairConfig(enabled=False),
        )


def test_repair_exhausts_attempts() -> None:
    catalog = get_catalog("iot_light_5")
    bad = '{"calls": [{"action": "set_light", "args": {"room": "moon", "state": "on"}}]}'
    completer = ScriptedCompleter([bad, bad, bad])

    with pytest.raises(DSLValidationError):
        run_dsl_with_repair(
            catalog,
            "거실 불 켜줘",
            completer,
            RepairConfig(enabled=True, max_attempts=2),
        )
    assert len(completer.calls) == 3


def test_repair_recovers_invalid_json() -> None:
    catalog = get_catalog("iot_light_5")
    not_json = "여기까지 생각해봤는데 결론은: set_light"
    good = '{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}'
    completer = ScriptedCompleter([not_json, good])

    result = run_dsl_with_repair(
        catalog,
        "거실 불 켜줘",
        completer,
        RepairConfig(enabled=True, max_attempts=1),
    )
    assert result.plan is not None
    assert len(result.raw["attempts"]) == 2
