# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: ToolCallOpt — *compiler-guided optimization for LLM tool calling*

The repo directory is `reflex-language-model` and the current Python package
namespace is `ganglion`. Use the name **ToolCallOpt** in new user-facing
artifacts (READMEs, reports, paper drafts). Treat **Ganglion** as the former
codename during the rename transition.

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
```

`--llm` choices: `rules` | `qwen` | `qwen-text` | `qwen-thinking` | `qwen-native`. `--tier` choices: `iot_light_5` | `home_iot_20` | `smart_home_50`. The runner prints a JSON summary to stdout; redirect to capture.

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

**Validator + emitter.** `Catalog.parse_json_dsl()` accepts either a string or mapping, normalises via `_validate_flat_args`, and returns an `ActionPlan` of immutable `ToolCall`s. `ActionPlan` equality is value equality, so `result.plan == expected` is the exact-match metric. There is no separate emitter step beyond this — the parsed plan IS the executable form, fed to `runtime/executor.py` (mock executor for tests).

**Tiers.** `ganglion/schema/{iot_light,home_iot,smart_home}.py` each export a module-level `CATALOG`. `ganglion/schema/__init__.py:get_catalog(tier)` is the registry. The three tiers exist specifically for the M2 scaling experiment (5 / 20 / 50 tools); the same dataset prompts are reused across tiers because the IoT-light intents are a subset of the larger catalogs.

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
- `runs/` is checked in and contains experiment outputs that back the report; treat it as data, not scratch.
- The package uses `from __future__ import annotations` and frozen dataclasses throughout — keep both when extending.
