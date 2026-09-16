[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[lm_model_registry]] · [[lm_request_serve]]

# lm_client

Module-1 inference surface. Specifies the **`ModelClient` protocol** and the concrete adapters — OpenAI-compatible (`QwenJSONDSLClient` / `QwenFreeformJSONDSLClient` / `QwenNativeToolClient`, against DashScope, vLLM or OpenAI), deterministic rules, and local HF (`LocalHFClient`: in-process transformers + PEFT LoRA, with a process-wide load / unload / status lifecycle) — that turn a per-case prompt + a [[contract_catalog]] into a validated `ActionPlan`. A client that cannot produce a plan raises **`ModelOutputError`** carrying the model's raw output and repair attempts, so the run owner can still record a full trace in [[analyzer_trace_store]]; clients are constructed by name through [[lm_model_registry]].

## Role

Define the synchronous `ModelClient` protocol and pin the contract of its transport adapters (OpenAI-compatible / rules / local HF) so the analyzer, benchmark and console layers see one result shape — and one failure shape — across providers.

## Scope

- **in-scope**:
  - `ganglion/lm/client.py` — `ModelClient` Protocol with single method `invoke(prompt: str) -> ModelResult`; `ModelResult` (frozen: `plan: ActionPlan`, `raw: Any`, `latency_ms: float`, `input_tokens: int | None`, `output_tokens: int | None`); and `class ModelOutputError(DSLValidationError)` (base imported from `ganglion.contract.tool_spec`) with `.raw: Any` — the client's raw output in one of the shapes `attempts_from_raw` accepts (`str`, `{"attempts": […], "final_content"}`, `{"content", "parse_strategy"}`, native `list`) — and `.attempts: tuple[dict, ...]` = `attempts_from_raw(raw)` (lazy import from `ganglion.analyzer.trace` inside the raising function; the module-level import graph stays `lm.dashscope → analyzer.repair → contract`). The stale docstring reference `ganglion.runtime.rules` becomes `ganglion.lm.rules`.
  - `ganglion/lm/dashscope.py` — three OpenAI-SDK clients sharing `QwenConfig(api_key, model, base_url, disable_thinking, provider="dashscope")`, `provider ∈ {"dashscope", "vllm", "openai"}`:
    1. `QwenJSONDSLClient` — `response_format={"type":"json_object"}`; the only client wired to the repair slot: `invoke` = `run_dsl_with_repair(catalog, prompt, completer, RepairConfig)` ([[analyzer_repair_policy]]), whose terminal failure is `RepairExhaustedError(DSLValidationError)` with `.raw = {"attempts": […], "final_content": str}` and `.attempts`.
    2. `QwenFreeformJSONDSLClient` — no `response_format`; its own "Return JSON only" system string + `catalog.render_json_dsl()`; output salvaged by `parse_json_dsl_lenient` (strict → fenced ```json``` → first decodable `{...}`); used for `freeform` and `thinking`; populates `raw["parse_strategy"]`; on `DSLValidationError` raises `ModelOutputError(str(exc), raw=content, attempts=attempts_from_raw(content), input_tokens=…, output_tokens=…)` — the counts are carried on the exception so a generation that happened but did not validate is still priced ([[lm_request_serve]] reads them off it).
    3. `QwenNativeToolClient` — sends `tools=catalog.render_openai_tools()` + `tool_choice="auto"`; converts returned `tool_calls` to `dsl_calls = [{"action": name, "args": json.loads(arguments or "{}")}]` and validates via `catalog.parse_json_dsl({"calls": dsl_calls}, prompt=prompt)`. Validation failure → `ModelOutputError(raw=dsl_calls)`; no `tool_calls` → `ModelOutputError("model did not return a tool call", raw=message.content or "")` (was a bare `RuntimeError`, which lost the text). Success `raw` stays `[{"name", "arguments"}]` per validated call — `attempts_from_raw` accepts both list item shapes.
  - **Thinking switch per provider** — one helper `_thinking_extra_body(config: QwenConfig, enable: bool) -> dict | None` used at all three call sites (json-dsl completer construction, freeform `invoke`, native `invoke`): `dashscope` → `{"enable_thinking": enable}`; `vllm` → `{"chat_template_kwargs": {"enable_thinking": enable}}`; `openai` → `None`. The freeform client's `enable_thinking` constructor kwarg is the `enable` value (it ignores `config.disable_thinking`, so [[lm_model_registry]] must pass `enable_thinking=(spec.client == "thinking")`); the other two use `not config.disable_thinking`.
  - `ganglion/lm/rules.py` — `RuleBasedJSONDSLClient`: deterministic regex/keyword stand-in bound to `iot_light_5`; zero network; used by `pytest`, the offline runner and the console `seed`. Its `parse_json_dsl` call is wrapped: `except DSLValidationError as exc: raise ModelOutputError(str(exc), raw=payload, attempts=attempts_from_raw(payload)) from exc` — without this a rules failure has no `.raw` and is misfiled as `syntax_invalid`.
  - `ganglion/lm/local_hf.py` — in-process HF inference; the file keeps `evaluate_lora`, `split_train_eval`, `write_report`, `write_split_jsonls` intact and gains:
    - `local_hf_available() -> tuple[bool, str]` — torch + transformers importable; the detail names the CUDA state. Imported lazily by [[lm_model_registry]]'s `availability()`; never at registry-load time.
    - `_MODEL_CACHE: dict[tuple[str, str | None, str, str], tuple[Any, Any]]` — `(base_model, adapter_dir, dtype, device) → (model, tokenizer)`; process-wide and shared across catalogs (a catalog changes the prompt, never the weights).
    - `load_local_model(spec: ModelSpec) -> tuple[Any, Any]` — cached; `load_lora_for_inference(spec.adapter_dir, base_model=spec.base_model, bf16=(spec.dtype == "bfloat16"))` when `adapter_dir` is set, else `load_base_for_inference(spec.base_model, bf16=…)` (both from `ganglion.lm.finetune.sft`, both `device_map="auto"`); `spec.device != "auto"` → `model.to(spec.device)` after load; `dtype ∉ {"bfloat16", "float32"}` → `ValueError`; loads serialised by a module-level `threading.Lock`.
    - `unload_local_model(spec) -> bool` — pops the cache entry, `gc.collect()` + `torch.cuda.empty_cache()`; `False` when nothing was loaded.
    - `local_model_status(spec) -> {"status": "loaded" | "not_loaded" | "unavailable", "detail", "device", "vram_mb": float | None}` — `unavailable` iff `local_hf_available()[0]` is false; `vram_mb = torch.cuda.memory_allocated() / 2**20` when CUDA is up, else `None`.
    - `LocalHFClient(catalog, spec, *, generate_fn=None)` implementing `ModelClient`: `messages = _dsl_messages(catalog, prompt)` (the same `SYSTEM_PROMPT_TEMPLATE` render that `generate_dsl` assembles internally and that SFT trained on — byte-identical); text from `generate_dsl(model, tokenizer, catalog, prompt, max_new_tokens=spec.max_new_tokens, compiled_grammar=cg)` with `cg = compile_catalog_grammar(catalog, tokenizer, vocab_size=model.config.vocab_size)` only when `spec.grammar_mask` and `xgrammar` is importable (else ignored with a one-time `warnings.warn`; [[lm_grammar_mask]]); parse `catalog.parse_json_dsl(text, prompt=prompt)` (`parse_strategy="strict"`), on `DSLValidationError` fall back to `parse_json_dsl_lenient(text, catalog=catalog, prompt=prompt)` (`"fenced"` | `"embedded"`); still failing → `ModelOutputError(str(exc), raw=text, attempts=attempts_from_raw(text), input_tokens=…, output_tokens=…)` (the counts computed before parsing travel on the exception). Returns `ModelResult(plan, raw={"content": text, "parse_strategy": strategy}, latency_ms, input_tokens=len(prompt ids), output_tokens=len(generated ids))` — both counts re-tokenised with the loaded tokenizer after `generate_dsl` returns text. `generate_fn(messages, spec) -> str` is the test seam: when injected no model is loaded, `torch` is never imported and both token counts are `None`. The model loads lazily on the first `invoke` (or via `load_local_model`).
  - Construction: every client is built by `build_client_from_spec(spec, catalog, *, repair)` from [[lm_model_registry]]; `cli.build_client(name, catalog, *, repair)` remains as a thin `--llm`-name wrapper over it. Explicit `QwenConfig` construction and `QwenConfig.from_env()` stay supported for scripts.
  - System prompt: the json-dsl path (via `_dsl_messages`) and `LocalHFClient` render `SYSTEM_PROMPT_TEMPLATE` from `ganglion/lm/prompts.py` — the SSOT shared with [[lm_finetune]] training data; freeform and native keep their own system strings.
  - Event emission: one `lm.inference.completed` or `lm.inference.failed` per `invoke()` — materialised as ledger rows by the **run owner** from the returned `ModelResult` or the raised `ModelOutputError`: [[console_operator]] chat writes one row per invoke; batch runs ([[benchmark_iot]], [[benchmark_bfcl]]) fold the per-invocation signal into the `Trace` (`error_type`) and write run-level rows only. A client has no `run_id` and never writes the ledger itself.
