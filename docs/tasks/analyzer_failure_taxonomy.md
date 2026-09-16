[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_failure_taxonomy

Deterministic, rule-based classifier that buckets every recorded trace into exactly one `FailureType`. The output is the substrate that [[analyzer_rule_synthesis]] (ToolSpec patch proposal from frequencies) and [[analyzer_metrics]] / [[analyzer_analyze]] (by-type histograms over [[contract_catalog]] runs) build on top of. This primitive does **not** classify with an LLM judge — only regex / structural matchers over already-recorded fields from [[analyzer_trace_store]].

Status: implemented at `ganglion/analyzer/taxonomy.py` (tests `tests/test_analyzer_taxonomy.py`); 2026-09-16 console-batch revisions (`_calls_for_matching`, abstention / syntax conditions, `unclassified` convention, one ledger row per pass) in progress.

## Role

Map one `Trace` → one `(FailureType, confidence)` pair via priority-ordered deterministic rules — matching on the **raw decoded plan when validation failed** — persist the classification as a sidecar JSONL (never as a mutation of the trace store), and record one `analyzer.failure.classified` ledger row per classification pass.

## Scope

- **in-scope**:
  - `FailureType` enum (one classification per trace; priority-ordered when multiple causes apply):
    - `syntax_invalid` — no decodable output: `raw_plan is None` **and** (`parse_strategy == "failed"` **or** (`plan is None and (error_type or raw_output)`)). A trace whose client attached raw that decodes to a mapping is never `syntax_invalid` — it falls through to the structural matchers on `raw_plan`. The `raw_plan is None` guard applies to *both* signals: [[lm_request_serve]] stamps `parse_strategy="failed"` on every errored `ServeResult`, so without it every chat validation failure was filed here at confidence 1.0 and no structural matcher ever ran.
    - `unknown_tool` — predicted action name not present in the catalog's tool set.
    - `wrong_action` — predicted call's action != ground-truth tool name (only when ground truth available).
    - `missing_required_arg` — a required arg from [[contract_catalog]] is absent in the predicted call.
    - `unknown_arg` — predicted call carries an arg name not declared on the tool.
    - `type_mismatch` — predicted arg value's runtime type incompatible with declared `ArgSpec`.
    - `value_out_of_enum` — `EnumArg` value not in allowed set and no alias matched.
    - `value_out_of_range` — `IntArg` / `NumberArg` / `TimeArg` value outside `[min_value, max_value]`.
    - `alias_unrecognised` — string value "looks like" a known alias but is not mapped; the synthesis hook for [[analyzer_rule_synthesis]].
    - `abstention_miss_should_call` — the model produced **no calls at all** — fires only when BOTH `plan` and `raw_plan` are `None` (or decode to zero calls) and gold has ≥ 1 call. A validation failure with a decodable `raw_plan` is *not* an abstention.
    - `abstention_miss_should_abstain` — predicted ≥ 1 call when gold expects empty (BFCL `irrelevance`; [[contract_null_action]]).
    - `parallel_order_mismatch` — right set of calls, wrong matching after permutation check.
    - `partial_arg_value_mismatch` — arg name set matches gold but values only partially correct.
    - `no_failure` — exact-match trace, `confidence = 1.0`.
  - **`unclassified` convention**: the fall-through `("no_failure", 0.0)` — gold present but nothing matched, or no gold and no catalog, or a matcher raised — is *not* a pass. Every consumer that counts (histograms in [[analyzer_analyze]], the console's trace `counts`, `classification_unknown_rate` below) reports `no_failure` with `confidence == 0.0` under the key `unclassified`.
  - `_calls_for_matching(trace) -> list[dict]` = calls of `trace.plan` if not `None`, else calls of `trace.raw_plan` (else `[]`). The six argument-level matchers (`missing_required_arg`, `unknown_arg`, `type_mismatch`, `value_out_of_enum`, `alias_unrecognised`, `value_out_of_range`) and `unknown_tool` / `wrong_action` read it, so a trace that failed validation is classified by *what the model actually said*. `plan` is preferred so existing fixtures stay valid.
  - `classify(trace, catalog=None, gold=None) -> Classification(trace_id, failure_type, confidence, evidence)`; `classify_traces(traces, catalog, golds: Mapping[case_id, ActionPlan] | None)` — `golds` comes from `gold_map` in [[analyzer_label_store]] (human label wins over `expected_plan`); `write_classified_sidecar(classifications, path)`.
  - Confidence: `1.0` for deterministic matches; `0.8` for the fuzzy `alias_unrecognised`; `0.0` only for the degenerate fall-through.
  - Priority order (first match wins; `MATCHERS_IN_PRIORITY_ORDER` is the SSOT): `syntax_invalid` > `unknown_tool` > `wrong_action` > `abstention_miss_should_call` > `abstention_miss_should_abstain` > `missing_required_arg` > `unknown_arg` > `type_mismatch` > `value_out_of_enum` > `alias_unrecognised` > `value_out_of_range` > `parallel_order_mismatch` > `partial_arg_value_mismatch` > `no_failure`.
  - Persistence: sidecar JSONL at `runs/traces/<catalog_id>/<run_id>/classified.jsonl`, rows `{trace_id, failure_type, confidence, evidence}`. Derived artifact: re-running overwrites it (traces + matcher version reproduce it byte-for-byte).
  - Ledger: one `analyzer.failure.classified` row per classification pass over a run — payload `{n_classified, histogram}` (with `unclassified`), `refs.classified` = the sidecar path, correlation `{catalog_id, run_id}` — through [[analyzer_event_ledger]]. Per-trace granularity is in the sidecar, not in events.
  - Inputs: `DSLValidationError` message strings from `ganglion/contract/tool_spec.py` / `ganglion/contract/catalog.py:validate_call`; BFCL grader buckets from `ganglion/benchmarks/bfcl/grader.py` (`func_match`, `wrong_count`, `unexpected_param`, …) map into the enum above.
  - Target module path: `ganglion/analyzer/taxonomy.py`.
- **out-of-scope**:
  - LLM-judge classification; training a classifier; confidence calibration.
  - Cross-case root-cause aggregation — [[analyzer_rule_synthesis]].
  - Retroactive mutation of `traces.jsonl` — forbidden by [[analyzer_trace_store]].
  - Choosing the gold — `resolve_gold` / `gold_map` in [[analyzer_label_store]]; this primitive receives a `golds` map.
  - Coverage-gap analysis; repair-policy decisions ([[analyzer_repair_policy]]).
  - Versioned re-classification files (`classified-v<N>.jsonl`) — deferred; the sidecar is derived and overwritten.
  - Deciding what the client's raw looks like — `raw_plan` is provided by [[analyzer_trace_store]] (`raw_plan_from_attempts`).
- **on violation**: if a trace fits multiple priority-equal types (impossible under the strict order; only a priority-table bug), pick the higher-priority one and log a `WARNING` with the trace id and matched set. Do not silently mis-classify, do not crash the stream. If a consumer is found counting `("no_failure", 0.0)` as a pass, that is a bug in the consumer — the fix is the `unclassified` convention above, not a new enum member.

## Procedure

```
for each trace in TraceStore.iter(catalog_id, run_id):     # triggered by analyzer.trace.recorded / analyze_run
    if trace missing required fields (trace_id, catalog_id): log WARNING; skip
    catalog ← resolve_catalog(trace.catalog_id)   # cached; None for bfcl/* (structural matchers skipped)
    gold ← golds.get(trace.case_id)
    calls ← _calls_for_matching(trace)            # plan if validated, else raw_plan
    # syntax_invalid iff raw_plan is None and (parse_strategy == "failed" or (plan is None and (error_type or raw_output)))

    for matcher in MATCHERS_IN_PRIORITY_ORDER:
        try:    hit = matcher(trace, catalog, gold)
        except Exception as err:
            log ERROR("matcher %s raised %s", matcher.name, err)
            classify as ("no_failure", 0.0, {"matcher_error": name}); break     # → unclassified
        if hit: break
    else: ("no_failure", 1.0, {})

write classified.jsonl (overwrite; sort_keys per row)
emit analyzer.failure.classified(n_classified, histogram)          # one row per pass
```

Matchers are pure functions over `(Trace, Catalog | None, ActionPlan | None)`.

### Evidence schema

- `syntax_invalid` → `{raw: str, parse_error: str}`.
- `unknown_tool` / `wrong_action` → `{predicted_action, expected_action | null, known_actions}`.
- `missing_required_arg` / `unknown_arg` → `{action, arg_name, declared_args}`.
- `type_mismatch` → `{action, arg_name, expected_type, actual_type, value_repr}`.
- `value_out_of_enum` → `{action, arg_name, value, allowed}`.
- `value_out_of_range` → `{action, arg_name, value, min | null, max | null}`.
- `alias_unrecognised` → `{action, arg_name, value, nearest_alias, edit_distance}` (confidence 0.8).
- `abstention_miss_*` → `{predicted_count, expected_count}`.
- `parallel_order_mismatch` / `partial_arg_value_mismatch` → `{predicted, expected}` plus a diff summary.
- `no_failure` → `{}`; the degenerate fall-through may carry `{"matcher_error": name}`.

### Matcher ordering rationale

Shape-level (nothing decodable) → address-level (tool name) → argument-level (presence, type, value) → match-level (permutation / partial value). Once a coarser cause is found we stop; a trace that is both `unknown_tool` and `value_out_of_enum` reports the coarser one, which is what synthesis needs first. Because argument-level matchers now read `raw_plan`, the `missing_required_arg` bucket is populated by exactly the traces `set_default` synthesis exists for — previously they were lost to `syntax_invalid` / abstention.

## Contract

- **in**: `Trace` records from [[analyzer_trace_store]] (with `plan`, `raw_plan`, `parse_strategy`, `error_type`, `raw_output`), the `Catalog` for the `catalog_id` (`None` for `bfcl/*`), and a `golds` map from [[analyzer_label_store]].
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/classified.jsonl` — one row per trace, `{trace_id, failure_type, confidence, evidence}`.
  - One `analyzer.failure.classified(n_classified, histogram)` ledger row per pass.
- **event**: consume `analyzer.trace.recorded`; emit `analyzer.failure.classified`.
- **failure**:
  - Matcher raises → that trace is `("no_failure", 0.0)` = `unclassified`; the pass continues.
  - Trace missing `trace_id` / `catalog_id` → log + skip; no row.
  - Catalog unresolvable (`bfcl/*`) → catalog-free matchers only (gold comparison, abstention); structural buckets unavailable.
- **success**:
  - `pytest tests/test_analyzer_taxonomy.py` stays green, including `test_priority_ordering_syntax_beats_unknown_tool` (`parse_strategy == "failed"` with a validated plan present but no `raw_plan` is still `syntax_invalid`) and `test_chat_path_failed_strategy_with_decodable_raw_is_classified_structurally` (the same strategy with a decodable `raw_plan` classifies structurally); fixtures that put unvalidated dicts in `plan` are moved to `raw_plan` and classify identically.
  - `tests/test_substrate_failure_path.py`: a trace with `plan is None`, `raw_plan = {"calls":[{"action":"set_light","args":{"room":"living"}}]}` classifies as `missing_required_arg` (not abstention, not syntax).
  - A trace with `plan is None`, `raw_plan is None`, `error_type` set classifies as `syntax_invalid`; one with `raw_plan = {"calls": []}` and gold calls classifies as `abstention_miss_should_call`.
  - Re-running over an unchanged store + matcher set produces a byte-identical `classified.jsonl`.
  - `classification_unknown_rate` stays below 5 % on any production run.

## Observation

- `failure_type_distribution[catalog_id, run_id]` — histogram over the 14 names plus `unclassified` (from [[analyzer_analyze]]'s `histogram`).
- `classification_confidence_mean` — mean `confidence` across the run; drops mean the fuzzy matcher fires more (a [[analyzer_rule_synthesis]] candidate) or matchers are raising.
- `classification_unknown_rate` — `unclassified ÷ traces with exact_match = False`; > 5 % means the taxonomy is incomplete.
- `raw_plan_matched_share` — traces classified from `raw_plan` (i.e. `plan is None` and bucket ≠ `syntax_invalid`) ÷ traces with `plan is None`; the measure of how much the raw-preservation fix recovered.
- `priority_collision_count` — `WARNING`-logged collisions; non-zero = priority-table bug.

## Wikilinks

[[analyzer_trace_store]] · [[analyzer_label_store]] · [[analyzer_metrics]] · [[analyzer_rule_synthesis]] · [[analyzer_repair_policy]] · [[analyzer_analyze]] · [[analyzer_event_ledger]] · [[contract_catalog]] · [[contract_null_action]]
