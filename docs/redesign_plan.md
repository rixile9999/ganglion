# Ganglion redesign — migration plan

## 1. Why

This plan operationalises [`docs/goal/goal.md`](goal/goal.md) and the
companion design note [`docs/factory_design.md`](factory_design.md): the
redesign reorganises today's `ganglion/{dsl,schema,runtime,eval,bfcl,factory}/`
into four target namespaces — `contract/` (Module 3: schemas + DSL),
`lm/` (Module 1: model production), `analyzer/` (Module 2: statistical
analysis + compiler-correction), and `benchmarks/` (consumers that emit
traces). The spec layer (task docs under `docs/tasks/`) lands first, in the
same batch as this document. Code migration follows in five further batches,
each behind deprecation shims so `pytest -q` stays green throughout.

## 2. Target layout

```
ganglion/
├── contract/               # Module 3 — schemas, DSL, validation
│   ├── types.py
│   ├── arg_spec.py
│   ├── tool_spec.py
│   ├── catalog.py
│   ├── schema_compiler.py
│   ├── parse.py
│   └── builtins/
│       ├── iot_light.py
│       ├── home_iot_20.py
│       └── smart_home_50.py
├── lm/                     # Module 1 — model production
│   ├── client.py
│   ├── dashscope.py
│   ├── rules.py
│   ├── local_hf.py
│   ├── grammar.py
│   ├── prompts.py
│   ├── synth/{teacher,strategies,pipeline}.py
│   └── finetune/{config,data,sft,dpo}.py
├── analyzer/               # Module 2 — statistical-analysis + compiler-correction
│   ├── trace.py
│   ├── taxonomy.py
│   ├── metrics.py
│   ├── rules.py
│   ├── repair.py
│   ├── verifier.py
│   └── reports.py
├── benchmarks/             # consumers — emit traces
│   ├── iot/{dataset,grader,runner}.py
│   └── bfcl/{loader,grader,case_catalog,runner}.py
├── factory.py              # composite orchestrator
└── cli.py                  # `python -m ganglion …` dispatch
```

## 3. File-by-file migration map

Seven tables with columns `Today | Target | Notes` — one per existing
`ganglion/` source namespace (3.1–3.6), plus 3.7 for the
`runs/factory_*/*.py` scripts that get promoted to first-class library
modules.

### 3.1 `ganglion/dsl/` → `ganglion/contract/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/dsl/tool_spec.py` | `ganglion/contract/tool_spec.py` | Future split: move `ArgSpec` subclasses (`EnumArg`, `IntArg`, `StringArg`, `TimeArg`, `RawArg`) into `contract/arg_spec.py`. Defer to a follow-up batch. |
| `ganglion/dsl/catalog.py` | `ganglion/contract/catalog.py` | Verbatim move. `render_json_dsl()` and `render_openai_tools()` stay on `Catalog`. |
| `ganglion/dsl/compiler.py` | `ganglion/contract/schema_compiler.py` | Rename. `compile_tool_calling_schema` and `CompiledToolMapper` stay as the public surface. |
| `ganglion/dsl/types.py` | `ganglion/contract/types.py` | Verbatim. `ToolCall`, `ActionPlan`. |
| `ganglion/dsl/emitter.py` | `ganglion/contract/emitter.py` | Verbatim (legacy emitter; not consumed by the new `factory.py` composite, kept for back-compat with older scripts in `runs/`). |
| `ganglion/dsl/validator.py` | `ganglion/contract/parse.py` | Merge with `json_extract.py` — the strict and lenient parsers belong in one module. |
| `ganglion/dsl/json_extract.py` | `ganglion/contract/parse.py` | Merge into the parse module (`parse_json_dsl_lenient`, strategies). |
| `ganglion/dsl/__init__.py` | (shim) | Re-exports from `ganglion.contract`. See §5 for the shim contract. |

### 3.2 `ganglion/schema/` → `ganglion/contract/builtins/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/schema/iot_light.py` | `ganglion/contract/builtins/iot_light.py` | Verbatim. The filename stays short even though the registered tier slug is `iot_light_5` via `get_catalog`. |
| `ganglion/schema/home_iot.py` | `ganglion/contract/builtins/home_iot_20.py` | Verbatim contents; **filename rename** to align with the registry slug `home_iot_20`. |
| `ganglion/schema/smart_home.py` | `ganglion/contract/builtins/smart_home_50.py` | Verbatim contents; **filename rename** to align with the registry slug `smart_home_50`. |
| `ganglion/schema/__init__.py` | (shim) | Keep `get_catalog`, `TIERS`; re-import from `ganglion.contract.builtins`. See §5. |