- **out-of-scope**:
  - Model training / LoRA SFT / DPO — [[lm_finetune]]. Training-data synthesis — [[lm_data_synth]].
  - Registry file, `ModelSpec`, `${VAR}` expansion, adapter discovery, `model_fingerprint` — [[lm_model_registry]].
  - Single-request serving, F⁰ / Fᴷ diff, client caching — [[lm_request_serve]].
  - Per-case iteration, dataset loading, metric aggregation — [[benchmark_iot]], [[benchmark_bfcl]].
  - Trace persistence and `attempts_from_raw` / `raw_plan_from_attempts` themselves — [[analyzer_trace_store]]; this task only *calls* `attempts_from_raw`.
  - Repair policy authoring (`RepairConfig`, `run_dsl_with_repair`, `RepairExhaustedError`) — [[analyzer_repair_policy]].
  - Grammar compilation — [[lm_grammar_mask]]; the local client only calls `compile_catalog_grammar` and hands the result to `generate_dsl`.
  - `availability()`, adapter discovery, the `ModelSpec` fields (`device`, `dtype`, `max_new_tokens`, `grammar_mask`) — [[lm_model_registry]]; the HTTP `health` / `load` / `unload` routes and chat auto-load — [[console_operator]].
  - GPU residency policy (eviction, multi-model VRAM budgets) — `_MODEL_CACHE` is a plain dict; `unload_local_model` is the only eviction.
  - Catalog construction / DSL rendering / validation — [[contract_catalog]].
  - Providers whose request shape is not OpenAI-compatible (Anthropic, Gemini, …) — a future client class per provider, registered via [[lm_model_registry]].
  - Async / streaming public surface — `QwenFreeformJSONDSLClient` streams internally in thinking mode to capture `reasoning_content`, but `invoke` stays synchronous.
