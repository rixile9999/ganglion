# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Ganglion — *spec-based tool-calling optimisation model factory*

The repo directory is `reflex-language-model` and the Python package
namespace is `ganglion` (this is what `pyproject.toml` packages — see
`[tool.setuptools.packages.find] include = ["ganglion*"]`). The public project
name is **Ganglion**. An earlier draft used a different public name briefly
before reverting to Ganglion; if you spot any stray reference to that earlier
name in docs or commit history, treat it as outdated and align it with
Ganglion.

## Project Purpose

POC testing whether compact Action IRs emitted by an LLM can replace native
tool/function-call schemas in the prompt while preserving accuracy. The
hypothesis is that an IR-style intermediate output reduces input token cost and
latency vs handing the model the full OpenAI tool schema. See `overview.md`
(Korean) and `docs/goal/goal.md` for the broader research goal,
`docs/factory_design.md` for the architecture anchor, and
`docs/poc_verification_report.md` for measured results.

There is also a `QWEN.md` written for a separate agent — its content overlaps with this file; if you update operational facts here, check whether `QWEN.md` needs the same change.

## Common Commands

```bash
pip install -e ".[dev]"                                          # install + dev deps
pytest                                                           # full test suite
pytest tests/test_validator.py::test_set_light_basic            # single test
python examples/iot_light/generate_dataset.py                    # regenerate 500-case dataset
python -m ganglion.cli --llm rules --tier iot_light_5            # offline (no API)
python -m ganglion.cli --llm qwen  --tier iot_light_5            # JSON DSL via Qwen
python -m ganglion.cli --llm qwen-native --tier home_iot_20      # native tool-call baseline
python -m ganglion.cli --llm qwen --repair --repair-max-attempts 1  # repair loop
python -m ganglion.cli --llm qwen --repeat 5                     # repeat each case for latency stats
python -m ganglion.benchmarks.iot.scaling                        # measure DSL vs native catalog sizes
bash runs/m2_run.sh   # batch experiment scripts; outputs JSON into runs/m{2,3,4}/
python runs/aggregate.py                                         # compact tables from runs/*.json

# BFCL v4 single-turn external benchmark (per-case Catalog, M1'~M5')
python -m ganglion.cli --llm qwen        --bfcl simple_python --bfcl-per-category 100
python -m ganglion.cli --llm qwen-native --bfcl all           --bfcl-per-category 100
python -m ganglion.cli --llm qwen        --bfcl irrelevance   --bfcl-allow-empty-calls
python -m ganglion.cli --llm qwen        --bfcl callable      --repair --repair-max-attempts 1
python -m ganglion.cli --llm qwen        --bfcl all --bfcl-output runs/bfcl/<name>_cases.jsonl \
                                          > runs/bfcl/<name>_summary.json
python runs/bfcl/aggregate.py                                    # cross-phase BFCL tables
```

`--llm` choices: `rules` | `qwen` | `qwen-text` | `qwen-thinking` | `qwen-native`. `--tier` choices: `iot_light_5` | `home_iot_20` | `smart_home_50`. `--bfcl` choices: `simple_python` | `multiple` | `parallel` | `parallel_multiple` | `irrelevance` | `callable` (the four non-irrelevance categories) | `all` (all five). `rules` has no BFCL adapter — use one of the `qwen*` clients. The runner prints a JSON summary to stdout; redirect to capture.

## Required Environment

- Python 3.11+
- `DASHSCOPE_API_KEY` for any `qwen*` path. Optional: `GANGLION_MODEL` (default `qwen3.6-plus`), `DASHSCOPE_BASE_URL`, `GANGLION_ENABLE_THINKING`. Legacy `RLM_MODEL` / `RLM_ENABLE_THINKING` are still read as a fallback for older scripts and historical reports.

## Architecture (three-module triad — see `docs/factory_design.md`)

The codebase is organised into three peer modules per `docs/goal/goal.md`,
plus benchmark consumers and a composite orchestrator:

```
ganglion/
├── cli.py                    CLI dispatch (`python -m ganglion.cli`)
├── factory.py                Composite orchestrator (`run_pipeline`)
├── contract/                 Module 3 — schemas, DSL, validation (leaf)
├── lm/                       Module 1 — language-model production
├── analyzer/                 Module 2 — statistical analysis + compiler-correction
└── benchmarks/{iot,bfcl}/    consumers (emit traces)
```

Data flow per case: `user prompt → ModelClient.invoke() → JSON DSL string → Catalog.parse_json_dsl() → ActionPlan → analyzer.metrics.summarize`. The deterministic emission and validation are the load-bearing pieces; the LLM only produces a DSL string.

**Module 3 (`ganglion/contract/`) — Catalog is the compiler boundary.** A `Catalog` (`ganglion/contract/catalog.py`) bundles `ToolSpec`s and renders two artifacts from the same source of truth:
- `render_json_dsl()` — short text appended to the system prompt for DSL paths.
- `render_openai_tools()` — full OpenAI `tools=[...]` schema for the native baseline.

