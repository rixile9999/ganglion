[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_event_ledger

Append-only JSONL **ledger** that materialises the events every task doc in this tree declares in its `Contract.event` clause. Until now "connect via events, not direct calls" had no runtime substrate — `ganglion/factory.py` orchestrates by function call and no bus exists. This primitive makes each declared event a durable, content-addressed row on disk, correlated to the run it belongs to, so the console ([[console_operator]]) and the composites ([[analyzer_analyze]], [[factory_pipeline]]) can *observe* what happened by reading files. It is deliberately **not** an in-process pub/sub bus: nothing subscribes, nothing fans out; a row is a fact.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/ledger.py`, tests `tests/test_analyzer_ledger.py`.

## Role

Persist one idempotent, content-addressed JSONL row per emitted event, in the run directory the event is correlated with (or the global ledger when it is not), and read them back filtered by `(catalog_id, run_id)`.

## Scope

- **in-scope**:
  - `Event` frozen dataclass: `event_id: str`, `name: str`, `ts: str` (ISO-8601 UTC, `Z`), `producer: str` (e.g. `console.api`, `cli.trace_store`, `analyzer.analyze`), `correlation: Mapping[str, Any]` (`catalog_id`, `run_id`, optionally `session_id`, `trace_id`, `patch_id`), `payload: Mapping[str, Any]`, `refs: Mapping[str, str]` (`{name: path}` pointers to the artifacts the event is about).
  - `emit(base_dir, name, payload, *, producer, correlation, refs=None) -> Event`:
    - `event_id = "ev-" + sha256(name + stable_json(correlation) + stable_json(payload))[:16]` where `stable_json = json.dumps(obj, sort_keys=True, ensure_ascii=False)`.
    - Target file: `<base>/<catalog_id>/<run_id>/events.jsonl` when `correlation` carries both `catalog_id` and `run_id`; otherwise `<base>/ledger/global.jsonl`.
    - Idempotent: if `event_id` already exists in the target file, nothing is written and the existing `Event` is returned. Same input ⇒ same row (task_principle §8).
    - Append under a per-process lock; exactly one line per event, `json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)`.
  - `read_events(base_dir, catalog_id=None, run_id=None) -> list[Event]` — reads the run file when both filters are given, every `events.jsonl` under `<base>/<catalog_id>/` with one filter, the whole tree plus `ledger/global.jsonl` with none. Order = file order.
  - `EVENT_NAMES` — the closed vocabulary. `emit` raises `ValueError` for any other name:
    `lm.inference.completed`, `lm.inference.failed`, `analyzer.run.recorded`, `analyzer.trace.recorded`, `analyzer.label.recorded`, `analyzer.failure.classified`, `analyzer.rule.proposed`, `analyzer.correction.attributed`, `analyzer.metrics.summarized`, `analyzer.patch.decided`, `analyzer.compare.completed`, `contract.catalog.compiled`.
  - Payload discipline: ids, plans (Action IR dicts), counts, paths only. No raw model output, no attempts arrays, no file contents — those live in the artifacts that `refs` point at ([[analyzer_trace_store]] etc.).
  - Correlation discipline: `run_id` is the plain run name (may contain one `/` for `console/<session_id>`); `catalog_id` is a builtin tier, `compiled/<sha12>` or `bfcl/<category>` — the same vocabulary as [[analyzer_trace_store]].
- **out-of-scope**:
  - Subscribing, fan-out, callbacks, SSE, sockets — nothing in this primitive *reacts* to a row. Consumers poll files (the console) or read on demand (composites).
  - Defining what each event means — every name in `EVENT_NAMES` is declared by the doc that emits it ([[lm_client]], [[lm_request_serve]], [[analyzer_run_manifest]], [[analyzer_trace_store]], [[analyzer_label_store]], [[analyzer_failure_taxonomy]], [[analyzer_rule_synthesis]], [[analyzer_correction_attribution]], [[analyzer_metrics]], [[analyzer_patch_decision]], [[analyzer_compare]], [[contract_schema_compiler]]). This doc owns the *envelope*, not the semantics.
  - The derived global index (`runs/ledger/index.jsonl`, gitignored) and any `reindex` command — deferred; `read_events` scans.
  - Cross-process locking — each process writes only its own run directories; `global.jsonl` receives single-line `O_APPEND` writes and no reader assumes ordering across processes.
  - Retention, compaction, rotation of `events.jsonl`.
  - Legacy event names that are declared by older docs but absent from `EVENT_NAMES` (`analyzer.repair.replayed`, `analyzer.rule.proposed_escalated`, `benchmark.iot.{completed,failed}`, `benchmark.bfcl.completed`, `factory.evaluation.*`, `factory.pipeline.*`) — they remain doc-level declarations; adding one to the ledger vocabulary is a change to this doc's `EVENT_NAMES` in the same PR as the emitter.
- **on violation**: an unknown `name` is a `ValueError` at the call site — never coerced, never written under a "misc" name. An `OSError` on append is re-raised (fail loud); the caller decides whether the surrounding write (trace, label, decision) is rolled back. A payload that is not JSON-serialisable is a `TypeError` at the call site.

## Procedure

```
emit(base_dir, name, payload, *, producer, correlation, refs=None):
    if name not in EVENT_NAMES: raise ValueError(name)
    event_id ← "ev-" + sha256(name + stable_json(correlation) + stable_json(payload))[:16]
    target ← <base>/<catalog_id>/<run_id>/events.jsonl
             if {"catalog_id","run_id"} ⊆ correlation else <base>/ledger/global.jsonl
    with process_lock:
        if event_id in ids_in(target): return existing_event   # idempotent
        mkdir -p target.parent
        append one line: json.dumps(Event(...).to_dict(), sort_keys=True, ensure_ascii=False)
    return event

