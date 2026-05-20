[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Supersedes: [legacy/external_benchmark_bfcl](./legacy/external_benchmark_bfcl.md)

# benchmark_bfcl

BFCL v4 single-turn benchmark consumer. Loads the deterministic seed=42
subsample at `examples/bfcl/v4/sample/*.jsonl`, compiles a fresh `Catalog` per
case via [[contract_schema_compiler]], drives a `ModelClient` from
[[lm_client]] against each case, and grades the resulting `ActionPlan` with a
Python re-implementation of upstream BFCL's AST checker. One trace per case is
emitted into [[analyzer_trace_store]]; cross-phase aggregation belongs to
[[analyzer_metrics]] / [[factory_evaluation]]. Each BFCL case ships its own
tool list, so per-case catalogs are compiled and discarded — nothing is cached
across cases.

## Role

Run BFCL v4 single-turn Python categories against a configurable `ModelClient`
and emit a per-case trace + a per-category completion event suitable for the
downstream metrics pipeline.

## Scope

- **in-scope**:
  - Five-category loader: `simple_python`, `multiple`, `parallel`,
    `parallel_multiple`, `irrelevance`. JSONL row shape
    `{id, question, function, ground_truth?}`; `user_message` extracted from
    `question[0][-1]`.
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
  - Per-case runner: takes `(client_factory, cases, repeat, allow_empty_calls)`,
    iterates cases, builds a per-case catalog, instantiates a client from the
    factory, invokes, grades via `ast_match()`, and produces a per-case record
    matching today's `runs/bfcl/*_cases.jsonl` shape.
  - CLI surface: `--bfcl <category|callable|all>`,
    `--bfcl-per-category N` (head slicing), `--bfcl-skip-per-category N`
    (sampling offset), `--bfcl-output PATH` (per-case JSONL),
    `--bfcl-allow-empty-calls`.
  - Target paths: `ganglion/benchmarks/bfcl/{loader,grader,case_catalog,runner}.py`.

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

- **on violation**: if a grader case requires upstream BFCL behaviour we have
  not yet replicated (a new type-coercion rule, a Java/JS branch, an
  `is_variable` extension), **escalate** — do not silently approximate. The
  AST checker is a faithfulness contract; soft-fixing it inside this task
  invalidates the comparability claim against the upstream leaderboard.

## Procedure

```
on benchmark_bfcl(category, client_factory, allow_empty_calls):
    cases ← load_category(category)[skip : skip + per_category]
    consume contract.catalog.published                     # per case below
    for case in cases:
        try:
            catalog ← build_case_catalog(case,
                          allow_empty_calls = allow_empty_calls
                                              or case.category == "irrelevance")
        except SchemaCompileError:
            log + degenerate_cases += 1 ; continue
        client ← client_factory(catalog)                   # [[lm_client]]
        try:
            result ← client.invoke(case.user_message)
        except Exception as e:
            emit lm.inference.failed(case.id, e) ; continue
        grade ← ast_match(result.plan.calls if result.plan else (), case)
        emit lm.inference.completed(case.id, plan, latency_ms, tokens, grade)
        append per-case row to <run_dir>/<category>_cases.jsonl
    emit benchmark.bfcl.completed(category, summary_path)

on malformed BFCL row: log + count in degenerate_cases; no trace emitted.
on grader raises:         record error_type = "grader_error:<exc-class>"; continue.
on --llm rules + --bfcl:  SystemExit (rules has no BFCL adapter; use qwen*).
```

## Contract

- **in**:
  - BFCL category: one of `simple_python | multiple | parallel | parallel_multiple | irrelevance`,
    or the meta-selectors `callable` (the four non-irrelevance categories) /
    `all` (all five).
  - `client_factory: Callable[[Catalog], ModelClient]` — built externally
    from [[lm_client]]; this task does not know about provider configuration.
  - `allow_empty_calls: bool` — explicit opt-in for non-irrelevance categories
    (irrelevance always gets `True` regardless of the flag).
- **out**:
  - Per-case JSONL at `<run_dir>/<category>_cases.jsonl` — today's
    `runs/bfcl/*_cases.jsonl` shape: one row per case with `{id, category,
    user_message, ground_truth, predicted, runs[], grade}`.
  - Per-category summary at `<run_dir>/<category>_summary.json` — keys
    `{total, ast_match_rate, syntax_valid_rate, latency_ms_{mean,p50,p95,stddev},
    input_tokens_total, output_tokens_total, dsl_chars_mean,
    native_chars_mean, by_category, error_type_counts, failures[]}`.
- **event**:
  - consume `contract.catalog.published` (per case, from
    [[contract_schema_compiler]] / [[contract_catalog]]).
  - emit `lm.inference.completed(case_id, plan, latency_ms, tokens, grade)`
    per case, into [[analyzer_trace_store]].
  - emit `lm.inference.failed(case_id, error)` on per-case client/parse
    exception.
  - emit `benchmark.bfcl.completed(category, summary_path)` once per
    category run.
- **failure**:
  - Malformed BFCL row → log + skip + count in `degenerate_cases`; the row
    produces no trace and no `lm.inference.*` event.
  - Per-case client/parse exception → record `lm.inference.failed`, mark
    case syntax-invalid, continue the run.
  - Grader exception → record on the case as `error_type = "grader_error:<cls>"`,
    continue the run.
  - Unknown category in jsonl (loader rejection) → hard `ValueError`; run
    aborts before any case event is emitted.
- **success**:
  - `pytest tests/test_bfcl_smoke.py` passes.
  - `pytest tests/test_bfcl_grader.py` passes.
  - Smoke run `--bfcl simple_python --bfcl-per-category 5 --llm qwen`
    produces exactly 5 `lm.inference.completed` traces and one
    `benchmark.bfcl.completed` event whose `summary_path` exists, contains
    `total == 5`, and has `ast_match_rate ∈ [0,1]`.

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
  `build_case_catalog(case)`. Per-case compile is the hot path (every BFCL
  case compiles a fresh catalog) and a perf-regression surface.
- `degenerate_cases` — count of rows skipped due to malformed input; a
  non-zero value implies the SSOT sample has drifted and
  [[analyzer_trace_store]] should reject the run.

Wikilinks: [[contract_catalog]], [[contract_schema_compiler]],
[[contract_null_action]], [[lm_client]], [[analyzer_trace_store]],
[[analyzer_metrics]], [[benchmark_iot]].
