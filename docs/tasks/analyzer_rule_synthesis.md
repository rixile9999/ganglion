[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_rule_synthesis

Promote the hand-coded R1–R11 post-correction rules in `runs/factory_bfcl/post_correction.py` to a first-class synthesis loop that consumes bucketed failure classifications from [[analyzer_failure_taxonomy]] and emits **proposed** `ToolSpec` rule patches. This is the literal "module 2 (analysis) outputs data that module 3 (contract) consumes to refine itself" feedback edge from `docs/goal/goal.md` §2 — the compiler / error-correction surface.

The boundary is load-bearing: synthesis **proposes**; humans decide ([[analyzer_patch_decision]]) and port. `ToolSpec` stays a human authoring surface. The analyzer surfaces candidates with attached evidence; it never mutates a Catalog. The companion *contract* edge — retiring hooks that no longer rescue — is [[analyzer_correction_attribution]], which issues `retire_rule` patches into the same file.

Status: implemented at `ganglion/analyzer/rules.py` (tests `tests/test_analyzer_rules.py`); 2026-09-16 console-batch revisions (`golds=`, `_pred_calls` / `_gold_calls`, `retire_rule` operation, public `make_patch_id`, payload SSOT below) in progress. The earlier "spec only" status is stale.

## Role

Mine bucketed failure traces for repeated, narrow patterns and emit machine-readable `ToolSpec` patch proposals (`add_alias`, `set_default`, `enable_strip_unknown_args`, `add_prompt_correction`, `extend_argspec`, `ESCALATE`; plus `retire_rule` accepted from the attribution edge) — one ledger row per proposal, never auto-applied.

## Scope

- **in-scope**:
  - Target module: `ganglion/analyzer/rules.py`.
  - `synthesize_rules(classifications, traces, catalog, config=RuleSynthConfig(), *, golds: Mapping[str, ActionPlan] | None = None) -> list[RulePatch]`, sorted by confidence descending. `golds` is `gold_map` from [[analyzer_label_store]] (human label wins over `expected_plan`).
  - Trace access helpers, so that traces which **failed validation** still feed the matchers:
    - `_pred_calls(tr) -> list[dict]` = calls of `tr.plan` if not `None` else of `tr.raw_plan` (mirror of the taxonomy's `_calls_for_matching`); used by `_match_missing_required_arg`, `_match_unknown_arg`, `_match_type_mismatch` in place of `tr.plan`. In `_match_missing_required_arg` the gold block sits *outside* the predicted-calls guard — a trace with no predicted calls still contributes its gold value. No matcher skips a trace because `tr.plan is None`: that guard was exactly what hid validation-failure traces — the ones `set_default` exists for — from synthesis.
    - `_gold_calls(tr, golds) -> list[dict]` = the `golds[tr.case_id]` plan if present else `tr.expected_plan`; used by the four functions that read gold: `_match_missing_required_arg`, `_functional_alias_map` (takes a `golds` parameter; shared by `_match_value_out_of_enum` and `_match_alias_unrecognised`), `_match_abstention_miss_should_call`, `_match_type_mismatch`.
  - `RuleSynthConfig(min_failure_count=5, min_consistency=0.8, saturation_count=20, min_confidence_apply=0.7)`.
  - Pattern matchers, registered in `_MATCHERS` (one per bucket):
    - `missing_required_arg` — the SAME default closes the gap in ≥ N traces → `set_default`.
    - `unknown_arg` — the SAME arg name in ≥ N traces: safe-to-drop share > 0.8 → `enable_strip_unknown_args`; else consistent value shape → `extend_argspec` (shape a, a `ToolSpec` shape change flagged for review).
    - `value_out_of_enum` / `alias_unrecognised` — functional `(observed → accepted)` map in ≥ K = 3 traces → `add_alias` (enum / string).
    - `abstention_miss_should_call` — cluster of empty-call traces whose gold names a tool → `add_prompt_correction`.
    - `type_mismatch` — consistent recovering transform → `extend_argspec` (shape b); a transform needing a new `ArgSpec` variant → `ESCALATE`.
  - **Patch record SSOT** (`RulePatch.to_dict()` keys: `patch_id`, `catalog_id`, `target_tool`, `operation`, `payload`, `evidence`, `source_failure_type`, `created_at`):
    - `patch_id = make_patch_id(catalog_id, operation, target_tool, payload)` = `"rs-<catalog_id>-" + sha256(json{operation, target_tool, payload} sort_keys)[:12]` — public alias of `_make_patch_id`, also used by [[analyzer_correction_attribution]]. `catalog_id` is **`catalog.name`**, so a compiled catalog must be built with `name=catalog_id` (`compile_tool_calling_schema(tools, name="compiled/<sha12>", ...)`, see [[contract_schema_compiler]]) or its patches carry a foreign id.
    - `evidence = {failure_count: int, support_share: float (4 dp), example_trace_ids: list[str] ≤ 5, confidence: float (4 dp), evidence_quality: "low" (present only when confidence < 0.4)}`; `confidence = frequency × consistency × narrowness` with `frequency = min(1, failure_count / saturation_count)`.
    - payloads by operation:
      - `set_default` (`missing_required_arg`): `{"arg", "default", "predicate_hint": {"requires_args": [sorted co-occurring arg names]}}` — the predicate the port should gate on (`lambda args: all(k in args for k in requires_args)`; empty list = always).
      - `enable_strip_unknown_args` (`unknown_arg`): `{"strip_unknown_args": true}` (no `arg`).
      - `extend_argspec` shape a (`unknown_arg`): `{"arg", "observed_types": {py_type: count}, "spec_hint": "RawArg"}` = add a new arg; shape b (`type_mismatch`): `{"arg", "spec_hint": "IntArg" | "IntArg(allow_percent=True)" | "RawArg", "transform": "int_from_string" | "percent" | "strip_unit"}`. Only `transform == "percent"` is mechanically previewable ([[contract_patch_apply]]); the rest are review hints.
      - `add_alias` (`value_out_of_enum` | `alias_unrecognised`): `{"arg", "aliases": {observed.strip().lower(): accepted}, "kind": "enum" | "string"}`.
      - `add_prompt_correction` (`abstention_miss_should_call`): `{"system_nudge", "trigger_tool"}`; `target_tool` = first gold action.
      - `ESCALATE` (`type_mismatch`): `{"arg", "blocked_reason"}`, `evidence.confidence` fixed at 0.5. Escalations are rows in the same file with `operation="ESCALATE"` — there is no separate `analyzer.rule.proposed_escalated` event (not in the ledger vocabulary).
      - `retire_rule` (`no_failure`, produced by [[analyzer_correction_attribution]], accepted by `RulePatch.from_dict`): `{"hook_kind": "defaults_when_missing" | "strip_unknown_args" | "prompt_correction", "arg": str | null}`.
  - `write_proposed_patches_sidecar(patches, path)` and `write_synthesis_summary(patches, path) -> dict` (`{total_patches, by_failure_type, confidence_histogram{low, mid, high}}`) — unchanged.
  - Ledger: one `analyzer.rule.proposed` row per patch with payload `{patch_id, operation, target_tool, source_failure_type, confidence}` and `refs.proposed_patches` (the `created_at` field is excluded so re-synthesis on unchanged evidence dedupes), correlation `{catalog_id, run_id}`, through [[analyzer_event_ledger]].
  - **Dry-run only.** Synthesis writes `proposed_patches.jsonl` and ledger rows. It does NOT mutate any `ToolSpec` / `Catalog` under `ganglion/contract/builtins/` or any compiled catalog.
- **out-of-scope**:
  - Applying patches — `apply_patch` in [[contract_patch_apply]] is a pure preview; the decision is [[analyzer_patch_decision]]'s; the port is a reviewed edit. **This is the most important non-goal.**
  - Repair-loop policy ([[analyzer_repair_policy]]); failure → SFT augmentation ([[lm_data_synth]], [[analyzer_label_store]] export); trace storage ([[analyzer_trace_store]]); classification ([[analyzer_failure_taxonomy]]).
  - Matchers for the seven buckets without one (`unknown_tool`, `wrong_action`, `value_out_of_range`, `abstention_miss_should_abstain`, `parallel_order_mismatch`, `partial_arg_value_mismatch`, `syntax_invalid`) — these are "no contract patch → label and retrain" cards, not synthesis targets.
  - Retire-candidate detection — [[analyzer_correction_attribution]]; this doc only accepts the `retire_rule` operation.
  - LLM-judge rule discovery; cross-catalog transfer; cross-run aggregation ([[analyzer_metrics]], [[analyzer_compare]]).
- **on violation**: if a proposed patch would change the *shape* of `ToolSpec` (new `ArgSpec` variant, new field, an operation the validator does not implement) — emit it as `operation="ESCALATE"` with `blocked_reason`, never as an applicable operation. If a caller passes traces whose `plan` holds unvalidated dicts, `_pred_calls` still prefers `plan` — the producer bug is caught by [[analyzer_trace_store]]'s tests, not patched here.

## Procedure

```
trigger: analyze_run (see analyzer_analyze) for (catalog_id, run_id), or explicit invocation

steps:
    1. trace_lookup ← {trace_id: trace}; group classifications by failure_type
    2. for (failure_type, matcher) in _MATCHERS:     # missing_required_arg, unknown_arg, value_out_of_enum,
                                                      # alias_unrecognised, abstention_miss_should_call, type_mismatch
         patches += matcher(group, trace_lookup, catalog, config, golds)
         on matcher exception: log; continue (fail loud per group, not per run)
    3. sort by evidence.confidence desc
    4. caller appends retire_rule patches from analyzer_correction_attribution, then
       write_proposed_patches_sidecar(<run dir>/proposed_patches.jsonl)
       write_synthesis_summary(<run dir>/proposed_patches.summary.json)
       emit analyzer.rule.proposed per patch (payload without created_at)

on no failures in input:       empty proposed_patches.jsonl; no rows; exit 0 (normal terminal state)
on confidence < 0.4:           still emit, evidence_quality = "low"
```

## Contract

- **in**: classifications for one `(catalog_id, run_id)` from [[analyzer_failure_taxonomy]], the run's traces from [[analyzer_trace_store]], the `Catalog` (name == `catalog_id`), and `golds` from [[analyzer_label_store]].
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/proposed_patches.jsonl` — one record per patch (empty file is valid); includes `retire_rule` rows appended by the composite.
  - `runs/traces/<catalog_id>/<run_id>/proposed_patches.summary.json`.
- **event**: consume `analyzer.failure.classified`; emit `analyzer.rule.proposed(patch_id, operation, target_tool, source_failure_type, confidence)` — one row per patch (including `ESCALATE` and `retire_rule` rows).
- **failure**:
  - Empty input → empty file, no rows. Not an error.
  - Ambiguous evidence → patch with `evidence_quality: "low"`.
  - Matcher exception → logged; other groups continue.
  - `ToolSpec`-shape change implied → `ESCALATE` row.
- **success**:
  - `pytest tests/test_analyzer_rules.py` green, including existing fixtures that place dicts in `plan` (still preferred by `_pred_calls`).
  - `tests/test_substrate_failure_path.py`: a validation-failed trace (`plan is None`, `raw_plan` set, gold via `golds=`) classified `missing_required_arg` yields a `set_default` patch for `set_light.state` with `payload.default == "on"`.
  - `make_patch_id` is importable and equals `RulePatch.patch_id` for every emitted patch; `RulePatch.from_dict` round-trips a `retire_rule` record.
  - Zero patches when the fixture contains zero failures.

## Observation

- `patches_proposed_count[catalog_id, failure_type]` — per run, from `proposed_patches.summary.json`.
- `patch_acceptance_rate` — now computed by [[analyzer_patch_decision]]'s `precision_summary` (blind acceptances ÷ proposals); reported only with `n_proposed`.
- `patch_evidence_density` — mean `failure_count` per proposed patch.
- `escalation_rate` — `ESCALATE` rows ÷ matcher hits; persistently high means `ToolSpec` needs a new variant (Module 3 design surface).
- `low_confidence_share` — rows with `confidence < 0.4` ÷ rows.
- `foreign_catalog_id_count` — rows whose `catalog_id` ≠ the run's `catalog_id`; must be 0 (a compiled catalog built without `name=catalog_id`).

Sibling docs in the analyzer module: [[analyzer_trace_store]], [[analyzer_failure_taxonomy]], [[analyzer_repair_policy]], [[analyzer_metrics]], [[analyzer_label_store]], [[analyzer_correction_attribution]], [[analyzer_patch_decision]], [[analyzer_analyze]], [[analyzer_event_ledger]]. Downstream: [[contract_patch_apply]], [[contract_catalog]] (via reviewed edits), [[factory_pipeline]].
