[← New tasks](./README.md) · Composite principle: [workflow_principle](../agent-forge/workflow_principle.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# console_operator (composite)

**This is a composite task doc.** It names the operator console proposed in [`docs/refine_proposal.md`](../refine_proposal.md) and mocked in `web/mockups/*.dc.html`: one standard-library HTTP process that serves the `web/` pages and a `/api/*` JSON surface over the run-directory tree (`runs/traces/<catalog_id>/<run_id>/`). Every GET is a **projection of files** the primitives already write (`manifest.json`, `traces.jsonl`, sidecars, `events.jsonl`); every POST **calls one primitive through a single writer thread** and records that primitive's declared event. The console owns no analysis, no validation and no model logic of its own — it is the seam where [[lm_request_serve]], [[analyzer_trace_store]], [[analyzer_label_store]], [[analyzer_patch_decision]], [[analyzer_correction_attribution]], [[analyzer_compare]], [[analyzer_run_manifest]], [[analyzer_event_ledger]], [[contract_describe]] and [[contract_patch_apply]] become usable by a person.

Why a composite and not an atomic doc: "chat → label → analyze → decide → compare, on one catalog, with every step leaving a file" is not reducible to any primitive's contract, the wiring would otherwise be re-scripted per page, and future workflows (a labelling sprint, a rule-retirement pass) will compose against the console's routes as if they were primitives ([`workflow_principle` §Composition](../agent-forge/workflow_principle.md)).

## Role

Serve the operator console — static `web/` pages plus `/api/*` — where every GET projects run-directory files and every POST calls exactly one primitive through a single writer, emitting the primitive's events and never re-implementing a primitive.

## Scope

- **in-scope**:
  - Package `ganglion/console/{__init__,server,api,writer,seed,__main__}.py`; standard library only (`http.server.ThreadingHTTPServer`, `json`, `urllib.parse`, `threading`, `queue`). Bind `127.0.0.1`, default port 8766 (`GANGLION_CONSOLE_PORT`; 8765 is the doc-graph viewer).
  - Static serving from the `--web` dir (default `<repo>/web`): `/` → `web/index.html`, any `/<name>.html`, `/assets/*`, `/mockups/*`; path-traversal safe; correct content types; no directory listing.
  - `writer.py` — `Writer`: one daemon thread + `queue.Queue`; `submit(fn, *args) -> Any` blocks the caller until executed and re-raises exceptions. **All** file writes go through it: `TraceStore.append`, `LabelStore.append`, `DecisionStore.append`, `write_manifest` / `write_run_bundle`, `ledger.emit`, `register_compiled`, `analyze_run`, `compare_runs`, `write_exports`, and `load_local_model` / `unload_local_model` for `local_hf` specs — GPU loads serialise behind the same queue as file writes ([[lm_client]]).
  - Handlers: JSON in / out (`application/json; charset=utf-8`), body ≤ 1 MiB, errors as `{"error": str, "detail": str}` with 400 / 404 / 409 / 422 / 500 / 503; same origin, no CORS. `{catalog_id}` never contains `/` except `compiled/<sha12>` and `bfcl/<cat>`; `{run_id}` may contain one `/` (`console/<session_id>`) — `/api/runs/<cat>/<run…>/<verb>` is parsed by splitting on the known trailing verbs `traces`, `analyze`, `patches`, `events`.
  - Route table (exact paths). *P* = projection (reads files only, creates none); *W* = one primitive through the `Writer`, emitting the listed events:

    | Route | Kind | Reads / calls | Response / emits |
    |---|---|---|---|
    | `GET /api/health` | P | config | `{"ok": true, "runs_dir", "web_dir", "registry_path", "version"}` |
    | `GET /api/catalogs` | P | `list_catalogs(base_dir)` — `TIERS` builtins + `<base>/compiled/*/catalog.json` | `{"catalogs": [{"catalog_id","n_tools","allow_empty_calls","fingerprint","source": "builtin"\|"compiled"}]}` |
    | `GET /api/catalogs/{catalog_id}` | P | `resolve_catalog`, `describe()`, `render_json_dsl()`, `render_openai_tools()`, `SYSTEM_PROMPT_TEMPLATE.format(dsl=…)`, `lm.grammar.catalog_to_json_schema` if importable, `source_tools_for` | `{"catalog_id","fingerprint","source","describe","json_dsl","openai_tools","system_prompt","json_schema"\|null,"source_tools"\|null}` |
    | `POST /api/catalogs/compile` `{"name","tools":[…],"allow_empty_calls"?}` | W | `register_compiled(base_dir, name, tools, allow_empty_calls=…)` — compiles with `compile_tool_calling_schema(tools, name=catalog_id, …)` so `Catalog.name == "compiled/<sha12>"` | `{"catalog_id","fingerprint","n_tools"}`; emits `contract.catalog.compiled` |
    | `GET /api/models?catalog_id=` | P | `Registry.list(catalog_id)`, `spec_to_row`, `model_fingerprint`; `fingerprint_match` = `spec.trained_on["catalog_fingerprint"] == active fingerprint` (`null` without `trained_on`) | `{"models": [row + {"fingerprint_match": bool\|null, "model_fingerprint"}]}` |
    | `GET /api/models/{model_id}/health` | P | rules → `live`; `openai_compat` → `GET <base_url>/models` (2 s timeout via urllib, auth header from `api_key_env`); `local_hf` → `local_model_status(spec)` ([[lm_client]]) | `{"status": "live"\|"down"\|"unknown", "detail"}` for rules / `openai_compat`; `{"status": "loaded"\|"not_loaded"\|"unavailable", "detail", "device", "vram_mb": float\|null}` for `local_hf` |
    | `POST /api/models/{model_id}/load` (empty body) | W | `availability(spec)` gate ([[lm_model_registry]]) → `load_local_model(spec)` through the `Writer` (GPU loads serialise; a cache hit returns at once) | `{"status": "loaded", "seconds": float, "device", "vram_mb"}`; 503 `{"error","detail"}` when `availability(spec)[0]` is false; 404 for a non-`local_hf` id; no ledger row |
    | `POST /api/models/{model_id}/unload` (empty body) | W | `unload_local_model(spec)` | `{"status": "not_loaded"}` — idempotent (200 when nothing was loaded); 404 for a non-`local_hf` id; no ledger row |
    | `POST /api/sessions` `{"catalog_id","model_id"}` | W | `write_manifest` (`benchmark="chat"`, `client_kind=spec.client`, `decoding` from the request), `describe.json`, `ledger.emit` | `{"session_id": "s-<YYYYmmdd-HHMMSS>-<4hex>", "run_id": "console/<session_id>", "manifest_path"}`; emits `analyzer.run.recorded` |
    | `POST /api/chat` `{"session_id","catalog_id","model_id","prompt","repair"?}` | W | `Server.serve` ([[lm_request_serve]]) → `TraceStore.append(Trace(...))` → `ledger.emit` ×2 | `ServeResult` fields + `{"trace_id","run_id","order_sensitive": true,"event_ids": […]}` + `"model_load_seconds": float` only on the call that loaded an unloaded `local_hf` model; emits `lm.inference.completed` or `lm.inference.failed`, then `analyzer.trace.recorded` |
    | `GET /api/runs?catalog_id=` | P | `list_runs`, trace / label counts, `read_summary`, sidecar presence | `{"runs": [manifest + {"n_traces","n_labels","summary"\|null,"has_classified","has_patches","has_corrections","has_compare": [names]}]}` |
    | `GET /api/runs/{cat}/{run}` | P | manifest, summary, `classified.jsonl` → histogram, `corrections.summary.json`, `precision_summary`, labels, `events.jsonl` tail | `{"manifest","summary","histogram","corrections","precision","n_labels","n_traces","compares": [names],"events_tail": [last 20]}` |
    | `GET /api/runs/{cat}/{run}/traces?filter=all\|invalid\|wrong\|unlabelled\|changed&failure_type=&parent=&limit=200&offset=0` | P | `TraceStore.iter`, `LabelStore.latest_by_trace`, `read_classified`, `compare-<parent>.json` | `{"traces": [row], "total", "counts": {"all","invalid","wrong","unlabelled","changed"}}` |
    | `GET /api/runs/{cat}/{run}/traces/{trace_id}` | P | `TraceStore.by_id(trace_id, catalog_id=, run_id=)`, classification, label, `corrections.jsonl` row | `{"trace","classification"\|null,"label"\|null,"attribution"\|null,"gold_origin"}` |
    | `POST /api/labels` `{"catalog_id","run_id","trace_id","verdict","expected_plan"?,"endorsed"?,"saw_f0"?,"note"?,"order_sensitive"?,"failure_hint"?,"time_to_label_ms"?,"supersedes"?}` | W | gate `resolve_catalog(cid).parse_json_dsl(expected_plan, prompt=trace.prompt)` (always the `Catalog`, never a `CompiledToolMapper`) → `LabelStore.append` → `ledger.emit` | `{"label_id","zone","family_id","graded_score": float\|null,"event_ids"}`; 422 when `expected_plan` fails validation (never auto-fixed); **verdict `should_abstain` is its own gate** — the empty plan `{"calls": []}` (or no `expected_plan`) is the gold by definition ([[analyzer_label_store]]) and is stored without a catalog parse, since all four builtin tiers set `allow_empty_calls=False`; any other payload under that verdict is 422; labeler from `GANGLION_LABELER` (default `operator`), `label_batch_id` = session / day; emits `analyzer.label.recorded` |
    | `POST /api/runs/{cat}/{run}/analyze` | W | `analyze_run(base_dir, cat, run)` | its result dict; 409 for `bfcl/*`; the primitive emits `analyzer.failure.classified`, `analyzer.rule.proposed`, `analyzer.correction.attributed` |
    | `GET /api/runs/{cat}/{run}/patches` | P | `read_patches`, `DecisionStore.latest_by_patch`, `precision_summary`, retire candidates from `corrections.summary.json`; preview via `apply_patch` re-parsing `attempts[0]` of the run's traces **only** for patches with a `blind` decision | `{"patches": [patch + {"decisions": {stage: decision}, "preview": {"rescues","regressions"}\|null}], "precision", "retire_candidates"}` |
    | `POST /api/patches/{patch_id}/decision` `{"catalog_id","run_id","stage","decision","reason"?,"time_to_decide_ms"?}` | W | `DecisionStore.append` → `ledger.emit` | `{"decision_id","event_ids"}`; emits `analyzer.patch.decided` |
    | `POST /api/patches/{patch_id}/ported` `{"catalog_id","run_id","commit_sha"}` | W | same, with `stage = decision = "ported"` | same |
    | `GET /api/compare?catalog_id=&run_a=&run_b=&allow_diff=` | W† | `compare_runs` (persists `compare-<safe(run_a)>.json`) → `ledger.emit` | `CompareResult.to_dict()`; 409 with detail on refusal; emits `analyzer.compare.completed` |
    | `GET /api/export/labels?catalog_id=&zones=train` | W† | `export_sft` + `write_exports` under `runs/labels/<catalog_id>/` | `{"paths": {…}, "n_sft", "n_hard"}` |
    | `GET /api/runs/{cat}/{run}/events` | P | `read_events` | `{"events": […]}` |

    † The two GETs that persist an artifact are idempotent (same inputs → same bytes) and are routed through the `Writer` like a POST; the "GET creates no files" invariant holds for every *P* route and is what the test asserts.
  - Chat trace shape: `Trace(case_id=f"chat-{sha1(prompt)[:8]}", catalog_id, run_id=f"console/{session_id}", source="lm.invoke", prompt, expected_plan=None, raw_output, attempts, raw_plan, parse_strategy, error_type=result.error, plan, latency_ms, input_tokens_total, output_tokens_total, model_id, timestamp, repeat_index=<number of traces already stored for (run_id, case_id)>)`. `repeat_index` is computed **inside the Writer** at write time: `trace_id` is content-addressed over `(case_id, model_id, run_id, attempts, catalog_id, repeat_index)` and `TraceStore.append` is a no-op on collision, so without it re-asking the same prompt to the deterministic `rules` model would never produce a second trace.
  - Trace row: `{trace_id, case_id, prompt, plan, raw_plan, expected_plan, error_type, status, failure_type, failure_confidence, label, direction}` with `status ∈ {"pass","fail","invalid","ungraded"}` decided by gold (`resolve_gold`, [[analyzer_label_store]]), never by the taxonomy; `failure_type` from `classified.jsonl`, where `no_failure` with `confidence == 0.0` (the call-count-mismatch fall-through) is reported as `"unclassified"` in both the row and the `counts` — the same rule `analyze_run`'s `histogram` applies; `direction ∈ {null,"fixed","regressed","same_pass","same_fail"}` requires `parent` (or `manifest.parent_run_id`) and the compare file; `changed` = `fixed | regressed`.
  - `__main__.py` subcommands: `serve [--port] [--runs runs/traces] [--models configs/models.yaml] [--web web]`; `seed [--runs] [--catalog iot_light_5] [--limit 100]`; `analyze <catalog_id> <run_id>`; `export-labels <catalog_id>`; `compare <catalog_id> <run_a> <run_b>`.
  - `seed` — so every page has real data offline. Two runs, both `iteration 0`, through the same code path as `cli --trace-store` (`run_iot` → `traces_from_results` → `TraceStore` → `write_run_bundle`, [[benchmark_iot]]), then `analyze_run` on each:
    1. `rules-seed`: registry `rules` over `examples/iot_light/dataset.jsonl` (+ `examples/iot_light/adversarial_cases.jsonl` when present), `[:limit]`.
    2. `rules-degraded-seed`: `ganglion/console/seed.py`'s console-owned degraded client wrapping `RuleBasedJSONDSLClient` — drops `state` from `set_light` when neither `brightness` nor `color_temp` is present, echoes a trailing `#N` token as arg `id`, and raises `ModelOutputError(str(exc), raw=payload, attempts=attempts_from_raw(payload))` on validation failure. The plain rules model never fails validation, so without this run `proposed_patches.jsonl` is empty, `precision_summary` is all zeros and the Rules page has nothing to decide on.
  - Local models (`kind: local_hf`, [[lm_model_registry]] / [[lm_client]]): `{model_id}` in `/api/models/{model_id}/<verb>` is one path segment (`@` and `:` allowed, never `/`), verbs `health | load | unload`. `POST /api/chat` with an unloaded local model auto-loads it inside the same `Writer` job as the serve call — `availability(spec)` gate (503 when false), then `load_local_model(spec)` timed, then `server.serve` — so the first call is slow and the response carries `model_load_seconds`; a later chat hits the process-wide cache and omits the key. Load / unload are not ledger events (`EVENT_NAMES` is closed and a load changes no run data): `model_load_seconds` on the chat response and the `health` projection are their only record, and neither route touches the runs dir.
  - Page-side half of the API contract: `web/assets/api.js` exposing `window.GanglionAPI = { get(path), post(path, body), ctx: {catalog, model, session, run}, saveCtx(), loadCtx(), fmt, renderPlanCalls(plan), diffPlans(a, b), escapeHtml }` — its **presence** is part of this composite's `out`; its internals and the pages that use it are owned by the web tasks.
  - Tests: `tests/test_console_api.py`.
- **out-of-scope**:
  - Re-implementing any primitive: no classification, rule synthesis, attribution, comparison, validation or model calls outside the library surfaces named in the route table. If a handler grows logic, it belongs in a primitive doc.
  - Page layout, markup and UX (`web/*.html`, `web/assets/style.css`, mockups) — the web tasks; this doc fixes only the JSON shapes and the `api.js` surface they consume.
  - Auth, TLS, multi-user, remote binding — `127.0.0.1` only; a deployment concern if ever needed.
  - Training, adapter production, `trained_on` provenance — [[lm_finetune]]; the console only exports labels.
  - Auto-applying patches to a live catalog — decisions are recorded ([[analyzer_patch_decision]]); application is a reviewed code edit or [[factory_pipeline]]'s `auto_apply`. The preview in `/patches` is in-memory ([[contract_patch_apply]]) and writes nothing.
  - Retention / GC / `reindex` of `runs/traces/**` and `runs/ledger/index.jsonl` — a separate operational task.
  - BFCL runs beyond read-only display — `bfcl/*` catalogs are per-case and not resolvable; `analyze` on them is 409, status comes from `summary.json`.
  - An in-process event bus, SQLite, FastAPI — reserved names only; events are ledger rows ([[analyzer_event_ledger]]).
- **on violation**: if a handler needs to write a file the `Writer` does not already serialise, or compute a verdict no primitive exposes — **stop**. Do not add a second writer or an inline computation; add the capability to the owning primitive doc (or a new primitive), then consume it here.

## Procedure

```
on start (serve):
    registry ← load_registry(--models); store ← TraceStore(--runs); labels ← LabelStore(--runs)
    decisions ← DecisionStore(--runs); server ← Server(registry, resolve_catalog)      # [[lm_request_serve]]
    writer ← Writer().start(); bind 127.0.0.1:<port>; serve web/ + /api/*

on GET <projection route>:            read manifest.json / traces.jsonl / sidecars / events.jsonl → JSON; create nothing
on POST /api/catalogs/compile:        writer.submit(register_compiled …) → emit contract.catalog.compiled
on POST /api/sessions:                writer.submit(write_manifest + describe.json) → emit analyzer.run.recorded
on GET /api/models/{id}/health:       rules → live; openai_compat → probe <base_url>/models; local_hf → local_model_status(spec)   # creates nothing
on POST /api/models/{id}/load:        404 unless spec.kind == "local_hf"; 503 unless availability(spec)[0];
                                      else writer.submit(load_local_model, spec) timed → {"status": "loaded", "seconds", "device", "vram_mb"}
on POST /api/models/{id}/unload:      404 unless local_hf; writer.submit(unload_local_model, spec) → {"status": "not_loaded"}
on POST /api/chat:                    503 if spec.kind == "local_hf" and not availability(spec)[0]
                                      writer.submit(λ: if local_hf and local_model_status(spec) ≠ loaded: t0 ← now; load_local_model(spec); load_s ← now − t0
                                                      r ← server.serve(req); n ← count(run_id, case_id);
                                                      store.append(Trace(…, repeat_index=n));
                                                      emit lm.inference.completed | lm.inference.failed;
                                                      emit analyzer.trace.recorded)
                                      → response + {"model_load_seconds": load_s} only when a load happened
on POST /api/labels:                  gate expected_plan via resolve_catalog(cid).parse_json_dsl(…, prompt=trace.prompt)
                                      → 422 on DSLValidationError; else writer.submit(labels.append) → emit analyzer.label.recorded
on POST …/analyze:                    409 if cat startswith "bfcl/"; else writer.submit(analyze_run)   # primitive emits its 3 events
on POST /api/patches/{id}/decision:   writer.submit(decisions.append) → emit analyzer.patch.decided
on GET /api/compare:                  writer.submit(compare_runs) → emit analyzer.compare.completed; 409 on ValueError refusal
on GET /api/export/labels:            writer.submit(write_exports)

on seed:                              run rules-seed, run rules-degraded-seed (both iteration 0) → analyze_run ×2

on primitive exception inside writer: re-raised to the handler → 500 {"error","detail"}; the console writes nothing of its own
on body > 1 MiB / invalid JSON:       400; unknown run / trace / patch / model / catalog: 404; validation-gated input: 422
```

## Contract

- **in**: HTTP requests on `127.0.0.1:<port>`; the runs dir (`--runs`, default `runs/traces`); `configs/models.yaml` ([[lm_model_registry]]); the `web/` dir; env `GANGLION_CONSOLE_PORT`, `GANGLION_LABELER` (default `operator`); for `local_hf` specs, the HF hub cache / `base_model` path and `adapter_dir` (read only when a load is requested).
- **out**:
  - JSON responses with the exact shapes in the route table.
  - Files, written only through the `Writer` and only by the primitives: `<base>/<catalog_id>/console/<session_id>/{manifest.json,describe.json,traces.jsonl,events.jsonl,labels.jsonl}`, `<base>/<catalog_id>/<run_id>/{classified.jsonl,proposed_patches.jsonl,proposed_patches.summary.json,corrections.jsonl,corrections.summary.json,patch_decisions.jsonl,compare-<run_a_safe>.json}`, `<base>/compiled/<sha12>/catalog.json`, `runs/labels/<catalog_id>/{human_sft.jsonl,hard_pool.jsonl}`.
  - `web/assets/api.js` present and exposing `window.GanglionAPI`.
  - After `seed`: two runs under `<base>/iot_light_5/`; `proposed_patches.jsonl` of `rules-degraded-seed` holding ≥ 1 patch with `operation ∈ {set_default, enable_strip_unknown_args}`; its `classified.jsonl` histogram with `missing_required_arg > 0`.
- **event**:
  - consume (as ledger rows, for `events_tail`, run detail and the `direction` / `precision` projections): `analyzer.run.recorded`, `analyzer.trace.recorded`, `analyzer.label.recorded`, `analyzer.failure.classified`, `analyzer.rule.proposed`, `analyzer.correction.attributed`, `analyzer.patch.decided`, `analyzer.compare.completed`, `contract.catalog.compiled`, `lm.inference.completed`, `lm.inference.failed`.
  - emit (through the primitives' own `ledger.emit`, one row per POST side effect): `analyzer.run.recorded` (sessions), `lm.inference.completed` | `lm.inference.failed` + `analyzer.trace.recorded` (chat), `analyzer.label.recorded` (labels), `analyzer.patch.decided` (decision / ported), `contract.catalog.compiled` (compile), `analyzer.compare.completed` (compare); `analyze` delegates its three emissions to the primitive. Model `load` / `unload` emit nothing.
- **failure**:
  - Primitive raises inside the `Writer` → 500 with `detail = "<ExcClass>: <msg>"`; the console never catches-and-continues.
  - `expected_plan` fails `parse_json_dsl` → 422; label not written, no event. Verdict `should_abstain` bypasses that parse and accepts only `{"calls": []}` / no plan (anything else → 422).
  - `analyze` / `compare` on `bfcl/*`, or a `compare_runs` refusal (manifest mismatch without `allow_diff`) → 409 with the refusal detail.
  - Unknown `model_id`, `catalog_id`, `run_id`, `trace_id`, `patch_id` → 404 — including a `run_a` / `run_b` with no `manifest.json` on `GET /api/compare` (an unmanifested shard is an unknown run, not a refusal).
  - Model failure inside chat → **200** with `error` set and `raw` / `attempts` preserved (a failed inference is still a trace); transport / auth failures likewise.
  - `Writer` thread dead → 503 on every W route; P routes keep serving.
  - `POST /api/models/{id}/load`, or `POST /api/chat` on a `local_hf` model, with `availability(spec)[0] == false` → 503 with the availability detail; a load raising inside the `Writer` (CUDA OOM, missing weights) → 500 and no trace (nothing was invoked — unlike a model-output failure, which is a 200 trace); `load` / `unload` on a non-`local_hf` id → 404; `unload` of a model that is not loaded → 200 `{"status": "not_loaded"}`.
- **success**: `pytest tests/test_console_api.py` passes — the server started on port 0 with a `tmp_path` runs dir and the built-in registry (rules only), driven with `urllib.request`: health; catalogs list + detail for `iot_light_5`; compile a 2-tool OpenAI list → `compiled/<sha12>` and `resolve_catalog(cid).name == cid`; sessions + chat with `rules` on `"거실 불 켜줘"` → trace stored, `plan.calls[0].action == "set_light"`; labels `correct` and `incorrect`, invalid `expected_plan` → 422; the `seed` helper with `--limit 20` → runs list, run detail, traces filters, analyze, patches with a real `patch_id`, decision, compare (a run against itself → ok, `em_delta == 0`), export; **8 concurrent chat POSTs with 8 distinct prompts → exactly 8 new traces and no interleaved lines, plus one repeated prompt → a second trace via `repeat_index`**; a snapshot of the runs dir before and after every *P* route is identical; with a `local_hf` spec added to the test registry and `load_local_model` / the client's `generate_fn` monkeypatched (no GPU): `GET …/health` → `not_loaded`, `POST …/load` → `loaded` with `seconds ≥ 0`, the first chat carries `model_load_seconds` and the second does not, `POST …/unload` → `not_loaded` twice, and with `availability` patched to `(False, …)` both `POST …/load` and chat → 503; and `test -f web/assets/api.js && grep -q "window.GanglionAPI" web/assets/api.js`.

## Inheritance (per [workflow_principle](../agent-forge/workflow_principle.md))

| Mechanism | What this composite inherits |
|---|---|
| Pointer | [`task_principle`](../agent-forge/task_principle.md), [`workflow_principle`](../agent-forge/workflow_principle.md). |
| Template | Same six sections as the primitives. |
| Pattern | [[factory_pipeline]]'s subscribe-aggregate-emit, inverted for a human in the loop: the loop *pauses* at every decision and the ledger rows are the resume signal. |
| Data | The route table above is this doc's data section; a new page or endpoint adds a row here, never a new writer. |

## Observation

Composite aggregates the primitives' metrics; the only new ones are about the seam itself:

- `console_request_count{route, status}` and `console_error_rate` = 5xx ÷ requests.
- `writer_queue_depth_max`, `writer_latency_ms_p95` — the serialisation cost of the single writer; a rising p95 on `analyze` is expected, on `chat` it is not.
- `get_side_effect_count` = files created by *P* routes — must be 0 (the test predicate).
- `chat_traces_per_session`, `chat_error_rate{model_id}` — from `lm.inference.failed` rows.
- `label_minutes_total{catalog_id}` = Σ `time_to_label_ms` ÷ 60 000; `decision_minutes_total` = Σ `time_to_decide_ms` ÷ 60 000 — the human-cost numbers the MLSys draft needs.
- `patch_acceptance_rate`, `precision_at_conf` — passed through from `precision_summary` ([[analyzer_patch_decision]]).
- `seed_patch_count` = patches in `rules-degraded-seed` — must be ≥ 1, else the offline demo is empty.
- `local_model_load_seconds{model_id}` — from `POST /api/models/{id}/load` responses and `model_load_seconds` on chat; `local_model_unavailable_count` = 503s on load / chat for `local_hf` ids.

## Negative checks (composite anti-patterns — per [workflow_principle](../agent-forge/workflow_principle.md))

- [ ] Declares its own `in / out / event / failure / success` — not just a route list.
- [ ] Does not wrap a single primitive — wires ten primitives behind one writer.
- [ ] Does not mutate any primitive's `in-scope`; the degraded seed client is console-owned (`ganglion/console/seed.py`), not a change to [[lm_client]].
- [ ] Procedure is `on <request> → primitive → emit` shaped; no handler computes a verdict a primitive does not expose.

Status: spec, implementation in progress (2026-09-16). The local-HF `health` / `load` / `unload` routes and chat auto-load are specified in this revision (local-HF addendum, 2026-09-16); the earlier "reserved" placeholder is lifted.

Related: [[lm_request_serve]], [[lm_model_registry]], [[analyzer_trace_store]], [[analyzer_label_store]], [[analyzer_patch_decision]], [[analyzer_correction_attribution]], [[analyzer_compare]], [[analyzer_run_manifest]], [[analyzer_event_ledger]], [[contract_describe]], [[contract_patch_apply]], [[benchmark_iot]], [[factory_pipeline]].
