"""[[lm_request_serve]] — one prompt in, one ServeResult out; failures preserved."""
from __future__ import annotations

import json

import pytest

from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import EnumArg, StringArg, ToolSpec
from ganglion.lm.client import ModelOutputError, ModelResult
from ganglion.lm.registry import RULES_SPEC, ModelSpec, Registry
from ganglion.lm.serve import ServeRequest, ServeResult, Server, hooks_diff

IOT = get_catalog("iot_light_5")


def _passthrough(args, catalog, depth, prompt=None):
    """Custom validator that keeps every arg — lets F⁰ accept unknown args."""
    return dict(args)


TOY = Catalog(
    name="toy",
    tools=(
        ToolSpec(
            name="ping",
            description="ping a room",
            args=(
                ("room", StringArg(description="room", required=True)),
                ("mode", EnumArg(values=("fast", "slow"), required=False)),
            ),
            custom_validator=_passthrough,
            strip_unknown_args=True,
            defaults_when_missing=(("mode", "fast", lambda args: True),),
        ),
    ),
)


def _resolve(catalog_id: str) -> Catalog:
    if catalog_id == "toy":
        return TOY
    return get_catalog(catalog_id)


class _Scripted:
    """Client returning canned ModelResults / raising canned exceptions."""

    def __init__(self, catalog: Catalog, outcomes) -> None:
        self.catalog = catalog
        self.outcomes = list(outcomes)

    def invoke(self, prompt: str) -> ModelResult:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        text = outcome if isinstance(outcome, str) else json.dumps(outcome, ensure_ascii=False)
        plan = self.catalog.parse_json_dsl(text, prompt=prompt)
        return ModelResult(
            plan=plan,
            raw={"content": text, "parse_strategy": "strict"},
            latency_ms=1.5,
            input_tokens=11,
            output_tokens=7,
        )


SCRIPTED_SPEC = ModelSpec(model_id="scripted", kind="rules", provider="none")


def _server(monkeypatch, outcomes=()):
    """Server whose 'scripted' model is a `_Scripted` client over the resolved catalog."""
    registry = Registry([RULES_SPEC, SCRIPTED_SPEC])
    server = Server(registry, _resolve)

    from ganglion.lm import registry as registry_mod

    real_build = registry_mod.build_client_from_spec

    def fake_build(spec, catalog, *, repair=None):
        if spec.model_id == "scripted":
            return _Scripted(catalog, outcomes)
        return real_build(spec, catalog, repair=repair)

    monkeypatch.setattr("ganglion.lm.serve.build_client_from_spec", fake_build)
    return server


# ---------------------------------------------------------------------------


def test_rules_model_end_to_end_offline() -> None:
    server = Server(Registry([RULES_SPEC]), _resolve)
    res = server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="거실 불 켜줘"))
    assert isinstance(res, ServeResult)
    assert res.error is None
    assert res.plan == {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    assert res.tool_calls == [{"name": "set_light", "arguments": {"room": "living", "state": "on"}}]
    assert res.f0_plan == res.plan and res.f0_error is None and res.hooks_diff == ()
    assert len(res.attempts) == 1 and res.raw_plan == res.plan
    assert res.parse_strategy == "json_object"
    assert res.model_id == "rules" and res.catalog_id == "iot_light_5"
    assert res.catalog_fingerprint.startswith("cf-") and res.catalog_fingerprint == IOT.fingerprint()
    assert res.latency_ms >= 0 and res.input_tokens is None
    json.dumps(res.to_dict(), ensure_ascii=False)


def test_model_output_error_preserves_raw_and_attempts(monkeypatch) -> None:
    raw = '{"calls":[{"action":"set_light","args":{"room":"living"}}]}'
    server = _server(monkeypatch, [ModelOutputError("set_light.state is required", raw=raw)])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="거실 불"))
    assert res.error == "set_light.state is required"
    assert res.plan is None and res.tool_calls == []
    assert res.raw == raw
    assert res.attempts[0]["content"] == raw
    assert res.raw_plan == json.loads(raw)
    assert res.f0_plan is None and res.f0_error is not None and "state" in res.f0_error
    assert res.hooks_diff == ()
    assert res.parse_strategy == "failed"
    assert res.input_tokens is None and res.output_tokens is None


def test_repair_exhausted_error_is_preserved_the_same_way(monkeypatch) -> None:
    from ganglion.analyzer.repair import RepairExhaustedError

    content = '{"calls":[{"action":"turn_on_lamp","args":{}}]}'
    exc = RepairExhaustedError(
        "unsupported action: turn_on_lamp",
        attempts=[{"attempt": 0, "content": content, "error": "unsupported action"}],
        final_content=content,
    )
    server = _server(monkeypatch, [exc])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="x", repair=True))
    assert res.error.startswith("unsupported action")
    assert res.raw == exc.raw and len(res.attempts) == 1
    assert res.raw_plan == json.loads(content)
    assert res.f0_error is not None and "turn_on_lamp" in res.f0_error


