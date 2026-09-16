import pytest

from ganglion.contract.tool_spec import DSLValidationError
from ganglion.lm.dashscope import (
    CompletionResponse,
    RepairConfig,
    run_dsl_with_repair,
)
from ganglion.contract.builtins import get_catalog


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
    # Unknown action — prompt-aware post-correction can't rescue this, so
    # it stays an invalid payload that exercises the repair loop.
    bad = '{"calls": [{"action": "turn_on_lamp", "args": {"room": "living", "state": "on"}}]}'
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
    assert "turn_on_lamp" in attempts[0]["error"] or "unsupported" in attempts[0]["error"]
    repair_user_msg = completer.calls[1][-1]
    assert repair_user_msg["role"] == "user"
    assert "previous JSON failed" in repair_user_msg["content"]


def test_repair_disabled_propagates_error() -> None:
    catalog = get_catalog("iot_light_5")
    # Unknown action — prompt-aware post-correction can't rescue this, so
    # it stays an invalid payload that exercises the repair loop.
    bad = '{"calls": [{"action": "turn_on_lamp", "args": {"room": "living", "state": "on"}}]}'
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
    # Unknown action — prompt-aware post-correction can't rescue this, so
    # it stays an invalid payload that exercises the repair loop.
    bad = '{"calls": [{"action": "turn_on_lamp", "args": {"room": "living", "state": "on"}}]}'
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


def test_exhausted_repair_raises_repair_exhausted_error_with_raw() -> None:
    """Terminal failure carries the attempt chain so the trace store keeps it."""
    from ganglion.analyzer.repair import RepairExhaustedError

    catalog = get_catalog("iot_light_5")
    bad = '{"calls": [{"action": "turn_on_lamp", "args": {"room": "living", "state": "on"}}]}'
    completer = ScriptedCompleter([bad, bad])

    with pytest.raises(RepairExhaustedError) as excinfo:
        run_dsl_with_repair(
            catalog,
            "거실 불 켜줘",
            completer,
            RepairConfig(enabled=True, max_attempts=1),
        )
    exc = excinfo.value
    assert isinstance(exc, DSLValidationError)
    assert len(exc.attempts) == 2
    assert [a["attempt"] for a in exc.attempts] == [0, 1]
    assert all(a["content"] == bad for a in exc.attempts)
    assert all("error" in a for a in exc.attempts)
    assert exc.raw == {"attempts": list(exc.attempts), "final_content": bad}
    assert exc.__cause__ is not None


def test_repair_disabled_error_also_carries_raw() -> None:
    from ganglion.analyzer.repair import RepairExhaustedError

    catalog = get_catalog("iot_light_5")
    not_json = "여기까지 생각해봤는데 결론은: set_light"
    with pytest.raises(RepairExhaustedError) as excinfo:
        run_dsl_with_repair(catalog, "거실 불 켜줘", ScriptedCompleter([not_json]), RepairConfig())
    assert excinfo.value.raw["final_content"] == not_json
    assert excinfo.value.attempts[0]["input_tokens"] == 10
