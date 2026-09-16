[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[lm_client]] · [[lm_request_serve]]

# lm_model_registry

Named model registry for Module 1. Today a model is chosen by a `--llm` flavour (`rules | qwen | qwen-text | qwen-thinking | qwen-native`) plus environment variables (`DASHSCOPE_API_KEY`, `GANGLION_MODEL`, `DASHSCOPE_BASE_URL`, `GANGLION_ENABLE_THINKING`), so a trace's `model_id` cannot say which served model, endpoint or adapter produced it, and a locally served vLLM model only works through the `DASHSCOPE_BASE_URL` trick. This task replaces that with `configs/models.yaml` → `ModelSpec` → `ModelClient`, so every run, session, label and manifest carries a real `model_id` and `model_fingerprint`, and the console's model picker ([[console_operator]]) lists the same entries the CLI accepts.

## Role

Resolve a `model_id` to a `ModelSpec` from a checked-in YAML registry (plus discovered LoRA adapters) and build the matching `ModelClient` for a given `Catalog`.

## Scope

- **in-scope**:
  - `ganglion/lm/registry.py` (new):
    ```python
    @dataclass(frozen=True)
    class ModelSpec:
        model_id: str
        kind: str                      # "rules" | "openai_compat" | "local_hf"
        client: str = "json-dsl"       # "json-dsl" | "freeform" | "thinking" | "native"
        provider: str = "dashscope"    # "dashscope" | "vllm" | "openai" | "none" | "local"
        base_url: str | None = None
        served_model: str | None = None
        api_key_env: str | None = None # None → "EMPTY"
        base_model: str | None = None
        adapter_dir: str | None = None
        catalog_ids: tuple[str, ...] = ("*",)
        trained_on: Mapping[str, Any] | None = None   # {"catalog_fingerprint", "dataset_sha256", "run_id"}
        notes: str = ""
        device: str = "auto"           # local_hf only: "auto" (device_map) | "cuda" | "cuda:N" | "cpu"
        dtype: str = "bfloat16"        # local_hf only: "bfloat16" | "float32" (checked at load time)
        max_new_tokens: int = 256      # local_hf only: passed to generate_dsl
        grammar_mask: bool = False     # local_hf only: honoured only when xgrammar is importable
        def to_jsonable(self) -> dict: ...
        def supports(self, catalog_id: str) -> bool     # "*" or exact match
    class Registry:
        def __init__(self, specs: Sequence[ModelSpec], *, path: Path | None = None)
        def get(self, model_id: str) -> ModelSpec       # KeyError if unknown
        def list(self, catalog_id: str | None = None) -> list[ModelSpec]   # filtered by supports()
        def to_jsonable(self) -> list[dict]
        path: Path | None
    def load_registry(path: str | Path | None = None, *, discover: bool = True) -> Registry
    def build_client_from_spec(spec: ModelSpec, catalog: Catalog, *, repair: RepairConfig | None = None) -> ModelClient
    def model_fingerprint(spec: ModelSpec) -> str
    def availability(spec: ModelSpec) -> tuple[bool, str]
    def spec_to_row(spec: ModelSpec) -> dict            # to_jsonable() + {"available", "available_detail"}
    def discover_adapters(roots: Sequence[str] = ("runs",), *, base_model_fallback: str | None = None) -> list[ModelSpec]
    ```
  - **Resolution order** for the registry file: explicit `path` → `GANGLION_MODELS` env → `configs/models.yaml` relative to cwd, then to the repo root. Missing file → a registry holding only the built-in `rules` spec (not an error). Entries are validated on load: `kind ∈ {rules, openai_compat, local_hf}`, `client ∈ {json-dsl, freeform, thinking, native}`, `provider ∈ {dashscope, vllm, openai, none, local}`, `local_hf` requires `base_model`; anything else → `ValueError` naming the entry.
  - `${VAR:-default}` expansion in every string value (e.g. `${GANGLION_VLLM_MODEL:-Qwen/Qwen3-1.7B}`); `DASHSCOPE_BASE_URL`, when set, overrides `base_url` for `provider: dashscope` only (keeps today's local-vLLM trick working).
  - `build_client_from_spec` mapping (the client classes themselves are [[lm_client]]):
    - `rules` → `RuleBasedJSONDSLClient()`.
    - `openai_compat` → explicit `QwenConfig(api_key=os.environ.get(spec.api_key_env or "", "EMPTY"), model=spec.served_model, base_url=spec.base_url or <QwenConfig default>, disable_thinking=(spec.client != "thinking"), provider=spec.provider)` — never `base_url=None` into `OpenAI(...)` — then by `spec.client`: `json-dsl` → `QwenJSONDSLClient(catalog, config, repair=repair)`; `freeform` → `QwenFreeformJSONDSLClient(catalog, config, enable_thinking=False)`; `thinking` → `QwenFreeformJSONDSLClient(catalog, config, enable_thinking=True)`; `native` → `QwenNativeToolClient(catalog, config)`.
    - `local_hf` → `LocalHFClient(catalog, spec)` ([[lm_client]]). Construction is lazy: no `torch` import and no weights load until the first `invoke()` or an explicit `load_local_model(spec)`, so building a client for an unavailable local model succeeds and the failure surfaces at first use.
  - `model_fingerprint(spec)`: `"mf-" + sha256(served_model | base_model | adapter sha)[:12]`, where the adapter sha covers `adapter_config.json` plus the sizes of `*.safetensors` in `adapter_dir`; `"mf-rules"` for `rules`.
  - `availability(spec) -> tuple[bool, str]`: `rules` → `(True, "")`; `openai_compat` → `(True, "")` when `api_key_env` is `None` or the env var is set, else `(False, "<env> not set")`; `local_hf` → `local_hf_available()` from `ganglion/lm/local_hf.py` (torch + transformers importable; the detail names the CUDA state) **and**, when `base_model` is a filesystem path, that it exists (a hub id is not resolved — no network at availability time). Checked lazily: importing the registry or calling `load_registry` never imports `torch`.
  - Local-model lifecycle — consumed here and by [[console_operator]], implemented in `ganglion/lm/local_hf.py` and specified in [[lm_client]]: `local_hf_available() -> tuple[bool, str]`; `load_local_model(spec) -> (model, tokenizer)` (process-wide `_MODEL_CACHE` keyed `(base_model, adapter_dir, dtype, device)`, shared across catalogs, loads serialised by a module lock); `unload_local_model(spec) -> bool`; `local_model_status(spec) -> {"status": "loaded" | "not_loaded" | "unavailable", "detail", "device", "vram_mb": float | None}`. The registry never calls `load_local_model` itself.
  - `discover_adapters`: every directory under the roots (max depth 6) containing `adapter_config.json` becomes `ModelSpec(model_id=f"local:{dir.name}", kind="local_hf", provider="local", base_model=adapter_config["base_model_name_or_path"], adapter_dir=str(dir), catalog_ids=("*",), notes="discovered")`; `load_registry(discover=True)` merges them after the YAML entries (YAML wins on an id clash). `base_model_fallback` is used when the file has no `base_model_name_or_path` (no fallback → skip the directory); an unreadable / non-JSON `adapter_config.json` → skip (logged), never abort. Discovered specs carry the `device` / `dtype` / `max_new_tokens` / `grammar_mask` defaults.
  - `configs/models.yaml` (checked in; list under `models:`):
    ```yaml
    models:
      - {model_id: rules, kind: rules, client: json-dsl, provider: none, catalog_ids: [iot_light_5, home_iot_20, smart_home_50], notes: offline regex stand-in (single call; scaling tiers only)}
      - {model_id: qwen3.6-plus@dashscope,          kind: openai_compat, provider: dashscope, client: json-dsl, served_model: qwen3.6-plus, base_url: https://dashscope-intl.aliyuncs.com/compatible-mode/v1, api_key_env: DASHSCOPE_API_KEY, catalog_ids: ["*"]}
      - {model_id: qwen3.6-plus-text@dashscope,     ..., client: freeform}
      - {model_id: qwen3.6-plus-thinking@dashscope, ..., client: thinking}
      - {model_id: qwen3.6-plus-native@dashscope,   ..., client: native}
      - {model_id: local@vllm, kind: openai_compat, provider: vllm, client: json-dsl, served_model: "${GANGLION_VLLM_MODEL:-Qwen/Qwen3-1.7B}", base_url: "${GANGLION_VLLM_BASE_URL:-http://127.0.0.1:8000/v1}", catalog_ids: ["*"], notes: "vllm serve … --enable-lora --max-lora-rank 32"}
      - {model_id: qwen3-0.6b@local, kind: local_hf, provider: local, base_model: Qwen/Qwen3-0.6B, catalog_ids: ["*"]}
      - {model_id: qwen3-1.7b@local, kind: local_hf, provider: local, base_model: Qwen/Qwen3-1.7B, catalog_ids: ["*"]}
      # - {model_id: iot-lora@local, kind: local_hf, provider: local, base_model: Qwen/Qwen3-1.7B, adapter_dir: runs/<…>/adapter,
      #    trained_on: {catalog_fingerprint: cf-…, dataset_sha256: …, run_id: …}}
    ```
  - CLI wiring in `ganglion/cli.py`: `--model <model_id>` and `--models <yaml>`; `--llm` keeps working with `default=None` and is mutually exclusive with `--model` (an argparse group — the default **must** be `None`, otherwise any "was `--llm` given?" check sees `"rules"`); both absent → registry id `rules`. `--llm` maps to registry ids: `rules → rules`, `qwen → qwen3.6-plus@dashscope`, `qwen-text → qwen3.6-plus-text@dashscope`, `qwen-thinking → qwen3.6-plus-thinking@dashscope`, `qwen-native → qwen3.6-plus-native@dashscope`. `build_client(name, catalog, *, repair)` remains as a thin `--llm`-name wrapper that delegates to `build_client_from_spec`. `--llm rules` with `--bfcl` still exits (rules has no BFCL adapter).
  - Tests: `tests/test_lm_registry.py`; the local-HF discovery / availability cases in `tests/test_lm_local_hf.py` (`discover_adapters` over a `tmp_path` tree with a fake `adapter_config.json`; `availability()` on a `local_hf` spec reporting the transformers state without `torch` entering `sys.modules` at registry-load time).
- **out-of-scope**:
  - The client classes, their prompts, `ModelOutputError`, provider thinking quirks — [[lm_client]].
  - In-process HF inference — `LocalHFClient.invoke`, the `generate_dsl` path, the grammar mask, and the bodies of `load_local_model` / `unload_local_model` / `local_model_status` — [[lm_client]]; this doc only names the lifecycle surface it and the console consume.
  - GPU residency policy (eviction, multi-model VRAM budgeting) — `_MODEL_CACHE` is a plain dict and `unload_local_model` is the only eviction; the HTTP `health` / `load` / `unload` routes — [[console_operator]].
  - Serving a request, F⁰ / Fᴷ diff, client caching per `(model_id, catalog_id, repair, …)` — [[lm_request_serve]].
  - HTTP surface: `GET /api/models`, `/health` probes (`GET <base_url>/models`), `fingerprint_match` against the active catalog — [[console_operator]].
  - Producing adapters and `trained_on` provenance at training time — [[lm_finetune]]; this task only *reads* `adapter_config.json`.
  - Repair policy semantics — [[analyzer_repair_policy]]; the `repair` kwarg is passed through untouched.
  - Secrets: the registry stores the *name* of an env var, never a key; no keyring, no `.env` loading.
  - Persisting `model_id` / `model_fingerprint` into traces and manifests — [[analyzer_trace_store]], [[analyzer_run_manifest]] consume the values.
- **on violation**: if a new provider needs anything beyond `base_url` + api-key header + the thinking switch (custom auth, a non-OpenAI request shape), **stop** — do not special-case it in the registry. Add a client class under [[lm_client]] with its own `provider` value, then register it here.

## Procedure

```
load_registry(path=None, *, discover=True):
    file  ← path or $GANGLION_MODELS or ./configs/models.yaml or <repo>/configs/models.yaml
    specs ← [ModelSpec(**expand_env(entry)) for entry in yaml["models"]] if file exists else [RULES_SPEC]
    validate kinds / clients / providers / local_hf.base_model            # ValueError on the first bad entry
    if discover: specs += [s for s in discover_adapters() if s.model_id ∉ {yaml ids}]
    return Registry(specs, path=file)

build_client_from_spec(spec, catalog, *, repair=None):
    "rules"          → RuleBasedJSONDSLClient()
    "openai_compat"  → config ← QwenConfig(api_key=env(spec.api_key_env) or "EMPTY", model=spec.served_model,
                                           base_url=($DASHSCOPE_BASE_URL if spec.provider == "dashscope" and set)
                                                    or spec.base_url or <QwenConfig default>,
                                           disable_thinking=(spec.client != "thinking"), provider=spec.provider)
                       client by spec.client (json-dsl | freeform | thinking | native)   # [[lm_client]]
    "local_hf"       → LocalHFClient(catalog, spec)              # lazy: weights load on first invoke / load_local_model ([[lm_client]])

availability(spec):
    "rules"          → (True, "")
    "openai_compat"  → (True, "") if spec.api_key_env is None or env set else (False, f"{spec.api_key_env} not set")
    "local_hf"       → ok, detail ← local_hf_available()                 # lazy import inside the call, never at module import
                       if ok and spec.base_model looks like a path and not exists → (False, f"{base_model} not found")

discover_adapters(roots=("runs",), *, base_model_fallback=None):
    for dir in walk(roots, max_depth=6) if (dir / "adapter_config.json").is_file():
        cfg  ← json.load(adapter_config.json)                              # unreadable → log + skip
        base ← cfg.get("base_model_name_or_path") or base_model_fallback   # None → skip
        yield ModelSpec(model_id=f"local:{dir.name}", kind="local_hf", provider="local", base_model=base,
                        adapter_dir=str(dir), catalog_ids=("*",), notes="discovered")

cli main():
    model_id ← args.model or (LLM_TO_MODEL_ID[args.llm] if args.llm else "rules")
    spec     ← load_registry(args.models).get(model_id)                                   # KeyError → SystemExit(2)
    client   ← build_client_from_spec(spec, catalog, repair=repair)

on unknown model_id:        KeyError (CLI: SystemExit listing registry ids)
on malformed yaml entry:    ValueError naming the entry — never skip it silently
on unset api_key_env:       build succeeds with api_key="EMPTY" (vLLM accepts it); availability() reports (False, "<env> not set")
on unreadable adapter_config.json during discovery: skip that directory (logged); never abort the load
```

## Contract

- **in**: `configs/models.yaml` (or `GANGLION_MODELS` / explicit path); env vars named by `api_key_env`; `DASHSCOPE_BASE_URL`; adapter directories under `runs/`; a `Catalog` for `build_client_from_spec`; for `local_hf` specs, the HF hub cache or the `base_model` path and the optional `adapter_dir` (read only at load time, never at registry load).
- **out**:
  - `Registry` with `get / list / to_jsonable / path`; `ModelSpec.to_jsonable()` (every field, the four `local_hf` ones included); `spec_to_row(spec)` = `to_jsonable() + {"available": bool, "available_detail": str}`; `discover_adapters(...)` specs with `notes == "discovered"`.
  - A constructed `ModelClient` from `build_client_from_spec` (no network at build time).
  - `model_fingerprint(spec)` matching `^mf-([0-9a-f]{12}|rules)$`.
- **event**: none emitted, none consumed — a synchronous config surface. `model_id` and `model_fingerprint` travel inside `analyzer.run.recorded` (`RunManifest`), `lm.inference.*` and `analyzer.label.recorded` payloads ([[analyzer_event_ledger]]).
- **failure**:
  - Unknown `model_id` → `KeyError`; CLI → `SystemExit` listing the known ids.
  - Malformed entry (bad `kind` / `client` / `provider`, `local_hf` without `base_model`, non-list `models:`) → `ValueError`.
  - Registry file missing → rules-only registry, `Registry.path is None`; not an error.
  - `local_hf` with torch / transformers missing, or a `base_model` path that does not exist → `availability(spec) == (False, detail)`; `build_client_from_spec` still returns a client and the first `invoke` / `load_local_model` raises (`RuntimeError` naming the detail) — never at registry load.
  - `local_hf` with `dtype ∉ {"bfloat16", "float32"}` → `ValueError` from `load_local_model`, not from `load_registry` (the loaders know only those two).
  - Unreadable `adapter_config.json` → that directory skipped; the load completes.
- **success**: `pytest tests/test_lm_registry.py` passes, asserting at least: (1) the checked-in `configs/models.yaml` loads and resolves every `--llm` mapping target; (2) `${VAR:-default}` expands with and without the env var set; (3) a missing file yields a registry whose only id is `rules`; (4) a bad `kind` raises `ValueError`; (5) `build_client_from_spec(get("rules"), iot_light_5)` returns a `RuleBasedJSONDSLClient`; (6) `build_client_from_spec` for `local@vllm` with a stubbed `OpenAI` receives `api_key="EMPTY"` and the expanded `base_url`, and `DASHSCOPE_BASE_URL` overrides only `provider: dashscope`; (7) `model_fingerprint` is stable and equals `"mf-rules"` for rules; (8) `supports("*")` semantics; (9) the checked-in `qwen3-0.6b@local` / `qwen3-1.7b@local` entries load as `kind == "local_hf"`, `provider == "local"` with the four local defaults. `pytest tests/test_lm_local_hf.py` covers `discover_adapters` and lazy `availability()` as listed above. And `python -m ganglion.cli --model rules --tier iot_light_5 --limit 5` exits 0 with the same summary as `--llm rules`.

## Observation

- `registry_spec_count{kind}` = entries after load (YAML + discovered).
- `registry_load_errors` = `ValueError`s raised by `load_registry` — must be 0 on the checked-in file (a CI predicate).
- `adapter_discovered_count` = specs with `notes == "discovered"`.
- `available_share` = specs with `availability(spec)[0]` ÷ total — what the console can actually run on this machine.
- `local_model_status{model_id}` = `local_model_status(spec)["status"]` per `local_hf` spec (`loaded | not_loaded | unavailable`) plus its `vram_mb` — what the console's model picker shows next to the Load / Unload buttons.
- `client_build_count{model_id}` = `build_client_from_spec` calls, i.e. [[lm_request_serve]]'s cache misses.

Status: spec, implementation in progress (2026-09-16). Target files `ganglion/lm/registry.py` (new), `configs/models.yaml` (new, including the `qwen3-0.6b@local` / `qwen3-1.7b@local` entries), `ganglion/cli.py` (`--model`, `--models`, `--llm default=None`), `tests/test_lm_registry.py` (new), `tests/test_lm_local_hf.py` (new, shared with [[lm_client]]). The `local_hf` kind, `availability()`, `discover_adapters` and the lifecycle surface land in the same cycle (local-HF addendum, 2026-09-16); the earlier "phase 2" deferral is lifted.