def test_default_rescue_shows_in_f0_error_not_hooks_diff(monkeypatch) -> None:
    out = {"calls": [{"action": "set_light", "args": {"room": "living", "brightness": 70}}]}
    server = _server(monkeypatch, [out])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="거실 70%"))
    assert res.error is None
    assert res.plan["calls"][0]["args"]["state"] == "on"
    assert res.f0_plan is None and "state" in res.f0_error
    assert res.hooks_diff == ()
    assert res.parse_strategy == "strict"
    assert (res.input_tokens, res.output_tokens, res.latency_ms) == (11, 7, 1.5)


def test_prompt_correction_rewrite_shows_in_hooks_diff(monkeypatch) -> None:
    out = {"calls": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}
    server = _server(monkeypatch, [out])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="복도 불 켜줘"))
    assert res.plan["calls"][0]["args"]["room"] == "hallway"
    assert res.f0_plan["calls"][0]["args"]["room"] == "office"
    assert res.hooks_diff == ("set_light.room: office → hallway",)


def test_dropped_unknown_arg_and_default_in_hooks_diff(monkeypatch) -> None:
    out = {"calls": [{"action": "ping", "args": {"room": "living", "extra": 1}}]}
    server = _server(monkeypatch, [out])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="toy", prompt="p"))
    assert res.error is None
    assert res.plan == {"calls": [{"action": "ping", "args": {"room": "living", "mode": "fast"}}]}
    assert res.f0_plan == {"calls": [{"action": "ping", "args": {"room": "living", "extra": 1}}]}
    assert "ping: dropped unknown arg 'extra'" in res.hooks_diff
    assert "ping.mode: (missing) → fast (default)" in res.hooks_diff
    assert res.catalog_fingerprint == TOY.fingerprint()


def test_generic_exception_becomes_error_without_raw(monkeypatch) -> None:
    server = _server(monkeypatch, [RuntimeError("boom")])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="x"))
    assert res.error == "RuntimeError: boom"
    assert res.raw is None and res.attempts == () and res.raw_plan is None
    assert res.f0_plan is None and res.f0_error is None
    assert res.plan is None and res.parse_strategy == "failed"


def test_unknown_model_id_raises_keyerror() -> None:
    server = Server(Registry([RULES_SPEC]), _resolve)
    with pytest.raises(KeyError):
        server.serve(ServeRequest(model_id="ghost", catalog_id="iot_light_5", prompt="x"))


def test_unresolvable_catalog_propagates() -> None:
    def resolve(catalog_id: str) -> Catalog:
        raise ValueError(f"not resolvable: {catalog_id}")

    server = Server(Registry([RULES_SPEC]), resolve)
    with pytest.raises(ValueError):
        server.serve(ServeRequest(model_id="rules", catalog_id="bfcl/simple_python", prompt="x"))


def test_client_cache_keyed_by_model_catalog_and_repair(monkeypatch) -> None:
    server = _server(monkeypatch, [])
    server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="거실 불 켜줘"))
    server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="안방 불 꺼줘"))
    assert server.build_count == 1
    server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="거실 불 켜줘", repair=True))
    assert server.build_count == 2
    server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="x", repair=True, repair_max_attempts=2))
    assert server.build_count == 3
    server.invalidate("rules")
    server.serve(ServeRequest(model_id="rules", catalog_id="iot_light_5", prompt="거실 불 켜줘"))
    assert server.build_count == 4


def test_hooks_diff_pure_function() -> None:
    assert hooks_diff(None, {"calls": []}) == ()
    a = {"calls": [{"action": "t", "args": {"x": 1, "y": "a"}}]}
    b = {"calls": [{"action": "t", "args": {"x": 1, "y": "b", "z": True}}]}
    assert hooks_diff(a, a) == ()
    assert hooks_diff(a, b) == ("t.z: (missing) → true (default)", "t.y: a → b")
    assert hooks_diff(b, a) == ("t: dropped unknown arg 'z'", "t.y: b → a")


def test_failed_generation_still_carries_token_counts(monkeypatch) -> None:
    """A generation that happened but did not validate is still billable.

    `LocalHFClient` / the freeform Qwen client count tokens *before* parsing;
    without carrying them on `ModelOutputError` the failed call vanished from
    token accounting (observed live: `input_tokens` null on a 456-token prompt).
    """
    raw = '{"calls":[{"action":"set_light","args":{"room":"living"}}]}'
    exc = ModelOutputError(
        "set_light.state is required", raw=raw, input_tokens=456, output_tokens=31
    )
    server = _server(monkeypatch, [exc])
    res = server.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="거실 불"))
    assert res.error == "set_light.state is required"
    assert res.input_tokens == 456 and res.output_tokens == 31
    # Absent counts stay absent (the default is still None).
    plain = _server(monkeypatch, [ModelOutputError("boom", raw=raw)])
    res2 = plain.serve(ServeRequest(model_id="scripted", catalog_id="iot_light_5", prompt="거실 불"))
    assert res2.input_tokens is None and res2.output_tokens is None
