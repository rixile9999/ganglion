[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Supersedes: [legacy/external_benchmark_bfcl](./legacy/external_benchmark_bfcl.md)

# benchmark_bfcl

BFCL v4 single-turn benchmark consumer. Loads the deterministic seed=42
subsample at `examples/bfcl/v4/sample/*.jsonl`, compiles a fresh `Catalog` per
case via [[contract_schema_compiler]], drives a `ModelClient` from
[[lm_client]] against each case, and grades the resulting `ActionPlan` with a
Python re-implementation of upstream BFCL's AST checker. One trace per
invocation is materialised for [[analyzer_trace_store]] — **failed
invocations included, with the model's raw output preserved from the
exception** — and, when a trace store is given, a per-category run bundle
(`manifest.json` + `summary.json`) is written under `bfcl/<category>`.
Cross-phase aggregation belongs to [[analyzer_metrics]] /
[[factory_evaluation]]. Each BFCL case ships its own tool list, so per-case
catalogs are compiled and discarded — nothing is cached across cases, and a
`bfcl/<category>` run is therefore **read-only** in the console: there is no
single catalog to resolve, so `analyze` / labels do not apply.

## Role

Run BFCL v4 single-turn Python categories against a configurable
`ModelClient`, materialise per-case traces plus a per-category run bundle,
and emit the run-level events suitable for the downstream metrics pipeline.

## Scope

- **in-scope**:
  - Five-category loader: `simple_python`, `multiple`, `parallel`,
    `parallel_multiple`, `irrelevance`. JSONL row shape
    `{id, question, function, ground_truth?}`; `user_message` extracted from
    `question[0][-1]`. `BFCLCase.ground_truth` is
    `tuple[{"<func>": {"<arg>": [accepted values…]}}] | None` — a *set of
    acceptable values per arg* consumed by `ast_match`, not a single plan.
  - Per-case `Catalog` compile via [[contract_schema_compiler]]
    (`compile_tool_calling_schema(case.tools, name=f"bfcl_{case.id}", allow_empty_calls=…)`).
    Fresh per case, never cached across cases.
  - `Catalog.allow_empty_calls` set conditionally — `True` for the
    `irrelevance` category, or whenever the runner is invoked with
    `--bfcl-allow-empty-calls` (see [[contract_null_action]]).
  - BFCL AST grader (Python port of upstream `ast_checker`):
    - `irrelevance` (no `ground_truth`): valid iff `predicted_calls == ()`.
    - `simple` (1 call): `_simple()` checker against the single answer set.
    - `multiple` (1 call out of N candidate functions): `_multiple()`.
    - `parallel` / `parallel_multiple` (N calls, order-insensitive):
      `_parallel_no_order()`.
    - Type coercion: `int → float` promotion, `tuple → list` normalisation,
      string standardisation (case-insensitive, punctuation stripped) via
      `_string_checker`, `_list_checker`, `_dict_checker`, `_list_dict_checker`.
    - `is_variable` placeholder support on the BFCL ground-truth side.
  - Per-case runner `run_bfcl(client_factory, cases, *, repeat=1, allow_empty_calls=False) -> list[BFCLCaseResult]`
    (`ganglion/benchmarks/bfcl/runner.py`): builds a per-case catalog,
    instantiates a client from the factory, invokes `repeat` times, grades
    `runs[0]` via `ast_match()`. **Failure raw preservation:** the per-run
    `except Exception` records `BFCLRunResult(plan=None, raw=getattr(exc, "raw", None), latency_ms=<elapsed>, input_tokens=None, output_tokens=None, error=f"{type(exc).__name__}: {exc}")`
    — it never raises; the `raw` attribute is what `ModelOutputError` /
    `RepairExhaustedError` carry ([[lm_client]], [[analyzer_repair_policy]]).
  - Trace materialisation — `traces_from_bfcl_results(results: list[BFCLCaseResult], *, category: str, run_id: str, model_id: str) -> list[Trace]`:
    one `Trace` per `(case, repeat_index)` with `catalog_id=f"bfcl/{category}"`,
    `source="benchmark.bfcl"`, `case_id=case.id`, `prompt=case.user_message`,
    **`expected_plan=None` always** (the runner never has an Action-IR gold —
    `ground_truth` is a per-arg acceptance set), `attempts=attempts_from_raw(run.raw)`,
    `raw_output=attempts[-1]["content"]` if any else `""`, `raw_plan=raw_plan_from_attempts(attempts)`,
    `plan=run.plan.to_jsonable()` or `None`, `error_type=run.error` (only when the
    run itself errored — the AST verdict `grade.valid` / `grade.error_type` is
    **not** stored on the `Trace`; it lives in `summary.json` and the per-case
    JSONL), `parse_strategy` from `raw["parse_strategy"]` when present else
    `"json_object"`, `latency_ms`, tokens (`0` when `None`), `timestamp`,
    `run_id`, `model_id`, `repeat_index`.
  - CLI persistence (`ganglion/cli.py`, BFCL branch): the same
    `--trace-store <dir>`, `--run-id <name>`, `--iteration <int>`,
    `--parent-run <run_id>` flags as [[benchmark_iot]]. When `--trace-store`
    is given, **per category**: append the traces from
    `traces_from_bfcl_results` to `TraceStore(dir)` (shard
    `<dir>/bfcl/<category>/<run_id>/traces.jsonl`), then
    `write_run_bundle(dir, manifest, summary, report_md=None)`
    ([[analyzer_run_manifest]]) with
    `RunManifest(run_id, catalog_id=f"bfcl/{category}", catalog_fingerprint="" (per-case catalogs — unknown), model_id=spec.model_id, benchmark="bfcl", metric_kind="ast_match", client_kind=spec.client, model_fingerprint=model_fingerprint(spec), dataset_path="examples/bfcl/v4/sample/<category>.jsonl", dataset_sha256, n_cases=<cases in that category>, limit=--bfcl-per-category, decoding={"repair", "repair_max_attempts", "thinking", "repeat", "grammar_mask": false, "allow_empty_calls"}, iteration, parent_run_id, git_head, python, started_at, finished_at)`
    and `summary=summarize_bfcl(<that category's results>)` plus the
    `bfcl_*` flags; each run dir path printed to stderr. The merged
    stdout summary and `--bfcl-output` are unchanged.
  - CLI surface: `--bfcl <category|callable|all>`,
    `--bfcl-per-category N` (head slicing), `--bfcl-skip-per-category N`
    (sampling offset), `--bfcl-output PATH` (per-case JSONL),
    `--bfcl-allow-empty-calls`, plus the persistence flags above.
  - Target paths: `ganglion/benchmarks/bfcl/{loader,grader,case_catalog,runner}.py`,
    the BFCL branch of `ganglion/cli.py`.

- **out-of-scope**:
  - BFCL multi-turn categories (`multi_turn_*`). Single-turn only — the
    one-shot DSL-emission hypothesis is not measured by multi-turn dialogue.
  - Non-Python categories (Java, JavaScript, REST). `convert_func_name` and
    language-specific coercion branches are deliberately omitted.
  - BFCL exec match (running tool calls against a sandbox). AST match only.
  - Live / enterprise-contributed BFCL categories. Sample is non-live only.
  - Dataset regeneration. `examples/bfcl/v4/subsample.py` is a separate tool
    and the sample rows under `examples/bfcl/v4/sample/*.jsonl` are SSOT —
    never hand-edit.
  - Fine-tuning data generation from BFCL — that is [[lm_data_synth]]
    territory.
  - Upstream `bfcl_eval` invocation. The AST checker is re-implemented in
    `ganglion/benchmarks/bfcl/grader.py`; we do not shell out to upstream.
  - Cross-phase / cross-category aggregation tables. Per-category run here;
    aggregation is [[analyzer_metrics]] / [[factory_evaluation]].
  - `Trace` schema and `attempts_from_raw` — [[analyzer_trace_store]];
    `RunManifest` — [[analyzer_run_manifest]]; ledger rows —
    [[analyzer_event_ledger]]; client / registry — [[lm_client]],
    [[lm_model_registry]].
  - Converting `ground_truth` into an Action-IR gold, classification, rule
    synthesis or labelling on `bfcl/*` runs — per-case catalogs are not
    resolvable, so `analyze_run` raises `CatalogNotResolvable` and
    [[console_operator]] shows these runs read-only with status from
    `summary.json`.
  - A markdown `report.md` for BFCL bundles — `render_markdown` targets the
    IoT summary keys; the BFCL bundle ships `report_md=None` until
    [[analyzer_metrics]] grows an `ast_match` view.

- **on violation**: if a grader case requires upstream BFCL behaviour we have
  not yet replicated (a new type-coercion rule, a Java/JS branch, an
  `is_variable` extension), **escalate** — do not silently approximate. The
  AST checker is a faithfulness contract; soft-fixing it inside this task
  invalidates the comparability claim against the upstream leaderboard. If a
  consumer wants `expected_plan` on a BFCL trace, do not fabricate one from
  `ground_truth` here — that is a separate gold-derivation task.

## Procedure

```
on benchmark_bfcl(category, client_factory, allow_empty_calls) — cli --bfcl <cat|callable|all>:
    cases ← load_category(category)[skip : skip + per_category][:limit]
    consume contract.catalog.published                     # per case below
    started_at ← now
    results ← run_bfcl(client_factory, cases, repeat, allow_empty_calls):
        for case in cases:
            catalog ← build_case_catalog(case, allow_empty_calls = allow_empty_calls or case.category == "irrelevance")
            client  ← client_factory(catalog)                                        # [[lm_client]] via registry
            for r in range(max(1, repeat)):
                try:    res ← client.invoke(case.user_message)
                        BFCLRunResult(plan=res.plan, raw=res.raw, latency_ms, input_tokens, output_tokens)
                except Exception as exc:                                             # never aborts the batch
                        BFCLRunResult(plan=None, raw=getattr(exc, "raw", None), latency_ms=elapsed, tokens=None,
                                      error=f"{type(exc).__name__}: {exc}")
            grade ← ast_match(runs[0].plan.calls if runs[0].plan else (), case)
            BFCLCaseResult(case, runs, grade, dsl_chars, native_chars)
    summary ← summarize_bfcl(results) + bfcl_* flags; print → stdout; --bfcl-output → per-case JSONL

    if --trace-store:
        for category in categories:
            res_c   ← [r for r in results if r.case.category == category]
            traces  ← traces_from_bfcl_results(res_c, category=category, run_id=run_id, model_id=spec.model_id)
            store   ← TraceStore(dir); for t in traces: store.append(t)               # shard bfcl/<category>/<run_id>
            manifest ← RunManifest(… fields above …, catalog_id=f"bfcl/{category}", metric_kind="ast_match", finished_at=now)
            run_dir ← write_run_bundle(dir, manifest, summarize_bfcl(res_c) + flags, report_md=None)
            emit analyzer.run.recorded(catalog_id=f"bfcl/{category}", run_id, manifest_path, n_traces=len(traces))
            emit analyzer.metrics.summarized(catalog_id=f"bfcl/{category}", run_id, summary_path, n_traces)
            print(run_dir) → stderr

on malformed BFCL row:    log + count in degenerate_cases; no trace emitted.
on grader raises:         grade.error_type = "grader_error:<exc-class>"; continue.
on --llm rules + --bfcl:  SystemExit (rules has no BFCL adapter; use a qwen* / registry model).
```

## Contract

- **in**:
  - BFCL category: one of `simple_python | multiple | parallel | parallel_multiple | irrelevance`,
    or the meta-selectors `callable` (the four non-irrelevance categories) /
    `all` (all five).
  - `client_factory: Callable[[Catalog], ModelClient]` — built externally
    from [[lm_model_registry]] / [[lm_client]]; this task does not know
    about provider configuration.
  - `allow_empty_calls: bool` — explicit opt-in for non-irrelevance categories
    (irrelevance always gets `True` regardless of the flag).
  - Persistence: `trace_store: Path | None`, `run_id`, `iteration`, `parent_run`.
- **out**:
  - `list[BFCLCaseResult]` from `run_bfcl`, always — even when every
    invocation failed.
  - Per-case JSONL at `--bfcl-output` — today's `runs/bfcl/*_cases.jsonl`
    shape: one row per case with `{id, category, tool_count, expects_call,
    ast_valid, grade_error_type, syntax_valid, error, latency_ms,
    input_tokens, output_tokens, dsl_chars, native_chars, predicted}`.
  - Merged summary on stdout — keys `{total, ast_match_rate, syntax_valid_rate,
    latency_ms_{mean,p50,p95,stddev}, input_tokens_total, output_tokens_total,
    dsl_chars_mean, native_chars_mean, by_category, error_type_counts,
    failures[]}` plus the `bfcl_*` flags.
  - With `--trace-store`, per category: `<dir>/bfcl/<category>/<run_id>/{traces.jsonl, manifest.json, summary.json, events.jsonl}`
    — one trace per `(case, repeat_index)`, failed invocations included with
    `plan: null`, `error_type`, `attempts`, `raw_plan`; `expected_plan: null`
    on every row; run dir paths on stderr.
- **event**:
  - consume `contract.catalog.published` (per case, from
    [[contract_schema_compiler]] / [[contract_catalog]]).
  - emit, as ledger rows in each `<run_dir>/events.jsonl` when
    `--trace-store` is given ([[analyzer_event_ledger]]):
    `analyzer.run.recorded(catalog_id="bfcl/<category>", run_id, manifest_path, n_traces)`
    and `analyzer.metrics.summarized(catalog_id, run_id, summary_path, n_traces)`.
    Per-invocation `lm.inference.completed | failed` are folded into the
    `Trace` (`error_type`); `benchmark.bfcl.completed` remains an in-process
    signal for [[factory_pipeline]] and is not a ledger row.
- **failure**:
  - Malformed BFCL row → log + skip + count in `degenerate_cases`; the row
    produces no trace.
  - Per-case client/parse exception → `BFCLRunResult(error=…, raw=<exception .raw or None>)`,
    case marked syntax-invalid, run continues; its trace keeps `raw_plan` /
    `attempts` when `.raw` was present.
  - Grader exception → record on the case as `error_type = "grader_error:<cls>"`,
    continue the run.
  - Unknown category in jsonl (loader rejection) → hard `ValueError`; run
    aborts before any case is invoked.
  - `TraceStore` / bundle `OSError` → propagate; stdout summary already printed.
- **success**:
  - `pytest tests/test_bfcl_smoke.py tests/test_bfcl_grader.py tests/test_bfcl_runner.py` passes,
    the runner test asserting at least: a client factory whose client raises
    `ModelOutputError(raw=…)` → `runs[0].raw` equals the exception's `.raw`
    and `runs[0].error` is set; `traces_from_bfcl_results(run_bfcl(stub_factory, cases[:3]), category="simple_python", run_id="t", model_id="stub")`
    → 3 traces with `catalog_id == "bfcl/simple_python"`,
    `source == "benchmark.bfcl"`, `expected_plan is None`, `prompt == case.user_message`.
  - Smoke run `--bfcl simple_python --bfcl-per-category 5 --model qwen3.6-plus@dashscope --trace-store <tmp> --run-id t1`
    produces `<tmp>/bfcl/simple_python/t1/traces.jsonl` with exactly 5 lines,
    a `manifest.json` with `benchmark == "bfcl"`, `metric_kind == "ast_match"`,
    `n_cases == 5`, and a `summary.json` with `total == 5` and
    `ast_match_rate ∈ [0,1]`.

## Observation

- `ast_match_rate[category]` = `∑ grade.valid ÷ total[category]`. Primary
  headline metric, directly comparable to the upstream BFCL leaderboard.
- `by_error_type[category]` = histogram of `grade.error_type` over failing
  cases. Distinguishes wrong-function-name vs. wrong-args vs.
  irrelevance:unexpected_call rather than collapsing into one rate.
- `dsl_chars_mean`, `native_chars_mean` — per-case `Catalog.render_json_dsl()`
  vs `Catalog.render_openai_tools()` length on the wire (before tokenisation).
  IR-compression evidence, independent of any specific model.
- `per_case_catalog_compile_ms_mean` — wall-time for
  `build_case_catalog(case)`. Per-case compile is the hot path and a
  perf-regression surface.
- `degenerate_cases` — count of rows skipped due to malformed input; a
  non-zero value implies the SSOT sample has drifted and
  [[analyzer_trace_store]] should reject the run.
- `trace_raw_preserved_rate{model_id}` = failed runs with `raw is not None`
  ÷ failed runs — target 1.0; below it a client raises without `.raw`.

## Status

Status: spec, implementation in progress (2026-09-16). Live: `ganglion/benchmarks/bfcl/{loader,grader,case_catalog,runner}.py`, the BFCL branch of `ganglion/cli.py`. This revision adds failure raw preservation in the runner's per-run `except`, `traces_from_bfcl_results` (with `expected_plan=None` always), and the per-category `--trace-store` bundle.

Wikilinks: [[contract_catalog]], [[contract_schema_compiler]],
[[contract_null_action]], [[lm_client]], [[lm_model_registry]],
[[analyzer_trace_store]], [[analyzer_run_manifest]], [[analyzer_event_ledger]],
[[analyzer_metrics]], [[benchmark_iot]], [[console_operator]].
