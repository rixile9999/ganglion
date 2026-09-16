[← New tasks](./README.md) · Composite principle: [workflow_principle](../agent-forge/workflow_principle.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_analyze (composite)

**This is a composite task doc.** It names the one-run analysis pass — classify, synthesise, attribute — that the console's `POST /api/runs/{cat}/{run}/analyze`, the `analyze` / `seed` subcommands and [[factory_pipeline]]'s per-iteration step all need. No primitive owns "run the three analyzer primitives over one run and leave every sidecar in place": [[analyzer_failure_taxonomy]] classifies, [[analyzer_rule_synthesis]] proposes, [[analyzer_correction_attribution]] attributes — each writes its own file and emits its own event. The outer contract here ("after one call, `classified.jsonl`, `proposed_patches.jsonl` (+ `.summary.json`), `corrections.jsonl` (+ `.summary.json`) exist and agree with each other") is not reducible to any of them, and the wiring would otherwise be re-scripted at every call site.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/analyze.py`, tests `tests/test_analyzer_analyze.py`.

## Role

Run classification, rule synthesis (including retire candidates) and correction attribution over one `(catalog_id, run_id)` with one gold map, write the three sidecar pairs, and return a machine-readable digest.

## Scope

- **in-scope**:
  - `analyze_run(base_dir, catalog_id, run_id, *, store=None, label_store=None, config=RuleSynthConfig()) -> dict`:
    1. `traces ← TraceStore.iter(catalog_id, run_id)`; `catalog ← resolve_catalog(catalog_id, base_dir=base_dir)`; `golds ← gold_map(traces, LabelStore.latest_by_trace(...))`.
    2. `classify_traces(traces, catalog, golds=golds)` → `write_classified_sidecar(... classified.jsonl)`; emit `analyzer.failure.classified`.
    3. `synthesize_rules(classifications, traces, catalog, config, golds=golds)` + `retire_candidates_as_patches(catalog_id, corrections_summary)` → `write_proposed_patches_sidecar(... proposed_patches.jsonl)` + `write_synthesis_summary(... proposed_patches.summary.json)`; emit `analyzer.rule.proposed` per patch.
    4. `summarize_corrections(catalog, traces, {case_id: (gold, origin)})` → `write_corrections(...)`; emit `analyzer.correction.attributed`. (Computed before step 3's write so retire candidates join the same `proposed_patches.jsonl`.)
    - returns `{"n_traces", "n_classified", "histogram": {failure_type: count}, "n_patches", "corrections": summary, "paths": {classified, proposed_patches, proposed_patches_summary, corrections, corrections_summary}}`.
  - `read_classified(base_dir, catalog_id, run_id) -> dict[str, dict]` (`trace_id → row`), `read_patches(base_dir, catalog_id, run_id) -> list[dict]`.
  - `histogram(classified) -> dict[str, int]` — all 14 `FailureType` names present (0 default) **plus `unclassified`**: a row with `failure_type == "no_failure"` and `confidence == 0.0` is the taxonomy's degenerate fall-through (gold present but nothing matched, or no gold and no catalog) and is counted as `unclassified`, never as a pass. The console's trace `counts` use the same rule.
  - Idempotent re-runs: the sidecars are derived artifacts and are overwritten; the ledger rows dedupe by content ([[analyzer_event_ledger]]), so re-analysing an unchanged run writes no new event.
  - All file writes go through the caller's serialised writer when invoked from the console ([[console_operator]]).
- **out-of-scope**:
  - Re-implementing any primitive's logic — this composite calls the three public entry points and nothing else.
  - Metrics summarisation (`summary.json`, `report.md`) — written by the run producer at bundle time ([[analyzer_run_manifest]] / [[analyzer_metrics]]); `analyze_run` does not re-summarise.
  - Comparing runs — [[analyzer_compare]].
  - Deciding on patches — [[analyzer_patch_decision]].
  - `bfcl/<category>` runs — per-case catalogs are not resolvable; `analyze_run` raises `CatalogNotResolvable` and the console answers 409. BFCL runs are read-only in the console (status from `summary.json`).
  - Choosing golds — `gold_map` from [[analyzer_label_store]] is used as-is.
- **on violation**: if a step needs something no primitive exposes (e.g. a per-trace attribution inside the taxonomy evidence), stop and add it to that primitive's doc; do not compute it inline here. If the catalog cannot be resolved, raise — never analyse against a guessed catalog.

## Procedure

```
on POST /api/runs/{cat}/{run}/analyze | python -m ganglion.console analyze <cat> <run> | seed | factory iteration:
    result ← analyze_run(base_dir, cat, run)
on CatalogNotResolvable (bfcl/*): 409 / non-zero exit; no sidecar touched
on a primitive raising:            propagate (fail loud); sidecars written so far remain (each is atomic per file)
```

## Contract

- **in**: `(base_dir, catalog_id, run_id)`; traces, labels, catalog resolved from the runs dir.
- **out**: `classified.jsonl`, `proposed_patches.jsonl`, `proposed_patches.summary.json`, `corrections.jsonl`, `corrections.summary.json` under `<base>/<catalog_id>/<run_id>/`; the returned digest dict.
- **event**: consume `analyzer.trace.recorded`, `analyzer.label.recorded` (inputs); emit `analyzer.failure.classified`, `analyzer.rule.proposed`, `analyzer.correction.attributed` — each declared by its primitive; this composite emits nothing of its own.
- **failure**: `bfcl/*` → `CatalogNotResolvable`; empty run → all sidecars written empty, `histogram` all zeros, `n_patches = 0`; primitive exception → propagate.
- **success** (`pytest tests/test_analyzer_analyze.py`): on the `rules-degraded-seed` fixture (see [[console_operator]] `seed`), `analyze_run` returns `histogram["missing_required_arg"] > 0`, `n_patches ≥ 1` with at least one `set_default` or `enable_strip_unknown_args`, `paths` all exist, `read_patches` round-trips every `patch_id`, and `sum(histogram.values()) == n_classified`; a second call yields byte-identical sidecars (modulo `created_at`) and no new `events.jsonl` lines beyond the `created_at`-free ones.

## Inheritance (per [workflow_principle](../agent-forge/workflow_principle.md))

| Mechanism | What this composite inherits |
|---|---|
| Pointer | [`task_principle`](../agent-forge/task_principle.md), [`workflow_principle`](../agent-forge/workflow_principle.md). |
| Template | Same six sections as the primitives. |
| Pattern | The `synth → … → rule` inner segment of [[factory_pipeline]], collapsed to one run and one call. |
| Data | None. |

## Observation

- `analyze_runs_total[catalog_id]` = calls; `analyze_wall_ms` = wall time per call (O(n_traces × hooks)).
- `unclassified_share[run_id]` = `histogram["unclassified"] ÷ n_classified`; > 0.05 is the taxonomy-gap alarm from [[analyzer_failure_taxonomy]].
- `patches_per_run[run_id]` = `n_patches` split by `operation` (from `proposed_patches.summary.json` + the `retire_rule` rows).
- `sidecar_agreement` = `n_classified == n_traces == corrections.n`; a mismatch means a primitive skipped traces silently.

## Negative checks (composite anti-patterns)

- [ ] Declares its own `in / out / event / failure / success` — not a topology diagram.
- [ ] Wraps three primitives, not one.
- [ ] Mutates no primitive's `in-scope`; new needs go into the primitive's doc.
- [ ] Procedure delegates by public entry point + declared events; no doc is invoked by name.

Related: [[analyzer_failure_taxonomy]], [[analyzer_rule_synthesis]], [[analyzer_correction_attribution]], [[analyzer_label_store]], [[analyzer_trace_store]], [[analyzer_run_manifest]], [[analyzer_event_ledger]], [[console_operator]], [[factory_pipeline]].