- **on violation**: if a client cannot produce a plan it **fails loud** with `ModelOutputError` (or `RepairExhaustedError` on the repair path) — never a bare `DSLValidationError` / `RuntimeError` without `.raw`, and never a silent salvage. Abstention vs. retry is an analyzer decision, not a client default. If a new provider needs more than `base_url` + api key + the thinking switch, add a client class here; do not branch inside an existing one.

## Procedure

```
construct client (via build_client_from_spec, [[lm_model_registry]]):
    config  ← QwenConfig(api_key, model, base_url, disable_thinking, provider)   # or from_env() / explicit
    catalog ← provided by caller ([[contract_catalog]])
    repair  ← RepairConfig(enabled, max_attempts)                                 # json-dsl only ([[analyzer_repair_policy]])
    thinking_body ← _thinking_extra_body(config, enable)                          # dashscope | vllm | openai

invoke(prompt) per client:

  QwenJSONDSLClient:
      return run_dsl_with_repair(catalog, prompt, _OpenAIDSLCompleter(openai, model, thinking_body), repair)
      # terminal validation failure → RepairExhaustedError(raw={"attempts","final_content"}, attempts)

  QwenFreeformJSONDSLClient:
      messages ← [system("… Return JSON only, with no Markdown and no explanation.\n\n" + catalog.render_json_dsl()), user(prompt)]
      content, reasoning, usage ← create(..., extra_body=thinking_body, stream=enable_thinking)
      try:    plan, strategy ← parse_json_dsl_lenient(content, catalog=catalog, prompt=prompt)
      except DSLValidationError as e: raise ModelOutputError(str(e), raw=content, attempts=attempts_from_raw(content)) from e
      return ModelResult(plan, raw={"content", "parse_strategy": strategy, "thinking_enabled", "reasoning_chars"}, …)

  QwenNativeToolClient:
      resp ← create(..., tools=catalog.render_openai_tools(), tool_choice="auto", extra_body=thinking_body)
      if not resp.tool_calls: raise ModelOutputError("model did not return a tool call", raw=resp.content or "", attempts=…)
      dsl_calls ← [{"action": tc.function.name, "args": json.loads(tc.function.arguments or "{}")} for tc in resp.tool_calls]
      try:    plan ← catalog.parse_json_dsl({"calls": dsl_calls}, prompt=prompt)
      except DSLValidationError as e: raise ModelOutputError(str(e), raw=dsl_calls, attempts=attempts_from_raw(dsl_calls)) from e
      return ModelResult(plan, raw=[{"name", "arguments"} per validated call], …)

  RuleBasedJSONDSLClient:
      payload ← regex/keyword pipeline (catalog == iot_light_5)
      try:    plan ← catalog.parse_json_dsl(payload, prompt=prompt)
      except DSLValidationError as e: raise ModelOutputError(str(e), raw=payload, attempts=attempts_from_raw(payload)) from e
      return ModelResult(plan, raw=payload, input_tokens=None, output_tokens=None)

  LocalHFClient:
      messages ← _dsl_messages(catalog, prompt)                                   # SYSTEM_PROMPT_TEMPLATE, SFT-identical
      if generate_fn: text ← generate_fn(messages, spec); input_tokens = output_tokens = None   # test seam, no torch
      else:
          model, tok ← load_local_model(spec)                                     # lazy on first call; _MODEL_CACHE hit afterwards
          cg ← compile_catalog_grammar(catalog, tok, vocab_size=model.config.vocab_size) if spec.grammar_mask and xgrammar importable else None   # else warn once
          text ← generate_dsl(model, tok, catalog, prompt, max_new_tokens=spec.max_new_tokens, compiled_grammar=cg)
          input_tokens ← len(prompt ids); output_tokens ← len(tok(text).input_ids)
      try:    plan ← catalog.parse_json_dsl(text, prompt=prompt); strategy ← "strict"
      except DSLValidationError:
          try:    plan, strategy ← parse_json_dsl_lenient(text, catalog=catalog, prompt=prompt)   # fenced | embedded
          except DSLValidationError as e: raise ModelOutputError(str(e), raw=text, attempts=attempts_from_raw(text)) from e
      return ModelResult(plan, raw={"content": text, "parse_strategy": strategy}, latency_ms, input_tokens, output_tokens)

  load_local_model(spec):                                   # also called by console POST /api/models/{id}/load
      with _LOAD_LOCK:
          key ← (spec.base_model, spec.adapter_dir, spec.dtype, spec.device)
          if key in _MODEL_CACHE: return _MODEL_CACHE[key]
          bf16 ← spec.dtype == "bfloat16"                     # dtype ∉ {bfloat16, float32} → ValueError
          model, tok ← load_lora_for_inference(spec.adapter_dir, base_model=spec.base_model, bf16=bf16) if spec.adapter_dir
                       else load_base_for_inference(spec.base_model, bf16=bf16)          # both device_map="auto"
          if spec.device != "auto": model.to(spec.device)
          _MODEL_CACHE[key] ← (model, tok); return _MODEL_CACHE[key]
  unload_local_model(spec): with _LOAD_LOCK: hit ← _MODEL_CACHE.pop(key, None); gc.collect(); torch.cuda.empty_cache() if cuda; return hit is not None
  local_model_status(spec): "unavailable" if not local_hf_available()[0]; "loaded" iff key in _MODEL_CACHE else "not_loaded"; vram_mb from torch.cuda.memory_allocated() when cuda

on transport failure (network / 5xx / timeout): propagate the SDK exception (no .raw — nothing was generated)
on local load / generate failure (torch missing, CUDA OOM, missing weights): propagate (no .raw — nothing was generated)
on grammar_mask without xgrammar: warnings.warn once per process; decode unmasked (not a failure)
on auth missing (DASHSCOPE_API_KEY unset via from_env()): RuntimeError at construction; registry-built clients get api_key="EMPTY" and fail at first invoke
```

