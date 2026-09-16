"""[[lm_client]] §local HF + [[lm_model_registry]] discovery / availability.

No GPU, no downloads: the client is driven through its `generate_fn` seam
or through monkeypatched loaders; the registry-laziness check runs in a
subprocess so an earlier test importing torch cannot mask it.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ganglion.contract.builtins import get_catalog
from ganglion.lm import local_hf
from ganglion.lm.client import ModelOutputError
from ganglion.lm.local_hf import (
    LocalHFClient,
    load_local_model,
    local_hf_available,
    local_model_status,
    unload_local_model,
)
from ganglion.lm.prompts import SYSTEM_PROMPT_TEMPLATE
from ganglion.lm.registry import (
    ModelSpec,
    availability,
    build_client_from_spec,
    discover_adapters,
    load_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CATALOG = get_catalog("iot_light_5")
SPEC = ModelSpec(model_id="fake@local", kind="local_hf", provider="local", base_model="fake/model")

GOOD = '{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}'
FENCED = "Sure!\n```json\n" + GOOD + "\n```"
GARBAGE = "I cannot help with that."


def _hf_stack_present() -> bool:
    return all(importlib.util.find_spec(n) is not None for n in ("torch", "transformers"))


# ---------------------------------------------------------------------------
# LocalHFClient via the generate_fn seam
# ---------------------------------------------------------------------------


def test_generate_fn_seam_valid_then_fenced_then_garbage() -> None:
    outputs = iter([GOOD, FENCED, GARBAGE])
    seen: list[list[dict]] = []

    def generate_fn(messages, spec):
        seen.append(messages)
        assert spec is SPEC
        return next(outputs)

    client = LocalHFClient(CATALOG, SPEC, generate_fn=generate_fn)

    first = client.invoke("거실 불 켜줘")
    assert first.plan.calls[0].action == "set_light"
    assert first.raw == {"content": GOOD, "parse_strategy": "strict"}
    assert first.input_tokens is None and first.output_tokens is None
    assert first.latency_ms >= 0

    second = client.invoke("거실 불 켜줘")
    assert second.plan == first.plan
    assert second.raw["parse_strategy"] == "fenced"

    with pytest.raises(ModelOutputError) as excinfo:
        client.invoke("거실 불 켜줘")
    err = excinfo.value
    assert err.raw == GARBAGE
    assert err.attempts[0]["content"] == GARBAGE
    assert len(seen) == 3


def test_system_prompt_is_byte_identical_with_sft_template() -> None:
    captured: dict = {}

    def generate_fn(messages, spec):
        captured["messages"] = messages
        return GOOD

    LocalHFClient(CATALOG, SPEC, generate_fn=generate_fn).invoke("안방 불 꺼줘")
    messages = captured["messages"]
    assert messages[0] == {
        "role": "system",
        "content": SYSTEM_PROMPT_TEMPLATE.format(dsl=CATALOG.render_json_dsl()),
    }
    assert messages[1] == {"role": "user", "content": "안방 불 꺼줘"}


def test_build_client_from_spec_returns_local_client_lazily() -> None:
    client = build_client_from_spec(SPEC, CATALOG)
    assert isinstance(client, LocalHFClient)
    assert client.spec is SPEC and client.catalog is CATALOG


# ---------------------------------------------------------------------------
# Non-seam path with fake model / tokenizer (no torch)
# ---------------------------------------------------------------------------


class _FakeTokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return list(range(5 + len(messages)))

    def __call__(self, text):
        return SimpleNamespace(input_ids=list(range(len(text.split()))))


class _FakeModel:
    def __init__(self) -> None:
        self.device = "cpu"
        self.config = SimpleNamespace(vocab_size=100)
        self.moved_to: list[str] = []

    def to(self, device):
        self.moved_to.append(device)
        self.device = device
        return self

    def eval(self):
        return self


def test_invoke_counts_tokens_when_model_path_is_used(monkeypatch) -> None:
    fake = (_FakeModel(), _FakeTokenizer())
    monkeypatch.setattr(local_hf, "load_local_model", lambda spec: fake)
    calls: list[dict] = []

    def fake_generate_dsl(model, tokenizer, catalog, prompt, *, max_new_tokens, temperature=0.0, compiled_grammar=None):
        calls.append({"max_new_tokens": max_new_tokens, "grammar": compiled_grammar, "prompt": prompt})
        return GOOD

    monkeypatch.setattr(local_hf, "generate_dsl", fake_generate_dsl)
    spec = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="b", max_new_tokens=99)
    result = LocalHFClient(CATALOG, spec).invoke("거실 불 켜줘")
    assert result.plan.calls[0].args == {"room": "living", "state": "on"}
    assert result.input_tokens == 7 and result.output_tokens == 1
    assert calls == [{"max_new_tokens": 99, "grammar": None, "prompt": "거실 불 켜줘"}]


def test_grammar_mask_without_xgrammar_warns_once_and_decodes_unmasked(monkeypatch) -> None:
    fake = (_FakeModel(), _FakeTokenizer())
    monkeypatch.setattr(local_hf, "load_local_model", lambda spec: fake)
    monkeypatch.setattr(local_hf, "generate_dsl", lambda *a, **k: GOOD)
    monkeypatch.setattr(local_hf, "_GRAMMAR_WARNED", False)
    # Make `import xgrammar` fail regardless of the environment.
    monkeypatch.setitem(sys.modules, "xgrammar", None)
    spec = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="b", grammar_mask=True)
    client = LocalHFClient(CATALOG, spec)
    with pytest.warns(RuntimeWarning, match="xgrammar"):
        client.invoke("x")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.invoke("y")  # second call: no warning, still works


def test_load_local_model_cache_unload_and_status(monkeypatch) -> None:
    from ganglion.lm.finetune import sft

    loads: list[tuple] = []

    def fake_load_base(base_model, *, bf16=True):
        loads.append((base_model, bf16))
        return _FakeModel(), _FakeTokenizer()

    monkeypatch.setattr(sft, "load_base_for_inference", fake_load_base)
    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (True, "fake torch"))
    monkeypatch.setattr(local_hf, "_release_device_memory", lambda: None)
    monkeypatch.setattr(local_hf, "_MODEL_CACHE", {})
    spec = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="b", dtype="float32", device="cpu")

    assert local_model_status(spec)["status"] == "not_loaded"
    assert unload_local_model(spec) is False

    model, tok = load_local_model(spec)
    assert loads == [("b", False)]
    assert model.moved_to == ["cpu"]
    assert load_local_model(spec) == (model, tok)  # cache hit
    assert loads == [("b", False)]
    status = local_model_status(spec)
    assert status["status"] == "loaded" and status["device"] == "cpu" and status["detail"] == "fake torch"
    assert "vram_mb" in status

    # A different catalog does not change the key (shared weights).
    assert local_hf._cache_key(spec) in local_hf._MODEL_CACHE
    assert unload_local_model(spec) is True
    assert unload_local_model(spec) is False
    assert local_model_status(spec)["status"] == "not_loaded"


def test_load_local_model_uses_lora_loader_with_adapter(monkeypatch, tmp_path) -> None:
    from ganglion.lm.finetune import sft

    calls: list = []

    def fake_load_lora(adapter_dir, *, base_model, bf16=True):
        calls.append((adapter_dir, base_model, bf16))
        return _FakeModel(), _FakeTokenizer()

    monkeypatch.setattr(sft, "load_lora_for_inference", fake_load_lora)
    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (True, ""))
    monkeypatch.setattr(local_hf, "_MODEL_CACHE", {})
    spec = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="b", adapter_dir=str(tmp_path))
    model, _ = load_local_model(spec)
    assert calls == [(str(tmp_path), "b", True)]
    assert model.moved_to == []  # device "auto" → no .to()


def test_load_local_model_rejects_bad_dtype_before_any_import(monkeypatch) -> None:
    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    spec = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="b", dtype="float16")
    with pytest.raises(ValueError, match="dtype"):
        load_local_model(spec)


def test_load_local_model_unavailable_raises_runtime_error(monkeypatch) -> None:
    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (False, "torch not importable"))
    monkeypatch.setattr(local_hf, "_MODEL_CACHE", {})
    with pytest.raises(RuntimeError, match="torch not importable"):
        load_local_model(SPEC)
    assert local_model_status(SPEC) == {
        "status": "unavailable", "detail": "torch not importable", "device": "auto", "vram_mb": None,
    }


# ---------------------------------------------------------------------------
# availability (lazy) + real local_hf_available
# ---------------------------------------------------------------------------


def test_local_hf_available_matches_installed_stack() -> None:
    ok, detail = local_hf_available()
    assert ok == _hf_stack_present()
    assert isinstance(detail, str) and detail


def test_registry_load_and_availability_never_import_torch_eagerly() -> None:
    code = (
        "import sys\n"
        "from ganglion.lm.registry import load_registry, availability, build_client_from_spec\n"
        "from ganglion.contract.builtins import get_catalog\n"
        "reg = load_registry(discover=False)\n"
        "spec = reg.get('qwen3-0.6b@local')\n"
        "build_client_from_spec(spec, get_catalog('iot_light_5'))\n"
        "assert 'torch' not in sys.modules, 'torch imported at registry load / client build'\n"
        "ok, detail = availability(spec)\n"
        "import importlib.util\n"
        "exp = all(importlib.util.find_spec(n) is not None for n in ('torch', 'transformers'))\n"
        "assert ok == exp, (ok, detail)\n"
        "print(ok, detail)\n"
    )
    proc = subprocess.run([PY, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr


def test_availability_local_hf_missing_path_and_adapter(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (True, "ok"))
    missing = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="/definitely/not/here")
    ok, detail = availability(missing)
    assert not ok and "not found" in detail
    hub = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model="Qwen/Qwen3-0.6B")
    assert availability(hub) == (True, "ok")
    with_adapter = ModelSpec(
        model_id="m", kind="local_hf", provider="local", base_model="Qwen/Qwen3-0.6B", adapter_dir=str(tmp_path / "nope")
    )
    ok, detail = availability(with_adapter)
    assert not ok and "adapter_dir" in detail
    (tmp_path / "base").mkdir()
    local_path = ModelSpec(model_id="m", kind="local_hf", provider="local", base_model=str(tmp_path / "base"))
    assert availability(local_path) == (True, "ok")


# ---------------------------------------------------------------------------
# discover_adapters
# ---------------------------------------------------------------------------


def test_discover_adapters_on_tmp_tree(tmp_path, monkeypatch) -> None:
    runs = tmp_path / "runs"
    good = runs / "exp1" / "adapter"
    good.mkdir(parents=True)
    (good / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "Qwen/Qwen3-1.7B", "r": 32}))
    nobase = runs / "exp2" / "adapter_nobase"
    nobase.mkdir(parents=True)
    (nobase / "adapter_config.json").write_text(json.dumps({"r": 8}))
    corrupt = runs / "exp3" / "adapter_bad"
    corrupt.mkdir(parents=True)
    (corrupt / "adapter_config.json").write_text("{not json")
    too_deep = runs / "a" / "b" / "c" / "d" / "e" / "f" / "g"
    too_deep.mkdir(parents=True)
    (too_deep / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "x"}))
    monkeypatch.chdir(tmp_path)

    specs = discover_adapters()
    assert [s.model_id for s in specs] == ["local:adapter"]
    spec = specs[0]
    assert spec.kind == "local_hf" and spec.provider == "local" and spec.notes == "discovered"
    # adapter_dir is relative to the walked root (portable in models.yaml).
    assert spec.base_model == "Qwen/Qwen3-1.7B"
    assert spec.adapter_dir == str(Path("runs") / "exp1" / "adapter")
    assert Path(spec.adapter_dir).resolve() == good.resolve()
    assert spec.catalog_ids == ("*",)
    assert (spec.device, spec.dtype, spec.max_new_tokens, spec.grammar_mask) == ("auto", "bfloat16", 256, False)

    with_fallback = discover_adapters(base_model_fallback="fallback/base")
    ids = {s.model_id: s for s in with_fallback}
    assert ids["local:adapter_nobase"].base_model == "fallback/base"
    assert "local:adapter_bad" not in ids

    assert discover_adapters(roots=("nowhere",)) == []
    reg = load_registry(tmp_path / "missing.yaml", discover=True)
    assert reg.ids() == ["rules", "local:adapter"]
