"""Offline tests for synth pipeline. No DashScope calls — teacher is mocked."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ganglion.factory.customer.synth import (
    DashScopeTeacher,
    SynthConfig,
    SynthExample,
    SynthStats,
    estimate_cost,
    read_jsonl,
    synth_gate,
    synthesize,
    write_jsonl,
)
from ganglion.schema import get_catalog


# ----------------------------------------------------------------------
# synth_gate
# ----------------------------------------------------------------------


def test_synth_gate_accepts_valid_pair() -> None:
    catalog = get_catalog("iot_light_5")
    pair = {
        "intent": "Turn on the living room light",
        "dsl": {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]},
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert kept and reason is None


def test_synth_gate_rejects_wrong_tool() -> None:
    catalog = get_catalog("iot_light_5")
    pair = {
        "intent": "What's the bedroom state?",
        "dsl": {"calls": [{"action": "get_light_state", "args": {"room": "bedroom"}}]},
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert not kept
    assert reason == "wrong_tool"


def test_synth_gate_rejects_unparseable_dsl() -> None:
    catalog = get_catalog("iot_light_5")
    pair = {
        "intent": "Turn on the light",
        "dsl": {"calls": [{"action": "definitely_not_a_tool", "args": {}}]},
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert not kept
    assert reason == "parse"


def test_synth_gate_rejects_empty_intent() -> None:
    catalog = get_catalog("iot_light_5")
    pair = {
        "intent": "",
        "dsl": {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]},
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert not kept


def test_synth_gate_rejects_multi_call_for_tool_anchored() -> None:
    catalog = get_catalog("iot_light_5")
    pair = {
        "intent": "Turn on living and bedroom",
        "dsl": {
            "calls": [
                {"action": "set_light", "args": {"room": "living", "state": "on"}},
                {"action": "set_light", "args": {"room": "bedroom", "state": "on"}},
            ]
        },
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert not kept
    assert reason == "wrong_tool"


def test_synth_gate_rejects_invalid_arg_value() -> None:
    catalog = get_catalog("iot_light_5")
    # "garage" is not a valid room enum value
    pair = {
        "intent": "Turn on garage light",
        "dsl": {"calls": [{"action": "set_light", "args": {"room": "garage", "state": "on"}}]},
    }
    kept, reason = synth_gate(catalog, pair, expected_tool="set_light")
    assert not kept
    assert reason == "parse"


# ----------------------------------------------------------------------
# Mock teacher + synthesize end-to-end
# ----------------------------------------------------------------------


class _MockTeacher:
    """Returns canned JSON responses, cycling through if asked more than provided."""

    def __init__(self, responses: list[str], in_toks: int = 100, out_toks: int = 50) -> None:
        self.responses = responses
        self.calls = 0
        self._in = in_toks
        self._out = out_toks

    def call(self, messages: list[dict[str, Any]]) -> tuple[str, int, int]:
        r = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return r, self._in, self._out


class _ToolAwareMockTeacher:
    """Reads the expected tool name from the user message and returns canned pairs for it."""

    def __init__(self, pairs_per_tool: dict[str, list[dict]], in_toks: int = 100, out_toks: int = 50) -> None:
        self.pairs_per_tool = pairs_per_tool
        self.calls = 0
        self._in = in_toks
        self._out = out_toks

    def call(self, messages: list[dict[str, Any]]) -> tuple[str, int, int]:
        user_content = messages[1]["content"]
        self.calls += 1
        for tool_name, pairs in self.pairs_per_tool.items():
            if f"must be exactly: {tool_name}" in user_content:
                return json.dumps({"pairs": pairs}), self._in, self._out
        return json.dumps({"pairs": []}), self._in, self._out


def _pair(action: str, args: dict, intent: str) -> dict:
    return {"intent": intent, "dsl": {"calls": [{"action": action, "args": args}]}}


def _valid_pair_response(actions_args: list[tuple[str, dict, str]]) -> str:
    """Build a teacher response from a list of (action, args, intent_text)."""
    pairs = [_pair(action, args, intent) for action, args, intent in actions_args]
    return json.dumps({"pairs": pairs})


def test_synthesize_keeps_valid_pairs() -> None:
    catalog = get_catalog("iot_light_5")
    teacher = _ToolAwareMockTeacher({
        "list_devices": [
            _pair("list_devices", {}, "list all devices"),
            _pair("list_devices", {}, "show me devices"),
        ],
        "get_light_state": [
            _pair("get_light_state", {"room": "living"}, "what is the living state"),
            _pair("get_light_state", {"room": "bedroom"}, "is bedroom on"),
        ],
        "set_light": [
            _pair("set_light", {"room": "living", "state": "on"}, "turn on living"),
            _pair("set_light", {"room": "kitchen", "state": "off"}, "turn off kitchen"),
        ],
        "schedule_light": [
            _pair(
                "schedule_light",
                {"room": "bedroom", "at": "07:00", "state": "on"},
                "schedule bedroom on at 7am",
            ),
            _pair(
                "schedule_light",
                {"room": "office", "at": "09:00", "state": "off"},
                "turn off office at 9am",
            ),
        ],
        "create_scene": [
            _pair(
                "create_scene",
                {
                    "name": "movie",
                    "actions": [
                        {"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 30}}
                    ],
                },
                "set up movie scene",
            ),
            _pair(
                "create_scene",
                {
                    "name": "focus",
                    "actions": [
                        {"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 80}}
                    ],
                },
                "set up focus scene",
            ),
        ],
    })
    cfg = SynthConfig(
        n_target=10,
        samples_per_request=2,
        max_attempts_per_tool=3,
        max_cost_usd=10.0,
        dedupe_threshold=1.01,  # disable dedup so we can count exactly
    )
    examples, stats = synthesize(catalog, cfg, teacher=teacher)

    # 5 tools × 2 target each = 10 expected
    assert stats.n_kept == 10
    assert stats.n_attempted == 10  # all valid, no drops
    assert all(isinstance(e, SynthExample) for e in examples)
    # Strategies should cover all 5 tools
    strategies = {e.strategy for e in examples}
    assert len(strategies) == 5


def test_synthesize_drops_invalid_pairs() -> None:
    catalog = get_catalog("iot_light_5")
    bad_response = json.dumps({
        "pairs": [
            # Wrong tool name
            {"intent": "x", "dsl": {"calls": [{"action": "definitely_not_a_tool", "args": {}}]}},
            # Invalid enum value
            {"intent": "y", "dsl": {"calls": [{"action": "set_light", "args": {"room": "garage", "state": "on"}}]}},
        ]
    })
    teacher = _MockTeacher([bad_response])
    cfg = SynthConfig(
        n_target=2, samples_per_request=2, max_attempts_per_tool=2, max_cost_usd=10.0
    )
    examples, stats = synthesize(catalog, cfg, teacher=teacher)
    assert stats.n_kept == 0
    assert stats.n_attempted >= 2


def test_synthesize_respects_cost_cap() -> None:
    catalog = get_catalog("iot_light_5")
    # Each call costs estimate_cost(qwen3.6-plus, 1_000_000, 1_000_000) ≈ 0.0008+0.0024 = $3.2
    teacher = _MockTeacher(
        [_valid_pair_response([("list_devices", {}, "list devices")])],
        in_toks=1_000_000,
        out_toks=1_000_000,
    )
    cfg = SynthConfig(
        n_target=1000,
        samples_per_request=1,
        max_attempts_per_tool=100,
        max_cost_usd=0.50,  # one call already exceeds this
    )
    examples, stats = synthesize(catalog, cfg, teacher=teacher)
    assert stats.cost_capped
    # Should have made at most a few calls before cap fired
    assert stats.n_calls <= 3


def test_synthesize_dedup_removes_near_duplicates() -> None:
    """Use a single-tool catalog so the test targets dedup behavior, not routing."""
    from dataclasses import replace
    catalog = get_catalog("iot_light_5")
    set_light = next(t for t in catalog.tools if t.name == "set_light")
    one_tool = replace(catalog, tools=(set_light,))

    teacher = _ToolAwareMockTeacher({
        "set_light": [
            _pair("set_light", {"room": "living", "state": "on"}, "turn on the living room light"),
            _pair("set_light", {"room": "living", "state": "on"}, "turn on the living room light"),
        ],
    })
    cfg = SynthConfig(
        n_target=2,
        samples_per_request=2,
        max_attempts_per_tool=2,
        max_cost_usd=10.0,
        dedupe_threshold=0.9,
    )
    examples, stats = synthesize(one_tool, cfg, teacher=teacher)
    # Both pairs accepted, then dedup removes one
    assert stats.n_kept == 2
    assert stats.n_deduped == 1
    assert len(examples) == 1


def test_jsonl_roundtrip(tmp_path) -> None:
    examples = [
        SynthExample(
            intent="turn on living",
            expected_dsl='{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}',
            strategy="tool_anchored:set_light",
            teacher_score=1.0,
        )
    ]
    path = tmp_path / "out.jsonl"
    write_jsonl(examples, path)
    loaded = read_jsonl(path)
    assert len(loaded) == 1
    assert loaded[0].intent == examples[0].intent
    assert loaded[0].strategy == examples[0].strategy


def test_estimate_cost_known_model() -> None:
    # qwen3.6-plus: 0.0008 input, 0.0024 output per 1k
    cost = estimate_cost("qwen3.6-plus", 1000, 1000)
    assert cost == pytest.approx(0.0032)


def test_estimate_cost_unknown_model_uses_default() -> None:
    cost = estimate_cost("unknown-model", 1000, 1000)
    # default 0.001 / 0.003
    assert cost == pytest.approx(0.004)


def test_dashscope_teacher_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        DashScopeTeacher()
