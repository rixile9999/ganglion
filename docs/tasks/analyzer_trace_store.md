[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_trace_store

Append-only JSONL store of every inference trace produced by Ganglion's [[lm_client]] and benchmark runners. This task is the **substrate** that every other analyzer task ([[analyzer_failure_taxonomy]], [[analyzer_metrics]], [[analyzer_rule_synthesis]], [[analyzer_repair_policy]]) reads from. Today's per-run artifacts (`runs/m{2,3,4}/*.json`, `runs/bfcl/*_cases.jsonl`, `runs/factory_bfcl/<cat>/eval_holdout_cases.jsonl`) hold the same logical thing in three scattered shapes; this spec canonicalises one shape so the analyzer can do its job uniformly.

## Role

Capture every inference attempt as an immutable, content-addressed JSONL record in a fixed on-disk layout, and emit a `analyzer.trace.recorded` event per record so downstream analyzer primitives can subscribe without polling the filesystem.

## Scope

- **in-scope**:
  - `Trace` frozen dataclass with fields:
    - `trace_id: str` — content-hash of `(case_id, model_id, run_id, attempt_index)`. Stable across re-ingestion.
    - `case_id: str` — case identifier from the source dataset (BFCL `id`, IoT `case.id`, factory holdout row id, …).
    - `catalog_id: str` — identifier of the `Catalog` the inference ran against (e.g. `iot_light_5`, `bfcl/simple_python/<case_id>`).
    - `source: str` — one of `"benchmark.iot"`, `"benchmark.bfcl"`, `"lm.invoke"`, `"factory.eval"`.
    - `prompt: str` — full user prompt sent to the model.
    - `expected_plan: dict | None` — JSON form of the gold `ActionPlan` if known; `None` for irrelevance / open-ended invocations.
    - `raw_output: str` — model's raw string output (pre-parse).
    - `attempts: list[dict]` — per-repair-attempt records `{attempt_index, content, input_tokens, output_tokens, error_msg}`.
    - `parse_strategy: str` — one of `"strict"`, `"fenced"`, `"embedded"`, `"failed"`.
    - `error_type: str | None` — left `None` by this store; populated by [[analyzer_failure_taxonomy]] in a separate sidecar.
    - `plan: dict | None` — final parsed `ActionPlan` as JSON, or `None` on parse failure.
    - `latency_ms: float`.
    - `input_tokens_total: int`, `output_tokens_total: int`.
    - `model_id: str` — e.g. `qwen3.6-plus`, `qwen3.6-flash`, `rules`.
    - `timestamp: str` — ISO 8601 UTC, second precision.
  - `TraceStore` class living at `ganglion/analyzer/trace.py` with:
    - `append(trace: Trace) -> str` — writes one JSONL line; returns `trace_id`. Idempotent: same `trace_id` already on disk → skip silently.
    - `iter(catalog_id: str | None = None, run_id: str | None = None) -> Iterable[Trace]` — read path; filters by directory level when supplied.
    - `by_id(trace_id: str) -> Trace | None` — direct lookup (scans the indexed subtree).
  - Persistence layout: `runs/traces/<catalog_id>/<run_id>/traces.jsonl`. Uncompressed by default for grep-ability; gzip is a downstream concern.
  - Event subscription: consume `lm.inference.completed`, `lm.inference.failed`, `benchmark.iot.completed`, `benchmark.bfcl.completed`. For benchmark events, the payload references per-case detail (inline list or file path) which the store unfolds into one `Trace` per case.
  - Idempotency: re-running the same `(case_id, model_id, run_id, attempt_index)` is a no-op — `trace_id` collides and the duplicate write is skipped. This lets benchmark replays be safely re-ingested.
  - **Append-only invariant**: traces are NEVER mutated in place. Classification, annotation, or repair-replay results live in *separate* files. If a downstream consumer wants to "fix" a trace, the contract is to emit a NEW trace with a fresh `trace_id`, never to rewrite an existing line.
  - Target source path: `ganglion/analyzer/trace.py`.
- **out-of-scope**:
  - Trace classification (error-type bucketing) — owned by [[analyzer_failure_taxonomy]].
  - Aggregation and statistical summaries (rates, percentiles, token totals across traces) — owned by [[analyzer_metrics]].
  - Rule / alias / repair-prompt synthesis driven by trace clusters — owned by [[analyzer_rule_synthesis]].
  - Repair replay or policy fitting — owned by [[analyzer_repair_policy]].
  - **In-place mutation of recorded traces** — this is a strict invariant; consumers that need annotation write sidecar files keyed by `trace_id`.
  - Retention / GC / compaction of `runs/traces/**` — separate operational concern; this primitive only appends.
  - Cross-run trace correlation, joins, or schema migrations — separate analyzer-level concerns layered on top of `iter()`.
  - Backfilling legacy `runs/m{2,3,4}/*.json` and `runs/bfcl/*_cases.jsonl` into the canonical shape — a migration task, tracked separately.
- **on violation**: if a consumer attempts to mutate a recorded trace (open in `w`/`r+`, rewrite a line, or hand `TraceStore` a `Trace` with an existing `trace_id` but different content), **fail loud**. The contract is to construct and `append()` a *new* `Trace` with a fresh `trace_id` — never to overwrite. Malformed inbound events are dropped with a log entry and counted in `ingest_dropped_rate`; they do not crash the bus.

## Procedure

```
on subscribe at startup:
    bus.on("lm.inference.completed",   handle_lm)
    bus.on("lm.inference.failed",      handle_lm)
    bus.on("benchmark.iot.completed",  handle_benchmark)
    bus.on("benchmark.bfcl.completed", handle_benchmark)

handle_lm(payload):
    trace ← Trace.from_lm_event(payload)
    trace_id ← TraceStore.append(trace)
    if trace_id was newly written:
        bus.emit("analyzer.trace.recorded",
                 trace_id=trace_id, case_id=trace.case_id,
                 catalog_id=trace.catalog_id, source=trace.source)

handle_benchmark(payload):
    for case in payload.cases:           # inline list or loaded from payload.cases_path
        trace ← Trace.from_benchmark_case(payload.run_id, payload.catalog_id, case)
        trace_id ← TraceStore.append(trace)
        if newly written:
            bus.emit("analyzer.trace.recorded", …)

TraceStore.append(trace):
    path ← runs/traces/<trace.catalog_id>/<trace.run_id>/traces.jsonl
    ensure parent dirs exist
    if index_for(path).contains(trace.trace_id):
        return trace.trace_id            # idempotent skip
    with open(path, "a") as f:
        f.write(json.dumps(asdict(trace), sort_keys=True) + "\n")
    index_for(path).add(trace.trace_id)
    return trace.trace_id

TraceStore.iter(catalog_id=None, run_id=None):
    walk runs/traces/[catalog_id|*]/[run_id|*]/traces.jsonl
    for each line: yield Trace(**json.loads(line))

TraceStore.by_id(trace_id):
    build (or reuse) a per-subtree index lazily on first lookup;
    return matching Trace or None.
```

## Contract

- **in**: events from Module 1 ([[lm_client]]) and the benchmark runners ([[benchmark_iot]], [[benchmark_bfcl]]). Payloads carry either per-case detail inline (`cases: [...]`) or a path reference (`cases_path: <file>`).
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/traces.jsonl` — one JSON line per recorded trace; UTF-8; newline-terminated.
  - One `analyzer.trace.recorded(trace_id, case_id, catalog_id, source)` event per *newly* appended trace (idempotent skips emit nothing).
- **event**:
  - consume: `lm.inference.completed`, `lm.inference.failed`, `benchmark.iot.completed`, `benchmark.bfcl.completed`.
  - emit: `analyzer.trace.recorded(trace_id, case_id, catalog_id, source)`.
- **failure**:
  - Malformed event payload (missing `case_id`, `model_id`, or `run_id`) → log + drop; increment `ingest_dropped_rate`; do not crash the bus.
  - Disk full / `OSError` on append → **fail loud**, surface the exception so the orchestrator can stop the run rather than silently lose traces.
  - Duplicate `trace_id` already on disk → idempotent skip; no event emitted.
  - Consumer attempts in-place mutation → contract violation, raised as `TraceStoreImmutableError`.
- **success**:
  - A synthetic run of N cases produces exactly N traces in `runs/traces/<catalog_id>/<run_id>/traces.jsonl` with N distinct `trace_id`s.
  - `TraceStore.iter(catalog_id, run_id)` yields all N back.
  - `TraceStore.by_id(trace_id)` resolves each.
  - Re-ingesting the same N events yields zero net new lines and zero net new `analyzer.trace.recorded` emissions.

## Observation

- `trace_count_total` — cumulative count of distinct `trace_id`s across `runs/traces/**`.
- `trace_count_by_source` — same, sliced by `source` (`benchmark.iot | benchmark.bfcl | lm.invoke | factory.eval`).
- `trace_bytes_total` — `du -b runs/traces/`; bounds the storage budget consumers should expect.
- `ingest_dropped_rate` — `malformed_event_drops / total_events_received`; non-zero indicates an upstream emitter is shipping incomplete payloads and needs investigation. Reported per-source so a faulty emitter is identifiable without grepping logs.
