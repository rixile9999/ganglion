# Ganglion task specs

This directory holds the task specs for Ganglion's three-module architecture (see [`docs/goal/goal.md`](../goal/goal.md) and [`docs/factory_design.md`](../factory_design.md)). Every doc follows the six-section template from [`task_principle`](../agent-forge/task_principle.md) — `Role / Scope / Procedure / Contract / Observation` — with a non-empty `out-of-scope`.

**Specs are the SSOT. Implementations follow them, not the other way around.** Composition rules for atomic vs. composite tasks live in [`workflow_principle`](../agent-forge/workflow_principle.md).

**2026-09-16 console batch.** The operator console ([`docs/refine_proposal.md`](../refine_proposal.md)) added thirteen docs and revised nine. New: [analyzer_catalogs](./analyzer_catalogs.md), [contract_describe](./contract_describe.md), [contract_patch_apply](./contract_patch_apply.md), [lm_model_registry](./lm_model_registry.md), [lm_request_serve](./lm_request_serve.md), [analyzer_event_ledger](./analyzer_event_ledger.md), [analyzer_label_store](./analyzer_label_store.md), [analyzer_run_manifest](./analyzer_run_manifest.md), [analyzer_compare](./analyzer_compare.md), [analyzer_patch_decision](./analyzer_patch_decision.md), [analyzer_correction_attribution](./analyzer_correction_attribution.md), [analyzer_analyze](./analyzer_analyze.md) (composite), [console_operator](./console_operator.md) (composite). Revised: [analyzer_trace_store](./analyzer_trace_store.md), [analyzer_repair_policy](./analyzer_repair_policy.md), [analyzer_failure_taxonomy](./analyzer_failure_taxonomy.md), [analyzer_rule_synthesis](./analyzer_rule_synthesis.md), [analyzer_metrics](./analyzer_metrics.md), [lm_client](./lm_client.md), [contract_catalog](./contract_catalog.md), [benchmark_iot](./benchmark_iot.md), [benchmark_bfcl](./benchmark_bfcl.md). Every declared event in the batch is a row in the append-only ledger (`analyzer_event_ledger.EVENT_NAMES`); the run bundle (`manifest.json` + sidecars under `runs/traces/<catalog_id>/<run_id>/`) is the list SSOT. Docs marked `Status: spec, implementation in progress (2026-09-16)` land code in the same cycle.

**Local models (2026-09-16 addendum to the console batch).** The model picker is not limited to API endpoints. [lm_model_registry](./lm_model_registry.md) adds `kind: local_hf` (`provider: local`; `base_model` = HF hub id or path, optional `adapter_dir`, plus `device` / `dtype` / `max_new_tokens` / `grammar_mask`), `availability()` (lazy — `torch` is never imported at registry load) and `discover_adapters()` (every `adapter_config.json` under `runs/` becomes `local:<dir>`); [lm_client](./lm_client.md) specifies `LocalHFClient` (in-process transformers + PEFT LoRA, the SFT-identical `SYSTEM_PROMPT_TEMPLATE`, strict-then-lenient parse, `ModelOutputError` with raw preserved) and the process-wide `load_local_model` / `unload_local_model` / `local_model_status` lifecycle in `ganglion/lm/local_hf.py`; [console_operator](./console_operator.md) exposes it as `GET /api/models/{id}/health`, `POST /api/models/{id}/load` (through the single writer, 503 when unavailable), `POST /api/models/{id}/unload`, and auto-loads on the first `POST /api/chat` (`model_load_seconds` in that response). A locally *served* model (vLLM) stays `kind: openai_compat, provider: vllm`; `xgrammar` is optional and `grammar_mask` is ignored with a warning when it is absent. The checked-in `configs/models.yaml` ships `qwen3-0.6b@local` and `qwen3-1.7b@local`.

## Module 3 — `ganglion/contract/`

Common schema/DSL surface. Built first because it's a leaf in the DAG; both `lm/` and `analyzer/` depend on it.

| Doc | Purpose |
|---|---|
| [contract_catalog](./contract_catalog.md) | `Catalog` / `ToolSpec` / `ArgSpec` contract surface. Dual rendering (DSL + OpenAI tools) from one SSOT. |
| [contract_schema_compiler](./contract_schema_compiler.md) | Compile OpenAI / MCP / bare / BFCL schemas into a `Catalog`. Live; supersedes [`legacy/tool_schema_compiler`](./legacy/tool_schema_compiler.md). |
| [contract_null_action](./contract_null_action.md) | `{"calls": []}` valid iff `Catalog.allow_empty_calls=True`. Live; supersedes [`legacy/null_action_contract`](./legacy/null_action_contract.md). |
| [contract_tier_home_assistant](./contract_tier_home_assistant.md) | `home_assistant_4` tier: `iot_light_5` projected onto Home Assistant's Assist API tools (`HassTurnOn/Off`, `HassLightSet`, `GetLiveContext`) + derived dataset. Not on the 5/20/50 curve. |
| [contract_describe](./contract_describe.md) | `Catalog.describe()` JSON snapshot + `catalog_fingerprint()` (`cf-…`); the identity every trace, label and manifest stamps. |
| [contract_patch_apply](./contract_patch_apply.md) | Pure `apply_patch(catalog, patch) -> Catalog` preview for `RulePatch` operations (incl. `retire_rule`), `strip_hooks` for F⁰, `PatchNotApplicableError`. Never mutates a builtin. |

