[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[lm_client]] · [[lm_model_registry]]

# lm_request_serve

Single-request serving surface: one `(model_id, catalog_id, prompt)` in, one `ServeResult` out. It is the adapter the operator console's chat page ([[console_operator]]) sits on, and the only place that computes the **F⁰ / Fᴷ** view of one inference — the plan the model produced *alone* (post-correction hooks stripped, [[contract_patch_apply]]) next to the plan the full catalog accepted — so the operator can see what the contract fixed before labelling. Unlike a `ModelClient`, `serve()` never raises for a model failure: raw text and repair attempts are preserved on the result so a failed inference is still a usable trace.

## Role

Serve one prompt through a registry-resolved `ModelClient` against a resolved `Catalog` and return a `ServeResult` that preserves raw / attempts on failure and exposes the F⁰-vs-Fᴷ hook diff.

## Scope

- **in-scope**:
  - `ganglion/lm/serve.py` (new):
    ```python
    @dataclass(frozen=True)
    class ServeRequest:
        model_id: str; catalog_id: str; prompt: str
        repair: bool = False; repair_max_attempts: int = 1; session_id: str | None = None
    @dataclass(frozen=True)
    class ServeResult:
        plan: dict | None; tool_calls: list[dict]; f0_plan: dict | None; f0_error: str | None
        hooks_diff: tuple[str, ...]
        raw: Any; attempts: tuple[dict, ...]; raw_plan: dict | None; parse_strategy: str
        latency_ms: float; input_tokens: int | None; output_tokens: int | None; error: str | None
        model_id: str; catalog_id: str; catalog_fingerprint: str
    class Server:
        def __init__(self, registry: Registry, resolve_catalog: Callable[[str], Catalog]) ...
        def serve(self, req: ServeRequest) -> ServeResult
    ```
  - Client cache keyed `(model_id, catalog_id, repair, repair_max_attempts)` → `build_client_from_spec(spec, catalog, repair=RepairConfig(enabled=req.repair, max_attempts=req.repair_max_attempts))` ([[lm_model_registry]]); one client per key for the server's lifetime.
  - Failure preservation: `ModelOutputError` / `RepairExhaustedError` ([[lm_client]], [[analyzer_repair_policy]]) → `error=str(exc)`, `raw=exc.raw`, `attempts=exc.attempts`; any other `Exception` → `error=f"{type(exc).__name__}: {exc}"`, `raw=None`, `attempts=()`. `serve()` raises only for caller errors: unknown `model_id` (`KeyError`) and unresolvable `catalog_id` (`CatalogNotResolvable`, a `ValueError`).
  - Success path: `plan=result.plan.to_jsonable()`, `raw=result.raw`, `attempts=attempts_from_raw(result.raw)`, `raw_plan=raw_plan_from_attempts(attempts)` ([[analyzer_trace_store]]), `parse_strategy` = `raw["parse_strategy"]` when `raw` is a mapping carrying it else `"json_object"`, `latency_ms` / tokens from `ModelResult`.
  - **Fᴷ** is `plan`. **F⁰** = `strip_hooks(catalog, ALL_HOOK_KINDS).parse_json_dsl(raw_plan, prompt=prompt)` when `raw_plan is not None`; `DSLValidationError` → `f0_plan=None`, `f0_error=str(exc)`. Aliases (`EnumArg.aliases` / `StringArg.aliases`) are part of the `ArgSpec`, not a hook, so F⁰ still canonicalises `거실 → living`; only `defaults_when_missing`, `strip_unknown_args` and `prompt_correction` differ between F⁰ and Fᴷ (the raw-vs-canonical view is `raw_plan` vs `plan`, both on the result).
  - `tool_calls = emit_tool_calls(<ActionPlan>, catalog)` — pass the validated `ActionPlan` object, never the dict (a dict makes `emit_tool_calls` re-parse and re-apply hooks); `[]` when `plan is None`. Shape `[{"name", "arguments"}]`.
  - `hooks_diff`: per call `i`, compare `f0_plan.calls[i].args` with `plan.calls[i].args` and emit one line per difference — `"<tool>.<arg>: <f0 value> → <plan value>"`, `"<tool>.<arg>: (missing) → <value> (default)"`, `"<tool>: dropped unknown arg '<name>'"`; `()` when the two plans are equal or when F⁰ failed (`f0_error` then carries the rescue reason).
  - `catalog_fingerprint = catalog.fingerprint()` ([[contract_describe]]).
  - Tests: `tests/test_lm_serve.py` — the `rules` model end-to-end offline; a scripted client raising `ModelOutputError(raw=…)` → `error` set, `raw` / `attempts` preserved, `plan is None`; a scripted output `{"calls":[{"action":"set_light","args":{"room":"living","brightness":70}}]}` → `plan.state == "on"`, `f0_error` mentions `state`, `hooks_diff == ()`; a scripted output with an undeclared arg on a `strip_unknown_args` tool → `hooks_diff` contains `dropped unknown arg`; unknown `model_id` → `KeyError`.
- **out-of-scope**:
  - Registry loading, `ModelSpec`, client construction rules — [[lm_model_registry]].
  - HTTP, sessions, `Trace` persistence, `repeat_index`, ledger rows — [[console_operator]] wraps `serve()` and owns the `Trace(source="lm.invoke")` write and the `lm.inference.*` / `analyzer.trace.recorded` rows.
  - Labels and gold — [[analyzer_label_store]]; `serve()` has no notion of expected plans.
  - F⁰ / Fᴷ **attribution** over stored runs (per-kind ablation, rescue / regression statistics) — [[analyzer_correction_attribution]]; this task computes the two plans for one live request only.
  - Batch / benchmark runs — [[benchmark_iot]], [[benchmark_bfcl]].
  - Model loading / unloading for local HF specs — [[lm_client]] §3b (the client loads lazily on first `invoke`; `serve()` just measures the latency; the console reports `model_load_seconds`).
  - Streaming, async, cancellation — synchronous only.