`ganglion/contract/tool_spec.py` defines `ToolSpec` plus arg variants `EnumArg`, `IntArg`, `NumberArg`, `StringArg`, `BoolArg`, `TimeArg`, `RawArg`. `EnumArg.aliases` and `StringArg.aliases` are the canonicalisation hook (e.g. `"거실" → "living"`).

`ganglion/contract/schema_compiler.py:compile_tool_calling_schema` consumes external tool schemas (OpenAI / MCP / bare function schemas / BFCL `function` entries) and produces a `CompiledToolMapper` wrapping a `Catalog`. It normalises BFCL-specific type aliases (`dict→object`, `float→number`, `tuple→array`) at every nesting level and propagates `allow_empty_calls`.

`Catalog.parse_json_dsl()` accepts either a string or mapping and returns an `ActionPlan` of immutable `ToolCall`s. `ActionPlan` equality is value equality, so `result.plan == expected` is the exact-match metric.

**Tiers.** `ganglion/contract/builtins/{iot_light,home_iot,smart_home}.py` each export a module-level `CATALOG`. `ganglion/contract/builtins/__init__.py:get_catalog(tier)` is the registry. The three tiers exist specifically for the M2 scaling experiment (5 / 20 / 50 tools); the same dataset prompts are reused across tiers because the IoT-light intents are a subset of the larger catalogs. BFCL runs bypass this registry entirely — they construct catalogs per case from BFCL's `function` field via the schema compiler.

**Module 1 (`ganglion/lm/`) — language-model production.** Three OpenAI-SDK-against-DashScope clients in `ganglion/lm/dashscope.py`:
- `QwenJSONDSLClient` — uses `response_format={"type": "json_object"}`; goes through `run_dsl_with_repair()` so it supports the M4 repair loop.
- `QwenFreeformJSONDSLClient` — no `response_format`; output is salvaged by `parse_json_dsl_lenient()`. Used for `qwen-text` and `qwen-thinking`.
- `QwenNativeToolClient` — sends `tools=catalog.render_openai_tools()`, then converts the returned `tool_calls` back into the same DSL shape so it shares the validator and equality semantics.

`RuleBasedJSONDSLClient` (`ganglion/lm/rules.py`) is a regex/keyword stand-in matched to the `iot_light_5` catalog only — it lets `pytest` and the offline runner work without API access.

`ganglion/lm/local_hf.py` runs local transformers + PEFT inference. `ganglion/lm/grammar.py` compiles a `Catalog` to JSON Schema → XGrammar logits processor. `ganglion/lm/finetune/sft.py` is the LoRA SFT trainer (TRL `SFTTrainer`, `assistant_only_loss=True`). `ganglion/lm/synth/{ingest,pipeline,strategies}.py` is the teacher-driven data synthesis pipeline.

**Module 2 (`ganglion/analyzer/`) — statistical analysis + compiler-correction (goal §2).**
- `analyzer/trace.py` — `Trace` + `TraceStore` append-only JSONL substrate.
- `analyzer/taxonomy.py` — `FailureType` enum (14 buckets) + `classify()` with priority-ordered matchers.
- `analyzer/metrics.py` — unified summary surface (`summarize`, `CaseResult`, `RunResult`, `graded_score`).
- `analyzer/rules.py` — **goal §2 feedback edge**: proposes `ToolSpec` patches from failure histograms (R1-R11 patterns promoted from `runs/factory_bfcl/post_correction.py`).
- `analyzer/repair.py` — `RepairConfig` + `run_dsl_with_repair` repair-loop policy.
- `analyzer/verifier.py` — continuous reward function `make_verifier(catalog)`.
- `analyzer/reports.py` — markdown renderer over summary JSON.

**Benchmark consumers (`ganglion/benchmarks/`).** `benchmarks/iot/{dataset,runner,scaling,executor}.py` for the IoT tier surface; `benchmarks/bfcl/{loader,grader,case_catalog,runner}.py` for BFCL v4 single-turn. Each BFCL case ships its own tool list, so the runner compiles a fresh `Catalog` per case via `compile_tool_calling_schema`. **Null action contract:** `Catalog.allow_empty_calls=True` makes `{"calls":[]}` a valid Action IR, closing the `irrelevance` abstention gap (see `docs/tasks/contract_null_action.md`).

**Composite orchestrator (`ganglion/factory.py`).** `run_pipeline(PipelineConfig)` wires synth → finetune → benchmark → analyzer.{trace, failure, metrics, rule} → contract.catalog.published into one iterated loop. See `docs/tasks/factory_pipeline.md`.

**CLI dispatch (`ganglion/cli.py`).** `python -m ganglion.cli …` parses argparse, builds the client via `build_client(llm_choice, catalog, repair)`, dispatches to either `benchmarks/iot/runner.py:run_iot` or `benchmarks/bfcl/runner.py:run_bfcl`, prints JSON summary.

