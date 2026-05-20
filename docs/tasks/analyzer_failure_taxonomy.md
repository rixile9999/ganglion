[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_failure_taxonomy

Deterministic, rule-based classifier that buckets every recorded trace into exactly one `FailureType`. The output is the substrate that [[analyzer_rule_synthesis]] (ToolSpec patch proposal from frequencies) and [[analyzer_metrics]] (by-type breakdowns over [[contract_catalog]] runs) build on top of. This primitive does **not** classify with an LLM judge — only regex / structural matchers over already-recorded fields from [[analyzer_trace_store]].

## Role

Map one `Trace` → one `(FailureType, confidence)` pair via priority-ordered deterministic rules. Persist the classification as a sidecar JSONL (never as a mutation of the trace store). Emit one `analyzer.failure.classified` event per classified trace so downstreams can aggregate without re-reading the JSONL.

## Scope

- **in-scope**:
  - `FailureType` enum (one classification per trace; priority-ordered when multiple causes apply):
    - `syntax_invalid` — JSON parse failed (`parse_strategy="failed"` in the trace).
    - `unknown_tool` — predicted action name not present in the catalog's tool set.
    - `wrong_action` — predicted call's action != ground-truth tool name (only when ground truth available).
    - `missing_required_arg` — a required arg from [[contract_catalog]] is absent in the predicted call.
    - `unknown_arg` — predicted call carries an arg name not declared on the tool.
    - `type_mismatch` — predicted arg value's runtime type incompatible with declared `ArgSpec` (e.g. string where `IntArg` expected).
    - `value_out_of_enum` — `EnumArg` value not in allowed set and no alias matched.
    - `value_out_of_range` — `IntArg` / `NumberArg` / `TimeArg` value falls outside `[min_value, max_value]`.
    - `alias_unrecognised` — string value "looks like" a known alias (e.g. `"living room"` when alias key is `"living"`) but is not mapped; this is the synthesis hook for [[analyzer_rule_synthesis]].
    - `abstention_miss_should_call` — predicted empty calls when ground truth expects ≥1 call (callable case wrongly abstained — relates to [[contract_null_action]]).
    - `abstention_miss_should_abstain` — predicted ≥1 call when ground truth expects empty (BFCL `irrelevance`).
    - `parallel_order_mismatch` — BFCL parallel categories: right set of calls, wrong matching after permutation check.
    - `partial_arg_value_mismatch` — arg name set matches ground truth but values are only partially correct (Jaccard over `(name, value)` pairs < 1).
    - `no_failure` — exact-match trace; included so the classifier is total over the trace stream.
  - `classify(trace) -> (FailureType, confidence ∈ [0, 1])`. Confidence is rule-source-derived: `1.0` for deterministic regex / structural matches (e.g. catalog membership lookup); `<1.0` when a heuristic matcher fires (notably `alias_unrecognised`, which uses fuzzy comparison and is the only sub-1.0 path in v1).
  - Priority order (first match wins): `syntax_invalid` > `unknown_tool` > `wrong_action` > `abstention_miss_should_call` > `abstention_miss_should_abstain` > `missing_required_arg` > `unknown_arg` > `type_mismatch` > `value_out_of_enum` > `alias_unrecognised` > `value_out_of_range` > `parallel_order_mismatch` > `partial_arg_value_mismatch` > `no_failure`.
  - Persistence: classified output is a **sidecar** JSONL at `runs/traces/<catalog_id>/<run_id>/classified.jsonl`. Each row: `{trace_id, failure_type, confidence, evidence}`. The trace store is never mutated (append-only contract from [[analyzer_trace_store]]).
  - Re-classification: when matchers change, re-running `classify` over an existing trace store writes `classified-v<N>.jsonl` alongside the previous file; older files are kept for diffing.
  - Target module path: `ganglion/analyzer/taxonomy.py`.
  - Surface today's informal classifications: regex matchers consume `DSLValidationError` message strings from `ganglion/dsl/tool_spec.py` and field paths from `ganglion/dsl/catalog.py:validate_call`; the existing post-hoc analyser `runs/factory_bfcl/analyze_failures.py` is the reference implementation that this task formalises. BFCL-side bucketed types from `ganglion/bfcl/grader.py:GraderResult.error_type` (`func_match`, `wrong_count`, `unexpected_param`, …) map into the enum above.

- **out-of-scope**:
  - LLM-judge classification — deterministic rules only in this primitive; a future task may add a separate judge primitive that emits its own event.
  - Training a classifier (logistic / tree / NN over trace features) — out of scope; classification stays deterministic.
  - Cross-case root-cause analysis ("most failures share the same arg name") — that aggregation belongs to [[analyzer_rule_synthesis]].
  - Retroactive mutation of `traces.jsonl` — forbidden by [[analyzer_trace_store]]'s append-only contract.
  - Coverage gap analysis ("we have no failures in category X because we never tested X") — separate concern from per-trace classification.
  - Confidence calibration / probability estimation — current confidence is rule-source-derived, not learned; no Brier-score work here.
  - Repair-policy decisions ("which failures are auto-fixable") — see [[analyzer_repair_policy]].

- **on violation**: if a trace fits multiple priority-equal types (which should be impossible under the strict priority order above; only triggered by a bug in the priority table), pick the higher-priority one and emit a `WARNING`-level log line including the trace id and the matched type set. Do **not** silently mis-classify, and do not crash the stream — downstream metrics still need the count.

## Procedure

```
for each trace in stream("analyzer.trace.recorded"):
    if trace missing required fields (no trace_id, no predicted, no catalog_id):
        log WARNING; skip; continue

    catalog = resolve_catalog(trace.catalog_id)   # cached per stream
    if catalog is None:
        log WARNING("catalog %s unresolved", trace.catalog_id); skip; continue

    for matcher in MATCHERS_IN_PRIORITY_ORDER:
        try:
            hit = matcher(trace, catalog)
        except Exception as err:
            log ERROR("matcher %s raised %s", matcher.name, err)
            classify as ("no_failure", 0.0)   # fail loud, low confidence
            break
        if hit:
            ftype, confidence, evidence = hit
            break
    else:
        ftype, confidence, evidence = ("no_failure", 1.0, {})

    write_jsonl(
        "runs/traces/{catalog_id}/{run_id}/classified.jsonl",
        {trace_id, failure_type: ftype, confidence, evidence},
    )
    emit("analyzer.failure.classified",
         trace_id=trace.id, failure_type=ftype, confidence=confidence)
```

Matchers are pure functions over `(Trace, Catalog)`; the catalog is resolved by `catalog_id` once per stream and cached. The `MATCHERS_IN_PRIORITY_ORDER` tuple is the SSOT for priority — changing order requires a doc + test update in the same PR.

### Evidence schema

Each row's `evidence` dict is matcher-specific but always JSON-serialisable and field-named after the matched cause, so downstream synthesis can group by structural keys:

- `syntax_invalid` → `{raw: str, parse_error: str}`.
- `unknown_tool` / `wrong_action` → `{predicted_action: str, expected_action: str | null, known_actions: list[str]}`.
- `missing_required_arg` / `unknown_arg` → `{action: str, arg_name: str, declared_args: list[str]}`.
- `type_mismatch` → `{action: str, arg_name: str, expected_type: str, actual_type: str, value_repr: str}`.
- `value_out_of_enum` → `{action: str, arg_name: str, value: str, allowed: list[str]}`.
- `value_out_of_range` → `{action: str, arg_name: str, value: number, min: number | null, max: number | null}`.
- `alias_unrecognised` → `{action: str, arg_name: str, value: str, nearest_alias: str, edit_distance: int}` (fuzzy; confidence < 1).
- `abstention_miss_*` → `{predicted_count: int, expected_count: int}`.
- `parallel_order_mismatch` / `partial_arg_value_mismatch` → `{predicted: [...], expected: [...]}` plus a diff summary.
- `no_failure` → `{}`.

### Matcher ordering rationale

The priority list goes from "shape-level" (could not even parse JSON) → "address-level" (tool name resolution) → "argument-level" (presence, then type, then value) → "match-level" (multi-call permutation / partial value). This monotonic narrowing means once a coarser cause is found we stop, so a trace that is both `unknown_tool` and (hypothetically) `value_out_of_enum` reports the coarser one — which is what synthesis needs to know first.

## Contract

- **in**: `Trace` records produced by [[analyzer_trace_store]] (one per evaluated case), plus the `Catalog` for each `catalog_id` referenced. Triggered by the `analyzer.trace.recorded` event.
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/classified.jsonl` — one row per trace, schema `{trace_id: str, failure_type: FailureType, confidence: float, evidence: dict}`.
  - On re-classification with changed matchers: a parallel `classified-v<N>.jsonl` is written; prior versions are kept.
  - One `analyzer.failure.classified(trace_id, failure_type, confidence)` event per classified trace.
- **event**: consumes `analyzer.trace.recorded`; emits `analyzer.failure.classified`.
- **failure**:
  - Matcher raises an exception → log + classify trace as `("no_failure", 0.0)` (fail loud, low confidence surfaces the bug to dashboards built on `classification_confidence_mean`).
  - Trace missing required fields (`trace_id`, `predicted`, `catalog_id`) → log + skip; no row, no event.
  - Catalog for `catalog_id` cannot be resolved → log + skip; no row, no event. Operator must republish the catalog from [[contract_catalog]] before re-classification.
- **success**:
  - On a hand-labelled fixture set of ~30 traces (one per `FailureType`, plus a few cross-type edge cases), `classify()` achieves 100% agreement with the labels.
  - Re-running classify over an unchanged trace store + unchanged matcher set produces a `classified.jsonl` byte-identical to the previous one (modulo ordering already fixed by the stream).
  - `classification_unknown_rate` stays below 5% on any production run; higher values surface taxonomy gaps to the operator.

## Observation

- `failure_type_distribution[catalog_id]` — histogram of `FailureType` over the latest run, per catalog id. Fuel for [[analyzer_metrics]].
- `classification_confidence_mean` — arithmetic mean of `confidence` across the run. Sustained drops are a signal that the heuristic matchers (notably `alias_unrecognised`) are firing more often, which is a [[analyzer_rule_synthesis]] candidate.
- `classification_unknown_rate` — fraction of `no_failure` classifications among traces whose `exact_match=False`. High values mean the taxonomy is incomplete — operator should extend the enum.
- `priority_collision_count` — count of `WARNING`-logged collisions; non-zero means the priority table has a bug.

## Wikilinks

[[analyzer_trace_store]] · [[analyzer_metrics]] · [[analyzer_rule_synthesis]] · [[analyzer_repair_policy]] · [[contract_catalog]] · [[contract_null_action]]