## Contract

- **in**:
  - `catalog: Catalog` from [[contract_catalog]].
  - `prompt: str` — the user message for the case.
  - `QwenConfig(api_key, model, base_url, disable_thinking, provider)` — explicit (registry) or `from_env()`.
  - Optional `repair: RepairConfig` ([[analyzer_repair_policy]]; json-dsl client only).
  - For `LocalHFClient`: the `ModelSpec` (`base_model`, `adapter_dir`, `device`, `dtype`, `max_new_tokens`, `grammar_mask`) from [[lm_model_registry]] and the optional `generate_fn(messages, spec) -> str` test seam; the compiled grammar is built in-process via [[lm_grammar_mask]] when `grammar_mask` is on.
- **out**:
  - `ModelResult(plan, raw, latency_ms, input_tokens, output_tokens)` per successful invocation — `invoke` never returns `plan=None`.
  - On failure to produce a plan: `ModelOutputError` (or `RepairExhaustedError`) with `.raw` in an `attempts_from_raw`-accepted shape and non-empty `.attempts` whenever the model produced any text.
  - `load_local_model(spec)` → `(model, tokenizer)` (cached), `unload_local_model(spec)` → `bool`, `local_model_status(spec)` → the status dict — in-process state only; no files, no events.
- **event**:
  - emit `lm.inference.completed(case_id, catalog_id, model_id, plan, raw, latency_ms, input_tokens, output_tokens, parse_strategy)` / `lm.inference.failed(case_id, catalog_id, model_id, error_type, error_msg, raw, attempts)` — as ledger rows written by the run owner from the return value / exception ([[analyzer_event_ledger]]); `error_type ∈ {validation, transport, no_tool_call, generation, config}`.
  - consume none (clients are leaves).