- **on violation**: if a caller needs `serve()` to persist anything (trace, event, file) — **stop**; that is the composite's job ([[console_operator]]) and putting it here would make every `serve()` a side effect. If a client kind needs a new raw shape, extend `attempts_from_raw` in [[analyzer_trace_store]] first; do not special-case it here.

## Procedure

```
serve(req):
    spec    ← registry.get(req.model_id)                          # KeyError → propagate
    catalog ← resolve_catalog(req.catalog_id)                     # CatalogNotResolvable → propagate
    client  ← cache[(req.model_id, req.catalog_id, req.repair, req.repair_max_attempts)]
              or build_client_from_spec(spec, catalog, repair=RepairConfig(req.repair, req.repair_max_attempts))
    t0 ← now
    try:
        result   ← client.invoke(req.prompt)
        plan_obj ← result.plan; raw ← result.raw; error ← None
        tokens   ← (result.input_tokens, result.output_tokens); latency ← result.latency_ms
    except (ModelOutputError, RepairExhaustedError) as exc:
        plan_obj ← None; raw ← exc.raw; error ← str(exc); latency ← now - t0
        tokens ← (getattr(exc, "input_tokens", None), getattr(exc, "output_tokens", None))   # a failed generation is still billable
    except Exception as exc:
        plan_obj ← None; raw ← None; error ← f"{type(exc).__name__}: {exc}"; tokens ← (None, None); latency ← now - t0

    attempts ← attempts_from_raw(raw); raw_plan ← raw_plan_from_attempts(attempts)
    f0_plan, f0_error ← (None, None)
    if raw_plan is not None:
        try:    f0_plan ← strip_hooks(catalog, ALL_HOOK_KINDS).parse_json_dsl(raw_plan, prompt=req.prompt).to_jsonable()
        except DSLValidationError as exc: f0_error ← str(exc)
    plan       ← plan_obj.to_jsonable() if plan_obj else None
    tool_calls ← emit_tool_calls(plan_obj, catalog) if plan_obj else []
    hooks_diff ← diff_args(f0_plan, plan) if f0_plan and plan and f0_plan != plan else ()
    return ServeResult(plan, tool_calls, f0_plan, f0_error, hooks_diff, raw, attempts, raw_plan,
                       parse_strategy, latency, *tokens, error, req.model_id, req.catalog_id, catalog.fingerprint())

on model failure of any kind: never raise — error set, raw / attempts preserved (the trace must still be recordable)
on caller error (unknown model / catalog): raise — the console maps it to 404
```

## Contract

- **in**: `ServeRequest`; a `Registry` ([[lm_model_registry]]); `resolve_catalog: Callable[[str], Catalog]` (builtin tiers and `compiled/<sha12>`; `bfcl/*` is not resolvable).
- **out**: one `ServeResult` per call; `plan is None ⇔ error is not None`; `attempts` non-empty whenever the client produced any text; `f0_plan` / `f0_error` populated whenever `raw_plan` decoded.
- **event**: none emitted, none consumed. `ServeResult` is the payload source for the `lm.inference.completed` / `lm.inference.failed` rows and the `Trace` that [[console_operator]] writes ([[analyzer_event_ledger]], [[analyzer_trace_store]]).
- **failure**:
  - `ModelOutputError` / `RepairExhaustedError` → result with `error`, `raw`, `attempts`, `raw_plan`; F⁰ still attempted on `raw_plan`.
  - Transport / auth / generic exception → result with `error = "<ExcClass>: <msg>"`, `raw=None`, `attempts=()`.
  - `emit_tool_calls` or `strip_hooks` raising on a *validated* plan → propagate (`RuntimeError`); this is a contract bug, not a model failure.
  - Unknown `model_id` → `KeyError`; `bfcl/*` or unknown `catalog_id` → `CatalogNotResolvable`.
- **success**: `pytest tests/test_lm_serve.py` passes with the predicates listed under in-scope; and, through the console, `POST /api/chat` with the `rules` model and `"거실 불 켜줘"` returns `plan.calls[0].action == "set_light"`, `tool_calls[0].name == "set_light"`, `error is None`.

## Observation

- `serve_count{model_id, catalog_id}` and `serve_error_rate{model_id}` = `error is not None` ÷ calls.
- `serve_latency_ms_p95{model_id}` — from `latency_ms`, including failed calls.
- `f0_rescue_rate{catalog_id}` = calls with `f0_error is not None and plan is not None` ÷ calls with `raw_plan` — how often the hooks, not the model, made the plan valid.
- `hooks_diff_nonempty_rate{catalog_id}` = calls with `hooks_diff != ()` ÷ calls with both plans — how often a hook rewrote an already-valid plan (the "rewriting" class of [[analyzer_correction_attribution]]).
- `raw_preserved_rate` = failed calls with `attempts != ()` ÷ failed calls — must be 1.0 for `ModelOutputError` paths; below 1.0 means a client still raises without `.raw`.

Status: spec, implementation in progress (2026-09-16). Target files `ganglion/lm/serve.py` (new), `tests/test_lm_serve.py` (new).
