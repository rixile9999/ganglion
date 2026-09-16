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

# Registry-addressed models + run persistence
python -m ganglion.cli --model rules --tier iot_light_5           # registry id instead of --llm
python -m ganglion.cli --model qwen3.6-plus@dashscope --models configs/models.yaml --limit 2
python -m ganglion.cli --model rules --trace-store runs/traces --run-id rules-0 --iteration 0
python -m ganglion.cli --model rules --trace-store runs/traces --run-id rules-1 \
                       --iteration 1 --parent-run rules-0

# Operator console (docs/tasks/console_operator.md) — stdlib only, no extra deps
python -m ganglion.console seed  --runs runs/traces --limit 100   # two offline runs + analysis
python -m ganglion.console serve --runs runs/traces --web web     # http://127.0.0.1:8766
python -m ganglion.console analyze iot_light_5 rules-degraded-seed --runs runs/traces
python -m ganglion.console export-labels iot_light_5 --runs runs/traces --zones train
python -m ganglion.console compare iot_light_5 rules-seed rules-degraded-seed --runs runs/traces
```

Every command above assumes the repo root as cwd — `seed` resolves `examples/iot_light/dataset.jsonl` relatively, and the console's `--runs` / `--web` defaults are `runs/traces` and `<repo>/web`.

`--llm` choices: `rules` | `qwen` | `qwen-text` | `qwen-thinking` | `qwen-native`. `--tier` choices: `iot_light_5` | `home_iot_20` | `smart_home_50` | `home_assistant_4` (Home Assistant Assist projection of `iot_light_5`; auto-selects `examples/home_assistant/dataset.jsonl`, no `rules` client support). `--bfcl` choices: `simple_python` | `multiple` | `parallel` | `parallel_multiple` | `irrelevance` | `callable` (the four non-irrelevance categories) | `all` (all five). `rules` has no BFCL adapter — use one of the `qwen*` clients. The runner prints a JSON summary to stdout; redirect to capture.

`--model` takes a `model_id` from the registry (`configs/models.yaml`, override with `--models` or `$GANGLION_MODELS`) and is mutually exclusive with `--llm`; `--llm` is kept as a legacy alias that maps onto registry ids via `ganglion.lm.registry.LLM_TO_MODEL_ID` (`rules` → `rules`, `qwen` → `qwen3.6-plus@dashscope`, `qwen-text` → `…-text@dashscope`, `qwen-thinking` → `…-thinking@dashscope`, `qwen-native` → `…-native@dashscope`). With neither flag the run uses `rules`. `--trace-store <dir>` turns a run into a persisted run bundle under `<dir>/<catalog_id>/<run_id>/`; `--run-id` (default `<model_id>-<YYYYmmdd-HHMMSS>`, `@` → `-`), `--iteration` and `--parent-run` fill the manifest fields the console's Loops page and `compare_runs` read. A BFCL run writes one bundle per category (`catalog_id = bfcl/<category>`, `metric_kind = ast_match`, no `report.md` / `describe.json`, `expected_plan=None` on every trace).

## Required Environment

- Python 3.11+
- `DASHSCOPE_API_KEY` for any DashScope model. Optional: `GANGLION_MODEL` (default `qwen3.6-plus`; expanded inside `configs/models.yaml`), `DASHSCOPE_BASE_URL` (overrides `base_url` for `provider: dashscope` entries only), `GANGLION_MODELS` (registry path), `GANGLION_VLLM_MODEL` / `GANGLION_VLLM_BASE_URL` (the `local@vllm` entry), `GANGLION_CONSOLE_PORT` (console default 8766), `GANGLION_LABELER` (author recorded on `POST /api/labels`, default `operator`). Legacy `RLM_MODEL` / `RLM_ENABLE_THINKING` are still read as a fallback for older scripts and historical reports.
- `GANGLION_ENABLE_THINKING` is only read by `QwenConfig.from_env()`, i.e. when a client is constructed directly. Registry-built clients (everything the CLI and the console do) ignore it — thinking is the `client: thinking` registry entry instead.
- The console and the registry need nothing beyond the two runtime deps (`openai`, `pyyaml`) — `ganglion/console/` is standard library only. `kind: local_hf` models additionally need the `[factory]` extra (torch + transformers + peft); `xgrammar` (also in that extra) is optional and a missing one downgrades `grammar_mask: true` to a one-time warning.

## Architecture (three-module triad — see `docs/factory_design.md`)

The codebase is organised into three peer modules per `docs/goal/goal.md`,
plus consumers (benchmarks, the operator console) and a composite orchestrator:

```
ganglion/
├── cli.py                    CLI dispatch (`python -m ganglion.cli`)
├── factory.py                Composite orchestrator (`run_pipeline`)
├── contract/                 Module 3 — schemas, DSL, validation (leaf)
├── lm/                       Module 1 — language-model production
├── analyzer/                 Module 2 — statistical analysis + compiler-correction
├── benchmarks/{iot,bfcl}/    consumers (emit traces)
└── console/                  consumer — operator HTTP console over the run bundles
```

Data flow per case: `user prompt → ModelClient.invoke() → JSON DSL string → Catalog.parse_json_dsl() → ActionPlan → analyzer.metrics.summarize`. The deterministic emission and validation are the load-bearing pieces; the LLM only produces a DSL string.

**Module 3 (`ganglion/contract/`) — Catalog is the compiler boundary.** A `Catalog` (`ganglion/contract/catalog.py`) bundles `ToolSpec`s and renders three artifacts from the same source of truth:
- `render_json_dsl()` — short text appended to the system prompt for DSL paths.
- `render_openai_tools()` — full OpenAI `tools=[...]` schema for the native baseline.
- `describe()` — the complete JSON snapshot the catalog fingerprint is taken over (below).

`ganglion/contract/tool_spec.py` defines `ToolSpec` plus arg variants `EnumArg`, `IntArg`, `NumberArg`, `StringArg`, `BoolArg`, `TimeArg`, `RawArg`. `EnumArg.aliases` and `StringArg.aliases` are the canonicalisation hook (e.g. `"거실" → "living"`).

`ganglion/contract/schema_compiler.py:compile_tool_calling_schema` consumes external tool schemas (OpenAI / MCP / bare function schemas / BFCL `function` entries) and produces a `CompiledToolMapper` wrapping a `Catalog`. It normalises BFCL-specific type aliases (`dict→object`, `float→number`, `tuple→array`) at every nesting level and propagates `allow_empty_calls`.

`Catalog.parse_json_dsl()` accepts either a string or mapping and returns an `ActionPlan` of immutable `ToolCall`s. `ActionPlan` equality is value equality, so `result.plan == expected` is the exact-match metric.

`ganglion/contract/describe.py` is the third renderer over the same `ToolSpec` traversal: `describe(catalog)` (also `Catalog.describe()`) emits a deterministic JSON dict covering everything the validator reads but neither render exposes — aliases, defaults, strip flags, arg descriptions, `bool_true`/`bool_false` — with callables reduced to presence booleans. `catalog_fingerprint(catalog)` (also `Catalog.fingerprint()`) hashes that dict into a `cf-<sha12>` string; it is the first element of the identity triple (`catalog_fingerprint`, `model_id`, `dataset_sha256`) every trace, label, manifest and patch carries. Changing the *body* of a hook callable deliberately does not move the fingerprint.

`ganglion/contract/patch.py` applies a `RulePatch` without publishing anything: `apply_patch(catalog, patch) -> Catalog` is pure (`dataclasses.replace` all the way down) and raises `PatchNotApplicableError` for anything not mechanically applicable (a new arg, a `RawArg` swap, a prompt nudge, `ESCALATE`). `strip_hooks(catalog, ALL_HOOK_KINDS)` returns a copy with `defaults_when_missing` / `strip_unknown_args` / `prompt_correction` removed from every tool — that is the F⁰ ("model alone") catalog the chat page and the correction attribution re-parse stored raw output through. `custom_validator` is never stripped.

**Tiers.** `ganglion/contract/builtins/{iot_light,home_iot,smart_home,home_assistant}.py` each export a module-level `CATALOG`. `ganglion/contract/builtins/__init__.py:get_catalog(tier)` is the registry; `SCALING_TIERS` names the three M2 scaling tiers (5 / 20 / 50 tools), which share the same dataset prompts because the IoT-light intents are a subset of the larger catalogs. `home_assistant_4` is a projection of `iot_light_5` onto Home Assistant's Assist API tool shape with its own derived dataset (`examples/home_assistant/dataset.jsonl`, regenerate via `examples/home_assistant/generate_dataset.py`); it is not on the scaling curve. See `docs/tasks/contract_tier_home_assistant.md`. BFCL runs bypass this registry entirely — they construct catalogs per case from BFCL's `function` field via the schema compiler.

**Module 1 (`ganglion/lm/`) — language-model production.** Three OpenAI-SDK-against-DashScope clients in `ganglion/lm/dashscope.py`:
- `QwenJSONDSLClient` — uses `response_format={"type": "json_object"}`; goes through `run_dsl_with_repair()` so it supports the M4 repair loop.
- `QwenFreeformJSONDSLClient` — no `response_format`; output is salvaged by `parse_json_dsl_lenient()`. Used for `qwen-text` and `qwen-thinking`.
- `QwenNativeToolClient` — sends `tools=catalog.render_openai_tools()`, then converts the returned `tool_calls` back into the same DSL shape so it shares the validator and equality semantics.

`RuleBasedJSONDSLClient` (`ganglion/lm/rules.py`) is a regex/keyword stand-in matched to the `iot_light_5` intents — it therefore also scores 1.0 on `home_iot_20` / `smart_home_50` (same prompts, superset catalogs; `configs/models.yaml` lists all three under `catalog_ids`) but 0.0 on `home_assistant_4`, which renames every action — it lets `pytest` and the offline runner work without API access.

`ganglion/lm/local_hf.py` runs local transformers + PEFT inference. Besides the held-out LoRA evaluator (`split_train_eval`, `evaluate_lora`, `write_report`) it now also carries the in-process model lifecycle — `local_hf_available()`, `load_local_model()` / `unload_local_model()` / `local_model_status()` over a process-wide model cache — and `LocalHFClient`, a `ModelClient` that builds the SFT-identical system prompt, generates with `generate_dsl`, parses strictly then leniently, and raises `ModelOutputError` with the raw text attached. `torch` / `transformers` are never imported at module import time. `ganglion/lm/grammar.py` compiles a `Catalog` to JSON Schema → XGrammar logits processor. `ganglion/lm/finetune/sft.py` is the LoRA SFT trainer (TRL `SFTTrainer`, `assistant_only_loss=True`). `ganglion/lm/synth/{ingest,pipeline,strategies}.py` is the teacher-driven data synthesis pipeline.

**Model registry (`ganglion/lm/registry.py`, `configs/models.yaml`).** A checked-in YAML file is the list of models the CLI (`--model`) and the console's picker share. One entry is a frozen `ModelSpec`; `kind` picks the client family and `client` picks the prompt/decoding path:

- `kind: rules` — `RuleBasedJSONDSLClient`, offline, `iot_light_5` only.
- `kind: openai_compat` — any OpenAI-compatible chat endpoint; `provider` is `dashscope` | `vllm` | `openai` and only selects how the thinking switch is sent (`extra_body.enable_thinking` vs `extra_body.chat_template_kwargs.enable_thinking` vs nothing).
- `kind: local_hf` — in-process `LocalHFClient` (`provider: local`); `base_model` is a hub id or path, `adapter_dir` an optional LoRA, plus `device` / `dtype` / `max_new_tokens` / `grammar_mask`.

`client` is `json-dsl` | `freeform` | `thinking` | `native`. String values support `${VAR:-default}` expansion; secrets are never stored — `api_key_env` names the variable. `load_registry(path)` resolves explicit path → `$GANGLION_MODELS` → `configs/models.yaml`, and merges LoRA adapter dirs discovered under `runs/` as `local:<dir>` specs (YAML wins on an id clash). `build_client_from_spec(spec, catalog, repair=…)` is the one place a client is constructed; `model_fingerprint(spec)` returns `mf-<sha12>` (`mf-rules` for the rules model) and `availability(spec)` answers whether this machine can run it without importing torch.

**Single-request serving (`ganglion/lm/serve.py`).** `Server.serve(ServeRequest) -> ServeResult` is the adapter the chat page sits on: it resolves catalog + model, invokes once, emits `[{name, arguments}]` tool calls, and computes the **F⁰ / Fᴷ** pair — the plan the model produced alone (via `strip_hooks`) next to the plan the full catalog accepted — plus a human-readable `hooks_diff`. Unlike a `ModelClient` it never raises for a model failure: `error` is set and `raw` / `attempts` / `raw_plan` survive, so a failed inference is still a recordable trace.

**Module 2 (`ganglion/analyzer/`) — statistical analysis + compiler-correction (goal §2).**
- `analyzer/trace.py` — `Trace` + `TraceStore` append-only JSONL substrate.
- `analyzer/taxonomy.py` — `FailureType` enum (14 buckets) + `classify()` with priority-ordered matchers.
- `analyzer/metrics.py` — unified summary surface (`summarize`, `CaseResult`, `RunResult`, `graded_score`).
- `analyzer/rules.py` — **goal §2 feedback edge**: proposes `ToolSpec` patches from failure histograms (R1-R11 patterns promoted from `runs/factory_bfcl/post_correction.py`).
- `analyzer/repair.py` — `RepairConfig` + `run_dsl_with_repair` repair-loop policy.
- `analyzer/verifier.py` — continuous reward function `make_verifier(catalog)`.
- `analyzer/reports.py` — markdown renderer over summary JSON.
- `analyzer/ledger.py` — append-only `Event` rows (`emit` / `read_events`) with a closed `EVENT_NAMES` vocabulary; content-hash idempotent. **Not** an in-process bus: nothing subscribes, consumers read files.
- `analyzer/labels.py` — `LabelRecord` / `LabelStore` (`labels.jsonl`), `resolve_gold` as the single gold join (human label beats dataset gold), `family_id_for` / `zone_for` for train / dev / release split, and `export_sft` in the exact shape `lm/synth/pipeline.write_jsonl` produces.
- `analyzer/manifest.py` — `RunManifest` + `write_run_bundle` (`manifest.json` + `summary.json` + `report.md`). "Scan every `manifest.json`" (`list_runs`) is the SSOT for listing runs; a shard without a manifest is not a run.
- `analyzer/compare.py` — `compare_runs` joins two runs of one catalog on `case_id`: transition matrix (`fixed` / `regressed` / `same_pass` / `same_fail`), paired-bootstrap 95 % CI on ΔEM, `manifest_diff`. Refuses mismatched `dataset_sha256` / `metric_kind` / `decoding` unless `allow_diff=True`.
- `analyzer/decisions.py` — `PatchDecision` ledger (`blind` → `after_preview` → `ported`) + `precision_summary`. Accepting a patch changes nothing; porting is a reviewed code edit recorded with its `commit_sha`.
- `analyzer/corrections.py` — F⁰ / Fᴷ attribution: re-parse each trace's *first* model output with hooks stripped and with the full catalog, attribute every pass/fail flip to a `(tool, hook_kind)` by single-kind ablation, and turn hooks that rescue nothing over a large enough sample into `retire_rule` proposals.
- `analyzer/catalogs.py` — `catalog_id` → `Catalog`. Builtin tier names via `builtins.TIERS`; `compiled/<sha12>` recompiled from `<base>/compiled/<sha12>/catalog.json` (`register_compiled` persists it); `bfcl/<category>` raises `CatalogNotResolvable` (per-case catalogs, read-only in the console).
- `analyzer/analyze.py` — composite `analyze_run(base_dir, catalog_id, run_id)`: classify → attribute → synthesise, writing each primitive's sidecar and emitting its declared event. Nothing is computed inline. `histogram()` reports all 14 `FailureType` names plus `unclassified`; a `no_failure` row with `confidence == 0.0` counts as `unclassified`, never as a pass.

**Run bundle layout.** Everything above is a sidecar in one directory per run. `runs/traces/<catalog_id>/<run_id>/` accumulates, as each producer runs: `traces.jsonl`, `manifest.json`, `summary.json`, `report.md`, `describe.json`, `events.jsonl`, `labels.jsonl`, `classified.jsonl`, `proposed_patches.jsonl` + `.summary.json`, `corrections.jsonl` + `.summary.json`, `patch_decisions.jsonl`, `compare-<run_a>.json`. Events with no run correlation go to `<base>/ledger/global.jsonl`; label exports go to `<base>/../labels/<catalog_id>/`. `run_id` is a plain name that may contain one `/` for console sessions (`console/<session_id>`).

**Benchmark consumers (`ganglion/benchmarks/`).** `benchmarks/iot/{dataset,runner,scaling,executor}.py` for the IoT tier surface; `benchmarks/bfcl/{loader,grader,case_catalog,runner}.py` for BFCL v4 single-turn. Each BFCL case ships its own tool list, so the runner compiles a fresh `Catalog` per case via `compile_tool_calling_schema`. **Null action contract:** `Catalog.allow_empty_calls=True` makes `{"calls":[]}` a valid Action IR, closing the `irrelevance` abstention gap (see `docs/tasks/contract_null_action.md`).

**Operator console (`ganglion/console/`).** A consumer package alongside `benchmarks/`, not a fourth module: `server.py` is a `ThreadingHTTPServer` on `127.0.0.1` serving `web/` statically plus `/api/*`; `api.py` is the route table and the truth about response shapes; `writer.py` is the single daemon thread every file write (and every local-model load) is queued through; `seed.py` produces the two offline runs; `__main__.py` exposes the same surface without HTTP (`serve` / `seed` / `analyze` / `export-labels` / `compare`). Standard library only. The contract is narrow: every `GET` is a projection of files under the runs dir, every `POST` calls exactly **one** analyzer/lm primitive through the writer and lets that primitive emit its own ledger row. No handler classifies, compares, validates or invokes a model itself. The two documented exceptions are `GET /api/compare` and `GET /api/export/labels`, which persist an idempotent artifact and are therefore routed through the writer too. See `docs/tasks/console_operator.md` and `ganglion/console/README.md`.

**Composite orchestrator (`ganglion/factory.py`).** `run_pipeline(PipelineConfig)` wires synth → finetune → benchmark → analyzer.{trace, failure, metrics, rule} → contract.catalog.published into one iterated loop. See `docs/tasks/factory_pipeline.md`.

**CLI dispatch (`ganglion/cli.py`).** `python -m ganglion.cli …` parses argparse, resolves a `ModelSpec` from the registry, builds the client via `build_client_from_spec` (the older `build_client(name, catalog, *, repair, registry=None)` remains and delegates to it), dispatches to either `benchmarks/iot/runner.py:run_iot` or `benchmarks/bfcl/runner.py:run_bfcl`, prints a JSON summary. With `--trace-store` it additionally persists the run: `persist_iot_run` / `persist_bfcl_run` turn the results into `Trace`s, write the run bundle and emit the ledger rows. `persist_iot_run` is also the code path `python -m ganglion.console seed` reuses, so a seeded run and a CLI run produce the same bundle shape.

## Things to Know Before Editing

- Adding or modifying a tool requires updating: the catalog module's `ToolSpec` under `ganglion/contract/builtins/`, any normalisation aliases, the dataset templates if relevant, and the rule-based client only if the tool falls inside `iot_light_5`. Validator changes should be matched by tests in `tests/test_validator.py`.
- The dataset (`examples/iot_light/dataset.jsonl`) is checked in and deterministic — regenerate via the script rather than hand-editing. `parse_json_dsl(row["expected"])` runs at load time, so a malformed `expected` field will surface as a load error in `tests/test_dataset_integrity.py`.
- `examples/bfcl/v4/sample/*.jsonl` is a deterministic seed=42 subsample of upstream BFCL v4. Regenerate via `python examples/bfcl/v4/subsample.py`; the upstream commit SHA is pinned in `examples/bfcl/v4/SOURCE.md`. Never hand-edit the sample rows — they are SSOT for `tests/test_bfcl_smoke.py` and the M1'~M5' reports.
- `runs/` is checked in and contains experiment outputs that back the reports; treat it as data, not scratch. `runs/bfcl/` and `runs/bfcl/flash/` follow the same convention. `runs/traces/` is the trace-store root the same rule applies to; point experiments at a scratch dir (`--trace-store` / `--runs`) unless the run is meant to be kept.
- **A failed inference must still produce a full trace.** Clients no longer drop the model's output on the floor: the json-dsl path raises `RepairExhaustedError` (from `analyzer/repair.py`) and the rules / freeform / native / local-HF paths raise `ModelOutputError` (from `lm/client.py`), both carrying `.raw` and `.attempts`. Runners record `raw=getattr(exc, "raw", None)` and never re-raise. When adding a client or a runner, preserve those two attributes — the taxonomy, rule synthesis and F⁰/Fᴷ attribution all read the failed output.
- **`Trace.plan` still means "validated plan" (`None` on failure); `Trace.raw_plan` is the decoded-but-unvalidated JSON of the last attempt that parsed as an object.** Never put an unvalidated dict into `plan`. `Trace` also gained `repeat_index`, and `_derive_trace_id` now hashes `catalog_id` and `repeat_index` alongside `(case_id, model_id, run_id, attempts)` — so trace ids from before the console batch will not reproduce, and a `--repeat N` run (or a console re-asking the same prompt) yields distinct traces instead of silently collapsing into one.
- `TraceStore` is now lock-guarded and re-reads a shard whose `(size, mtime)` changed, so a running console sees runs a CLI process wrote. It is still one writer per file: inside the console every write goes through `console/writer.py`.
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

- **Module 3 — contract**: [contract_catalog](docs/tasks/contract_catalog.md), [contract_schema_compiler](docs/tasks/contract_schema_compiler.md), [contract_null_action](docs/tasks/contract_null_action.md), [contract_tier_home_assistant](docs/tasks/contract_tier_home_assistant.md), [contract_describe](docs/tasks/contract_describe.md), [contract_patch_apply](docs/tasks/contract_patch_apply.md).
- **Module 1 — lm**: [lm_client](docs/tasks/lm_client.md), [lm_grammar_mask](docs/tasks/lm_grammar_mask.md), [lm_finetune](docs/tasks/lm_finetune.md), [lm_data_synth](docs/tasks/lm_data_synth.md), [lm_model_registry](docs/tasks/lm_model_registry.md), [lm_request_serve](docs/tasks/lm_request_serve.md).
- **Module 2 — analyzer**: [analyzer_trace_store](docs/tasks/analyzer_trace_store.md), [analyzer_failure_taxonomy](docs/tasks/analyzer_failure_taxonomy.md), [analyzer_metrics](docs/tasks/analyzer_metrics.md), [analyzer_rule_synthesis](docs/tasks/analyzer_rule_synthesis.md), [analyzer_repair_policy](docs/tasks/analyzer_repair_policy.md), [analyzer_verifier](docs/tasks/analyzer_verifier.md), [analyzer_event_ledger](docs/tasks/analyzer_event_ledger.md), [analyzer_label_store](docs/tasks/analyzer_label_store.md), [analyzer_run_manifest](docs/tasks/analyzer_run_manifest.md), [analyzer_compare](docs/tasks/analyzer_compare.md), [analyzer_patch_decision](docs/tasks/analyzer_patch_decision.md), [analyzer_correction_attribution](docs/tasks/analyzer_correction_attribution.md), [analyzer_catalogs](docs/tasks/analyzer_catalogs.md).
- **Consumers — benchmarks, console**: [benchmark_iot](docs/tasks/benchmark_iot.md), [benchmark_bfcl](docs/tasks/benchmark_bfcl.md), [benchmark_selection](docs/tasks/benchmark_selection.md) (decision record for external benchmarks on the local-model path), [console_operator](docs/tasks/console_operator.md) (also a composite, below).
- **Composites**: [factory_pipeline](docs/tasks/factory_pipeline.md), [factory_evaluation](docs/tasks/factory_evaluation.md), [analyzer_analyze](docs/tasks/analyzer_analyze.md) (one-run classify → attribute → synthesise), [console_operator](docs/tasks/console_operator.md) (the `web/` + `/api/*` surface over the run bundles).

`ganglion/analyzer/catalogs.py` is specified by [analyzer_catalogs](docs/tasks/analyzer_catalogs.md) — the `catalog_id` → `Catalog` resolver consumed by [analyzer_analyze](docs/tasks/analyzer_analyze.md) and [console_operator](docs/tasks/console_operator.md); its doc was written after the code landed, so read it before extending the resolver.

Pre-redesign task docs (M0-M5 / Phase 1-3 reports) live under [`docs/tasks/legacy/`](docs/tasks/legacy/) for historical reference. See [`docs/redesign_plan.md`](docs/redesign_plan.md) for the migration map.