read_events(base_dir, catalog_id=None, run_id=None):
    files ← [run file] | glob(<base>/<catalog_id>/**/events.jsonl) | glob(<base>/**/events.jsonl) + ledger/global.jsonl
    for line in files (file order): yield Event.from_dict(json.loads(line)); skip blank / undecodable lines

on OSError during append:      re-raise; no partial line is left (write is one os.write of the full line)
on unknown name:               ValueError before any I/O
```

## Contract

- **in**: `(name, payload, producer, correlation, refs)` from any emitter listed above; `base_dir` = the runs dir (default `runs/traces`).
- **out**:
  - `<base>/<catalog_id>/<run_id>/events.jsonl` — one JSON line per event; UTF-8; newline-terminated.
  - `<base>/ledger/global.jsonl` — same shape, for events without a run correlation (e.g. `contract.catalog.compiled`).
  - Return value: the `Event` written or already present.
- **event**: consume none; emit none of its own — this primitive is the transport every other `Contract.event` clause writes through.
- **failure**:
  - `name ∉ EVENT_NAMES` → `ValueError`; no write.
  - Non-serialisable payload → `TypeError`; no write.
  - `OSError` on append → re-raise; caller handles.
  - Duplicate `event_id` → no write; returns the existing row (not an error).
  - Undecodable line while reading → skipped and counted in `ledger_corrupt_lines`; reading continues.
- **success** (`pytest tests/test_analyzer_ledger.py`):
  - Emitting the same `(name, correlation, payload)` twice leaves exactly one line in the target file and returns equal `Event`s.
  - An event correlated with `(catalog_id, run_id)` lands in that run's `events.jsonl`; one without lands in `ledger/global.jsonl`.
  - `read_events(base, cid, rid)` returns the rows of that run only, in file order.
  - `emit(..., name="not.a.declared.event")` raises `ValueError` and creates no file.
  - 8 threads emitting distinct events into one file produce 8 well-formed lines (no interleaving).

## Observation

- `events_total[name]` = count of lines across `**/events.jsonl` + `ledger/global.jsonl`, sliced by `name`. Every name in `EVENT_NAMES` that stays at zero after a `seed` + `analyze` cycle is an emitter that is declared but not wired.
- `event_dedup_rate` = idempotent skips ÷ `emit` calls per process. A high rate is expected for re-runs of `analyze`; a high rate on `lm.inference.*` means a producer is re-emitting identical inferences instead of recording new ones.
- `dangling_event_names` = names appearing in any `docs/tasks/*.md` `Contract.event` clause that are not in `EVENT_NAMES` and not on the legacy allowlist above. Gate: zero for docs introduced in the 2026-09-16 console batch.
- `ledger_corrupt_lines` = undecodable lines skipped by `read_events`; non-zero means a writer bypassed `emit`.
- `ledger_bytes_total` = `du -b` over all ledger files; bounds what a console poll re-reads.

Related: [[analyzer_trace_store]] (sibling append-only substrate whose content-hash idempotency this copies), [[analyzer_analyze]], [[console_operator]], [[factory_pipeline]].