### 3.3 `ganglion/runtime/` → `ganglion/lm/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/runtime/qwen.py` (clients only) | `ganglion/lm/dashscope.py` | Split: the three clients (`QwenJSONDSLClient`, `QwenFreeformJSONDSLClient`, `QwenNativeToolClient`) live here. System prompts → `lm/prompts.py`. Repair loop → `analyzer/repair.py`. |
| `ganglion/runtime/qwen.py:run_dsl_with_repair` + `RepairConfig` | `ganglion/analyzer/repair.py` | Module-boundary cross — the repair loop is "compiler-correction" (Module 2), not model production. See [[analyzer_repair_policy]]. |
| `ganglion/runtime/rules.py` | `ganglion/lm/rules.py` | Verbatim. `RuleBasedJSONDSLClient` stays an `iot_light_5`-only stand-in for offline tests. |
| `ganglion/runtime/executor.py` | (stays for now; future home `lm/serving/executor.py`) | Out of scope for this redesign; the mock executor is only used in tests. |
| `ganglion/runtime/types.py` (`ModelResult`) | `ganglion/lm/client.py` | Co-locate `ModelResult` with the `ModelClient` protocol. |
| `ganglion/runtime/__init__.py` | (shim) | Re-exports from `ganglion.lm` + `ganglion.analyzer.repair`. See §5. |

