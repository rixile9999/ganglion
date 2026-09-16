[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_metrics

Pure offline aggregation surface over the [[analyzer_trace_store]] and the [[analyzer_failure_taxonomy]] classification sidecar. Consumes traces + classifications for a single `(catalog_id, run_id)`, folds them into a canonical `summary.json`, renders a stamped markdown `report.md`, and emits exactly one `analyzer.metrics.summarized` event. **This task does not read traces from anywhere except [[analyzer_trace_store]]; it does not mutate them; it does not classify; it does not synthesise repair rules.**

Unifies the summary code paths behind a single field-stable schema: `ganglion/analyzer/metrics.py:summarize` (IoT `CaseResult` lists) and the BFCL summary in `ganglion/benchmarks/bfcl/runner.py`. Persistence of the result next to the run's traces is delegated to the **run bundle** ([[analyzer_run_manifest]]); cross-run deltas are delegated to [[analyzer_compare]].

Status: `summarize` / `graded_score` / `CaseResult` / `RunResult` implemented at `ganglion/analyzer/metrics.py`, markdown renderer at `ganglion/analyzer/reports.py`. 2026-09-16 console-batch revision: `summary.json` + `report.md` are written by the run producer through `write_run_bundle`; `by_failure_type` comes from `classified.jsonl` via [[analyzer_analyze]].

## Role

Aggregate `Trace + Classification` pairs for one `(catalog_id, run_id)` into a versioned `summary.json` plus a stamped markdown report, and announce completion via `analyzer.metrics.summarized`.

## Scope

- **in-scope**:
  - Aggregation functions over `Trace + Classification` pairs:
    - Rates: `syntax_valid_rate`, `exact_match_rate`, `action_match_rate`, `ast_match_rate` (BFCL benches only — see [[benchmark_bfcl]]), `abstention_correct_rate` (from `Catalog.allow_empty_calls` cases via [[contract_null_action]] semantics).
    - Latency: `latency_ms_mean`, `latency_ms_p50`, `latency_ms_p95`, `latency_ms_stddev`.
    - Token totals: `input_tokens_total`, `output_tokens_total` (plus per-trace means as a drift signal).
    - Parse-strategy counts: `strict | fenced | embedded | failed` (Counter dict).
    - Repair counts: `repair_attempts_total`, `repair_successes_total`, sourced from the [[analyzer_repair_policy]] sidecar if present, falling back to the `Trace.attempts` field.
  - Breakdowns (each a sub-object of `summary.json`):
    - `by_tool` — per-tool failure attribution from `Classification.failed_tool`.
    - `by_arg` — per-arg failure attribution from `Classification.evidence.arg_name`.
    - `by_strategy` — per synth strategy if `Trace.meta.strategy` is populated.
    - `by_category` — per BFCL category for [[benchmark_bfcl]] runs; absent otherwise.
    - `by_failure_type` — histogram keyed on the [[analyzer_failure_taxonomy]] enum plus the `unclassified` key (`no_failure` at `confidence == 0.0`), computed by [[analyzer_analyze]]'s `histogram()` from `classified.jsonl`; absent when the sidecar is absent.
  - Graded score: per-trace `graded_score ∈ {0, 0.25, 0.5, 0.75, 1.0}` distribution + mean (`ganglion/analyzer/metrics.py:graded_score`); also returned by the console's `POST /api/labels` as immediate feedback ([[analyzer_label_store]]).
  - Canonical JSON schema with `schema_version: 1` (bump on any field rename / removal; additive fields are backwards-compatible).
  - Markdown report renderer suitable for `docs/*_report.md` files: one headline block, per-breakdown tables, top-N failures.
  - Persistence to `runs/traces/<catalog_id>/<run_id>/summary.json` + `runs/traces/<catalog_id>/<run_id>/report.md` — written **once, by the run producer** (CLI `--trace-store`, factory iteration) via `write_run_bundle(base_dir, manifest, summary, report_md)` from [[analyzer_run_manifest]], so a summary never exists without its `manifest.json`. Console chat sessions have no summary (`summary: null` in the run list). `read_summary(base_dir, catalog_id, run_id)` is the read path.
  - `summarize()` output keys today (the `schema_version: 1` surface): `total`, `runs_per_case`, `syntax_valid_rate`, `exact_match_rate`, `action_match_rate`, `latency_ms_{mean,p50,p95,stddev}`, `input_tokens_total`, `output_tokens_total`, `parse_strategy_counts`, `reasoning_chars_total`, `repair_attempts_total`, `repair_successes_total`, `failures[]`. The loop page's KPI tiles read exactly these with `src:` stamps.
  - Stamp convention: every numeric in `report.md` is followed by an HTML comment `<!-- src:summary.json#/path/to/field -->` so downstream verifiers (the legacy [[report_freshness]] concept, now under `docs/tasks/legacy/`) can cross-check prose against the underlying JSON.
  - Target implementation paths: `ganglion/analyzer/metrics.py` (aggregator + schema) and `ganglion/analyzer/reports.py` (markdown renderer + stamp helper).
- **out-of-scope**:
  - Trace ingestion, persistence, retention — see [[analyzer_trace_store]] (append-only contract; this task is read-only).
  - Classification rules, failure-type assignment, taxonomy evolution — see [[analyzer_failure_taxonomy]].
  - Repair rule *synthesis* from failure frequencies — see [[analyzer_rule_synthesis]].
  - Trace mutation of any kind, including in-place enrichment — forbidden by the [[analyzer_trace_store]] append-only contract.
  - Dashboards / interactive UI / web rendering — this task writes JSON + markdown to disk only.
  - Cross-run statistics (transition matrix, paired-bootstrap CI on ΔEM, `manifest_diff`, refusal on mismatched decoding) — owned by [[analyzer_compare]].
  - Writing `manifest.json` or listing runs — [[analyzer_run_manifest]]; this task hands its dict to `write_run_bundle` and never writes the bundle itself.
  - Real-time / streaming aggregation — strictly offline batch over completed runs only.
  - Editing or backfilling historical `runs/m{2,3,4}/*.json` artifacts — those remain as-is; this task writes under `runs/traces/…` exclusively.
- **on violation**: if a requested metric requires data outside the canonical `Trace` shape, do not fabricate it locally and do not read out-of-band files. Stop and escalate by proposing a new field on `Trace` via [[analyzer_trace_store]] in a separate PR. Likewise, if a metric requires data that should live on `Classification`, escalate to [[analyzer_failure_taxonomy]] — never invent a side-channel inside this task.

## Procedure

```
on analyzer.failure.classified(catalog_id, run_id) OR
   analyzer.trace.recorded(catalog_id, run_id) with no classifier configured:
    traces         ← TraceStore.open(catalog_id, run_id).iter_traces()
    classification ← ClassificationSidecar.open(catalog_id, run_id) or None
    repair_side    ← RepairSidecar.open(catalog_id, run_id) or None

    if traces is empty:
        write summary.json with { schema_version: 1, total: 0, degenerate: true }
        write report.md with the "no data" banner
        emit analyzer.metrics.summarized(catalog_id, summary_path, n_traces=0)
        return

    aggregator ← Aggregator(
        schema_version = 1,
        classified     = classification is not None,
        has_repair     = repair_side is not None,
    )
    for trace in traces:
        cls    ← classification.get(trace.id) if classification else None
        repair ← repair_side.get(trace.id)    if repair_side    else None
        aggregator.fold(trace, cls, repair)

    summary ← aggregator.finalize()
        # finalize() sorts latency samples to compute p50 / p95 / stddev,
        # rounds rates to 4 decimal places, drops empty Counter buckets,
        # and stamps `generated_at` (UTC ISO-8601) + `schema_version`.

    write runs/traces/<catalog_id>/<run_id>/summary.json   (atomic: tmp + os.replace)
    report ← render_markdown(summary, stamp = True)
    write runs/traces/<catalog_id>/<run_id>/report.md      (atomic: tmp + os.replace)
    emit analyzer.metrics.summarized(
        catalog_id  = catalog_id,
        summary_path = "runs/traces/<catalog_id>/<run_id>/summary.json",
        n_traces    = summary.total,
    )

on classification sidecar missing:
    set summary.classified = false
    compute only structural metrics (rates, latency, tokens, parse_strategy,
        graded_score); skip by_tool / by_arg / by_failure_type breakdowns.
    report.md prints a one-line banner "classification sidecar absent".

on Trace.attempts absent AND repair sidecar absent:
    omit repair_attempts_total and repair_successes_total
    (do not synthesise zero — absent ≠ zero, per task_principle §7 "fail loud").

on summary write IO error:
    do not emit analyzer.metrics.summarized.
    re-raise; outer harness handles retry. Partial writes are impossible
    because the atomic rename only flips the path on success.

on schema drift detected (e.g. unknown Trace field, missing required field):
    raise AnalyzerSchemaMismatch; escalate to [[analyzer_trace_store]].
    do not coerce, do not default, do not emit.
```

The aggregator is a fold — single pass, O(n_traces) time, O(distinct keys) space. Sorting (for p50 / p95) is deferred to `finalize()` so the fold itself stays streaming-friendly if a future task drops the offline-only restriction. Rates are computed in `finalize()` rather than incrementally, so the order of fold operations cannot influence the output bit-for-bit — this is what underwrites the success predicate's ≤1e-6 reproducibility claim.

## Contract

- **in**:
  - `Trace` stream from [[analyzer_trace_store]] for one `(catalog_id, run_id)`.
  - Optional `Classification` sidecar from [[analyzer_failure_taxonomy]] for the same key.
  - Optional repair sidecar from [[analyzer_repair_policy]].
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/summary.json` — fields documented above, gated by `schema_version: 1`. Stable field names; additive evolution only within a major version.
  - `runs/traces/<catalog_id>/<run_id>/report.md` — markdown with one HTML stamp comment per numeric claim.
- **event**: consume `analyzer.trace.recorded`, `analyzer.failure.classified`; emit exactly one `analyzer.metrics.summarized(catalog_id, summary_path, n_traces)` per completed run.
- **failure**:
  - Empty trace set → write `summary.json` with `total: 0, degenerate: true`; emit event with `n_traces=0`; do not crash.
  - Classification sidecar missing → write `summary.json` with `classified: false` and structural-only metrics; still emit event.
  - Repair sidecar missing AND `Trace.attempts` absent → omit repair fields entirely.
  - Required `Trace` field missing → stop, escalate via [[analyzer_trace_store]]; do not invent a default.
  - IO error on write → do not emit event; surface the error.
- **success**: against a hand-known fixture of 100 traces (golden bundle, checked in alongside the impl), every numeric field in `summary.json` is byte-for-byte reproducible to ≤1e-6 between independent runs, and `pytest -q tests/test_analyzer_metrics.py` exits zero.

## Observation

- `metric_run_count` = number of completed `analyzer.metrics.summarized` emissions per UTC day.
- `metric_run_duration_ms` = wall time from first fold to event emit; drift watch for the O(n) guarantee.
- `summary_size_bytes` = on-disk size of `summary.json`; drift surface if a future change accidentally inflates the schema.
- `summary_field_count` = number of leaf keys in `summary.json`; drift surface for accidental schema growth without a `schema_version` bump.
- `report_stamp_density` = stamped numerics ÷ total numerics in `report.md`; must be `1.0` once stamping is mandatory.
- `degenerate_run_rate` = degenerate (total=0) summaries ÷ total summaries; non-zero means upstream trace recording is broken.
- `classified_run_rate` = classified summaries ÷ total summaries; non-zero gap surfaces classifier lag.

## Wikilinks

[[analyzer_trace_store]] · [[analyzer_failure_taxonomy]] · [[analyzer_rule_synthesis]] · [[analyzer_repair_policy]] · [[analyzer_run_manifest]] · [[analyzer_compare]] · [[analyzer_analyze]] · [[analyzer_label_store]] · [[benchmark_iot]] · [[benchmark_bfcl]]
