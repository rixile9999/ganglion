[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# benchmark_iot

Adapter that drives the IoT-light tier benchmarks (`iot_light_5` / `home_iot_20` / `smart_home_50`) as a *consumer* of a constructed [[contract_catalog]] and [[lm_client]]. Loads the IoT JSONL datasets, invokes the client per case, grades with exact-match + action-match semantics, and emits one trace per case for [[analyzer_trace_store]] plus a single run-level event for downstream aggregation in [[analyzer_metrics]].

## Role

Run a catalog against the IoT dataset under a given client and emit per-case traces plus a run-completion event — never construct the catalog or the client itself.

## Scope

- **in-scope**:
  - Per-tier dataset loaders for `iot_light_5`, `home_iot_20`, and `smart_home_50`. All three load from the same `examples/iot_light/dataset.jsonl` because the smaller-tier intents are a subset of the larger catalogs; the tier label travels with the run for downstream slicing, not for filtering rows.
  - Adversarial merge: when `adversarial=True`, concatenate `examples/iot_light/dataset.jsonl` with `examples/iot_light/adversarial_cases.jsonl` (M4 adversarial set) into the case stream. The merge is in-memory and deterministic (base first, then adversarial in file order).
  - `Grader.match(predicted: ActionPlan | None, expected: ActionPlan) -> GradeResult` carrying `{exact_match: bool, action_match: bool, syntax_valid: bool, error_type: str | None}`. `exact_match` is `ActionPlan` value equality; `action_match` compares the action-name sequence only; `syntax_valid` is `predicted is not None`. This is today's IoT-side scoring — the BFCL AST grader is a sibling and lives under [[benchmark_bfcl]].
  - Per-case runner: `(client: ModelClient, catalog: Catalog, dataset, *, repeat: int = 1, repair_policy=None) -> RunHandle`. Iterates cases, calls `client.invoke(case.prompt)` `repeat` times, grades each invocation, and emits one `lm.inference.completed` event per invocation (consumed by [[analyzer_trace_store]]). On final case completion, emits one `benchmark.iot.completed(catalog_id, tier, summary_path)` where `summary_path` is the trace-store run directory.
  - `--repeat N` for latency stats (each repeat is its own trace; the grader runs per repeat so the analyzer can compute pass/repeat distributions).
  - `--limit N` flag wiring (slice the cases list before iteration).
  - `--repair` flag wiring: the repair *policy* is supplied externally (see [[analyzer_repair_policy]]); this task only forwards a `repair_policy` handle to the client factory boundary and records repair attempt counts surfaced by the client into the per-case trace payload.
  - Target paths: `ganglion/benchmarks/iot/{dataset,grader,runner}.py`.
- **out-of-scope**:
  - Client construction (`QwenJSONDSLClient`, `QwenNativeToolClient`, `RuleBasedJSONDSLClient`, factories) — see [[lm_client]]; the runner accepts a ready-constructed `ModelClient` and never imports a concrete client class.
  - Catalog construction — see [[contract_catalog]] and `ganglion/contract/builtins/`; the runner consumes a `Catalog` by reference, never instantiates `ToolSpec`s or resolves aliases.
  - Summarisation / aggregation across cases — `syntax_valid_rate`, `exact_match_rate`, latency percentiles, repair totals — see [[analyzer_metrics]] which consumes the trace store, not this runner.
  - Trace persistence (file layout, schema versioning, retention) — see [[analyzer_trace_store]]; this task only *emits* events.
  - BFCL benchmark — different dataset, per-case compiled catalog, AST grader. See [[benchmark_bfcl]].
  - Dataset regeneration — `examples/iot_light/generate_dataset.py` and `examples/iot_light/adversarial_cases.py` remain the SSOT for row content; this task only consumes the JSONL.
  - Tier dataset divergence — the same prompt rows are used across `iot_light_5` / `home_iot_20` / `smart_home_50` by design. If a future tier needs its own dataset, that is a separate task and a new `examples/<tier>/` tree.
  - Reward shaping (`graded_score`) — staying in the analyzer's domain; the grader here is binary per metric.
- **on violation**: if the grader needs catalog-specific knowledge beyond what `ToolSpec` exposes (e.g. cross-tool argument relations, side-effect ordering), do not branch inside the grader. Propose a Catalog extension via [[contract_catalog]] and gate the new comparison through the extended `Catalog` surface so all three tiers benefit symmetrically.

## Procedure

```
on benchmark.iot.requested(tier, llm, options) (or CLI invocation):
    cases ← load_dataset(tier, adversarial=options.adversarial, limit=options.limit)
            # iot_light_5 | home_iot_20 | smart_home_50 → examples/iot_light/dataset.jsonl
            # adversarial=True → concat examples/iot_light/adversarial_cases.jsonl
    grader ← Grader()                # exact_match + action_match + syntax_valid
    handle ← trace_store.open_run(catalog_id, tier)

    for case in cases:
        for _ in range(max(1, options.repeat)):
            try:
                result ← client.invoke(case.prompt)        # ModelResult
                grade  ← grader.match(result.plan, case.expected)
                emit lm.inference.completed(
                    case_id=case.id, plan=result.plan,
                    latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    repair_attempts=result.raw.get("attempts") if result.raw else None,
                    grade=grade,
                )
            except Exception as exc:
                emit lm.inference.failed(case_id=case.id, error=f"{type(exc).__name__}: {exc}")
                continue              # do not abort batch on per-case errors

    summary_path ← trace_store.close_run(handle)
    emit benchmark.iot.completed(catalog_id=catalog.id, tier=tier, summary_path=summary_path)

on dataset file missing (pre-run, before trace_store.open_run):
    emit benchmark.iot.failed(cause="dataset_missing"); raise FileNotFoundError; no per-case events.
on entire batch failure (run opened, every invoke raised — e.g. network down):
    close the run; emit benchmark.iot.completed(..., degenerate=True, cause=<first_error_class>).
    benchmark.iot.failed is reserved for *pre-run* failures only.
```

The runner never inspects `Catalog` internals beyond `catalog.id`; all DSL parsing / native-tool conversion happens inside the supplied `ModelClient`.

## Contract

- **in**:
  - `catalog: Catalog` — produced upstream by [[contract_catalog]] / `ganglion/contract/builtins/`.
  - `client: ModelClient` — constructed upstream by [[lm_client]].
  - `tier: Literal["iot_light_5", "home_iot_20", "smart_home_50"]`.
  - `dataset_path: Path` — defaults to `examples/iot_light/dataset.jsonl`.
  - Optional flags: `adversarial: bool = False`, `limit: int | None = None`, `repeat: int = 1`, `repair_policy: RepairPolicy | None = None`.
- **out**:
  - Per-case traces via event emission (the trace store records the payload — see [[analyzer_trace_store]]). One trace per invocation, so `repeat=N` produces `N × len(cases)` traces.
  - Exactly one `benchmark.iot.completed(catalog_id, tier, summary_path)` event per run, where `summary_path` points at the trace-store run directory.
- **event**:
  - consume: `contract.catalog.published(catalog_id, tier)` (gates the runner — runs only against a published catalog).
  - emit: `lm.inference.completed` (per invocation), `lm.inference.failed` (per failed invocation), `benchmark.iot.completed` (per run that opened a trace-store handle — including degenerate runs), `benchmark.iot.failed` (when the run could not open a handle at all, e.g. dataset missing, unknown tier).
- **failure**:
  - Dataset file missing → `FileNotFoundError`, `benchmark.iot.failed(cause="dataset_missing")`, exit non-zero. No traces.
  - Tier string not in the three accepted values → `ValueError`, no traces, no events beyond `benchmark.iot.failed(cause="unknown_tier")`.
  - Per-case `client.invoke` raises → trace records `lm.inference.failed` for that invocation, run continues, that case counts as `syntax_valid=False` in the downstream metrics.
  - Entire batch failure (every invoke raises in a row, e.g. auth missing) → `benchmark.iot.completed` still emitted with `degenerate=True` and `cause=<first_error_class>` so the trace store has a closing boundary.
- **success**: smoke run `python -m ganglion.benchmarks.iot.runner --llm rules --tier iot_light_5 --limit 5` produces exactly 5 `lm.inference.completed` events in the trace store and a single `benchmark.iot.completed` event whose `summary_path` exists on disk. `pytest tests/benchmarks/test_iot_runner.py` exercises the same path against the rule-based client offline.

## Observation

- `benchmark_case_count` = number of `lm.inference.completed` events with `tier == <tier>` since `run_open`. Per-tier counter.
- `benchmark_pass_rate` = Σ `grade.exact_match` ÷ `benchmark_case_count`. Computed per tier so M2 scaling can be read directly off the analyzer.
- `benchmark_action_pass_rate` = Σ `grade.action_match` ÷ `benchmark_case_count`. Diagnostic — when this diverges from `benchmark_pass_rate`, the failure mode is args-only.
- `benchmark_wall_minutes` = `(run_closed_at − run_opened_at) / 60`. Wall-clock cost of the batch, independent of per-invocation latency.
- `benchmark_repair_attempt_rate` = Σ `repair_attempts > 0` ÷ `benchmark_case_count`. Only meaningful when `repair_policy` is supplied; otherwise reported as `None` by the analyzer.

## Status

Spec-only. Implementation will land under `ganglion/benchmarks/iot/` and supersede today's `ganglion/eval/runner.py` IoT path. The current `ganglion/eval/{dataset,metrics,runner}.py` modules continue to back the CLI until the new module is wired through `ganglion/contract/` and [[analyzer_trace_store]] arrive.