### 3.4 `ganglion/eval/` → `ganglion/benchmarks/iot/` + `ganglion/analyzer/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/eval/runner.py` | Split: tier loop → `benchmarks/iot/runner.py`; BFCL loop → `benchmarks/bfcl/runner.py`; CLI dispatch → `ganglion/cli.py`. | Largest single split in the migration. The `argparse` dispatch becomes the `python -m ganglion` entrypoint. |
| `ganglion/eval/dataset.py` | `ganglion/benchmarks/iot/dataset.py` | Verbatim — IoT dataset loader. |
| `ganglion/eval/metrics.py` | `ganglion/analyzer/metrics.py` | **Cross-module migration.** This is THE consolidation of today's three eval-summary code paths (`eval/metrics.py`, `eval/bfcl_runner.summarize_bfcl`, ad-hoc summaries in `runs/factory_*/*.py`). |
| `ganglion/eval/bfcl_runner.py` | `ganglion/benchmarks/bfcl/runner.py` + `ganglion/benchmarks/bfcl/case_catalog.py` | Split: `run_bfcl`/`summarize_bfcl` → runner; `build_case_catalog` → its own module (it's the per-case compile that distinguishes BFCL from IoT). |
| `ganglion/eval/scaling.py` | `ganglion/analyzer/reports.py` (or a small helper sibling) | The DSL-vs-native catalog-size measurement is a one-off report; merge into `reports.py`. |
| `ganglion/eval/__init__.py` | (shim) | Re-exports. See §5. |

### 3.5 `ganglion/bfcl/` → `ganglion/benchmarks/bfcl/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/bfcl/loader.py` | `ganglion/benchmarks/bfcl/loader.py` | Verbatim. Reads `examples/bfcl/v4/sample/*.jsonl`. |
| `ganglion/bfcl/grader.py` | `ganglion/benchmarks/bfcl/grader.py` | Verbatim. The upstream AST checker port. |
| `ganglion/bfcl/__init__.py` | (shim) | Re-exports. See §5. |

### 3.6 `ganglion/factory/` → `ganglion/lm/` + `ganglion/analyzer/`

| Today | Target | Notes |
|---|---|---|
| `ganglion/factory/customer/ingest.py` | `ganglion/lm/synth/pipeline.py` (input-shape resolution) | Trivial wrapper around `schema_compiler.compile_tool_calling_schema`; folds in. |
| `ganglion/factory/customer/synth.py` | `ganglion/lm/synth/pipeline.py` | Body of the synthesise + gate loop. |
| `ganglion/factory/customer/train_lora.py` | `ganglion/lm/finetune/{config,data,sft}.py` | Split: config dataclass → `config.py`; data-prep → `data.py`; train loop → `sft.py`. |
| `ganglion/factory/customer/eval.py` | `ganglion/lm/local_hf.py` (`generate_dsl`) + (deprecate the summarise wrapper — see [[analyzer_metrics]]) | The local-HF generate path lives in `lm/`; the per-run aggregation lives in `analyzer/metrics.py`. |
| `ganglion/factory/customer/verifier.py` | `ganglion/analyzer/verifier.py` | Verifier is a Module-2 compiler-correction concern. See [[analyzer_verifier]]. |
| `ganglion/factory/customer/__init__.py` | (deprecate) | Re-exports during transition; removed in Batch 6. |
| `ganglion/factory/grammar/catalog_to_xgrammar.py` | `ganglion/lm/grammar.py` | Co-locate grammar compilation with the logits processor. |
| `ganglion/factory/grammar/xgrammar_processor.py` | `ganglion/lm/grammar.py` | Merge into the same module. |
| `ganglion/factory/grammar/__init__.py` | (deprecate) | Removed in Batch 6. |
| `ganglion/factory/prompts/synth_templates.py` | `ganglion/lm/synth/strategies.py` | Tool-anchored synthesis strategy. |
| `ganglion/factory/prompts/__init__.py` | (deprecate) | Removed in Batch 6. |
| `ganglion/factory/__init__.py` | (deprecate; the new factory is `ganglion/factory.py` composite) | Module → file rename. The composite at `ganglion/factory.py` wires the event bus. |

### 3.7 `runs/factory_*/*.py` scripts → first-class library modules

| Today | Target | Notes |
|---|---|---|
| `runs/factory_bfcl/post_correction.py` (R1–R11) | `ganglion/analyzer/rules.py` | Promoted to first-class rule synthesis; see [[analyzer_rule_synthesis]]. |
| `runs/factory_bfcl/analyze_failures.py` | `ganglion/analyzer/taxonomy.py` | Promoted to first-class failure taxonomy. |
| `runs/factory_bfcl/apply_post_corr_holdout.py` | (consumed by [[factory_pipeline]] composite, no standalone library) | Driven from the composite — the apply step is orchestration, not a library primitive. |
| `runs/factory_bfcl/apply_post_corr_to_phase3.py` | (consumed by [[factory_pipeline]] composite, no standalone library) | Same as above. |
| `runs/factory_bfcl/teacher_augment.py` | `ganglion/lm/synth/strategies.py` (paraphrase + synth strategies) | Promoted. |
| `runs/factory_bfcl/bfcl_bootstrap.py` | `ganglion/lm/synth/strategies.py` (self-bootstrap strategy) | Promoted. |
| `runs/factory_bfcl/bfcl_sft.py`, `bfcl_sft_v2.py` | `ganglion/lm/finetune/sft.py` | Promoted; v1/v2 differences become config flags. |
| `runs/factory_bfcl/bfcl_dpo.py` | `ganglion/lm/finetune/dpo.py` | Promoted. |
| `runs/factory_bfcl/bfcl_eval.py` | `ganglion/lm/local_hf.py` + `ganglion/analyzer/metrics.py` | Generate path → `lm/`; aggregation → `analyzer/`. |
| `runs/factory_phase2/dpo_train.py`, `dpo_pairs.py` | `ganglion/lm/finetune/dpo.py` | Promoted; `dpo_pairs.py` becomes a helper in the same module. |
| `runs/factory_phase2/grammar_ablation.py` | `ganglion/lm/grammar.py` (ablation helper) + [[factory_evaluation]] composite | Lives in `lm/grammar` with an ablation entrypoint. |
| `runs/factory_phase2/paraphrase_intents.py`, `paraphrase_ood.py`, `self_bootstrap.py` | `ganglion/lm/synth/strategies.py` | Promoted; each becomes a named strategy. |
| `runs/factory_phase2/recompute_with_corrections.py`, `recompute_with_defaults.py` | `ganglion/analyzer/rules.py` (apply path) | Re-aggregation passes that apply rules; absorbed by the rules module. |
| `runs/factory_phase2/train_v2_cuda.py` | `ganglion/lm/finetune/sft.py` (CUDA-specific config) | Promoted; CUDA path becomes a config preset. |
| `runs/*/aggregate.py`, `runs/bfcl/aggregate.py`, `runs/factory_bfcl/aggregate.py` | `ganglion/analyzer/reports.py` | Cross-phase aggregators all collapse into one report module. |

## 4. Batch ordering (which goes first, why)

1. **This batch (current)**: spec layer — all task docs under `docs/tasks/`
   plus this `docs/redesign_plan.md` — AND Module 3 code migration
   (`ganglion/dsl/` → `ganglion/contract/` + `ganglion/schema/` →
   `ganglion/contract/builtins/`) with deprecation shims. No
   internal-importer rewrites; shims keep everything green.
2. **Batch 2**: Module 1 code migration (`ganglion/runtime/` + relevant parts
   of `ganglion/factory/grammar` and `ganglion/factory/prompts` →
   `ganglion/lm/`). Internal imports under `ganglion/lm/*` use the new names;
   outside callers still go through shims if they exist.
3. **Batch 3**: Module 2 code migration (`ganglion/eval/metrics.py`,
   `ganglion/runtime/qwen.py:run_dsl_with_repair`,
   `ganglion/factory/customer/verifier.py`, plus
   `runs/factory_bfcl/post_correction.py` and `analyze_failures.py` →
   `ganglion/analyzer/`). This is the largest single batch because it
   consolidates the goal §2 surface (statistical analysis + compiler
   correction).
4. **Batch 4**: Benchmark migration (`ganglion/bfcl/` + IoT half of
   `ganglion/eval/runner.py` + `ganglion/eval/dataset.py` →
   `ganglion/benchmarks/`). Introduces structured trace event emission so
   downstream `analyzer/` consumes events instead of direct return values.
5. **Batch 5**: Composites (`ganglion/factory.py`, `ganglion/cli.py`). Wires
   the event bus; `python -m ganglion` dispatches to benchmark runners.
6. **Batch 6 (cleanup)**: Remove shims under `ganglion/dsl/`,
   `ganglion/schema/`, `ganglion/bfcl/`, `ganglion/eval/`,
   `ganglion/runtime/`, `ganglion/factory/`. Rewrite internal imports to
   canonical paths. Drop legacy `RLM_*` env-var fallbacks. Sync `QWEN.md` with
   `CLAUDE.md`.

## 5. Deprecation policy

- Each shim emits `DeprecationWarning` once on import (use
  `warnings.warn(..., DeprecationWarning, stacklevel=2)` guarded by a
  module-level `_warned` flag).
- Shims stay for at least one full batch cycle after their target ships.
  Batch 6 removes them.
- Tests pass with shims throughout (verified by `pytest -q` after each batch).
- Public re-export surface of a shim must match the **export list at the time
  of migration** — no silent additions, no silent removals.
- The shim file documents the canonical target in its module docstring so
  IDEs surface it on hover.

## 6. Breaking-change checklist

For this batch (spec + Module 3 migration):

- ☐ Any change to public Python API? **No.** Shims preserve all current
  `from ganglion.dsl import …` and `from ganglion.schema import …` imports.
- ☐ Any change to CLI? **No.** `python -m ganglion.eval.runner …` continues
  to work; the CLI dispatch move is deferred to Batch 5.
- ☐ Any change to `runs/` artifact format? **No.** Artifact paths
  (`runs/bfcl/*.json`, `runs/factory_*/`) and JSON shapes are preserved.
- ☐ Any change to `pyproject.toml`? **No** —
  `[tool.setuptools.packages.find] include = ["ganglion*"]` already covers
  the new sub-packages.
- ☐ Any change to env vars? **No.** `DASHSCOPE_API_KEY`, `GANGLION_MODEL`,
  `DASHSCOPE_BASE_URL`, `GANGLION_ENABLE_THINKING`, and the legacy `RLM_*`
  fallbacks all continue to work until Batch 6.

## 7. Risks

- **Shim sprawl**: if Batch 6 keeps slipping, the shims become permanent.
  Mitigation: track in `docs/tasks/legacy/README.md` as a follow-up item,
  with an owner and a target deadline.
- **Test-suite gaps**: `tests/factory/*` paths reference `ganglion.factory.*`
  directly; Batch 3 and Batch 5 will need test-path updates (or the
  `ganglion/factory/__init__.py` shim must re-export the migrated symbols
  during the transition).
- **Module-boundary cross is non-obvious**: `run_dsl_with_repair` moving out
  of `runtime/` (Module 1) into `analyzer/` (Module 2) will surprise
  reviewers reading the diff. The PR description must call this out
  explicitly and cite [[analyzer_repair_policy]].
- **Filename renames (`home_iot.py` → `home_iot_20.py`, `smart_home.py` →
  `smart_home_50.py`)**: the `git mv` must be a single commit so `git log
  --follow` keeps working. Don't combine with content edits.
- **Documentation drift between `CLAUDE.md` and `QWEN.md`**: both will need a
  same-touch update after Batch 6.
- **`runs/factory_*/` script promotion**: scripts in `runs/` are historically
  one-off; promoting them to library modules means tightening
  signatures, adding type hints, and writing tests. Budget for this in
  Batch 3 explicitly.

## 8. Tracking

Each batch lands behind a single PR titled `feat(redesign): batch N —
<theme>`. The PR description references this plan and the relevant task
docs. The progress table below is updated as batches merge:

| Batch | Theme | PR | Status |
|---|---|---|---|
| 1 | Spec layer + Module 3 (`contract/`) | — | in progress |
| 2 | Module 1 (`lm/`) | — | not started |
| 3 | Module 2 (`analyzer/`) | — | not started |
| 4 | Benchmarks (`benchmarks/`) | — | not started |
| 5 | Composites (`factory.py`, `cli.py`) | — | not started |
| 6 | Cleanup (remove shims, sync docs) | — | not started |
