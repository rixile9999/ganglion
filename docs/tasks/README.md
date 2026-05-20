# Ganglion task specs

This directory holds the task specs for Ganglion's three-module architecture (see [`docs/goal/goal.md`](../goal/goal.md) and [`docs/factory_design.md`](../factory_design.md)). Every doc follows the six-section template from [`task_principle`](../agent-forge/task_principle.md) — `Role / Scope / Procedure / Contract / Observation` — with a non-empty `out-of-scope`.

**Specs are the SSOT. Implementations follow them, not the other way around.** Composition rules for atomic vs. composite tasks live in [`workflow_principle`](../agent-forge/workflow_principle.md).

## Module 3 — `ganglion/contract/`

Common schema/DSL surface. Built first because it's a leaf in the DAG; both `lm/` and `analyzer/` depend on it.

| Doc | Purpose |
|---|---|
| [contract_catalog](./contract_catalog.md) | `Catalog` / `ToolSpec` / `ArgSpec` contract surface. Dual rendering (DSL + OpenAI tools) from one SSOT. |
| [contract_schema_compiler](./contract_schema_compiler.md) | Compile OpenAI / MCP / bare / BFCL schemas into a `Catalog`. Live; supersedes [`legacy/tool_schema_compiler`](./legacy/tool_schema_compiler.md). |
| [contract_null_action](./contract_null_action.md) | `{"calls": []}` valid iff `Catalog.allow_empty_calls=True`. Live; supersedes [`legacy/null_action_contract`](./legacy/null_action_contract.md). |

## Module 1 — `ganglion/lm/`

Language-model production: data synthesis, training, inference.

| Doc | Purpose |
|---|---|
| [lm_client](./lm_client.md) | `ModelClient` protocol + adapters (dashscope DSL JSON / freeform / native, rules, local HF). Emits `lm.inference.{completed,failed}` per case. |
| [lm_grammar_mask](./lm_grammar_mask.md) | Catalog → JSON Schema → XGrammar logits-processor; mask on/off ablation. |
| [lm_finetune](./lm_finetune.md) | LoRA SFT + DPO graded-reward against a Catalog. Training-prompt parity invariant with `lm/prompts.py`. |
| [lm_data_synth](./lm_data_synth.md) | Teacher-driven synthesis anchored to a Catalog. Strategies: tool-anchored / multi-tool / adversarial / abstain. |

## Module 2 — `ganglion/analyzer/`

Statistical analysis driving the calibration/correction process (goal §2). This module unifies today's scattered surface — `eval/metrics`, `runtime/qwen.run_dsl_with_repair`, `factory/verifier`, and the ad-hoc scripts under `runs/factory_bfcl/` — into one coherent feedback loop.

| Doc | Purpose |
|---|---|
| [analyzer_trace_store](./analyzer_trace_store.md) | Append-only JSONL trace substrate every other analyzer task reads from. |
| [analyzer_failure_taxonomy](./analyzer_failure_taxonomy.md) | Deterministic classification of traces into bucketed `FailureType`. |
| [analyzer_metrics](./analyzer_metrics.md) | Unified summary surface — replaces today's three eval-summary code paths. |
| [analyzer_rule_synthesis](./analyzer_rule_synthesis.md) | **The goal §2 feedback edge:** propose `ToolSpec` patches from failure histograms. |
| [analyzer_repair_policy](./analyzer_repair_policy.md) | Repair-loop policy as a configurable + replayable thing. |
| [analyzer_verifier](./analyzer_verifier.md) | Continuous reward function bound to a Catalog. |

## Consumers — `ganglion/benchmarks/`

Benchmark adapters. Not a peer module; they consume `Catalog` + `ModelClient` and emit traces into `analyzer_trace_store`.

| Doc | Purpose |
|---|---|
| [benchmark_iot](./benchmark_iot.md) | `iot_light_5` / `home_iot_20` / `smart_home_50` datasets + grader + runner. |
| [benchmark_bfcl](./benchmark_bfcl.md) | BFCL v4 single-turn loader + AST grader + per-case `Catalog` compile. Supersedes [`legacy/external_benchmark_bfcl`](./legacy/external_benchmark_bfcl.md). |

## Composites — orchestrators

| Doc | Aggregates | Outer signal |
|---|---|---|
| [factory_pipeline](./factory_pipeline.md) | All three modules + benchmarks | `factory.pipeline.iterated(catalog_id, iteration, eval_summary)` |
| [factory_evaluation](./factory_evaluation.md) | benchmark + analyzer (measure-only) | `factory.evaluation.completed(client_id, catalog_id, benchmark_id, summary_path)` |

Composites consume primitive events from their `Contract.event` clause; they never invoke another task doc by name.

## Legacy specs (superseded)

Pre-redesign task docs live under [`./legacy/`](./legacy/). Each carries a one-line `Superseded by [...]` pointer back into this TOC. They are retained for historical reference and for reproducibility of the M0–M5' / Phase 1–3 reports under `runs/`:

- [`legacy/tool_schema_compiler`](./legacy/tool_schema_compiler.md) → [contract_schema_compiler](./contract_schema_compiler.md)
- [`legacy/null_action_contract`](./legacy/null_action_contract.md) → [contract_null_action](./contract_null_action.md)
- [`legacy/external_benchmark_bfcl`](./legacy/external_benchmark_bfcl.md) → [benchmark_bfcl](./benchmark_bfcl.md)
- [`legacy/factory_bfcl`](./legacy/factory_bfcl.md), [`legacy/factory_bfcl_phase3`](./legacy/factory_bfcl_phase3.md) → [factory_pipeline](./factory_pipeline.md)
- [`legacy/dataset_integrity`](./legacy/dataset_integrity.md), [`legacy/catalog_spec_sync`](./legacy/catalog_spec_sync.md), [`legacy/eval_smoke_guard`](./legacy/eval_smoke_guard.md), [`legacy/report_freshness`](./legacy/report_freshness.md), [`legacy/release_health`](./legacy/release_health.md) — self-maintenance specs, deferred under the redesign.

## Reading order

1. [`docs/goal/goal.md`](../goal/goal.md) — the anchor.
2. [`docs/factory_design.md`](../factory_design.md) — the narrative.
3. [`docs/redesign_plan.md`](../redesign_plan.md) — the migration map.
4. [contract_catalog](./contract_catalog.md) — start with Module 3, the leaf module.
5. [lm_client](./lm_client.md) — Module 1 entry point.
6. [analyzer_trace_store](./analyzer_trace_store.md) — Module 2 substrate; everything else in analyzer reads from it.
7. [factory_pipeline](./factory_pipeline.md) — how it all comes together.
