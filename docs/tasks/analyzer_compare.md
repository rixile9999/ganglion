[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_compare

Paired, per-case comparison of two runs on the same catalog. [[analyzer_metrics]] deliberately stops at one run (`summary.json`) and deferred cross-run statistics to "a future `analyzer_compare` task" — this is that task. It answers the loop-page question "what changed between iteration N−1 and N?" with a transition matrix (`fixed / regressed / same_pass / same_fail`), a paired-bootstrap confidence interval on ΔEM, and a `manifest_diff` that names the training / decoding changes between the runs. It **refuses** to compare runs whose manifests differ in the things that would make the delta meaningless (dataset hash, metric kind, decoding block) unless the caller explicitly opts in.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/compare.py`, tests `tests/test_analyzer_compare.py`.

## Role

Join two runs' traces on `case_id`, grade each side with the run's own resolved gold, tabulate transitions, bootstrap the paired EM delta, and persist one `compare-<run_a>.json` in run B's directory.

## Scope

- **in-scope**:
  - `CompareResult` frozen dataclass (`to_dict`): `catalog_id`, `run_a`, `run_b`, `n`, `em_a`, `em_b`, `em_delta`, `ci95: tuple[float, float] | None`, `counts: dict` with keys `fixed`, `regressed`, `same_pass`, `same_fail`, `only_a`, `only_b`, `ungraded`; `per_case: tuple[dict, ...]` rows `{case_id, trace_a, trace_b, status_a, status_b, direction, failure_type_a, failure_type_b}`; `manifest_diff: dict`; `warnings: tuple[str, ...]`.
  - `compare_runs(base_dir, catalog_id, run_a, run_b, *, store=None, exclude_case_ids=(), bootstrap: int | None = 2000, seed=42, allow_diff=False) -> CompareResult`:
    - traces from [[analyzer_trace_store]] (`repeat_index == 0` only); join on `case_id`; `only_a` / `only_b` for unmatched cases; `exclude_case_ids` removed before anything is counted.
    - per-side status via `labels.resolve_gold` ([[analyzer_label_store]]) with *that run's* `latest_by_trace` labels: `pass` (plan equals gold), `fail` (valid plan, ≠ gold), `invalid` (`plan is None`), `ungraded` (no gold). `direction` ∈ `fixed` (fail/invalid → pass), `regressed` (pass → fail/invalid), `same_pass`, `same_fail`, `only_a`, `only_b`, `ungraded` (either side ungraded).
    - `em_x` = passes ÷ graded cases on side x; `em_delta = em_b − em_a`.
    - paired bootstrap: resample the graded case set `bootstrap` times with `random.Random(seed)`, statistic = mean of `(pass_b − pass_a)`; `ci95` = (2.5 %, 97.5 %) percentiles; `None` when `bootstrap` is `None` or graded `n < 2`.
    - `failure_type_a/b` read from each run's `classified.jsonl` ([[analyzer_failure_taxonomy]]) when present; `None` otherwise.
    - refusal: manifests ([[analyzer_run_manifest]]) that differ in `dataset_sha256`, `metric_kind`, or `decoding` → `ValueError` naming the differing keys, unless `allow_diff=True`, in which case each differing key becomes a `warnings` entry. `catalog_fingerprint` drift is always a warning (it is the thing the loop changes on purpose).
    - `manifest_diff` = `{key: {"a": …, "b": …}}` over `decoding`, `model_id`, `model_fingerprint`, `base_model`, `adapter_sha256`, `train_provenance`, `labels_used`, `catalog_fingerprint`, `dataset_sha256`, `iteration`.
    - writes `<run_b dir>/compare-<safe_run_name(run_a)>.json` (`safe_run_name`: `"/"` → `"__"`), `sort_keys=True`; overwrites an earlier comparison against the same `run_a`.
    - emits `analyzer.compare.completed(run_a, run_b, path)` with payload `{run_a, run_b, path, n, em_a, em_b, em_delta, ci95, counts}` and correlation `{catalog_id, run_id: run_b}` through [[analyzer_event_ledger]].
  - `safe_run_name(run_id) -> str`.
  - Consumers: the console `GET /api/compare` and the `changed` trace filter (`fixed | regressed`) in [[console_operator]]; the `compare` subcommand.
- **out-of-scope**:
  - Significance tests beyond the paired bootstrap (t-tests, permutation tests, multiple-comparison correction) — [[analyzer_metrics]] deferred them and so does this doc.
  - Comparing runs on different catalogs, or a builtin against a `compiled/<sha12>` — one `catalog_id` per call.
  - Multi-repeat aggregation (`repeat_index > 0`) — only the first repeat is compared; latency-repeat runs contribute their `repeat_index == 0` trace.
  - Family-level comparison across console chat sessions (prompts differ, no `case_id` join) — a follow-up once families ([[analyzer_label_store]]) are embedding-based.
  - Producing golds, labels, classifications or manifests — read only; nothing under the two run dirs is modified except the new `compare-*.json`.
  - The `pool ∩ train = 0` check against `hard_pool.jsonl` — belongs to the measurement protocol, not to this join.
- **on violation**: never "fix" a mismatch by silently dropping the differing key or by re-grading one side with the other side's labels. Refuse with the key names; the caller passes `allow_diff=True` knowingly and the result carries the warning rows. If a run has no manifest, refuse (`ValueError`) — an unmanifested shard is not a run ([[analyzer_run_manifest]]).

## Procedure

```
compare_runs(base, cid, run_a, run_b, *, store, exclude_case_ids, bootstrap, seed, allow_diff):
    ma, mb ← read_manifest(run_dir(base, cid, run_a)), read_manifest(run_dir(base, cid, run_b))   # ValueError if missing
    diff_keys ← {k for k in (dataset_sha256, metric_kind, decoding) if ma[k] != mb[k]}
    if diff_keys and not allow_diff: raise ValueError(f"manifests differ: {sorted(diff_keys)}")
    warnings ← [f"manifest differs in {k}" for k in diff_keys] + (["catalog_fingerprint drift"] if drift)
    ta, tb ← {t.case_id: t for t in store.iter(cid, run_x) if t.repeat_index == 0 and t.case_id ∉ exclude}
    la, lb ← LabelStore.latest_by_trace(cid, run_a), LabelStore.latest_by_trace(cid, run_b)
    ca, cb ← read_classified(base, cid, run_a) or {}, read_classified(base, cid, run_b) or {}
    for case_id in sorted(ta ∪ tb):
        status_x ← grade(t_x, resolve_gold(t_x, l_x))         # pass | fail | invalid | ungraded
        direction ← transition(status_a, status_b)            # fixed | regressed | same_pass | same_fail | only_a | only_b | ungraded
        per_case.append({...}); counts[direction] += 1
    graded ← [(pass_a, pass_b) for rows with both sides graded]
    em_a, em_b, em_delta ← means; ci95 ← paired_bootstrap(graded, bootstrap, seed) if bootstrap and len(graded) ≥ 2 else None
    result ← CompareResult(...)
    write <run_b dir>/compare-<safe_run_name(run_a)>.json
    emit analyzer.compare.completed(run_a, run_b, path)
    return result

on a side with plan None: status "invalid" (counts as a non-pass; fixed/regressed are defined over pass vs non-pass)
on no graded cases:       em_* = 0.0, ci95 = None, warnings += ["no graded cases"]; file still written
```

## Contract

- **in**: `catalog_id`, `run_a`, `run_b`; their manifests, traces, latest labels, and (optionally) `classified.jsonl` files; `exclude_case_ids`, `bootstrap`, `seed`, `allow_diff`.
- **out**:
  - `<base>/<catalog_id>/<run_b>/compare-<safe_run_name(run_a)>.json` — `CompareResult.to_dict()`.
  - Return value: the same `CompareResult`.
- **event**: consume `analyzer.run.recorded` (both runs must be recorded before they are comparable); emit `analyzer.compare.completed(run_a, run_b, path)`.
- **failure**:
  - Missing manifest on either side → `ValueError`; no file, no event.
  - `dataset_sha256` / `metric_kind` / `decoding` differ and `allow_diff=False` → `ValueError` listing the keys; no file, no event.
  - Same with `allow_diff=True` → proceed; `warnings` populated.
  - Empty intersection of `case_id`s → result with `n = 0`, all rows `only_a` / `only_b`, `ci95 = None`; file written (a comparison that says "nothing comparable" is a valid artifact).
  - IO error on write → re-raise; no event.
- **success** (`pytest tests/test_analyzer_compare.py`):
  - Comparing a run with itself gives `em_delta == 0.0`, `counts.fixed == counts.regressed == 0`, and no refusal (identical manifests).
  - A `tmp_path` fixture with run A (one case failing) and run B (that case passing, another regressing) yields `counts == {fixed: 1, regressed: 1, ...}` and `per_case` directions to match; `failure_type_*` populated when `classified.jsonl` is present.
  - Runs whose manifests differ in `decoding.repair` raise `ValueError` without `allow_diff` and return a `warnings` row with it.
  - `ci95` is a 2-tuple with `ci95[0] ≤ em_delta ≤ ci95[1]` for the fixture, and is identical across two calls with the same `seed`.
  - `safe_run_name("console/s-1") == "console__s-1"` and the file lands in run B's directory.

## Observation

- `compare_count[catalog_id]` = `compare-*.json` files under the catalog.
- `refusal_rate` = `ValueError` refusals ÷ `compare_runs` calls (per process); a high rate means producers are not holding the decoding block constant across iterations.
- `ungraded_share[run_b]` = `counts.ungraded ÷ n`; the panel hides the transition matrix above 0.5.
- `ci_width[run_b]` = `ci95[1] − ci95[0]`; with n = 500 at EM ≈ 0.9 expect ≈ 5 pp; wider intervals mean the run is too small to claim a delta.
- `net_delta_vs_transitions` = `(fixed − regressed) ÷ n` must equal `em_delta` on graded cases (an internal consistency assertion, checked in tests).

Related: [[analyzer_metrics]], [[analyzer_trace_store]], [[analyzer_label_store]], [[analyzer_run_manifest]], [[analyzer_failure_taxonomy]], [[analyzer_event_ledger]], [[console_operator]], [[factory_pipeline]].
