# Legacy task specs (superseded)

These task docs were authored against the **pre-redesign** layout (`ganglion/dsl/`, `ganglion/schema/`, `ganglion/runtime/`, `ganglion/eval/`, `ganglion/bfcl/`, `ganglion/factory/`). The redesign replaced today's surface with three peer modules — `contract/`, `lm/`, `analyzer/` — and a `benchmarks/` consumer namespace; see [`../../goal/goal.md`](../../goal/goal.md), [`../../factory_design.md`](../../factory_design.md), and [`../README.md`](../README.md).

The legacy docs are kept as historical reference and to keep the M0–M5' / Phase 1–3 reports under `runs/` reproducible.

| Legacy doc | Superseded by | Status |
|---|---|---|
| [tool_schema_compiler.md](./tool_schema_compiler.md) | [`../contract_schema_compiler.md`](../contract_schema_compiler.md) | Live — implementation in `ganglion/dsl/compiler.py` (will move to `ganglion/contract/schema_compiler.py` in Batch 1). |
| [null_action_contract.md](./null_action_contract.md) | [`../contract_null_action.md`](../contract_null_action.md) | Live — contract on `Catalog.allow_empty_calls`. |
| [external_benchmark_bfcl.md](./external_benchmark_bfcl.md) | [`../benchmark_bfcl.md`](../benchmark_bfcl.md) | Live — implementation in `ganglion/bfcl/` + `ganglion/eval/bfcl_runner.py` (will move to `ganglion/benchmarks/bfcl/` in Batch 4). |
| [factory_bfcl.md](./factory_bfcl.md) | [`../factory_pipeline.md`](../factory_pipeline.md) (composite) | Atomic one-shot replay. The composite formalises the iterated loop. |
| [factory_bfcl_phase3.md](./factory_bfcl_phase3.md) | Folded into [`../lm_data_synth.md`](../lm_data_synth.md) + [`../lm_finetune.md`](../lm_finetune.md) + [`../factory_pipeline.md`](../factory_pipeline.md) | Phase-3 augmentation / DPO recipes are now atomic primitives composable from factory_pipeline. |
| [dataset_integrity.md](./dataset_integrity.md) | Open — to be reworked once benchmarks emit traces; the integrity predicate becomes a [`../benchmark_iot.md`](../benchmark_iot.md) precondition. | Deferred. |
| [catalog_spec_sync.md](./catalog_spec_sync.md) | [`../contract_catalog.md`](../contract_catalog.md) (data section) + [`../analyzer_rule_synthesis.md`](../analyzer_rule_synthesis.md) (auto-proposal side) | Active — drift detection role split between contract authoring discipline and analyzer feedback. |
| [eval_smoke_guard.md](./eval_smoke_guard.md) | [`../factory_evaluation.md`](../factory_evaluation.md) + future CI guard task (deferred) | Deferred. |
| [report_freshness.md](./report_freshness.md) | Active spec; deferred follow-up. Not yet ported to new doc set. Marker convention `<!-- src:...#pointer -->` is still relevant; [`../analyzer_metrics.md`](../analyzer_metrics.md) writes markers into its reports. | Deferred. |
| [release_health.md](./release_health.md) | [`../factory_pipeline.md`](../factory_pipeline.md) aggregated verdict | Composite shape preserved in the new factory_pipeline. |

## Why "supersede & archive" rather than "refresh in place"
Per the user's choice in the redesign planning: clean break, no in-place rewrites mixed with new content. New docs sit alongside the new module names (`contract_*`, `lm_*`, `analyzer_*`); legacy docs stay frozen as historical reference.

## When can these be deleted?
After Batch 6 of `docs/redesign_plan.md` (shim removal), if the codebase has fully migrated and no internal docs reference these files, the `legacy/` directory may be deleted. Keep at least one cycle for historical record.
