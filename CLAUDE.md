# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Ganglion — *compiler-guided optimization for LLM tool calling*

The repo directory is `reflex-language-model` and the Python package
namespace is `ganglion` (this is what `pyproject.toml` packages — see
`[tool.setuptools.packages.find] include = ["ganglion*"]`). The public project
name is **Ganglion**. An earlier draft used a different public name briefly
before reverting to Ganglion; if you spot any stray reference to that earlier
name in docs or commit history, treat it as outdated and align it with
Ganglion.

`rlm_poc/` at the repo root is a vestigial directory from a prior rename and is
empty except for `__pycache__`. Don't add code there — everything lives under
`ganglion/`.

## Project Purpose

POC testing whether compact Action IRs emitted by an LLM can replace native
tool/function-call schemas in the prompt while preserving accuracy. The
hypothesis is that an IR-style intermediate output reduces input token cost and
latency vs handing the model the full OpenAI tool schema. See `overview.md`
(Korean) for the broader research goal and `docs/poc_verification_report.md`
for measured results.

There is also a `QWEN.md` written for a separate agent — its content overlaps with this file; if you update operational facts here, check whether `QWEN.md` needs the same change.

## Common Commands

```bash
pip install -e ".[dev]"                                          # install + dev deps
pytest                                                           # full test suite
pytest tests/test_validator.py::test_set_light_basic            # single test
python examples/iot_light/generate_dataset.py                    # regenerate 500-case dataset
python -m ganglion.eval.runner --llm rules --tier iot_light_5     # offline (no API)
python -m ganglion.eval.runner --llm qwen  --tier iot_light_5     # JSON DSL via Qwen
python -m ganglion.eval.runner --llm qwen-native --tier home_iot_20  # native tool-call baseline
python -m ganglion.eval.runner --llm qwen --repair --repair-max-attempts 1  # repair loop
python -m ganglion.eval.runner --llm qwen --repeat 5              # repeat each case for latency stats
python -m ganglion.eval.scaling                                   # measure DSL vs native catalog sizes
bash runs/m2_run.sh   # batch experiment scripts; outputs JSON into runs/m{2,3,4}/
python runs/aggregate.py                                         # compact tables from runs/*.json

# BFCL v4 single-turn external benchmark (per-case Catalog, M1'~M5')
python -m ganglion.eval.runner --llm qwen        --bfcl simple_python --bfcl-per-category 100
python -m ganglion.eval.runner --llm qwen-native --bfcl all           --bfcl-per-category 100
python -m ganglion.eval.runner --llm qwen        --bfcl irrelevance   --bfcl-allow-empty-calls
python -m ganglion.eval.runner --llm qwen        --bfcl callable      --repair --repair-max-attempts 1
python -m ganglion.eval.runner --llm qwen        --bfcl all --bfcl-output runs/bfcl/<name>_cases.jsonl \
                                                  > runs/bfcl/<name>_summary.json
python runs/bfcl/aggregate.py                                    # cross-phase BFCL tables
```

`--llm` choices: `rules` | `qwen` | `qwen-text` | `qwen-thinking` | `qwen-native`. `--tier` choices: `iot_light_5` | `home_iot_20` | `smart_home_50`. `--bfcl` choices: `simple_python` | `multiple` | `parallel` | `parallel_multiple` | `irrelevance` | `callable` (the four non-irrelevance categories) | `all` (all five). `rules` has no BFCL adapter — use one of the `qwen*` clients. The runner prints a JSON summary to stdout; redirect to capture.

## Required Environment

- Python 3.11+
- `DASHSCOPE_API_KEY` for any `qwen*` path. Optional: `GANGLION_MODEL` (default `qwen3.6-plus`), `DASHSCOPE_BASE_URL`, `GANGLION_ENABLE_THINKING`. Legacy `RLM_MODEL` / `RLM_ENABLE_THINKING` are still read as a fallback for older scripts and historical reports.

## Architecture

Data flow per case: `user prompt → ModelClient.invoke() → JSON DSL string → Catalog.parse_json_dsl() → ActionPlan → metrics`. The deterministic emission and validation are the load-bearing pieces; the LLM only produces a DSL string.

**Catalog is the compiler boundary.** A `Catalog` (`ganglion/dsl/catalog.py`) bundles `ToolSpec`s and renders two artifacts from the same source of truth:
- `render_json_dsl()` — short text appended to the system prompt for DSL paths.
- `render_openai_tools()` — full OpenAI `tools=[...]` schema for the native baseline.