- **failure**:
  - Validation failure, freeform / native / rules → `ModelOutputError(raw=…, attempts=…)`.
  - Validation failure, json-dsl → `RepairExhaustedError` from the repair loop (subclass of `DSLValidationError`, carries raw / attempts).
  - Native client returns no `tool_calls` → `ModelOutputError(raw=message.content or "")`.
  - Transport (`APIConnectionError`, `APITimeoutError`, 5xx) → SDK exception propagates; the run owner records `error = "<ExcClass>: <msg>"`, `raw=None`.
  - Missing `DASHSCOPE_API_KEY` with `QwenConfig.from_env()` → `RuntimeError` at construction; no event.
  - `LocalHFClient`: torch / transformers not importable at first `invoke` → `RuntimeError` carrying `local_hf_available()`'s detail (`error_type="config"`); CUDA OOM / missing weights during load or generate → propagate (`error_type="generation"`; no `.raw`, nothing was generated); `grammar_mask` without `xgrammar` → not a failure (one-time warning, unmasked decode); `dtype` outside `{"bfloat16", "float32"}` → `ValueError` at load.
- **success**:
  - `tests/test_repair_loop.py` — repair slot retries on `DSLValidationError`, accumulates `raw["attempts"]`, and the exhausted path raises a `RepairExhaustedError` that `except DSLValidationError` still catches.
  - `tests/test_lm_serve.py` — a scripted client's `ModelOutputError` surfaces `raw` / `attempts` unchanged through `serve()`.
  - `tests/test_lm_local_hf.py` — with an injected `generate_fn` (no GPU, `torch` never imported) that returns a valid DSL string then garbage: the first `invoke` yields a parsed plan with `raw["parse_strategy"] == "strict"`, the second raises `ModelOutputError` whose `.raw` is the text and `.attempts[0]["content"]` equals it; the system message the seam receives equals `SYSTEM_PROMPT_TEMPLATE.format(dsl=catalog.render_json_dsl())`; `local_model_status` on an unloaded spec reports `not_loaded` (or `unavailable` when transformers is absent) and `unload_local_model` returns `False`.
  - `tests/test_substrate_failure_path.py` — a validation-failing output through `run_iot` yields a trace with `raw_plan` and non-empty `attempts` (the `.raw` attribute is what makes this possible).
  - `tests/test_eval_runner.py` and `tests/test_bfcl_runner.py` keep passing with the protocol substituted for each transport.
  - Offline: `python -m ganglion.cli --model rules --tier iot_light_5 --limit 5` produces a parseable `ActionPlan` per case without network.

