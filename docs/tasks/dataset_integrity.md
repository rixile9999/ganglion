[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# dataset_integrity

Deterministic CI guarantee that every checked-in `examples/<tier>/dataset.jsonl` row remains parseable against its tier's `Catalog` and uniquely identified.

## Role

Verify that the checked-in dataset is structurally consistent with the current `ganglion/schema/*.py` catalogs and that gold expectations execute under `Catalog.parse_json_dsl`.

## Scope

- **in-scope**:
  - Every row in `examples/*/dataset.jsonl`: `expected` parses to a non-empty `ActionPlan` via the matching tier catalog.
  - Per-tier distribution invariants exported by the dataset's `generate_dataset.py` (e.g. `TARGET_COUNTS`).
  - Uniqueness of `case.id` and `case.prompt`.
- **out-of-scope**:
  - Regenerating datasets — owned by `examples/<tier>/generate_dataset.py`.
  - Editing any `expected` field — datasets are SSOT for gold behavior.
  - Validating prompt naturalness, diversity, or coverage.
  - Cross-tier reasoning (each tier's dataset is checked against its own catalog only).
  - API-backed eval correctness — that is [`eval_smoke_guard`](./eval_smoke_guard.md).
- **on violation**: if a row fails to parse, **stop** — do not patch the row. Emit `dataset_integrity_failed(file, row_id, error)` and surface as red CI. Row corrections are a human authoring task.

## Procedure

```
on push|pull_request affecting examples/**/dataset.jsonl
                              | ganglion/schema/**
                              | ganglion/dsl/**:
    for each examples/<tier>/dataset.jsonl:
        catalog ← get_catalog(<tier>)
        for each row:
            plan ← catalog.parse_json_dsl(row["expected"])
            assert plan.calls is non-empty
            assert every plan.calls[i].action ∈ catalog.tool_names
        assert |rows| == sum(TARGET_COUNTS.values())   # distribution invariant
        assert |{row.id for row in rows}| == |rows|    # id uniqueness
        assert |{row.prompt for row in rows}| == |rows|  # prompt uniqueness
on any assertion failure:
    record (file, row_index_or_invariant, error)
on records non-empty → emit `dataset_integrity_failed(sha, records)` and exit non-zero
else                 → emit `dataset_integrity_passed(sha)`
```

## Contract

- **in**: dataset JSONL files; tier→catalog mapping via `ganglion.schema.get_catalog`.
- **out**: `pytest tests/test_dataset_integrity.py` exit code; GitHub status check `dataset-integrity`.
- **event**: consume `push | pull_request`; emit `dataset_integrity_passed(sha) | dataset_integrity_failed(sha, records)` (exactly one per SHA).
- **failure**:
  - Parse error → `dataset_integrity_failed(cause=parse, records=[…])`.
  - Unknown tier in JSONL filename → `dataset_integrity_failed(cause=unknown_tier)`.
  - Catalog import error → `dataset_integrity_failed(cause=catalog_import)`; no retry.
- **success**: pytest exit 0 on every row of every tracked tier; exactly one event emitted per SHA.

## Observation

- `dataset_integrity_pass_rate` = passed runs ÷ total runs on `main` (rolling 30d).
- `failed_row_count` = sum of records per failed run.
- `tracked_tier_count` = number of `examples/*/dataset.jsonl` files discovered — drift in this number without a doc update is itself a signal.