## Module 1 — `ganglion/lm/`

Language-model production: data synthesis, training, inference.

| Doc | Purpose |
|---|---|
| [lm_client](./lm_client.md) | `ModelClient` protocol + adapters (dashscope DSL JSON / freeform / native, rules, local HF). Emits `lm.inference.{completed,failed}` per case. |
| [lm_grammar_mask](./lm_grammar_mask.md) | Catalog → JSON Schema → XGrammar logits-processor; mask on/off ablation. |
| [lm_finetune](./lm_finetune.md) | LoRA SFT + DPO graded-reward against a Catalog. Training-prompt parity invariant with `lm/prompts.py`. |
| [lm_data_synth](./lm_data_synth.md) | Teacher-driven synthesis anchored to a Catalog. Strategies: tool-anchored / multi-tool / adversarial / abstain. |
| [lm_model_registry](./lm_model_registry.md) | `configs/models.yaml` → `ModelSpec` / `Registry`, provider quirks (dashscope / vllm / openai / local HF), `build_client_from_spec`, `model_fingerprint`, `cli --model`. |
| [lm_request_serve](./lm_request_serve.md) | Serve one interactive request: resolve catalog + model, invoke, emit tool calls, preserve raw on failure; F⁰/Fᴷ `hooks_diff` per `ServeResult`. |

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
| [analyzer_event_ledger](./analyzer_event_ledger.md) | Append-only `events.jsonl` / `ledger/global.jsonl` rows for every declared event; content-hash idempotent; the closed `EVENT_NAMES` vocabulary. Transport, not a bus. |
| [analyzer_label_store](./analyzer_label_store.md) | `LabelRecord` sidecar (`labels.jsonl`), `resolve_gold` as the single gold join, family / zone assignment, corrected-SFT export shaped like synth `write_jsonl`. No DPO pairs. |
| [analyzer_run_manifest](./analyzer_run_manifest.md) | Immutable `manifest.json` per run + `summary.json` / `report.md` bundle; `list_runs` manifest scan is the list SSOT. |
| [analyzer_compare](./analyzer_compare.md) | Paired per-case diff of two runs: transition matrix, paired-bootstrap CI on ΔEM, `manifest_diff`, refusal on mismatched dataset / metric / decoding. |
| [analyzer_patch_decision](./analyzer_patch_decision.md) | `patch_decisions.jsonl` ledger (`blind` → `after_preview` → `ported`); `precision_summary`; accept changes nothing, porting is an explicit `commit_sha`. |
| [analyzer_correction_attribution](./analyzer_correction_attribution.md) | F⁰ / Fᴷ re-parse of `attempts[0]`, rescue / regression per `(tool, hook_kind)`, conservative vs rewriting, retire predicate (`n_active ≥ 30`, CP95 upper < 0.10) → `retire_rule` patches. |
| [analyzer_catalogs](./analyzer_catalogs.md) | `catalog_id` → `Catalog`: builtin tiers, `compiled/<sha12>` persisted as `catalog.json` (submission order, never `sort_keys`), `bfcl/*` unresolvable by design. |

One-run composition over these primitives is [analyzer_analyze](./analyzer_analyze.md), listed under Composites below.

## Consumers — `ganglion/benchmarks/`, `ganglion/console/`

Not peer modules; they consume `Catalog` + `ModelClient` and emit traces into `analyzer_trace_store`.

| Doc | Purpose |
|---|---|
| [benchmark_iot](./benchmark_iot.md) | `iot_light_5` / `home_iot_20` / `smart_home_50` datasets + grader + runner. |
| [benchmark_bfcl](./benchmark_bfcl.md) | BFCL v4 single-turn loader + AST grader + per-case `Catalog` compile. Supersedes [`legacy/external_benchmark_bfcl`](./legacy/external_benchmark_bfcl.md). |
| [benchmark_selection](./benchmark_selection.md) | Decision record (2026-09-09): which external benchmarks the local-model path runs. BFCL v4 retained as controlled baseline; MCPMark and home-assistant-datasets adopted; MCP-Atlas deferred; LiveMCPBench rejected. |
| [console_operator](./console_operator.md) | The `web/` + `/api/*` surface over the run bundles. Composite — also listed below; `ganglion/console/` is a consumer package alongside `benchmarks/`. |

## Composites — orchestrators

| Doc | Aggregates | Outer signal |
|---|---|---|
| [factory_pipeline](./factory_pipeline.md) | All three modules + benchmarks | `factory.pipeline.iterated(catalog_id, iteration, eval_summary)` |
| [factory_evaluation](./factory_evaluation.md) | benchmark + analyzer (measure-only) | `factory.evaluation.completed(client_id, catalog_id, benchmark_id, summary_path)` |
| [analyzer_analyze](./analyzer_analyze.md) | taxonomy + rule synthesis + correction attribution over one run | the three primitives' events (`analyzer.failure.classified`, `analyzer.rule.proposed`, `analyzer.correction.attributed`); sidecars in the run dir |
| [console_operator](./console_operator.md) | `web/` + `/api/*` over the run bundles; single writer thread for every file write | consumes the ledger; emits nothing of its own |

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
7. [analyzer_event_ledger](./analyzer_event_ledger.md) and [analyzer_run_manifest](./analyzer_run_manifest.md) — how events and runs become files.
8. [factory_pipeline](./factory_pipeline.md) — how it all comes together; [console_operator](./console_operator.md) — how an operator drives one turn of it.