This dual rendering is what makes the DSL-vs-native comparison apples-to-apples. When adding a new tool, define one `ToolSpec` and both renderings update.

**ToolSpec / ArgSpec.** `ganglion/dsl/tool_spec.py` defines `ToolSpec` plus arg variants `EnumArg`, `IntArg`, `StringArg`, `TimeArg`, `RawArg`. `EnumArg.aliases` and `StringArg.aliases` are the canonicalisation hook (e.g. `"거실" → "living"`, `"영화 모드" → "movie"`). `RawArg` exists for shapes the generic renderer can't express, like nested `create_scene.actions`; pair it with a `custom_validator` on the `ToolSpec` (see `iot_light.py`).

**Schema → DSL compiler.** `ganglion/dsl/compiler.py:compile_tool_calling_schema` consumes external tool schemas (OpenAI / MCP / bare function schemas / BFCL `function` entries) and produces a `CompiledToolMapper` wrapping a `Catalog`. It normalises BFCL-specific type aliases (`dict→object`, `float→number`, `tuple→array`) at every nesting level and propagates `allow_empty_calls`. See `docs/tool_schema_compiler.md` for the long-form design and `docs/tasks/tool_schema_compiler.md` for the 6-section task spec. Tests live in `tests/test_tool_schema_compiler.py` and `tests/test_compiler_bfcl_features.py`.

**External benchmark: BFCL v4.** `ganglion/bfcl/` ports the BFCL v4 single-turn evaluation surface — `loader.py` reads the deterministic subsample at `examples/bfcl/v4/sample/{simple_python,multiple,parallel,parallel_multiple,irrelevance}.jsonl`, and `grader.py` re-implements the upstream Python AST checker (string standardisation, list/dict checker, parallel no-order, irrelevance branch). `ganglion/eval/bfcl_runner.py` is the library entry (`run_bfcl`, `summarize_bfcl`, `build_case_catalog`), driven through the same `ganglion.eval.runner --bfcl …` CLI. Each BFCL case ships its own tool list, so the runner compiles a fresh `Catalog` per case via `compile_tool_calling_schema`. **Null action contract:** `Catalog.allow_empty_calls=True` makes `{"calls":[]}` a valid Action IR, closing the `irrelevance` abstention gap (see `docs/tasks/null_action_contract.md` and `docs/bfcl_m5_abstention_report.md`). Result artifacts live under `runs/bfcl/` (M1'~M5') and `runs/bfcl/flash/` (qwen3.6-flash replay).

**Validator + emitter.** `Catalog.parse_json_dsl()` accepts either a string or mapping, normalises via `_validate_flat_args`, and returns an `ActionPlan` of immutable `ToolCall`s. `ActionPlan` equality is value equality, so `result.plan == expected` is the exact-match metric. There is no separate emitter step beyond this — the parsed plan IS the executable form, fed to `runtime/executor.py` (mock executor for tests).

**Tiers.** `ganglion/schema/{iot_light,home_iot,smart_home}.py` each export a module-level `CATALOG`. `ganglion/schema/__init__.py:get_catalog(tier)` is the registry. The three tiers exist specifically for the M2 scaling experiment (5 / 20 / 50 tools); the same dataset prompts are reused across tiers because the IoT-light intents are a subset of the larger catalogs. BFCL runs bypass this registry entirely — they construct catalogs per case from BFCL's `function` field via the schema compiler.

**Runtime clients.** `ganglion/runtime/qwen.py` has three OpenAI-SDK-against-DashScope clients:
- `QwenJSONDSLClient` — uses `response_format={"type": "json_object"}`; goes through `run_dsl_with_repair()` so it supports the M4 repair loop.
- `QwenFreeformJSONDSLClient` — no `response_format`; output is salvaged by `parse_json_dsl_lenient()` (strict → fenced ```json``` → first decodable `{...}`). Used for `qwen-text` and `qwen-thinking`.
- `QwenNativeToolClient` — sends `tools=catalog.render_openai_tools()`, then converts the returned `tool_calls` back into the same DSL shape so it shares the validator and equality semantics.

`RuleBasedJSONDSLClient` (`runtime/rules.py`) is a regex/keyword stand-in matched to the `iot_light_5` catalog only — it lets `pytest` and the offline runner work without API access. It will not produce sensible output for other tiers.

