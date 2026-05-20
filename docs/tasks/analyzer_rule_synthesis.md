[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_rule_synthesis

Promote the hand-coded R1–R11 post-correction rules in `runs/factory_bfcl/post_correction.py` to a first-class synthesis loop that consumes bucketed failure classifications from [[analyzer_failure_taxonomy]] and emits **proposed** `ToolSpec` rule patches as events. This is the literal "module 2 (analysis) outputs data that module 3 (contract) consumes to refine itself" feedback edge from `docs/goal/goal.md` §2 — the compiler / error-correction surface.

The boundary is load-bearing: synthesis **proposes**; humans (or [[factory_pipeline]] with an explicit gating flag) **apply**. `ToolSpec` stays a human authoring surface. The analyzer surfaces high-confidence candidates with attached evidence; it never mutates a Catalog.

## Role

Mine bucketed failure traces for repeated, narrow patterns and emit machine-readable `ToolSpec` patch proposals (`defaults_when_missing`, `strip_unknown_args`, `prompt_correction`, `EnumArg.aliases`, `StringArg.aliases`, type-relaxation `ArgSpec` extensions) — one event per proposal, never auto-applied.

## Scope

- **in-scope**:
  - Target module: `ganglion/analyzer/rules.py`.
  - Consume `analyzer.failure.classified` payloads produced by [[analyzer_failure_taxonomy]] (read from [[analyzer_trace_store]]).
  - Pattern matchers, one per `FailureType` bucket:
    - `missing_required_arg` — when the SAME default value would close the gap in ≥ N traces (N defaults to 5), propose a `defaults_when_missing=(arg_name, value, predicate)` rule. Predicate is synthesised from the other-args co-occurrence pattern in the failing traces (e.g. `lambda a: "brightness" in a`).
    - `unknown_arg` — when the SAME arg name appears in ≥ M traces (M defaults to 5):
      - If `N_safe_to_drop / (N_safe_to_drop + N_carries_signal) > 0.8` → propose `strip_unknown_args=True` on the target tool.
      - Else if value shape is consistent (same JSON type, narrow value set) → propose extending `ArgSpec` with a new entry (typically a `RawArg` carrying the observed JSON schema fragment). The extension is flagged as a `ToolSpec` *shape* change → see `on violation`.
    - `value_out_of_enum` — when traces show a consistent `(observed_value → accepted_enum_value)` mapping in ≥ K traces (K defaults to 3) and the mapping is functional (no `observed_value` maps to two different accepted values), propose `EnumArg.aliases` extension `{observed: accepted, ...}`.
    - `alias_unrecognised` — analogous to `value_out_of_enum` but for `StringArg.aliases` (free-form string canonicalisation, e.g. `"거실" → "living"`).
    - `abstention_miss_should_call` — when a cluster of traces shows the model returned `{"calls":[]}` for prompts that DO match a tool (closest-tool similarity above the [[analyzer_failure_taxonomy]] threshold), propose a `prompt_correction` patch that injects a system-level nudge (e.g. "When the user request matches a known tool, call it; only abstain when no tool applies.").
    - `type_mismatch` — when a consistent transform recovers the right value (e.g. string `"42"` → int `42`, `"5%"` → `0.05`, `"175cm"` → `175`), propose the smallest `ArgSpec` relaxation that absorbs it (`IntArg(allow_percent=True)`, widened pattern on `StringArg`, etc.). When the transform requires a brand-new `ArgSpec` variant, escalate (see `on violation`).
  - Evidence object attached to every proposed patch:
    - `failure_count` — number of traces matched by the matcher.
    - `support_share` = `failure_count / total_failures_of_this_type_for_target`.
    - `example_trace_ids` — up to 5 trace IDs (from [[analyzer_trace_store]]) for human inspection.
    - `confidence` ∈ [0, 1] = `frequency × consistency × narrowness`, where:
      - `frequency` ∈ [0,1] = `min(1, failure_count / saturation_count)` (saturation defaults to 20).
      - `consistency` ∈ [0,1] = fraction of matched traces whose recovery transform is identical.
      - `narrowness` ∈ [0,1] = `1 − (other_tools_with_same_pattern / total_tools_in_catalog)`. A patch firing on 90 % of one specific arg's failures is high confidence; a patch firing on 30 % of `unknown_arg` failures spread across the catalog is low.
  - Patch shape (JSON Patch-style, one record per line in `proposed_patches.jsonl`):
    ```json
    {
      "patch_id": "rs-<catalog_id>-<short_hash>",
      "catalog_id": "<catalog_id>",
      "target_tool": "set_light",
      "operation": "add_alias | set_default | enable_strip_unknown_args | add_prompt_correction | extend_argspec",
      "payload": { ... operation-specific ... },
      "evidence": {
        "failure_count": 12,
        "support_share": 0.86,
        "example_trace_ids": ["t1", "t2", "t3"],
        "confidence": 0.92
      },
      "source_failure_type": "value_out_of_enum",
      "created_at": "<iso8601>"
    }
    ```
  - Emit one `analyzer.rule.proposed(catalog_id, rule_patch, evidence)` event per record; the event payload is the JSON record verbatim.
  - **Dry-run only.** Synthesis writes `proposed_patches.jsonl` and emits events. It does NOT mutate any `ToolSpec` / `Catalog` module under `ganglion/schema/` or any compiled catalog under `ganglion/dsl/compiler.py`. Application is owned by [[factory_pipeline]] (composite) gated by humans or a flag.

- **out-of-scope**:
  - Automatic application of proposed patches to a Catalog. Humans (or [[factory_pipeline]] with a gating flag) decide. **This is the most important non-goal** — the analyzer suggests, the [[contract_catalog]] author approves. Bypassing this boundary collapses the module 2 ↔ module 3 separation.
  - Repair-loop policy changes (max-attempts, repair prompt wording) — owned by [[analyzer_repair_policy]].
  - Training-data augmentation from failures (turning failing traces into adversarial SFT examples) — owned by [[lm_data_synth]] (a sibling feedback edge: failures fuel synth instead of patches).
  - Trace ingestion, storage, retention — owned by [[analyzer_trace_store]].
  - Error classification / taxonomy bucketing — owned by [[analyzer_failure_taxonomy]]. This task consumes its events, does not redefine them.
  - LLM-judge rule discovery — explicitly deferred. Only deterministic pattern matchers in this primitive; an LLM-judge variant is a separate task once we can A/B against the deterministic baseline.
  - Cross-catalog rule transfer (proposing a patch for `home_iot_20` based on `iot_light_5` evidence) — separate concern; each invocation is scoped to a single `(catalog_id, run_id)` window.
  - Metrics aggregation across runs — owned by [[analyzer_metrics]]. This task emits per-run observation fields only.
  - Provider-side or DashScope-side mitigations (e.g. switching to native tool-calling) — out of band; rule synthesis targets the DSL contract surface only.

- **on violation**: if a proposed patch would change the *shape* of `ToolSpec` itself — adding a new `ArgSpec` variant, a new field on `ToolSpec`, a new operation that the validator does not yet implement — **do not emit the patch**. Instead emit `analyzer.rule.proposed_escalated(catalog_id, blocked_reason, example_trace_ids)`. That class of change is a Module 3 design decision, not rule synthesis.

## Procedure

```
trigger:
    new batch of analyzer.failure.classified events for (catalog_id, run_id)
    OR explicit invocation: python -m ganglion.analyzer.rules <catalog_id> <run_id>

steps:
    1. Group classifications by (catalog_id, failure_type, target_tool).
    2. For each group, run the matcher registered for failure_type:
         missing_required_arg     → DefaultRuleMatcher
         unknown_arg              → StripOrExtendMatcher
         value_out_of_enum        → EnumAliasMatcher
         alias_unrecognised       → StringAliasMatcher
         abstention_miss_should_call → PromptCorrectionMatcher
         type_mismatch            → ArgSpecRelaxationMatcher
    3. For each matcher hit:
         a. Compute failure_count, support_share, example_trace_ids.
         b. Compute confidence = frequency × consistency × narrowness.
         c. If patch shape would require a new ToolSpec variant → emit
            analyzer.rule.proposed_escalated and skip (see on violation).
         d. Otherwise serialise the patch record and append to
            runs/traces/<catalog_id>/<run_id>/proposed_patches.jsonl.
         e. Emit analyzer.rule.proposed(catalog_id, rule_patch, evidence).
    4. After all groups processed, write
       runs/traces/<catalog_id>/<run_id>/proposed_patches.summary.json
       with per-failure_type counts and the global confidence histogram.

on no failures in input:
    write empty proposed_patches.jsonl (one-line touch); emit no events;
    exit 0. This is a normal terminal state, not an error.

on ambiguous evidence (confidence < 0.4):
    still emit the patch (downstream gating handles), tagged
    "evidence_quality": "low" in the record.

on matcher exception:
    catch, log to runs/traces/<catalog_id>/<run_id>/synthesis.errors.jsonl,
    continue with next group. Never abort the run on a single matcher
    failure — fail loud per group, not per run.
```

## Contract

- **in**: classification stream from [[analyzer_failure_taxonomy]] for one `(catalog_id, run_id)` window — either via consumed `analyzer.failure.classified` events or by reading [[analyzer_trace_store]] directly.
- **out**:
  - `runs/traces/<catalog_id>/<run_id>/proposed_patches.jsonl` — one JSON record per proposed patch (empty file is a valid output).
  - `runs/traces/<catalog_id>/<run_id>/proposed_patches.summary.json` — `{ "total_patches": int, "by_failure_type": {...}, "confidence_histogram": {...} }`.
  - `runs/traces/<catalog_id>/<run_id>/synthesis.errors.jsonl` (only if any matcher raised).
- **event**:
  - consume: `analyzer.failure.classified`.
  - emit: `analyzer.rule.proposed(catalog_id, rule_patch, evidence)` — one per accepted patch.
  - emit: `analyzer.rule.proposed_escalated(catalog_id, blocked_reason, example_trace_ids)` — when a `ToolSpec`-shape change is implied (see `on violation`).
- **failure**:
  - empty input (no failures in this window) → emit empty `proposed_patches.jsonl`, no events. Not an error.
  - ambiguous evidence → emit patch with `confidence < 0.4` and `evidence_quality: "low"`; downstream gating decides.
  - matcher exception → log to `synthesis.errors.jsonl`; continue.
  - `ToolSpec`-shape change required → emit `analyzer.rule.proposed_escalated`, do not emit a regular patch.
- **success**: against the fixture under `tests/fixtures/analyzer/rule_synthesis/` (~50 hand-labelled traces with known optimal patches), the synthesis proposes ≥ 80 % of the optimal patches with `confidence ≥ 0.7`, and emits zero patches against the catalog when the fixture contains zero failures. Verified by `pytest tests/test_analyzer_rule_synthesis.py` (red until impl lands; doc-only PR is allowed to skip).

## Observation

- `patches_proposed_count[catalog_id, failure_type]` — counter, per-run.
- `patch_acceptance_rate` — of patches with `confidence > 0.7`, fraction that land in the next Catalog version via [[factory_pipeline]] / human review (joined from [[contract_catalog]] version diffs).
- `patch_evidence_density` — mean `failure_count` per proposed patch, per run. Low values signal noisy proposals.
- `escalation_rate` — `proposed_escalated` events ÷ total matcher hits. A persistently high rate is a signal that `ToolSpec` needs a new variant (Module 3 design surface).
- `low_confidence_share` — fraction of emitted patches with `confidence < 0.4`. Should trend down as the catalog stabilises.

## Status

Spec only. Implementation deferred to a follow-up PR. The hand-coded baseline lives in `runs/factory_bfcl/post_correction.py` (R1–R11) and `runs/factory_bfcl/apply_post_corr_to_phase3.py`; those are the reference behaviours the future `ganglion/analyzer/rules.py` should subsume. Until the implementation lands, [[factory_pipeline]] continues to call the static R1–R11 pipeline directly.

Sibling docs in the analyzer module: [[analyzer_trace_store]], [[analyzer_failure_taxonomy]], [[analyzer_repair_policy]], [[analyzer_metrics]]. Downstream consumer: [[contract_catalog]] (via [[factory_pipeline]]).
