[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# lm_client

Module-1 inference surface. Specifies the **`ModelClient` protocol** and three concrete adapters — DashScope (Qwen JSON-DSL / Qwen freeform / Qwen native tool-call), deterministic rules, local HF (transformers + PEFT) — that turn a per-case prompt + a [[contract_catalog]] into a validated `ActionPlan`. Every invocation emits a `lm.inference.{completed|failed}` event so [[analyzer_trace_store]] can ingest a uniform trace regardless of transport.

## Role

Define the synchronous `ModelClient` protocol and pin the contract of its three transport modules (DashScope / rules / local HF) so the analyzer and benchmark layers see one shape across providers.

## Scope

- **in-scope**:
  - `ganglion/lm/client.py` — `ModelClient` Protocol with single method `invoke(prompt: str) -> ModelResult`, plus the shared `ModelResult` (frozen dataclass: `plan: ActionPlan | None`, `raw: dict`, `latency_ms: float`, `input_tokens: int | None`, `output_tokens: int | None`).
  - `ganglion/lm/dashscope.py` — three OpenAI-SDK-against-DashScope clients sharing a single `DashScopeConfig`:
    1. `QwenJSONDSLClient` — `response_format={"type":"json_object"}`; the only client wired to the repair-policy slot from [[analyzer_repair_policy]].
    2. `QwenFreeformJSONDSLClient` — no `response_format`; output salvaged by `parse_json_dsl_lenient` (strict → fenced ```json``` → first decodable `{...}`); used for `qwen-text` and `qwen-thinking` flavors; populates `raw["parse_strategy"]`.
    3. `QwenNativeToolClient` — sends `tools=catalog.render_openai_tools()` + `tool_choice="auto"`; converts returned `tool_calls` back through `catalog.parse_json_dsl({"calls":[…]})` so the validator and equality semantics are shared with DSL paths.
  - `ganglion/lm/rules.py` — `RuleBasedJSONDSLClient`: deterministic regex/keyword stand-in bound to a specific catalog (today: `iot_light_5`); zero network, used by `pytest` and the offline runner.
  - `ganglion/lm/local_hf.py` — `LocalHFClient`: wraps `transformers.AutoModelForCausalLM` + optional PEFT LoRA adapter; calls `tokenizer.apply_chat_template` with the prompt from [[lm_prompts]]; accepts an optional `logits_processor` slot for the grammar mask from [[lm_grammar_mask]].
  - Repair-policy injection: clients accept an optional `RepairPolicy` from [[analyzer_repair_policy]]; on `DSLValidationError` they consult the policy to decide retry vs raise. Only `QwenJSONDSLClient` and `LocalHFClient` wire the slot — the freeform, native, and rules clients raise straight through.
  - System-prompt loading: every client renders its system prompt from [[lm_prompts]] (`ganglion/lm/prompts.py`) so the DSL/native-template strings are a single SSOT shared with [[lm_finetune]] training data.
  - Event emission: one event per `invoke()` — `lm.inference.completed{case_id, catalog_id, model_id, plan, raw, latency_ms, input_tokens, output_tokens, parse_strategy}` on success; `lm.inference.failed{case_id, catalog_id, model_id, error_type, error_msg, raw, attempts}` on terminal failure.
- **out-of-scope**:
  - Model training / LoRA SFT / DPO — see [[lm_finetune]].
  - Training-data synthesis — see [[lm_data_synth]].
  - Per-case iteration, dataset loading, metric aggregation — see [[benchmark_iot]] and [[benchmark_bfcl]].
  - Trace persistence — this task only *emits* events; storage and replay live in [[analyzer_trace_store]] and [[analyzer_repair_replay]].
  - Repair policy authoring — the slot is wired here; the policy lives in [[analyzer_repair_policy]] (which retries on which errors, backoff, attempt budget).
  - Grammar compilation — [[lm_grammar_mask]] owns the catalog → logits-processor pipeline; this task only accepts the compiled processor.
  - Catalog construction / DSL rendering / validation — owned by [[contract_catalog]].
  - Provider adapters beyond DashScope and local HF (Anthropic, OpenAI proper, Together, vLLM HTTP, etc.) — deferred; a future task per provider.
  - Async / streaming variants of `invoke` — `QwenFreeformJSONDSLClient` may stream internally for thinking-mode token capture, but the public surface stays synchronous; an async protocol is out of scope.
- **on violation**: if a client encounters a `DSLValidationError` *without* a configured `RepairPolicy`, it **fails loud** (re-raises). Silent salvage is forbidden — abstention vs. retry is an analyzer decision, not a client default.

## Procedure

```
construct client:
    cfg     ← DashScopeConfig.from_env() | explicit
    catalog ← provided by caller (Module 3 [[contract_catalog]])
    policy  ← optional RepairPolicy ([[analyzer_repair_policy]])
    grammar ← optional logits_processor ([[lm_grammar_mask]])   # local_hf only
    sys_prompt ← prompts.render(flavor, catalog)                # [[lm_prompts]]

invoke(prompt, case_id) per flavor:

  QwenJSONDSLClient:
      messages ← [system(sys_prompt), user(prompt)]
      for attempt in 0..policy.max_attempts:
          resp ← openai.chat.completions.create(
                    model=cfg.model, messages=messages,
                    response_format={"type":"json_object"},
                    extra_body={"enable_thinking": False} if cfg.disable_thinking)
          try:
              plan ← catalog.parse_json_dsl(resp.content, prompt=prompt)
              emit lm.inference.completed(...); return ModelResult(...)
          except DSLValidationError as e:
              if policy is None or not policy.should_retry(attempt, e):
                  emit lm.inference.failed(error_type="validation", ...); raise
              messages ← messages + [assistant(resp.content), user(policy.repair_hint(e))]

  QwenFreeformJSONDSLClient:
      messages ← [system(sys_prompt + "Return JSON only…"), user(prompt)]
      resp ← create(..., extra_body={"enable_thinking": flavor=="thinking"},
                         stream=(flavor=="thinking"))   # stream only to capture reasoning_content
      plan, strategy ← parse_json_dsl_lenient(resp.content, catalog=catalog, prompt=prompt)
      raw["parse_strategy"] ← strategy        # strict | fenced | embedded
      emit lm.inference.completed(parse_strategy=strategy, ...); return ModelResult(...)

  QwenNativeToolClient:
      messages ← [system("Choose the correct tool call …"), user(prompt)]
      resp ← create(..., tools=catalog.render_openai_tools(), tool_choice="auto")
      if not resp.tool_calls:
          emit lm.inference.failed(error_type="no_tool_call", raw=resp.content); raise
      dsl_calls ← [{"action": tc.function.name, "args": json.loads(tc.function.arguments)} for tc in resp.tool_calls]
      plan ← catalog.parse_json_dsl({"calls": dsl_calls}, prompt=prompt)
      emit lm.inference.completed(...); return ModelResult(...)

  RuleBasedJSONDSLClient:
      payload ← regex/keyword pipeline against catalog == iot_light_5
      plan    ← catalog.parse_json_dsl(payload, prompt=prompt)
      emit lm.inference.completed(parse_strategy="rules"); return ModelResult(input_tokens=None, output_tokens=None)

  LocalHFClient:
      inputs ← tokenizer.apply_chat_template([{role:system, sys_prompt},{role:user, prompt}],
                                             add_generation_prompt=True, return_tensors="pt", enable_thinking=False)
      gen_kwargs ← {max_new_tokens, do_sample=False, pad_token_id=…}
      if grammar: gen_kwargs["logits_processor"] = [grammar]
      output ← model.generate(inputs, **gen_kwargs)
      content ← tokenizer.decode(output[0, inputs.shape[-1]:], skip_special_tokens=True)
      same parse_json_dsl + repair-policy branch as QwenJSONDSLClient

on transport failure (network / 5xx / timeout): emit lm.inference.failed(error_type="transport"); raise
on auth missing (no DASHSCOPE_API_KEY for any qwen* client): raise at construction time; no event emitted
```

## Contract

- **in**:
  - `catalog: Catalog` from [[contract_catalog]] (must include `catalog_id` so events are joinable).
  - `prompt: str` — the user message for the case.
  - `case_id: str` — caller-supplied identity; propagated unchanged into events.
  - Optional `repair: RepairPolicy` ([[analyzer_repair_policy]]).
  - Optional `logits_processor: LogitsProcessor` ([[lm_grammar_mask]]; `local_hf` only).
  - `DashScopeConfig` (api_key, model, base_url, disable_thinking) — read from env by default.
- **out**:
  - `ModelResult(plan, raw, latency_ms, input_tokens, output_tokens)` per invocation. `plan is None` only when the client has already emitted `lm.inference.failed` and is propagating the exception to the caller — `invoke` never returns a `ModelResult(plan=None)`.
  - One `lm.inference.completed` *or* `lm.inference.failed` event per invocation (never both, never zero).
- **event**:
  - emit `lm.inference.completed(case_id, catalog_id, model_id, plan, raw, latency_ms, input_tokens, output_tokens, parse_strategy)`.
  - emit `lm.inference.failed(case_id, catalog_id, model_id, error_type, error_msg, raw, attempts)` where `error_type ∈ {validation, transport, no_tool_call, generation, config}`.
  - consume none (clients are leaves; the benchmark layer drives them).
- **failure**:
  - `DSLValidationError` + no policy → re-raise after `lm.inference.failed(error_type="validation")`.
  - `DSLValidationError` + policy says retry → loop until `policy.max_attempts`; on terminal failure emit `lm.inference.failed`.
  - Transport (`APIConnectionError`, `APITimeoutError`, 5xx) → emit `lm.inference.failed(error_type="transport")`; re-raise.
  - Native client returns no `tool_calls` → emit `lm.inference.failed(error_type="no_tool_call")`; raise `RuntimeError`.
  - Missing `DASHSCOPE_API_KEY` → raise `RuntimeError` at client construction; no event.
  - `LocalHFClient` `OOMError` / CUDA failure → emit `lm.inference.failed(error_type="generation")`; re-raise.
- **success**:
  - `tests/test_lm_client.py` exercises each adapter against a stub completer and asserts a `ModelResult` round-trips through `catalog.parse_json_dsl`.
  - `tests/test_repair_loop.py` confirms the policy slot retries on `DSLValidationError` and accumulates `raw["attempts"]`.
  - `tests/test_eval_runner.py` (Module 4) keeps passing with the protocol substituted for each transport.
  - Per-client smoke (`pytest -q -k lm_client_smoke`) produces a parseable `ActionPlan` on a hand-known prompt without hitting the network (rules + stubbed dashscope completer + stubbed HF tokenizer).

## Observation

- `inference_count_total{client, model_id}` — counter of completed + failed events.
- `inference_failure_rate{client, error_type}` — failed ÷ (completed + failed).
- `inference_latency_ms_{mean,p50,p95}{client}` — derived from `latency_ms` on the emitted events.
- `parse_strategy_counts{client, strategy∈{strict, fenced, embedded, rules, native}}` — per-client breakdown surfaced from the `parse_strategy` field on `lm.inference.completed`; only the freeform client spreads across `strict|fenced|embedded`.
- `input_tokens_total`, `output_tokens_total` per `{client, model_id}` — compression evidence vs the native baseline; rules/local-HF report `None` for input tokens, which the analyzer must treat as missing rather than zero.
- `repair_attempts_total`, `repair_successes_total` — pulled from `raw["attempts"]` (mirrors today's `metrics.summarize()`); zero for clients with no policy wired.

## Status

Spec only; impl follows. Pre-redesign code that this task supersedes lives at `ganglion/runtime/qwen.py`, `ganglion/runtime/rules.py`, `ganglion/runtime/types.py`, and the local-HF inference helper in `ganglion/factory/customer/train_lora.py:generate_dsl`. Migration: see [[code_migration]] for the move from `ganglion/runtime/` → `ganglion/lm/`.