**Repair loop (M4).** Lives in `run_dsl_with_repair()` in `runtime/qwen.py`. On `DSLValidationError` it appends the failed assistant message + a corrective user message and retries up to `RepairConfig.max_attempts` times. Token counts and per-attempt content are accumulated into `ModelResult.raw["attempts"]`, which `metrics.summarize()` reads to populate `repair_attempts_total` / `repair_successes_total`. Only `QwenJSONDSLClient` is wired to repair currently.

**Metrics.** `eval/metrics.py` reports `syntax_valid_rate`, `exact_match_rate` (full structural equality after normalisation), `action_match_rate` (action names only), latency mean/p50/p95/stddev, token totals, and per-strategy parse counts. The lenient parser populates `raw["parse_strategy"]` (`strict` | `fenced` | `embedded`) so you can see which extraction path succeeded.

## Things to Know Before Editing

- Adding or modifying a tool requires updating: the schema module's `ToolSpec`, any normalisation aliases, the dataset templates if relevant, and the rule-based client only if the tool falls inside `iot_light_5`. Validator changes should be matched by tests in `tests/test_validator.py`.
- The dataset (`examples/iot_light/dataset.jsonl`) is checked in and deterministic — regenerate via the script rather than hand-editing. `parse_json_dsl(row["expected"])` runs at load time, so a malformed `expected` field will surface as a load error in `tests/test_dataset_integrity.py`.
- `examples/bfcl/v4/sample/*.jsonl` is a deterministic seed=42 subsample of upstream BFCL v4. Regenerate via `python examples/bfcl/v4/subsample.py`; the upstream commit SHA is pinned in `examples/bfcl/v4/SOURCE.md`. Never hand-edit the sample rows — they are SSOT for `tests/test_bfcl_smoke.py` and the M1'~M5' reports.
- `runs/` is checked in and contains experiment outputs that back the reports; treat it as data, not scratch. `runs/bfcl/` and `runs/bfcl/flash/` follow the same convention.
- The package uses `from __future__ import annotations` and frozen dataclasses throughout — keep both when extending.

## Self-maintenance task docs (spec layer)

This repo carries two doc trees that govern how self-maintenance is designed and added:

- [`docs/agent-forge/`](docs/agent-forge/) — imported seed principles from the [agent-forge](https://github.com/EngramAICompany/agent-forge) repo. Treat as **read-only upstream**. Never hand-edit; if a principle change is needed, file the issue against agent-forge and re-import.
- [`docs/tasks/`](docs/tasks/) — Ganglion-side task specs that *apply* those principles to this repo's drift surfaces. The six-section template (`Role / Scope / Procedure / Contract / Observation`) from [`task_principle`](docs/agent-forge/task_principle.md) is mandatory.

Editor-side rules when adding self-maintenance behavior:

- **Spec first, impl after.** Write the task doc under `docs/tasks/` before authoring any `.github/workflows/*` or script. A workflow without its declaring doc is the anti-pattern called out in `task_principle`.
- **`out-of-scope` must not be empty.** An empty `out-of-scope` is the scope-creep surface, per [`task_principle` §3](docs/agent-forge/task_principle.md). Enumerate adjacent areas the task does *not* touch.
- **Connect via events, not direct calls.** Composite docs in `docs/tasks/` consume primitive events declared in their `Contract.event` clause. No task doc invokes another task doc by name.
- **`out` must be machine-verifiable.** Natural-language "reports" are ✗; produce files, status checks, or named events.
- **One responsibility per doc.** If the template is hard to fill, the task is too large — decompose. See [`workflow_principle`](docs/agent-forge/workflow_principle.md) for when to author atomic vs. composite.

The `docs/tasks/` set has two layers:

- **Self-maintenance specs (spec-only, impls deferred):** [dataset_integrity](docs/tasks/dataset_integrity.md), [catalog_spec_sync](docs/tasks/catalog_spec_sync.md), [eval_smoke_guard](docs/tasks/eval_smoke_guard.md), [report_freshness](docs/tasks/report_freshness.md), [release_health](docs/tasks/release_health.md). No `.github/workflows/*` impl yet; follow-up PRs must update the declaring doc in the same change.
- **External-adapter specs (live impl, doc is post-hoc reconciliation):** [tool_schema_compiler](docs/tasks/tool_schema_compiler.md), [null_action_contract](docs/tasks/null_action_contract.md), [external_benchmark_bfcl](docs/tasks/external_benchmark_bfcl.md). Implementations already live in `ganglion/dsl/compiler.py`, `ganglion/dsl/catalog.py`, `ganglion/bfcl/`, `ganglion/eval/bfcl_runner.py`, and `ganglion/eval/runner.py`. New behaviour in these areas must update the doc in the same PR.
