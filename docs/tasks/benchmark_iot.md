[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# benchmark_iot

Adapter that drives the IoT-light tier benchmarks (`iot_light_5` / `home_iot_20` / `smart_home_50`, plus the `home_assistant_4` projection from [[contract_tier_home_assistant]]) as a *consumer* of a constructed [[contract_catalog]] and [[lm_client]]. Loads the IoT JSONL datasets, invokes the client per case, grades with exact-match + action-match semantics, materialises one `Trace` per invocation for [[analyzer_trace_store]] — **failed invocations included, with the model's raw output preserved from the exception** — and, when a trace store is given, writes the run bundle (`manifest.json` + `summary.json` + `report.md`) that [[console_operator]], [[analyzer_compare]] and the analyzer sidecars are keyed on.

## Role

Run a catalog against the IoT dataset under a given client, materialise per-invocation traces and a run bundle, and emit the run-level events — never construct the catalog or the client itself.

## Scope

- **in-scope**:
  - Per-tier dataset loaders for `iot_light_5`, `home_iot_20`, and `smart_home_50`. All three load from the same `examples/iot_light/dataset.jsonl` because the smaller-tier intents are a subset of the larger catalogs; the tier label travels with the run for downstream slicing, not for filtering rows. `home_assistant_4` selects `examples/home_assistant/dataset.jsonl` (`default_dataset_for(tier)`).
  - Adversarial merge: when `adversarial=True`, concatenate `examples/iot_light/dataset.jsonl` with `examples/iot_light/adversarial_cases.jsonl` (M4 adversarial set) into the case stream. The merge is in-memory and deterministic (base first, then adversarial in file order).
  - Grading — `CaseResult.exact_match` / `action_match` / `valid` (`ganglion/analyzer/metrics.py`): `exact_match` is `ActionPlan` value equality over every repeat; `action_match` compares the action-name sequence only; `valid` is `plan is not None and error is None`. This is today's IoT-side scoring — the BFCL AST grader is a sibling and lives under [[benchmark_bfcl]].
  - Per-case runner `run_iot(client: ModelClient, dataset: Iterable[EvalCase], *, repeat: int = 1) -> list[CaseResult]` (`ganglion/benchmarks/iot/runner.py`): one `RunResult` per invocation, dataset order preserved. **Failure raw preservation:** `_invoke_once` catches every exception and records `RunResult(plan=None, raw=getattr(exc, "raw", None), latency_ms=None, input_tokens=None, output_tokens=None, error=f"{type(exc).__name__}: {exc}")` — it never raises. The `raw` attribute is what `ModelOutputError` / `RepairExhaustedError` carry ([[lm_client]], [[analyzer_repair_policy]]), so a validation failure keeps the model's text instead of `raw=None`.
  - Trace materialisation — `traces_from_results(results: list[CaseResult], *, catalog_id: str, run_id: str, model_id: str, source: str = "benchmark.iot", prompts_system: str = "") -> list[Trace]`: one `Trace` per `(case, repeat_index)` with `case_id=case.id`, `prompt=case.prompt`, `attempts=attempts_from_raw(run.raw)`, `raw_output=attempts[-1]["content"]` if any else `""`, `raw_plan=raw_plan_from_attempts(attempts)`, `plan=run.plan.to_jsonable()` or `None`, `expected_plan=case.expected.to_jsonable()`, `error_type=run.error`, `parse_strategy=run.raw["parse_strategy"]` when `raw` is a mapping carrying it else `"json_object"`, `latency_ms` and token totals (`0` when `None`), `timestamp` = now, `catalog_id`, `run_id`, `model_id`, `source`, `repeat_index`. It is the single IoT trace materialiser — `ganglion/factory.py` calls it too (`source="benchmark.iot"`, `error_type=run.error`; its private duplicates are removed).
  - CLI persistence (`ganglion/cli.py`, IoT branch): `--trace-store <dir>`, `--run-id <name>` (default `f"{model_id}-{YYYYmmdd-HHMMSS}"` with `@ → -`), `--iteration <int>`, `--parent-run <run_id>`. When `--trace-store` is given, after the run: every trace from `traces_from_results` is appended to `TraceStore(dir)`; then `write_run_bundle(base_dir, manifest, summary, report_md)` ([[analyzer_run_manifest]]) with `RunManifest(run_id, catalog_id=tier, catalog_fingerprint=catalog.fingerprint(), model_id=spec.model_id, benchmark="iot", metric_kind="exact_match", client_kind=spec.client ("rules" for rules), model_fingerprint=model_fingerprint(spec), dataset_path, dataset_sha256=dataset_sha256(dataset_path), n_cases=len(cases), limit, decoding={"repair", "repair_max_attempts", "thinking", "repeat", "grammar_mask": false}, iteration, parent_run_id, git_head=git_head(), python, started_at, finished_at)`, `summary=summarize(results)` plus `tier`, `llm` / `model_id`, `dsl_catalog_chars`, `openai_tools_chars` (as today), `report_md=render_markdown(summary)` ([[analyzer_metrics]]); the run dir path is printed to stderr. The JSON summary still goes to stdout; without `--trace-store` nothing else changes.
  - `--repeat N` for latency stats (each repeat is its own `RunResult` and its own trace, distinguished by `repeat_index`).
  - `--limit N` (slice the cases list before iteration); `--adversarial`; `--dataset <path>`.
  - `--repair` / `--repair-max-attempts`: the repair *policy* is supplied externally ([[analyzer_repair_policy]]); this task forwards a `RepairConfig` to the client factory boundary, and the repair attempt chain reaches the trace via `raw["attempts"]`.
  - Target paths: `ganglion/benchmarks/iot/{dataset,runner}.py`, the IoT branch of `ganglion/cli.py`.
- **out-of-scope**:
  - Client construction (`QwenJSONDSLClient`, `QwenNativeToolClient`, `RuleBasedJSONDSLClient`) — [[lm_client]]; resolving a `model_id` to a client — [[lm_model_registry]]. The runner accepts a ready-constructed `ModelClient` and never imports a concrete client class.
  - Catalog construction — [[contract_catalog]] and `ganglion/contract/builtins/`; the runner consumes a `Catalog` by reference.
  - `Trace` schema, `attempts_from_raw` / `raw_plan_from_attempts`, store locking and shard layout — [[analyzer_trace_store]]; `RunManifest` schema and `write_run_bundle` — [[analyzer_run_manifest]]; ledger row format — [[analyzer_event_ledger]].
  - Summarisation semantics (`syntax_valid_rate`, `exact_match_rate`, percentiles, repair totals) and the markdown renderer — [[analyzer_metrics]]; this task calls them.
  - Classification, rule synthesis, correction attribution on the finished run — `analyze_run`, triggered by the operator ([[console_operator]]) or the loop ([[factory_pipeline]]), never by the runner.
  - Reconstructing raw output for a client that raised without `.raw` — the fix belongs in [[lm_client]]; here `raw=None` is recorded honestly.
  - BFCL benchmark — [[benchmark_bfcl]]. Dataset regeneration — `examples/iot_light/generate_dataset.py` and `examples/iot_light/adversarial_cases.py` remain the SSOT. Tier dataset divergence — a future tier with its own dataset is a separate task. Reward shaping (`graded_score`) — the analyzer's domain.
- **on violation**: if the grader needs catalog-specific knowledge beyond what `ToolSpec` exposes (cross-tool argument relations, side-effect ordering), do not branch inside the grader — propose a Catalog extension via [[contract_catalog]]. If a client's failure arrives without `.raw`, do not synthesise one here — record `raw=None`, let `trace_raw_preserved_rate` drop, and fix the client.

## Procedure

```
on cli invocation (--tier, --model | --llm, [--trace-store …]) or benchmark.iot.request:
    catalog ← get_catalog(tier)
    spec    ← load_registry(--models).get(model_id); client ← build_client_from_spec(spec, catalog, repair=RepairConfig(…))
    cases   ← load_dataset(dataset_path, limit=limit, catalog=catalog)      # + adversarial merge when --adversarial
    started_at ← now
    results ← run_iot(client, cases, repeat=repeat):
        for case in cases:
            for r in range(max(1, repeat)):
                try:    res ← client.invoke(case.prompt)
                        RunResult(plan=res.plan, raw=res.raw, latency_ms=res.latency_ms, input_tokens, output_tokens)
                except Exception as exc:                                   # never aborts the batch
                        RunResult(plan=None, raw=getattr(exc, "raw", None), latency_ms=None, tokens=None,
                                  error=f"{type(exc).__name__}: {exc}")
    summary ← summarize(results) + tier / model / dsl_catalog_chars / openai_tools_chars     # [[analyzer_metrics]]
    print(json.dumps(summary)) → stdout

    if --trace-store:
        traces ← traces_from_results(results, catalog_id=tier, run_id=run_id, model_id=spec.model_id)
        store  ← TraceStore(dir); for t in traces: store.append(t)          # idempotent on trace_id
        manifest ← RunManifest(… fields above …, started_at, finished_at=now)
        run_dir ← write_run_bundle(dir, manifest, summary, render_markdown(summary))
        emit analyzer.run.recorded(catalog_id=tier, run_id, manifest_path, n_traces=len(traces))
        emit analyzer.metrics.summarized(catalog_id=tier, run_id, summary_path, n_traces)
        print(run_dir) → stderr

on dataset file missing:   FileNotFoundError before any invoke; no traces, no bundle.
on unknown tier:           ValueError from get_catalog; nothing written.
on every invoke failing:   the run still completes — summary has syntax_valid_rate == 0 and the bundle is still written (the failure is the data).
on TraceStore OSError:     propagate (fail loud) — the stdout summary has already been printed.
```

The runner never inspects `Catalog` internals; DSL parsing / native-tool conversion happens inside the supplied `ModelClient`, and the exception attributes are read with `getattr`, so the runner imports no client or error class.

## Contract

- **in**:
  - `catalog: Catalog` — from `ganglion/contract/builtins/` ([[contract_catalog]]).
  - `client: ModelClient` — from `build_client_from_spec` ([[lm_model_registry]] / [[lm_client]]).
  - `tier: Literal["iot_light_5", "home_iot_20", "smart_home_50", "home_assistant_4"]`.
  - `dataset_path: Path` — defaults to `default_dataset_for(tier)`.
  - Flags: `adversarial: bool = False`, `limit: int | None = None`, `repeat: int = 1`, `repair: RepairConfig`; persistence: `trace_store: Path | None`, `run_id: str`, `iteration: int | None`, `parent_run: str | None`.
- **out**:
  - `list[CaseResult]` from `run_iot` (dataset order; `repeat=N` → `N` runs per case), always — even when every invocation failed.
  - JSON summary on stdout.
  - With `--trace-store`: `<dir>/<tier>/<run_id>/traces.jsonl` (`N × len(cases)` lines, one per `(case, repeat_index)`, failed invocations included with `plan: null`, `error_type`, `attempts`, `raw_plan`), `manifest.json`, `summary.json`, `report.md`, `events.jsonl` (two rows); the run dir path on stderr.
- **event**:
  - consume: `contract.catalog.published(catalog_id, tier)` (gates the runner — runs only against a published catalog).
  - emit, as ledger rows in `<run_dir>/events.jsonl` when `--trace-store` is given ([[analyzer_event_ledger]]): `analyzer.run.recorded(catalog_id, run_id, manifest_path, n_traces)` and `analyzer.metrics.summarized(catalog_id, run_id, summary_path, n_traces)`. Per-invocation `lm.inference.completed | failed` are folded into the `Trace` (`error_type`) for batch runs; `benchmark.iot.completed / failed` remain in-process signals for [[factory_pipeline]] and are not ledger rows (they are not in `EVENT_NAMES`).
- **failure**:
  - Dataset file missing → `FileNotFoundError`, exit non-zero, no traces.
  - Tier not in the four accepted values → `ValueError`, nothing written.
  - Per-case `client.invoke` raises → `RunResult(error=…, raw=<exception .raw or None>)`, run continues; the case counts as `syntax_valid=False`; its trace has `plan=None` and, when `.raw` was present, `raw_plan` / `attempts` — which is what lets [[analyzer_failure_taxonomy]] file it as `missing_required_arg` rather than `syntax_invalid`.
  - Every invocation fails (auth missing, network down) → run completes with `syntax_valid_rate == 0`; bundle still written.
  - `TraceStore.append` / bundle write `OSError` → propagate; `write_manifest` is atomic (tmp + rename) so a run dir is never half-written.
- **success**: `pytest tests/test_eval_runner.py tests/test_substrate_failure_path.py tests/test_cli_trace_store.py` passes, asserting at least: (1) `run_iot` with `rules` over 5 cases → 5 `CaseResult`s, each `runs[0].plan` an `ActionPlan`; (2) a scripted client whose output is `{"calls":[{"action":"set_light","args":{"room":"living"}}]}` (no `state`, no default rescue) → `RunResult.error` set and `RunResult.raw` equal to the exception's `.raw`; `traces_from_results` → `plan is None`, `raw_plan` a dict, `attempts` non-empty; `classify` → `missing_required_arg`; (3) `python -m ganglion.cli --model rules --tier iot_light_5 --limit 5 --trace-store <tmp> --run-id t1` → `<tmp>/iot_light_5/t1/{traces.jsonl (5 lines), manifest.json (benchmark == "iot", n_cases == 5, catalog_fingerprint =~ ^cf-), summary.json, report.md}` and stderr names that dir; re-running the same command adds 0 lines to `traces.jsonl`.

## Observation

- `benchmark_case_count{tier}` = traces in `<run_dir>/traces.jsonl` with `repeat_index == 0`.
- `benchmark_pass_rate{tier}` = `summary.exact_match_rate` — read straight off `summary.json`, so M2 scaling can be read from the analyzer.
- `benchmark_action_pass_rate{tier}` = `summary.action_match_rate` — when it diverges from `benchmark_pass_rate`, the failure mode is args-only.
- `benchmark_wall_minutes` = `(manifest.finished_at − manifest.started_at) / 60`.
- `benchmark_repair_attempt_rate` = Σ `repair_attempts > 0` ÷ cases; only meaningful when `repair.enabled`.
- `trace_raw_preserved_rate{model_id}` = failed runs with `raw is not None` ÷ failed runs — target 1.0; below it a client raises without `.raw` ([[lm_client]]'s `raw_preserved_rate` names the client).
- `bundle_write_count` = runs with all of `manifest.json`, `summary.json`, `report.md` present ÷ runs with `traces.jsonl` — must be 1.0 for `--trace-store` runs.

## Status

Status: spec, implementation in progress (2026-09-16). Live: `ganglion/benchmarks/iot/{dataset,runner}.py`, the IoT branch of `ganglion/cli.py`. This revision adds failure raw preservation in `_invoke_once`, `traces_from_results`, and the `--trace-store` / `--run-id` / `--iteration` / `--parent-run` bundle path; the pre-redesign `ganglion/eval/` path is gone.
