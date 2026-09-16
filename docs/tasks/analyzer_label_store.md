[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_label_store

Sidecar store for **human verdicts** on recorded traces. A benchmark trace carries its dataset gold in `Trace.expected_plan`; a console chat trace carries none. This primitive gives both a second, human-authored gold channel — `labels.jsonl` next to `traces.jsonl` — and defines the *single* join point (`resolve_gold`) through which every gold-dependent consumer ([[analyzer_failure_taxonomy]] via `classify_traces(golds=)`, [[analyzer_rule_synthesis]] via `synthesize_rules(golds=)`, [[analyzer_compare]], [[analyzer_correction_attribution]]) picks a gold. It also exports labels as **corrected-SFT rows** in the exact shape [[lm_data_synth]] writes, so a retrain consumes them without a new loader. No DPO pairs are produced here.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/labels.py`, tests `tests/test_analyzer_labels.py`.

## Role

Record one immutable, content-addressed label per human verdict against a `trace_id`, resolve the effective gold for any trace deterministically, assign each label a paraphrase family and a train/dev/release zone, and export train-zone labels as SFT rows that round-trip through the catalog.

## Scope

- **in-scope**:
  - `LabelRecord` frozen dataclass (fields, all serialised by `to_dict` / `from_dict`):
    - join keys: `label_id`, `trace_id`, `case_id`, `catalog_id`, `run_id`;
    - identity triple: `catalog_fingerprint`, `model_id`, `dataset_sha256` (`""` when unknown);
    - `origin: str` — `"human:<alias>"` | `"dataset:<sha>"` | `"teacher:<model>"`;
    - `verdict: str` — `"correct"` | `"incorrect"` | `"should_abstain"` | `"unsure"`;
    - `expected_plan: dict | None` — Action IR (`{"calls":[{"action","args"}]}`); required for `incorrect`; `{"calls": []}` for `should_abstain`;
    - `endorsed: str | None` — `"f0"` | `"fk"` | `"custom"` | `None` (which plan the labeler endorsed when F⁰ ≠ Fᴷ); `saw_f0: bool`; `failure_hint: str | None` (a `FailureType` value); `order_sensitive: bool`; `note: str`;
    - `family_id: str`; `zone: str`; `labeler: str`; `label_batch_id: str`; `time_to_label_ms: int | None`; `supersedes: str | None`; `created_at: str`.
  - `make_label_id(trace_id, origin, verdict, expected_plan, labeler, label_batch_id) -> str` = `"lb-" + sha256(...)[:16]` over the stable JSON of those six values. Re-recording the same verdict is a no-op.
  - `family_id_for(prompt: str, template_id: str | None = None) -> str` — `"fam-" + sha256("tpl:" + template_id)[:12]` when the dataset row has a template id; else the normalised prompt (NFKC → casefold → collapse whitespace → strip trailing punctuation and `#N` tokens) hashed to `"fam-" + sha256[:12]`.
  - `zone_for(family_id, *, train_share: int = 80, release_families: Collection[str] = ()) -> str` — `"release"` if the family is in `release_families`; else `"train"` if `int(sha256(family_id)[:8], 16) % 100 < train_share`; else `"dev"`. Deterministic; no first-seen ordering.
  - `LabelStore(base_dir)` — mirrors `TraceStore` ([[analyzer_trace_store]]): `append(rec) -> str` idempotent on `label_id`, path `<base>/<catalog_id>/<run_id>/labels.jsonl`; `iter(catalog_id=None, run_id=None)`; `latest_by_trace(catalog_id, run_id) -> dict[str, LabelRecord]` with `supersedes` chains resolved, newest wins; `count(catalog_id, run_id)`.
  - `resolve_gold(trace, labels: Mapping[str, LabelRecord]) -> tuple[ActionPlan | None, str]` — the only join: latest human label with verdict `incorrect` / `should_abstain` → its `expected_plan` (origin `"human:<alias>"`); verdict `correct` → `trace.plan` as gold (origin `"human:<alias>"`); else `trace.expected_plan` (origin `"dataset"`); else `(None, "none")`. Constructs `ActionPlan` from `ganglion.contract.types` without validation.
  - `gold_map(traces, labels) -> dict[str, ActionPlan]` — `case_id → gold` for `classify_traces(golds=)` and `synthesize_rules(golds=)`.
  - `export_sft(labels, traces_by_id, *, catalog, zones=("train",), include_correct=False) -> list[dict]` — one row per exportable label, shaped exactly like `ganglion/lm/synth/pipeline.py:write_jsonl`: `{"intent": prompt, "expected": json.dumps(plan, sort_keys=True, ensure_ascii=False), "strategy": f"human_label:{verdict}:{first_tool}", "teacher_score": 1.0, "id": sha1(intent + "|" + expected)[:12]}`. `plan` = the resolved gold's `to_jsonable()`. Every row's `expected` must round-trip through `catalog.parse_json_dsl`; rows that do not are dropped and counted. Labels whose `zone ∉ zones`, verdict `unsure`, or (unless `include_correct`) verdict `correct` are never exported.
  - `write_exports(base_labels_dir, catalog_id, sft_rows, hard_rows) -> dict[str, str]` — writes `runs/labels/<catalog_id>/human_sft.jsonl` and `runs/labels/<catalog_id>/hard_pool.jsonl` (same row shape; `hard_rows` = the `incorrect` / `should_abstain` labels whose zone is `dev`), plus `runs/labels/<catalog_id>/zones.jsonl` (`{family_id, zone}` per family seen); returns `{"human_sft", "hard_pool", "zones"}` → paths.
  - Emit `analyzer.label.recorded(label_id, trace_id, verdict, origin, zone)` per newly appended label, correlation `{catalog_id, run_id, trace_id}`, through [[analyzer_event_ledger]].
- **out-of-scope**:
  - Validating `expected_plan` — the producer (the console's `POST /api/labels`, see [[console_operator]]) must have passed it through `Catalog.parse_json_dsl(expected_plan, prompt=trace.prompt)` and rejected failures (422); this store never validates and never auto-fixes a plan.
  - DPO / preference pairs (`human_dpo.jsonl`) — not produced this cycle; human-canonical vs model-raw pairs are off-policy and the label volume (~50) is below any reportable threshold. Preference data, if ever, is sampled from the current policy under [[lm_finetune]].
  - Running SFT on the export — [[lm_finetune]] consumes `human_sft.jsonl` concatenated with the synth set; this doc only writes the file.
  - Embedding-nearest-neighbour family inheritance (cosine over an encoder) — deferred; v1 families are template-id or normalised-prompt hashes only.
  - Multi-labeler adjudication and inter-annotator agreement — one labeler; self-agreement is an Observation, not a mechanism.
  - Reward models, teacher-origin labels beyond storing the `origin` string, and the `zones` mutation of benchmark `Trace.expected_plan` — the dataset gold stays where [[analyzer_trace_store]] put it.
  - Mutating `traces.jsonl` in any way — labels are a sidecar keyed by `trace_id` (append-only invariant of [[analyzer_trace_store]]).
- **on violation**: an `expected_plan` that is not a mapping with a `calls` list, or a `verdict` outside the four values, is rejected with `ValueError` at `append` — never coerced. A label whose `trace_id` is unknown to the shard is rejected the same way. If a consumer needs a gold that neither a label nor `expected_plan` provides, `resolve_gold` returns `(None, "none")` and the consumer must treat the trace as *ungraded*, not as a failure.

## Procedure

```
on a verdict for trace_id (producer has already validated expected_plan):
    trace ← TraceStore.by_id(trace_id, catalog_id=…, run_id=…)      # must exist
    family_id ← family_id_for(trace.prompt, template_id=dataset_template_id(trace) or None)
    zone ← zone_for(family_id, train_share=80, release_families=release_set(catalog_id))
    rec ← LabelRecord(label_id=make_label_id(...), ..., family_id, zone, created_at=now_utc)
    if label_id already in <base>/<catalog_id>/<run_id>/labels.jsonl: return label_id   # idempotent
    append one line (sort_keys=True, ensure_ascii=False)
    emit analyzer.label.recorded(label_id, trace_id, verdict, origin, zone)

resolve_gold(trace, labels):
    lb ← labels.get(trace.trace_id)          # latest, supersedes-resolved
    if lb and lb.verdict in {incorrect, should_abstain}: return (ActionPlan(lb.expected_plan), lb.origin)
    if lb and lb.verdict == correct and trace.plan:   return (ActionPlan(trace.plan), lb.origin)
    if trace.expected_plan:                            return (ActionPlan(trace.expected_plan), "dataset")
    return (None, "none")

export (python -m ganglion.console export-labels <catalog_id>):
    labels ← LabelStore.iter(catalog_id); traces_by_id ← TraceStore
    sft_rows  ← export_sft(labels, traces_by_id, catalog=resolve_catalog(catalog_id), zones=("train",))
    hard_rows ← rows for incorrect/should_abstain labels with zone == "dev"
    paths ← write_exports(runs/labels, catalog_id, sft_rows, hard_rows)

on undo (z key): append a NEW label with supersedes=<old label_id>; never delete a line
on catalog_fingerprint mismatch at export: keep the row, count it in labels_stale_share (a warning, not a drop)
```

## Contract

- **in**: a verdict `{catalog_id, run_id, trace_id, verdict, expected_plan?, endorsed?, saw_f0?, note?, order_sensitive?, failure_hint?, time_to_label_ms?, supersedes?}` plus `labeler` and `label_batch_id` from the producer; traces from [[analyzer_trace_store]]; the `Catalog` (via `resolve_catalog`) for export round-tripping.
- **out**:
  - `<base>/<catalog_id>/<run_id>/labels.jsonl` — one line per label; append-only.
  - `runs/labels/<catalog_id>/human_sft.jsonl`, `runs/labels/<catalog_id>/hard_pool.jsonl`, `runs/labels/<catalog_id>/zones.jsonl`.
  - `resolve_gold` / `gold_map` return values consumed in-process by the analyzer primitives.
- **event**: consume `analyzer.trace.recorded` (a label can only target a recorded trace); emit `analyzer.label.recorded(label_id, trace_id, verdict, origin, zone)`.
- **failure**:
  - Unknown `trace_id`, bad `verdict`, malformed `expected_plan` → `ValueError`; no line, no event.
  - Duplicate `label_id` → idempotent skip; no event.
  - Export row fails `catalog.parse_json_dsl` → dropped, counted in `export_dropped_rows`; export continues.
  - `OSError` on append → re-raise (fail loud).
- **success** (`pytest tests/test_analyzer_labels.py`):
  - Appending the same verdict twice yields one line; `count == 1`.
  - `latest_by_trace` returns the superseding label when a `supersedes` chain exists.
  - `resolve_gold` prefers a human `incorrect` label over `expected_plan`, uses `trace.plan` for `correct`, falls back to `expected_plan`, and returns `(None, "none")` for an unlabelled chat trace.
  - `zone_for` is stable across calls and honours `release_families`; a hand-written paraphrase pair maps to the same `family_id`.
  - `export_sft` rows carry exactly the keys `intent, expected, strategy, teacher_score, id`, read back through `ganglion.lm.synth.pipeline.read_jsonl`, and every `expected` parses with `catalog.parse_json_dsl`; no `dev` / `release` label appears in `human_sft.jsonl`; `dev` incorrect labels appear only in `hard_pool.jsonl`.

## Observation

- `labels_total[catalog_id, verdict]` = lines in `**/labels.jsonl` by verdict.
- `gold_coverage[catalog_id, run_id]` = traces with `resolve_gold ≠ (None, "none")` ÷ traces. Panels that need gold (compare, corrections) hide below 0.5.
- `f0_seen_share` = labels with `saw_f0 = true` ÷ labels where F⁰ ≠ Fᴷ; the anti-circularity guard for [[analyzer_correction_attribution]].
- `export_dropped_rows` = rows rejected by the round-trip gate per export; non-zero means a stale label (fingerprint drift) or a producer that skipped validation.
- `labels_stale_share` = labels whose `catalog_fingerprint` differs from the current catalog's ÷ labels.
- `leakage_rate` = labels whose family is in `release_families` ÷ labels; must be 0 in any export.
- `self_agreement_kappa` = agreement between a family's original label and a re-label recorded ≥ 7 days later under a new `label_batch_id` (10 % random re-label); families with disagreement are excluded from export.
- `human_minutes[run_id]` = Σ `time_to_label_ms` ÷ 60 000 over the run's labels (computed at read time; never stored in a manifest).

Related: [[analyzer_trace_store]], [[analyzer_event_ledger]], [[analyzer_failure_taxonomy]], [[analyzer_rule_synthesis]], [[analyzer_compare]], [[analyzer_correction_attribution]], [[lm_data_synth]], [[lm_finetune]], [[console_operator]].
