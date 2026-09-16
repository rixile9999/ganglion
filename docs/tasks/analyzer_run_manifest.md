[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_run_manifest

A **run** is one `(catalog_id, run_id)` shard under the runs dir. Until now a shard was only `traces.jsonl`; nothing recorded *what* produced it — which model, which decoding block, which dataset hash, which iteration of the loop, which parent run. This primitive adds an immutable `manifest.json` per run, persists the metrics summary and report next to it as a **run bundle**, and makes "scan every `manifest.json`" the single source of truth for listing runs. The console's run picker ([[console_operator]]), the loop page, and [[analyzer_compare]]'s refusal rule all read manifests, never directory names.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/manifest.py`, tests `tests/test_analyzer_manifest.py`.

## Role

Write one atomic, never-updated `manifest.json` per run bundle, persist `summary.json` / `report.md` beside it, and enumerate runs by scanning manifests.

## Scope

- **in-scope**:
  - `RunManifest` frozen dataclass (`to_dict` / `from_dict`, tolerant of missing optional keys):
    - identity: `run_id`, `catalog_id`, `catalog_fingerprint`, `model_id`;
    - kind: `benchmark` ∈ `"iot" | "bfcl" | "chat"`, `metric_kind: str = "exact_match"` (or `"ast_match"`), `client_kind: str = ""` ∈ `"json-dsl" | "freeform" | "thinking" | "native" | "rules"`;
    - model provenance: `model_fingerprint: str = ""`, `served_model_version: str = ""`, `base_model: str | None`, `adapter_dir: str | None`, `adapter_sha256: str | None`, `train_provenance: Mapping | None`, `labels_used: Mapping[str, int] | None`;
    - data: `dataset_path: str = ""`, `dataset_sha256: str = ""`, `n_cases: int = 0`, `limit: int | None`;
    - `decoding: Mapping[str, Any]` — `{"repair": bool, "repair_max_attempts": int, "thinking": bool, "repeat": int, "grammar_mask": bool}`; the block [[analyzer_compare]] requires equal;
    - loop position: `iteration: int | None`, `parent_run_id: str | None`;
    - environment: `git_head`, `python`, `accelerator`, `started_at`, `finished_at`, `extra: Mapping[str, Any]`. `accelerator` is filled by `accelerator_stamp(local=spec.kind == "local_hf", device=spec.device)` — the real CUDA device name for a run that executes in this process, `""` for an API-served one (this box's GPU did not run it) and never the placement policy `"auto"`.
  - `run_dir(base_dir, catalog_id, run_id) -> Path` = `<base>/<catalog_id>/<run_id>/` (same rule as `TraceStore.shard_path().parent`).
  - `write_manifest(base_dir, manifest) -> Path` — atomic (`tmp` + `os.replace`); overwrites an existing file only with an identical run identity (`run_id`, `catalog_id`); a manifest is written once at run end and not updated afterwards (`human_minutes`, label counts, precision are computed at read time from sidecars).
  - `read_manifest(path) -> RunManifest`.
  - `list_runs(base_dir, catalog_id=None) -> list[RunManifest]` — scan `**/manifest.json`, sorted by `(catalog_id, iteration or -1, started_at)`. **This scan is the list SSOT**: a shard without a manifest is not a run (it is shown nowhere), and a manifest without traces is a degenerate run (shown with `n_traces = 0`).
  - `write_run_bundle(base_dir, manifest, summary: dict | None, report_md: str | None) -> Path` — writes `manifest.json`, `summary.json` (the [[analyzer_metrics]] `summarize()` dict, `sort_keys=True`), `report.md`; returns the run dir. `describe.json` (`Catalog.describe()` snapshot, see [[contract_describe]]) is written by producers that hold the catalog (console sessions always; CLI runs when a builtin or compiled catalog is resolvable).
  - `read_summary(base_dir, catalog_id, run_id) -> dict | None`.
  - `dataset_sha256(path) -> str` (sha256 of the file bytes; `""` when the path is empty or missing) and `git_head() -> str` (`""` outside a git checkout).
  - Producers: the CLI `--trace-store` path ([[benchmark_iot]] / [[benchmark_bfcl]] via `ganglion/cli.py`) after `traces_from_results` / `traces_from_bfcl_results`; the console `POST /api/sessions` (`benchmark="chat"`, `run_id="console/<session_id>"`); `factory.run_pipeline` per iteration shard.
  - Emit `analyzer.run.recorded(run_id, manifest_path)` with payload `{manifest_path, benchmark, metric_kind, n_cases, iteration, parent_run_id}` and correlation `{catalog_id, run_id}` through [[analyzer_event_ledger]], once per bundle write.
- **out-of-scope**:
  - Computing the summary — [[analyzer_metrics]] owns `summarize()`; this doc only persists what it is handed.
  - Producing traces — [[analyzer_trace_store]].
  - Deriving `train_provenance` — [[lm_finetune]] writes `train_provenance.json` beside `train_metrics.json`; the producer copies it into the manifest verbatim.
  - Model fingerprints — `model_fingerprint(spec)` is owned by [[lm_model_registry]]; the producer stamps the value.
  - Backfilling legacy `runs/m*`, `runs/bfcl`, `runs/factory_*` outputs into bundles — a separate migration task.
  - Multi-seed aggregation (`mean ± sd` across runs sharing everything but `model_fingerprint`) — a read-time concern for the loop page and [[analyzer_compare]], not stored here.
  - Commit policy for `runs/traces/**` (which session shards are committed) — repository policy, not this primitive.
- **on violation**: if a producer wants to *update* a manifest after the run (e.g. add label counts), stop — those are read-time aggregates over `labels.jsonl` / `patch_decisions.jsonl`. If a listing consumer is tempted to infer a run from a directory that has `traces.jsonl` but no `manifest.json`, stop — the fix is to write the manifest (or run `seed`), not to list unmanifested shards.

## Procedure

```
on run end (CLI --trace-store, console session create, factory iteration):
    manifest ← RunManifest(run_id, catalog_id, catalog_fingerprint=catalog.fingerprint(), model_id,
                           benchmark, metric_kind, client_kind, decoding={repair, repair_max_attempts,
                           thinking, repeat, grammar_mask}, dataset_path, dataset_sha256=dataset_sha256(path),
                           n_cases, limit, iteration, parent_run_id, git_head=git_head(), python, accelerator,
                           started_at, finished_at=now_utc, ...)
    dir ← write_run_bundle(base_dir, manifest, summary=summarize(results) or None, report_md=render or None)
    emit analyzer.run.recorded(run_id, manifest_path=dir/"manifest.json")
    print(dir, file=sys.stderr)

write_manifest(base_dir, manifest):
    path ← run_dir(...)/"manifest.json"; mkdir -p
    write json.dumps(manifest.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) to path.with_suffix(".tmp")
    os.replace(tmp, path)

list_runs(base_dir, catalog_id=None):
    for p in sorted(glob(<base>/[<catalog_id>|**]/manifest.json)): yield read_manifest(p)
    sort by (catalog_id, iteration if not None else -1, started_at)

on unreadable manifest.json: log WARNING with the path; skip it (the run is invisible until fixed)
on write IO error:           re-raise; no partial manifest (tmp + rename)
```

## Contract

- **in**: a finished run's identity and settings (from the producer), the `summarize()` dict and rendered report from [[analyzer_metrics]] (either may be `None` for chat sessions).
- **out**:
  - `<base>/<catalog_id>/<run_id>/manifest.json` — exactly one per run; immutable after the bundle write.
  - `<base>/<catalog_id>/<run_id>/summary.json`, `report.md` (when supplied), `describe.json` (when the producer holds the catalog).
  - `list_runs()` — the ordered run list every consumer uses.
- **event**: consume none; emit `analyzer.run.recorded(run_id, manifest_path)` once per bundle write.
- **failure**:
  - Manifest with a `run_id` containing a catalog prefix or more than one `/` → `ValueError` (the run id vocabulary is [[analyzer_trace_store]]'s).
  - `write_manifest` for an existing run dir whose manifest names a different `model_id` / `catalog_fingerprint` → `ValueError` (a shard is one run; use a new `run_id`).
  - Unreadable manifest during `list_runs` → skipped + warning; listing continues.
  - IO error on write → re-raise; nothing emitted.
- **success** (`pytest tests/test_analyzer_manifest.py`):
  - `write_run_bundle` then `list_runs` returns the manifest with equal `to_dict()`; `read_summary` returns the same dict that was written.
  - A `tmp_path` with two catalogs and three runs (iterations `None, 0, 1`) lists in `(catalog_id, -1/0/1, started_at)` order.
  - A directory containing `traces.jsonl` but no `manifest.json` is absent from `list_runs`.
  - `dataset_sha256` of `examples/iot_light/dataset.jsonl` is stable across two calls; `git_head()` returns `""` in a non-git `tmp_path`.
  - A manifest round-trips through `to_dict` / `from_dict` with `decoding`, `train_provenance` and `labels_used` intact.

## Observation

- `runs_total[catalog_id, benchmark]` = manifests found by `list_runs`.
- `unmanifested_shards` = `traces.jsonl` files whose directory has no `manifest.json`; must be 0 after `seed` — non-zero means a producer bypassed `write_run_bundle`.
- `bundle_completeness[run_id]` = presence bits for `summary.json`, `report.md`, `describe.json`; chat sessions legitimately lack the first two.
- `reproducible_share` = runs with non-empty `served_model_version` (or `model_fingerprint`) and `dataset_sha256` ÷ runs; API runs against unversioned aliases (e.g. `qwen3.6-plus`) count as non-reproducible and the loop page badges them.
- `iteration_chain_breaks` = manifests whose `parent_run_id` names no existing manifest.

Related: [[analyzer_trace_store]], [[analyzer_metrics]], [[analyzer_compare]], [[analyzer_event_ledger]], [[lm_model_registry]], [[lm_finetune]], [[benchmark_iot]], [[benchmark_bfcl]], [[console_operator]], [[factory_pipeline]].
