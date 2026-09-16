[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_trace_store

Append-only JSONL store of every inference trace produced by Ganglion's [[lm_client]] and benchmark runners. This task is the **substrate** that every other analyzer task ([[analyzer_failure_taxonomy]], [[analyzer_metrics]], [[analyzer_rule_synthesis]], [[analyzer_repair_policy]], [[analyzer_label_store]], [[analyzer_correction_attribution]], [[analyzer_compare]]) reads from. Today's per-run artifacts (`runs/m{2,3,4}/*.json`, `runs/bfcl/*_cases.jsonl`, `runs/factory_bfcl/<cat>/eval_holdout_cases.jsonl`) hold the same logical thing in three scattered shapes; this spec canonicalises one shape so the analyzer can do its job uniformly.

Status: implemented at `ganglion/analyzer/trace.py`; 2026-09-16 console-batch revisions (`raw_plan`, `repeat_index`, id payload, lock + refresh, `list_shards`, sidecar registry) in progress. Tests: `tests/test_analyzer_trace_store.py`, `tests/test_substrate_failure_path.py`.

## Role

Capture every inference attempt as an immutable, content-addressed JSONL record in a fixed on-disk layout — **preserving the raw model output even when validation failed** — and emit `analyzer.trace.recorded` per newly written record so downstream analyzer primitives can subscribe without polling the filesystem.

## Scope

- **in-scope**:
  - `Trace` frozen dataclass with fields:
    - `trace_id: str` — `"tr-" + sha256(...)[:16]` over `(case_id, model_id, run_id, catalog_id, repeat_index, attempts)` where `attempts` is the attempt chain (`attempt`, `content`, `input_tokens`, `output_tokens`, `error_msg`). Stable across re-ingestion.
    - `case_id: str` — case identifier from the source (BFCL `id`, IoT `case.id`, console `chat-<sha1(prompt)[:8]>`).
    - `catalog_id: str` — builtin tier (`iot_light_5`, `home_iot_20`, `smart_home_50`, `home_assistant_4`), `compiled/<sha12>`, or `bfcl/<category>`.
    - `run_id: str` — plain run name, no catalog prefix; MAY contain one `/` (`console/<session_id>`).
    - `source: str` — exactly one of `"benchmark.iot"`, `"benchmark.bfcl"`, `"lm.invoke"`.
    - `prompt: str` — full user prompt sent to the model.
    - `expected_plan: dict | None` — dataset gold as Action IR when the producer has one; `None` for chat traces and for BFCL (whose ground truth is a per-arg accepted-value set, not a plan — see [[benchmark_bfcl]]). Human golds never live here; they live in `labels.jsonl` ([[analyzer_label_store]]).
    - `raw_output: str` — content of the last attempt (`""` when there are no attempts).
    - `attempts: tuple[dict, ...]` — per-attempt records as written by `run_dsl_with_repair`: `{attempt, content, input_tokens, output_tokens, error?}`; normalised from any client's `raw` by `attempts_from_raw`.
    - `raw_plan: dict | None` — `json.loads` of the **last attempt whose content decodes to a mapping** (`raw_plan_from_attempts`), regardless of validation. This is what the taxonomy matches on when `plan` is `None`.
    - `plan: dict | None` — the **validated** `ActionPlan` as JSON; `None` whenever validation failed. `plan` and `raw_plan` are distinct fields with distinct meanings; never write an unvalidated dict into `plan`.
    - `parse_strategy: str` — `"strict" | "fenced" | "embedded" | "failed" | "json_object" | "rules"` (from `raw["parse_strategy"]` when the client reports one, else `"json_object"`).
    - `error_type: str | None` — `f"{type(exc).__name__}: {exc}"` from the runner when the invocation raised; `None` on success. Classification never mutates it (sidecar).
    - `repeat_index: int = 0` — position within `--repeat` runs, or the count of prior traces for the same `(run_id, case_id)` in a console session (so re-asking the same prompt is a new trace).
    - `latency_ms: float`, `input_tokens_total: int`, `output_tokens_total: int` (0 when the client reports `None`), `model_id: str`, `timestamp: str` (ISO 8601 UTC, `Z`).
    - `to_dict` / `from_dict` tolerate missing `raw_plan` / `repeat_index` in older shards.
  - Raw normalisation helpers (`ganglion/analyzer/trace.py`):
    - `attempts_from_raw(raw) -> tuple[dict, ...]`: `str` → one attempt `{attempt: 0, content: str}`; `{"attempts": [...], "final_content"}` → the attempts; `{"content": str, "parse_strategy": ...}` → one attempt; `list` (native tool calls, items in either `{name, arguments}` or `{action, args}` shape) → one attempt with `content = json.dumps({"calls": [{"action", "args"}, ...]})`; `None` → `()`; any other mapping → one attempt with `content = json.dumps(raw)`.
    - `raw_plan_from_attempts(attempts) -> dict | None`.
  - `TraceStore(base_dir="runs/traces")` at `ganglion/analyzer/trace.py`:
    - `append(trace) -> str` — one JSONL line; idempotent on `trace_id` (existing id → skip, return id).
    - `iter(catalog_id=None, run_id=None) -> Iterator[Trace]` — with both filters reads one shard; with `run_id` only, compares the *derived* run id from `list_shards` (so `console/<sid>` matches).
    - `by_id(trace_id, *, catalog_id=None, run_id=None) -> Trace | None` — when the pair is given only that shard is opened; otherwise the lazy global index.
    - `shard_path(catalog_id, run_id) -> Path`; `base_dir` property.
    - `list_shards() -> list[tuple[str, str]]` — every `traces.jsonl` under base as `(catalog_id, run_id)`. Split rule over the relative path parts before `traces.jsonl`: if `parts[0] ∈ {"compiled", "bfcl"}` the catalog id is the first **two** segments, else one; the remaining segments joined by `/` are the run id.
    - Concurrency: a `threading.RLock` around `append` / `iter` / `by_id`; `_maybe_refresh()` re-reads a shard whose `(size, mtime)` changed since last load, so a console process sees shards written by a CLI process. Cross-process writers never share a shard.
  - Persistence layout: `<base>/<catalog_id>/<run_id>/traces.jsonl` (`json.dumps(..., sort_keys=True, ensure_ascii=False)`, one line per trace). Uncompressed for grep-ability.
  - **Sidecar registry** — files that live beside `traces.jsonl` in the run dir, keyed by `trace_id` / `patch_id`, each owned elsewhere; the store ignores them all (it reads only the basename `traces.jsonl`):
    `manifest.json`, `summary.json`, `report.md`, `describe.json` ([[analyzer_run_manifest]] / [[analyzer_metrics]] / [[contract_describe]]); `events.jsonl` ([[analyzer_event_ledger]]); `labels.jsonl` ([[analyzer_label_store]]); `classified.jsonl` ([[analyzer_failure_taxonomy]]); `proposed_patches.jsonl`, `proposed_patches.summary.json` ([[analyzer_rule_synthesis]]); `corrections.jsonl`, `corrections.summary.json` ([[analyzer_correction_attribution]]); `patch_decisions.jsonl` ([[analyzer_patch_decision]]); `compare-<run_a_safe>.json` ([[analyzer_compare]]).
  - Producers (adapters that build `Trace`s; the store does not know about runners):
    - `benchmarks/iot/runner.py:traces_from_results(results, *, catalog_id, run_id, model_id, source="benchmark.iot", prompts_system="")` — one `Trace` per `(case, repeat_index)`; `attempts = attempts_from_raw(run.raw)`; `raw_plan = raw_plan_from_attempts(attempts)`; `plan = run.plan.to_jsonable()` or `None`; `expected_plan = case.expected.to_jsonable()`; `error_type = run.error`. Requires `_invoke_once` to record `raw = getattr(exc, "raw", None)` on exception — the raw-preservation fix from [[benchmark_iot]].
    - `benchmarks/bfcl/runner.py:traces_from_bfcl_results(results, *, category, run_id, model_id)` — `catalog_id = f"bfcl/{category}"`, `source = "benchmark.bfcl"`, `prompt = case.user_message`, `case_id = case.id`, `expected_plan = None` always, `error_type = run.error`.
    - The console chat path (`source = "lm.invoke"`, `expected_plan = None`; [[lm_request_serve]] / [[console_operator]]).
    - `factory.py` uses `traces_from_results` (no private duplicates).
  - **Append-only invariant**: traces are NEVER mutated in place. Classification, labels, attribution live in sidecars. A consumer that wants to "fix" a trace appends a NEW trace with a fresh `trace_id`.
- **out-of-scope**:
  - Classification — [[analyzer_failure_taxonomy]]. Aggregation — [[analyzer_metrics]]. Rule synthesis — [[analyzer_rule_synthesis]]. Repair replay — [[analyzer_repair_policy]]. Labels — [[analyzer_label_store]]. Manifests / run listing — [[analyzer_run_manifest]] (a shard without `manifest.json` is not a listed run).
  - In-place mutation of recorded traces — strict invariant.
  - Retention / GC / compaction of `runs/traces/**`.
  - Cross-run joins — [[analyzer_compare]].
  - Backfilling legacy `runs/m{2,3,4}/*.json` and `runs/bfcl/*_cases.jsonl` into the canonical shape — a migration task.
  - Deciding *what* a client's `raw` looks like — clients own that ([[lm_client]]); this store only normalises the five known shapes.
- **on violation**: a consumer that opens a shard for writing other than by `append` (`w` / `r+`, rewriting a line), or hands `TraceStore` a `Trace` with an existing `trace_id` but different content — **fail loud**. A producer that writes an unvalidated dict into `plan` is a contract violation caught by `tests/test_substrate_failure_path.py` (`plan is None` on validation failure while `raw_plan` is set). Malformed inbound records are dropped with a log entry and counted in `ingest_dropped_rate`.

## Procedure

```
producer (CLI --trace-store, console chat, factory iteration):
    traces ← traces_from_results(...) | traces_from_bfcl_results(...) | Trace(source="lm.invoke", ...)
    for trace in traces:
        trace_id ← TraceStore.append(trace)
        if newly written:
            emit analyzer.trace.recorded(trace_id, case_id, catalog_id, run_id, source, repeat_index)   # ledger row

TraceStore.append(trace):
    with lock:
        path ← <base>/<trace.catalog_id>/<trace.run_id>/traces.jsonl
        _maybe_refresh(path)                      # (size, mtime) changed → reload shard index
        if trace.trace_id in index(path): return trace.trace_id       # idempotent skip
        mkdir -p; append json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        index(path).add(trace.trace_id); update by_id cache if built
        return trace.trace_id

TraceStore.iter(catalog_id=None, run_id=None):
    with lock:
        shards ← [(catalog_id, run_id)] if both else [s for s in list_shards() if filters match]
        for shard: _maybe_refresh; yield Trace.from_dict(line) per line (skip blank / undecodable)

TraceStore.by_id(trace_id, *, catalog_id=None, run_id=None):
    with lock: open only that shard when the pair is given; else lazy global index
```

## Contract

- **in**: `CaseResult` lists from [[benchmark_iot]] / [[benchmark_bfcl]] (through the two adapter functions), or a `ServeResult` from [[lm_request_serve]] (console), each carrying the client's `raw` — including the `raw` attached to `ModelOutputError` / `RepairExhaustedError` on failure ([[lm_client]], [[analyzer_repair_policy]]).
- **out**:
  - `<base>/<catalog_id>/<run_id>/traces.jsonl` — one JSON line per recorded trace; UTF-8; newline-terminated.
  - One `analyzer.trace.recorded(trace_id, case_id, catalog_id, run_id, source, repeat_index)` ledger row per *newly* appended trace (idempotent skips emit nothing).
- **event**:
  - consume: `lm.inference.completed`, `lm.inference.failed` (ledger rows from [[lm_client]] / [[lm_request_serve]]); benchmark completions arrive as the adapter call at the CLI `--trace-store` boundary (the `benchmark.iot.completed` / `benchmark.bfcl.completed` names declared by [[benchmark_iot]] / [[benchmark_bfcl]] are not ledger rows in this cycle).
  - emit: `analyzer.trace.recorded(trace_id, case_id, catalog_id, run_id, source, repeat_index)`.
- **failure**:
  - Malformed record (missing `case_id`, `model_id`, `run_id`, `catalog_id`) → log + drop; `ingest_dropped_rate`++.
  - Disk full / `OSError` on append → **fail loud**; surface the exception.
  - Duplicate `trace_id` → idempotent skip; no event.
  - In-place mutation attempt → contract violation (`TraceStoreImmutableError`).
  - Client raised without `.raw` → the trace has `attempts == ()`, `raw_plan is None`, `error_type` set; classified `syntax_invalid` by the taxonomy. Every client is required to attach `raw` ([[lm_client]]); a missing `raw` is a client bug, visible as `raw_missing_share`.
- **success**:
  - A synthetic run of N cases × R repeats produces exactly N·R traces with distinct `trace_id`s; `iter(catalog_id, run_id)` yields them all; `by_id` resolves each, also with the `(catalog_id, run_id)` hint.
  - Re-ingesting the same N events yields zero net new lines and zero new `analyzer.trace.recorded` rows.
  - `list_shards()` on a tree containing `iot_light_5/r1/`, `compiled/abc123def456/r2/`, `bfcl/simple_python/r3/`, `iot_light_5/console/s-1/` returns `[("bfcl/simple_python","r3"), ("compiled/abc123def456","r2"), ("iot_light_5","console/s-1"), ("iot_light_5","r1")]` and `iter(run_id="console/s-1")` yields that shard's traces; an explicit pair with a three-segment catalog id (`iter(catalog_id="bfcl/simple_python/case-42", run_id="r")`, the pre-existing test) still resolves directly to `<base>/bfcl/simple_python/case-42/r/traces.jsonl` — the split rule applies only when `list_shards` has to derive the pair.
  - A second `TraceStore` on the same base sees a trace appended by the first after the file's `(size, mtime)` changes.
  - `tests/test_substrate_failure_path.py`: a `ScriptedCompleter` output `{"calls":[{"action":"set_light","args":{"room":"living"}}]}` (state missing, no brightness) driven through `run_dsl_with_repair` → `run_iot` → `traces_from_results` yields a trace with `plan is None`, non-empty `attempts`, `raw_plan` set, `error_type` starting with `RepairExhaustedError`; `classify` gives `missing_required_arg`; `synthesize_rules(..., golds=)` proposes `set_default`.

## Observation

- `trace_count_total` — distinct `trace_id`s across `runs/traces/**`.
- `trace_count_by_source` — sliced by `source` (`benchmark.iot | benchmark.bfcl | lm.invoke`).
- `raw_missing_share[run_id]` — traces with `error_type` set and `attempts == ()` ÷ traces with `error_type` set; must be 0 once every client attaches `raw`.
- `trace_bytes_total` — `du -b runs/traces/`.
- `ingest_dropped_rate` — `malformed_record_drops / total_records_received`, per source.
- `shard_refresh_count` — `_maybe_refresh` reloads per process; a proxy for cross-process write activity.
