"""[[lm_model_registry]] — configs/models.yaml → ModelSpec → ModelClient."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ganglion.contract.builtins import get_catalog
from ganglion.lm.dashscope import (
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
    _thinking_extra_body,
)
from ganglion.lm.registry import (
    LLM_TO_MODEL_ID,
    RULES_SPEC,
    ModelSpec,
    Registry,
    availability,
    build_client_from_spec,
    expand_env,
    load_registry,
    model_fingerprint,
    spec_to_row,
)
from ganglion.lm.rules import RuleBasedJSONDSLClient

REPO_YAML = Path(__file__).resolve().parents[1] / "configs" / "models.yaml"


class _StubOpenAI:
    """Stands in for `openai.OpenAI`; records constructor kwargs, no network."""

    instances: list["_StubOpenAI"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _StubOpenAI.instances.append(self)


@pytest.fixture
def stub_openai(monkeypatch):
    import openai

    _StubOpenAI.instances.clear()
    monkeypatch.setattr(openai, "OpenAI", _StubOpenAI)
    return _StubOpenAI


# ---------------------------------------------------------------------------
# (1) checked-in yaml + --llm mapping targets
# ---------------------------------------------------------------------------


def test_checked_in_yaml_loads_and_resolves_every_llm_mapping() -> None:
    reg = load_registry(REPO_YAML, discover=False)
    assert reg.path == REPO_YAML
    for llm_name, model_id in LLM_TO_MODEL_ID.items():
        spec = reg.get(model_id)
        assert spec.model_id == model_id, llm_name
    assert reg.get("rules").kind == "rules"
    assert reg.get("qwen3.6-plus@dashscope").client == "json-dsl"
    assert reg.get("qwen3.6-plus-text@dashscope").client == "freeform"
    assert reg.get("qwen3.6-plus-thinking@dashscope").client == "thinking"
    assert reg.get("qwen3.6-plus-native@dashscope").client == "native"
    assert reg.get("local@vllm").provider == "vllm"


def test_default_resolution_finds_repo_yaml(monkeypatch) -> None:
    monkeypatch.delenv("GANGLION_MODELS", raising=False)
    reg = load_registry(discover=False)
    assert reg.path is not None and reg.path.name == "models.yaml"
    assert "rules" in reg


# ---------------------------------------------------------------------------
# (2) ${VAR:-default} expansion
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_env_expansion_with_and_without_var(tmp_path, monkeypatch) -> None:
    yaml_path = _write_yaml(
        tmp_path / "models.yaml",
        "models:\n"
        "  - {model_id: m, kind: openai_compat, provider: vllm, client: json-dsl,\n"
        '     served_model: "${GANGLION_TEST_MODEL:-fallback-model}",\n'
        '     base_url: "${GANGLION_TEST_URL:-http://127.0.0.1:1/v1}", catalog_ids: ["*"]}\n',
    )
    monkeypatch.delenv("GANGLION_TEST_MODEL", raising=False)
    monkeypatch.delenv("GANGLION_TEST_URL", raising=False)
    spec = load_registry(yaml_path, discover=False).get("m")
    assert spec.served_model == "fallback-model"
    assert spec.base_url == "http://127.0.0.1:1/v1"

    monkeypatch.setenv("GANGLION_TEST_MODEL", "from-env")
    spec = load_registry(yaml_path, discover=False).get("m")
    assert spec.served_model == "from-env"


def test_expand_env_recurses_and_handles_unset_without_default(monkeypatch) -> None:
    monkeypatch.delenv("GANGLION_NOPE", raising=False)
    monkeypatch.setenv("GANGLION_YES", "y")
    out = expand_env({"a": "${GANGLION_NOPE}", "b": ["${GANGLION_YES}", 3], "c": "${GANGLION_NOPE:-d}"})
    assert out == {"a": "", "b": ["y", 3], "c": "d"}


def test_gan_models_env_var_selects_file(tmp_path, monkeypatch) -> None:
    yaml_path = _write_yaml(
        tmp_path / "alt.yaml",
        "models:\n  - {model_id: only, kind: rules, provider: none, catalog_ids: [iot_light_5]}\n",
    )
    monkeypatch.setenv("GANGLION_MODELS", str(yaml_path))
    reg = load_registry(discover=False)
    assert reg.ids() == ["only"] and reg.path == yaml_path


# ---------------------------------------------------------------------------
# (3) missing file → rules only
# ---------------------------------------------------------------------------


def test_missing_file_yields_rules_only_registry(tmp_path) -> None:
    reg = load_registry(tmp_path / "does-not-exist.yaml", discover=False)
    assert reg.ids() == ["rules"]
    assert reg.path is None
    assert reg.get("rules") == RULES_SPEC


# ---------------------------------------------------------------------------
# (4) validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry, needle",
    [
        ("{model_id: x, kind: bogus}", "kind"),
        ("{model_id: x, kind: openai_compat, client: fancy}", "client"),
        ("{model_id: x, kind: openai_compat, provider: mars}", "provider"),
        ("{model_id: x, kind: local_hf, provider: local}", "base_model"),
        ("{model_id: x, kind: rules, unknown_key: 1}", "unknown field"),
        ("{kind: rules}", "model_id"),
    ],
)
def test_bad_entries_raise_value_error_naming_the_entry(tmp_path, entry, needle) -> None:
    yaml_path = _write_yaml(tmp_path / "bad.yaml", f"models:\n  - {entry}\n")
    with pytest.raises(ValueError) as excinfo:
        load_registry(yaml_path, discover=False)
    assert needle in str(excinfo.value)
    assert "models[0]" in str(excinfo.value)


def test_models_must_be_a_list(tmp_path) -> None:
    yaml_path = _write_yaml(tmp_path / "bad.yaml", "models: {a: 1}\n")
    with pytest.raises(ValueError):
        load_registry(yaml_path, discover=False)


def test_duplicate_model_id_raises() -> None:
    with pytest.raises(ValueError):
        Registry([RULES_SPEC, RULES_SPEC])


def test_registry_get_unknown_raises_keyerror_listing_ids() -> None:
    reg = Registry([RULES_SPEC])
    with pytest.raises(KeyError) as excinfo:
        reg.get("nope")
    assert "rules" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (5)-(6) client construction
# ---------------------------------------------------------------------------


def test_build_rules_client() -> None:
    reg = load_registry(REPO_YAML, discover=False)
    client = build_client_from_spec(reg.get("rules"), get_catalog("iot_light_5"))
    assert isinstance(client, RuleBasedJSONDSLClient)
    assert client.invoke("거실 불 켜줘").plan.calls[0].action == "set_light"


def test_build_vllm_client_gets_empty_key_and_expanded_base_url(stub_openai, monkeypatch) -> None:
    monkeypatch.delenv("GANGLION_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("GANGLION_VLLM_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    reg = load_registry(REPO_YAML, discover=False)
    client = build_client_from_spec(reg.get("local@vllm"), get_catalog("iot_light_5"))
    assert isinstance(client, QwenJSONDSLClient)
    assert stub_openai.instances[-1].kwargs == {
        "api_key": "EMPTY",
        "base_url": "http://127.0.0.1:8000/v1",
    }
    assert client.config.provider == "vllm"
    assert client.config.model == "Qwen/Qwen3-1.7B"
    assert client.config.disable_thinking is True
    # vLLM thinking switch is the chat-template kwarg, not DashScope's flag.
    assert client._completer.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_dashscope_base_url_override_applies_only_to_dashscope(stub_openai, monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://override.local/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.delenv("GANGLION_VLLM_BASE_URL", raising=False)
    reg = load_registry(REPO_YAML, discover=False)
    catalog = get_catalog("iot_light_5")

    build_client_from_spec(reg.get("qwen3.6-plus@dashscope"), catalog)
    assert stub_openai.instances[-1].kwargs == {
        "api_key": "sk-test",
        "base_url": "http://override.local/v1",
    }
    build_client_from_spec(reg.get("local@vllm"), catalog)
    assert stub_openai.instances[-1].kwargs["base_url"] == "http://127.0.0.1:8000/v1"


def test_client_kind_mapping_and_thinking_switch(stub_openai, monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reg = load_registry(REPO_YAML, discover=False)
    catalog = get_catalog("iot_light_5")

    text = build_client_from_spec(reg.get("qwen3.6-plus-text@dashscope"), catalog)
    assert isinstance(text, QwenFreeformJSONDSLClient) and text.enable_thinking is False
    thinking = build_client_from_spec(reg.get("qwen3.6-plus-thinking@dashscope"), catalog)
    assert isinstance(thinking, QwenFreeformJSONDSLClient) and thinking.enable_thinking is True
    assert thinking.config.disable_thinking is False
    native = build_client_from_spec(reg.get("qwen3.6-plus-native@dashscope"), catalog)
    assert isinstance(native, QwenNativeToolClient)
    assert native.config.provider == "dashscope"
    # Unset api key env → "EMPTY" at build; availability reports it.
    assert stub_openai.instances[-1].kwargs["api_key"] == "EMPTY"
    assert availability(reg.get("qwen3.6-plus-native@dashscope")) == (False, "DASHSCOPE_API_KEY not set")


def test_base_url_none_falls_back_to_qwen_default(stub_openai, monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    spec = ModelSpec(model_id="x", kind="openai_compat", provider="openai", served_model="gpt-x")
    client = build_client_from_spec(spec, get_catalog("iot_light_5"))
    assert stub_openai.instances[-1].kwargs["base_url"].startswith("https://")
    assert _thinking_extra_body(client.config, True) is None


def test_thinking_extra_body_per_provider() -> None:
    from ganglion.lm.dashscope import QwenConfig

    assert _thinking_extra_body(QwenConfig(api_key="k", provider="dashscope"), False) == {"enable_thinking": False}
    assert _thinking_extra_body(QwenConfig(api_key="k", provider="vllm"), True) == {
        "chat_template_kwargs": {"enable_thinking": True}
    }
    assert _thinking_extra_body(QwenConfig(api_key="k", provider="openai"), True) is None
    with pytest.raises(ValueError):
        _thinking_extra_body(QwenConfig(api_key="k", provider="mars"), True)


# ---------------------------------------------------------------------------
# (7) fingerprint, (8) supports, (9) local entries
# ---------------------------------------------------------------------------


def test_model_fingerprint_stable_and_rules(tmp_path) -> None:
    assert model_fingerprint(RULES_SPEC) == "mf-rules"
    reg = load_registry(REPO_YAML, discover=False)
    spec = reg.get("qwen3.6-plus@dashscope")
    fp = model_fingerprint(spec)
    assert re.fullmatch(r"mf-[0-9a-f]{12}", fp)
    assert model_fingerprint(spec) == fp
    # Same served model, different adapter → different fingerprint.
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({"r": 32}), encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"\0" * 16)
    local = ModelSpec(model_id="l", kind="local_hf", provider="local", base_model="Qwen/Qwen3-1.7B")
    local_adapter = ModelSpec(
        model_id="la", kind="local_hf", provider="local", base_model="Qwen/Qwen3-1.7B", adapter_dir=str(adapter)
    )
    assert model_fingerprint(local) != model_fingerprint(local_adapter)
    assert model_fingerprint(local_adapter) == model_fingerprint(local_adapter)


def test_adapter_size_change_changes_fingerprint(tmp_path) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "w.safetensors").write_bytes(b"\0" * 16)
    spec = ModelSpec(model_id="la", kind="local_hf", provider="local", base_model="b", adapter_dir=str(adapter))
    before = model_fingerprint(spec)
    (adapter / "w.safetensors").write_bytes(b"\0" * 64)
    assert model_fingerprint(spec) != before


def test_supports_wildcard_and_exact() -> None:
    assert RULES_SPEC.supports("iot_light_5")
    assert not RULES_SPEC.supports("home_iot_20")
    anything = ModelSpec(model_id="a", kind="openai_compat")
    assert anything.supports("compiled/abc") and anything.supports("iot_light_5")
    reg = Registry([RULES_SPEC, anything])
    assert [s.model_id for s in reg.list("home_iot_20")] == ["a"]
    assert [s.model_id for s in reg.list()] == ["rules", "a"]
    assert reg.to_jsonable()[0]["model_id"] == "rules"


def test_local_entries_load_with_local_defaults() -> None:
    reg = load_registry(REPO_YAML, discover=False)
    for model_id, base in (("qwen3-0.6b@local", "Qwen/Qwen3-0.6B"), ("qwen3-1.7b@local", "Qwen/Qwen3-1.7B")):
        spec = reg.get(model_id)
        assert spec.kind == "local_hf" and spec.provider == "local"
        assert spec.base_model == base and spec.adapter_dir is None
        assert (spec.device, spec.dtype, spec.max_new_tokens, spec.grammar_mask) == ("auto", "bfloat16", 256, False)
        row = spec.to_jsonable()
        assert row["catalog_ids"] == ["*"] and row["device"] == "auto"
        json.dumps(row)  # JSON-safe


def test_to_jsonable_is_json_safe_with_trained_on() -> None:
    spec = ModelSpec(
        model_id="t", kind="local_hf", provider="local", base_model="b",
        trained_on={"catalog_fingerprint": "cf-1", "dataset_sha256": "", "run_id": "r"},
    )
    row = spec.to_jsonable()
    assert row["trained_on"] == {"catalog_fingerprint": "cf-1", "dataset_sha256": "", "run_id": "r"}
    json.dumps(row)


# ---------------------------------------------------------------------------
# availability rows + discovery merge
# ---------------------------------------------------------------------------


def test_availability_and_spec_to_row(monkeypatch) -> None:
    assert availability(RULES_SPEC) == (True, "")
    monkeypatch.delenv("GANGLION_TEST_KEY", raising=False)
    spec = ModelSpec(model_id="o", kind="openai_compat", api_key_env="GANGLION_TEST_KEY")
    assert availability(spec) == (False, "GANGLION_TEST_KEY not set")
    monkeypatch.setenv("GANGLION_TEST_KEY", "k")
    assert availability(spec) == (True, "")
    assert availability(ModelSpec(model_id="v", kind="openai_compat", provider="vllm")) == (True, "")
    row = spec_to_row(spec)
    assert row["available"] is True and row["available_detail"] == "" and row["model_id"] == "o"


def test_load_registry_merges_discovered_adapters_yaml_wins(tmp_path, monkeypatch) -> None:
    yaml_path = _write_yaml(
        tmp_path / "models.yaml",
        "models:\n"
        "  - {model_id: rules, kind: rules, provider: none, catalog_ids: [iot_light_5]}\n"
        "  - {model_id: 'local:adapter', kind: local_hf, provider: local, base_model: from-yaml}\n",
    )
    discovered = [
        ModelSpec(model_id="local:adapter", kind="local_hf", provider="local", base_model="from-disk", notes="discovered"),
        ModelSpec(model_id="local:other", kind="local_hf", provider="local", base_model="x", notes="discovered"),
    ]
    monkeypatch.setattr("ganglion.lm.registry.discover_adapters", lambda *a, **k: discovered)
    reg = load_registry(yaml_path, discover=True)
    assert reg.ids() == ["rules", "local:adapter", "local:other"]
    assert reg.get("local:adapter").base_model == "from-yaml"
    assert reg.get("local:other").notes == "discovered"
    assert load_registry(yaml_path, discover=False).ids() == ["rules", "local:adapter"]
