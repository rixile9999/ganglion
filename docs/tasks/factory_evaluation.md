[← New tasks](./README.md) · Composite principle: [workflow_principle](../agent-forge/workflow_principle.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# factory_evaluation (composite)

Measurement-only composite. Pick a `(client, catalog, benchmark)` tuple, run it once, summarise once, emit one terminal verdict. This is today's `python -m ganglion.eval.runner --llm … --tier …` / `--bfcl …` invocation, promoted to a named composite so other workflows can compose against it. **No training, no rule synthesis, no catalog mutation** — those live in [[factory_pipeline]]. This composite answers exactly one question: *is this catalog already good against this benchmark for this client?*

Cites [[workflow_principle]]: a composite earns its keep when its contract is *not reducible* to any single primitive's. Here the irreducible surface is the `(client × catalog × benchmark)` tuple selection plus the summary-forwarding boundary — no single primitive cares about all three.

## Role

Dispatch a single measurement run against a chosen `(client, catalog, benchmark)` tuple by wiring [[benchmark_iot]] / [[benchmark_bfcl]] into [[analyzer_trace_store]] + [[analyzer_metrics]], and emit exactly one `factory.evaluation.completed` event per invocation.

## Scope

- **in-scope**:
  - CLI dispatch: `python -m ganglion eval --client <client> --catalog <id> --benchmark <iot|bfcl> [--tier <iot_light_5|home_iot_20|smart_home_50>] [--bfcl-category <cat|callable|all>] [--repeat N] [--limit N]`. Target path: `ganglion/cli.py` (the `eval` subcommand).
  - Single (client, catalog, benchmark) tuple per invocation. The CLI rejects ambiguous combinations (e.g. `--tier` together with `--bfcl-category`) at parse time.
  - Wiring [[lm_client]] (selected via `--client`) and [[contract_catalog]] (selected via `--catalog`) into the chosen benchmark primitive.
  - Subscribing to `benchmark.iot.completed` or `benchmark.bfcl.completed`, waiting for [[analyzer_trace_store]] ingest, then requesting [[analyzer_metrics]] summarisation.
  - Aggregate emit: `factory.evaluation.completed(client_id, catalog_id, benchmark_id, summary_path)`.
  - Forwarding the [[analyzer_metrics]] summary JSON path + markdown report path; the composite never writes either artifact itself.
  - Per-invocation observation aggregates (`evaluation_runs_total`, `evaluation_wall_minutes`).
- **out-of-scope**:
  - Training — [[lm_finetune]] is **not** invoked from this composite. Eval is read-only against weights.
  - Data synth — [[lm_data_synth]] is not invoked. Datasets are taken as-is from the benchmark primitive.
  - Rule synthesis — [[analyzer_rule_synthesis]] does not run during measurement-only flows. Eval observes; it does not propose patches.
  - Catalog mutation — the [[contract_catalog]] passed in is read-only for the duration of this composite. No `ToolSpec` edits, no alias additions.
  - A/B comparisons across multiple clients or multiple catalogs in one run — exactly one tuple per invocation. Cross-run comparison is downstream report tooling (e.g. `runs/aggregate.py`, `runs/bfcl/aggregate.py`), not this composite.
  - Real-time dashboards / progress streaming — composite emits one terminal `factory.evaluation.completed` event; live observability is left to the event bus.
  - Defining new metrics — composite forwards the metrics [[analyzer_metrics]] already computes; it does not invent its own headline numbers.
- **on violation**: if the composite catches itself wanting to apply patches, mutate a catalog, or trigger fine-tuning, **stop and escalate** — those are [[factory_pipeline]] responsibilities. Emit `factory.evaluation.aborted(reason="scope_violation")` rather than silently expanding scope.

## Procedure

```
on factory.evaluation.start(client_id, catalog_id, benchmark_id, opts):
    validate opts (mutually-exclusive --tier vs --bfcl-category, --repeat ≥ 1, --limit ≥ 1)
    on validation error → fail loud at CLI (exit non-zero)
    enqueue benchmark.<benchmark_id>.request(client_id, catalog_id, opts)

on benchmark.iot.completed(catalog_id, ..., summary_path)
   | benchmark.bfcl.completed(catalog_id, ..., summary_path):
    # benchmark primitive has finished and written its per-case trace stream
    # to [[analyzer_trace_store]]; now request the summary pass
    enqueue analyzer.metrics.request(catalog_id, summary_path)

on analyzer.metrics.summarized(catalog_id, summary_path, n_traces):
    emit factory.evaluation.completed(client_id, catalog_id, benchmark_id, summary_path)
    stop

on benchmark.<id>.failed(reason)
   | analyzer.metrics.failed(reason):
    emit factory.evaluation.aborted(client_id, catalog_id, benchmark_id, reason)
    stop
```

The procedure delegates exclusively via events — no direct task-to-task calls (per [[workflow_principle]] §Decision-rule step 4). Each `enqueue` step is a one-way emission; the composite resumes work only on the next named consume edge.

## Contract

- **in**: `(client_id, catalog_id, benchmark_id, opts)` from CLI (`ganglion/cli.py` `eval` subcommand) or programmatic caller. `opts` carries `--tier | --bfcl-category | --repeat | --limit | --repair`.
- **out**:
  - Exactly one `factory.evaluation.completed(client_id, catalog_id, benchmark_id, summary_path)` event per invocation (or one `factory.evaluation.aborted` on failure).
  - One summary JSON + one markdown report at `summary_path` (both produced by [[analyzer_metrics]], not by this composite).
- **event**:
  - consume: `benchmark.iot.completed`, `benchmark.bfcl.completed`, `analyzer.metrics.summarized`, plus the corresponding `.failed` events.
  - emit: `factory.evaluation.completed | factory.evaluation.aborted`.
- **failure**:
  - Benchmark primitive failure → propagate as `factory.evaluation.aborted(reason="benchmark_failed", cause=<primitive reason>)`.
  - Metrics step failure (e.g. trace ingest missing for the requested `catalog_id`) → `factory.evaluation.aborted(reason="metrics_failed", cause=…)`.
  - Opts validation error (mutually-exclusive flags, malformed `--limit`) → fail loud at the CLI before any event is emitted; no aborted event for unparseable input.
  - Unknown `client_id` / `catalog_id` / `benchmark_id` → fail loud at the CLI; do not enqueue a benchmark request that will certainly fail downstream.
- **success**: the smoke run `ganglion eval --client rules --catalog iot_light_5 --benchmark iot --limit 5` produces a `factory.evaluation.completed` event whose `summary_path` points to a non-empty `summary.json` containing an `exact_match_rate` key. Composite is idempotent on identical `(client_id, catalog_id, benchmark_id, opts)` invocations modulo timestamp fields in the summary.

## Observation

- `evaluation_runs_total[client_id, catalog_id, benchmark_id]` — counter incremented on every terminal `factory.evaluation.{completed,aborted}` emission. Lets downstream tooling spot which tuples are over- or under-measured.
- `evaluation_wall_minutes` — wall-clock from `factory.evaluation.start` to terminal emission. Captures the composite's own overhead; primitive wall-clock is owned by the primitives.
- `evaluation_aborted_rate[reason]` — `factory.evaluation.aborted` ÷ total invocations, bucketed by `benchmark_failed | metrics_failed | scope_violation`. High `scope_violation` is a signal the CLI surface is exposing things eval shouldn't see.

The summary numbers themselves (`exact_match_rate`, `ast_match_rate`, `latency_ms_p50`, token totals, …) are **not new metrics** — this composite only forwards them from [[analyzer_metrics]]. Re-defining them here would be the *wrapper composite* anti-pattern called out in [[workflow_principle]] §Anti-patterns.

## Inheritance (per [[workflow_principle]])

| Mechanism | What this composite inherits |
|---|---|
| Pointer | This file links back to [task_principle](../agent-forge/task_principle.md) and [workflow_principle](../agent-forge/workflow_principle.md). |
| Template | Same 6-section structure as the primitives. |
| Pattern | Borrowed from [[release_health]] (event-aggregator composite) and the *docs_health_check* worked example in `workflow_principle`. |
| Data | None — `(client, catalog, benchmark)` tuple comes from the CLI invocation, not from a data table in this doc. If a registry of "known good tuples" emerges later, the right move is a `## Registered tuples` data slot here, not a parallel composite. |

## Negative checks (composite anti-patterns)

- ☐ This file declares its own `in / out / event / failure / success` — not just a topology diagram. (If it ever degenerates to "just a diagram", delete per [[workflow_principle]] §Anti-patterns.)
- ☐ Does not wrap a single primitive — three independent primitives ([[lm_client]], [[contract_catalog]], one of [[benchmark_iot]]/[[benchmark_bfcl]]) plus the [[analyzer_metrics]] summary edge are wired in.
- ☐ Does not mutate any primitive's `in-scope` — catalog is read-only, client weights are read-only, benchmark dataset is read-only.
- ☐ Does not redefine metrics that [[analyzer_metrics]] owns — composite only forwards `summary_path`.
- ☐ Distinct from [[factory_pipeline]] — pipeline trains and refines; this composite measures. If a single invocation finds itself wanting to write LoRA adapters or open a `catalog_spec_sync` PR, the abstraction boundary has leaked and the work belongs in [[factory_pipeline]].

## Relationship to existing CLI

Today's `python -m ganglion.eval.runner --llm … --tier …` and `python -m ganglion.eval.runner --llm … --bfcl …` invocations are the *de-facto* implementation of this composite — they just happen to inline the benchmark + summarisation steps under one entry point. Promotion to a named composite under `ganglion/cli.py` (the `eval` subcommand) does three things the inlined CLI cannot:

1. Names the (client × catalog × benchmark) tuple selection as a first-class contract, so other workflows (e.g. [[factory_pipeline]]'s pre/post-train evaluation gates) can subscribe to `factory.evaluation.completed` instead of shelling out and parsing stdout.
2. Decouples the *measurement* edge from the *summarisation* edge — today both live in `ganglion/eval/runner.py`; the composite splits them along the `benchmark.<id>.completed → analyzer.metrics.request` event boundary so [[analyzer_metrics]] can be replaced or extended without touching the benchmark primitives.
3. Makes the read-only invariant explicit. The current runner *happens* to be measurement-only; the composite contract makes that a checkable property (no `factory.*` events besides `evaluation.{completed,aborted}` are emitted).

Related: [[lm_client]], [[contract_catalog]], [[benchmark_iot]], [[benchmark_bfcl]], [[analyzer_trace_store]], [[analyzer_metrics]], [[factory_pipeline]].