## Observation

- `inference_count_total{client, model_id}` — completed + failed rows.
- `inference_failure_rate{client, error_type}` — failed ÷ (completed + failed).
- `inference_latency_ms_{mean,p50,p95}{client}` — from `latency_ms`.
- `parse_strategy_counts{client, strategy ∈ {strict, fenced, embedded, failed, json_object, rules}}` (the `Trace.parse_strategy` vocabulary of [[analyzer_trace_store]]) — the freeform and local HF clients spread across `strict | fenced | embedded`; native and json-dsl report `json_object`.
- `input_tokens_total`, `output_tokens_total` per `{client, model_id}` — compression evidence vs the native baseline; rules reports `None`, which the analyzer treats as missing, not zero.
- `repair_attempts_total`, `repair_successes_total` — from `raw["attempts"]`; zero for clients with no policy wired.
- `raw_preserved_rate{client}` = failed invocations whose exception carried `.raw` ÷ failed invocations — must be 1.0 for validation failures; below 1.0 means a client still raises without `.raw`.
- `local_model_load_seconds{model_id}` — wall time of a cache-miss `load_local_model`; `local_model_vram_mb{model_id}` from `local_model_status`; `grammar_mask_ignored_count` = invocations that requested `grammar_mask` without `xgrammar` — non-zero means the mask ablation is not actually running masked.

## Status

Status: spec, implementation in progress (2026-09-16). Live: `ganglion/lm/{client,dashscope,rules,prompts}.py`; `ganglion/lm/local_hf.py` currently holds `evaluate_lora` and gains `LocalHFClient` plus the `load_local_model` / `unload_local_model` / `local_model_status` lifecycle in this cycle (local-HF addendum, 2026-09-16 — the earlier "spec-only until phase 2" deferral is lifted). This revision adds `ModelOutputError` (raised by the rules, freeform and native clients), `QwenConfig.provider` + `_thinking_extra_body`, the native no-tool-call raw, and registry-based construction. Tests: `tests/test_repair_loop.py`, `tests/test_lm_serve.py`, `tests/test_lm_local_hf.py` (new), `tests/test_substrate_failure_path.py`.