## Things to Know Before Editing

- Adding or modifying a tool requires updating: the catalog module's `ToolSpec` under `ganglion/contract/builtins/`, any normalisation aliases, the dataset templates if relevant, and the rule-based client only if the tool falls inside `iot_light_5`. Validator changes should be matched by tests in `tests/test_validator.py`.
- The dataset (`examples/iot_light/dataset.jsonl`) is checked in and deterministic — regenerate via the script rather than hand-editing. `parse_json_dsl(row["expected"])` runs at load time, so a malformed `expected` field will surface as a load error in `tests/test_dataset_integrity.py`.
- `examples/bfcl/v4/sample/*.jsonl` is a deterministic seed=42 subsample of upstream BFCL v4. Regenerate via `python examples/bfcl/v4/subsample.py`; the upstream commit SHA is pinned in `examples/bfcl/v4/SOURCE.md`. Never hand-edit the sample rows — they are SSOT for `tests/test_bfcl_smoke.py` and the M1'~M5' reports.
- `runs/` is checked in and contains experiment outputs that back the reports; treat it as data, not scratch. `runs/bfcl/` and `runs/bfcl/flash/` follow the same convention.
- The package uses `from __future__ import annotations` and frozen dataclasses throughout — keep both when extending.
- The pre-redesign packages (`ganglion/dsl/`, `ganglion/schema/`, `ganglion/runtime/`, `ganglion/factory/` sub-package, `ganglion/bfcl/`, `ganglion/eval/`) have been removed. Use canonical imports only. Historical reports and `runs/factory_*/*.py` scripts that still reference these paths are out-of-scope for the redesign; they may need updating before being re-run.

## Task spec layer (`docs/tasks/`)

This repo carries two doc trees that govern how the redesign is structured:

- [`docs/agent-forge/`](docs/agent-forge/) — imported seed principles from the [agent-forge](https://github.com/EngramAICompany/agent-forge) repo. Treat as **read-only upstream**. Never hand-edit; if a principle change is needed, file the issue against agent-forge and re-import.
- [`docs/tasks/`](docs/tasks/) — Ganglion-side task specs aligned with goal.md's three-module triad. The six-section template (`Role / Scope / Procedure / Contract / Observation`) from [`task_principle`](docs/agent-forge/task_principle.md) is mandatory.

Editor-side rules when adding new behaviour:

- **Spec first, impl after.** Write or update the task doc under `docs/tasks/` before authoring code or workflow. A workflow without its declaring doc is the anti-pattern called out in `task_principle`.
- **`out-of-scope` must not be empty.** An empty `out-of-scope` is the scope-creep surface, per [`task_principle` §3](docs/agent-forge/task_principle.md). Enumerate adjacent areas the task does *not* touch.
- **Connect via events, not direct calls.** Composite docs in `docs/tasks/` consume primitive events declared in their `Contract.event` clause. No task doc invokes another task doc by name.
- **`out` must be machine-verifiable.** Natural-language "reports" are ✗; produce files, status checks, or named events.
- **One responsibility per doc.** If the template is hard to fill, the task is too large — decompose. See [`workflow_principle`](docs/agent-forge/workflow_principle.md) for when to author atomic vs. composite.

The task-doc set is grouped by module:

- **Module 3 — contract**: [contract_catalog](docs/tasks/contract_catalog.md), [contract_schema_compiler](docs/tasks/contract_schema_compiler.md), [contract_null_action](docs/tasks/contract_null_action.md).
- **Module 1 — lm**: [lm_client](docs/tasks/lm_client.md), [lm_grammar_mask](docs/tasks/lm_grammar_mask.md), [lm_finetune](docs/tasks/lm_finetune.md), [lm_data_synth](docs/tasks/lm_data_synth.md).
- **Module 2 — analyzer**: [analyzer_trace_store](docs/tasks/analyzer_trace_store.md), [analyzer_failure_taxonomy](docs/tasks/analyzer_failure_taxonomy.md), [analyzer_metrics](docs/tasks/analyzer_metrics.md), [analyzer_rule_synthesis](docs/tasks/analyzer_rule_synthesis.md), [analyzer_repair_policy](docs/tasks/analyzer_repair_policy.md), [analyzer_verifier](docs/tasks/analyzer_verifier.md).
- **Consumers — benchmarks**: [benchmark_iot](docs/tasks/benchmark_iot.md), [benchmark_bfcl](docs/tasks/benchmark_bfcl.md).
- **Composites**: [factory_pipeline](docs/tasks/factory_pipeline.md), [factory_evaluation](docs/tasks/factory_evaluation.md).

Pre-redesign task docs (M0-M5 / Phase 1-3 reports) live under [`docs/tasks/legacy/`](docs/tasks/legacy/) for historical reference. See [`docs/redesign_plan.md`](docs/redesign_plan.md) for the migration map.
