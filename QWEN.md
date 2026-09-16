# Ganglion — *spec-based tool-calling optimisation model factory*

## Project Overview

**Ganglion** is a research prototype testing whether compact Action IRs can
replace full tool schemas in LLM prompts while preserving tool-call accuracy.
The goal is to reduce token costs and latency for agent tool-calling workflows
and provide a clean optimization target for small tool-calling models.

**Core Hypothesis:** Instead of providing full tool schemas to the LLM on every
request, have the LLM generate a short Action IR that a deterministic
parser/validator converts into actual tool calls. This reduces token
consumption and improves response latency.

**Key Results (M1-M4):**
- **46-69% token reduction** vs native tool calling (scales with tool count)
- **100% exact match** accuracy on 500-case IoT dataset
- **19% faster** mean latency vs native tool calling
- Validated across 3 tool tiers: 5, 20, and 50 tools

**External benchmark — BFCL v4 (M1'~M5):**
- 500 cases (5 categories × 100, seed=42 deterministic subsample)
- DSL **86.2% AST** vs native 85.6% (qwen3.6-plus, M5 full run)
- **-62% input tokens, -25% p50 latency** preserved
- Irrelevance 74% → **90%** via `{"calls":[]}` no-call contract
- Replayed on qwen3.6-flash with similar deltas — framework value is model-agnostic

## Project Structure

```
reflex-language-model/
├── ganglion/                       # Package namespace (post-redesign)
│   ├── cli.py                     # CLI dispatch (`python -m ganglion.cli`)
│   ├── factory.py                 # Composite orchestrator (`run_pipeline`)
│   ├── contract/                  # Module 3 — schemas, DSL, validation (leaf)
│   │   ├── catalog.py             # Catalog, render_json_dsl, render_openai_tools
│   │   ├── tool_spec.py           # ToolSpec + ArgSpec subclasses (Enum/Int/Number/String/Time/Bool/Raw)
│   │   ├── types.py               # ActionPlan, ToolCall, DSLValidationError
│   │   ├── schema_compiler.py     # External-schema → Catalog (OpenAI/MCP/BFCL)
│   │   ├── parse.py               # parse_json_dsl, parse_json_dsl_lenient
│   │   ├── emitter.py             # Provider-neutral {name, arguments} emission
│   │   ├── describe.py            # describe() third render + catalog_fingerprint() "cf-<sha12>"
│   │   ├── patch.py               # Pure apply_patch() / strip_hooks() (F⁰ catalog)
│   │   └── builtins/{iot_light,home_iot,smart_home,home_assistant}.py + get_catalog
│   ├── lm/                        # Module 1 — language-model production
│   │   ├── client.py              # ModelClient protocol + ModelResult + ModelOutputError
│   │   ├── dashscope.py           # QwenJSONDSL / Freeform / Native clients + QwenConfig
│   │   ├── rules.py               # Deterministic rule-based client (iot_light_5)
│   │   ├── local_hf.py            # LocalHFClient + load/unload/status + evaluate_lora
│   │   ├── registry.py            # configs/models.yaml → ModelSpec / Registry / build_client_from_spec
│   │   ├── serve.py               # Server.serve(ServeRequest) → ServeResult (F⁰/Fᴷ + hooks_diff)
│   │   ├── grammar.py             # catalog → JSON Schema → XGrammar LogitsProcessor
│   │   ├── prompts.py             # SYSTEM_PROMPT_TEMPLATE + _dsl_messages (parity SSOT)
│   │   ├── synth/{ingest,pipeline,strategies}.py     # Teacher-driven synthesis
│   │   └── finetune/sft.py        # LoRA SFT (TRL SFTTrainer, assistant_only_loss)
│   ├── analyzer/                  # Module 2 — statistical-analysis + compiler-correction (goal §2)
│   │   ├── trace.py               # Trace + TraceStore (append-only JSONL substrate)
│   │   ├── taxonomy.py            # FailureType enum (14 buckets) + classify()
│   │   ├── metrics.py             # summarize, CaseResult, RunResult, graded_score
│   │   ├── rules.py               # RulePatch proposals from failure histograms (R1-R11 promoted)
│   │   ├── repair.py              # RepairConfig + run_dsl_with_repair + RepairExhaustedError
│   │   ├── verifier.py            # Continuous reward fn make_verifier(catalog)
│   │   ├── reports.py             # Markdown renderer over summary JSON
│   │   ├── ledger.py              # Append-only Event rows; closed EVENT_NAMES (not a bus)
│   │   ├── labels.py              # LabelRecord / LabelStore / resolve_gold / export_sft
│   │   ├── manifest.py            # RunManifest + write_run_bundle; list_runs is the list SSOT
│   │   ├── compare.py             # compare_runs: transition matrix + paired-bootstrap ΔEM CI
│   │   ├── decisions.py           # PatchDecision ledger (blind/after_preview/ported) + precision
│   │   ├── corrections.py         # F⁰/Fᴷ attribution per (tool, hook_kind) → retire_rule patches
│   │   ├── catalogs.py            # catalog_id → Catalog (builtin | compiled/<sha12> | bfcl/*)
│   │   └── analyze.py             # Composite analyze_run(): classify → attribute → synthesise
│   ├── benchmarks/                # Consumers — emit traces
│   │   ├── iot/{dataset,runner,scaling,executor}.py
│   │   └── bfcl/{loader,grader,case_catalog,runner}.py
│   └── console/                   # Consumer — operator HTTP console (stdlib only)
│       ├── server.py              # ThreadingHTTPServer on 127.0.0.1 + static web/
│       ├── api.py                 # /api/* route table (truth about response shapes)
│       ├── writer.py              # Single daemon thread; every file write is queued here
│       ├── seed.py                # rules-seed + rules-degraded-seed offline runs
│       └── __main__.py            # serve | seed | analyze | export-labels | compare
├── configs/
│   └── models.yaml                # Checked-in model registry (rules / openai_compat / local_hf)
├── web/                           # Console pages: chat, catalog, traces, rules, loops (live)
│   ├── assets/{style.css,api.js}  # No build step, no external libs
│   └── mockups/*.dc.html          # Static hi-fi artboards the live pages were drawn from
├── examples/
│   ├── iot_light/
│   │   └── generate_dataset.py     # 500-case deterministic dataset
│   └── bfcl/v4/
│       ├── sample/                  # Deterministic seed=42 subsample (5×100)
│       ├── subsample.py             # Regenerate the sample
│       └── SOURCE.md                # Pinned upstream commit SHA
├── tests/                          # Pytest test suite (`python -m pytest -q` for the count)
├── docs/
│   ├── goal/goal.md                       # Original goal (Korean)
│   ├── factory_design.md                  # Cornerstone design narrative
│   ├── redesign_plan.md                   # Old→new path migration map
│   ├── poc_verification_report.md         # Research report
│   ├── bfcl_m1_m4_result_report.md        # BFCL M1'~M4' results
│   ├── bfcl_m5_abstention_report.md       # M5 null-action contract
│   ├── bfcl_flash_replay_report.md        # qwen3.6-flash replay
│   ├── refine_proposal.md                 # Operator-console design rationale (Korean)
│   └── tasks/                              # 6-section task specs (+ legacy/)
└── runs/
    ├── m{2,3,4}/                           # IoT scaling/repeat/repair runs
    ├── bfcl/[flash/]                       # BFCL per-phase summaries + cases
    └── traces/<catalog_id>/<run_id>/       # Run bundles: traces.jsonl + manifest.json + sidecars
```

## Building and Running

### Prerequisites

- Python 3.11+
- Install dependencies: `pip install -e .`
- For LLM evaluation: Set `DASHSCOPE_API_KEY` environment variable

### Installation

```bash
pip install -e ".[dev]"
```

### Running Tests

```bash
# Full test suite
pytest

# Or via module
python -m pytest
```

### Running Evaluation

```bash
# Deterministic offline evaluation (no API cost)
python -m ganglion.cli --llm rules --tier iot_light_5

# Qwen structured JSON DSL evaluation (default: 500 cases)
python -m ganglion.cli --llm qwen --tier iot_light_5

# Qwen native tool calling baseline
python -m ganglion.cli --llm qwen-native --tier iot_light_5

# Qwen freeform (no response_format)
python -m ganglion.cli --llm qwen-text --tier iot_light_5

# Qwen thinking mode (no response_format)
python -m ganglion.cli --llm qwen-thinking --tier iot_light_5

# With repair loop (auto-retry on validation failure)
python -m ganglion.cli --llm qwen --tier iot_light_5 --repair --repair-max-attempts 1

# With repeated measurements (for latency statistics)
python -m ganglion.cli --llm qwen --tier iot_light_5 --repeat 5

# Limit cases for quick testing
python -m ganglion.cli --llm qwen --limit 10

# BFCL v4 single-turn external benchmark (per-case Catalog from BFCL `function`)
python -m ganglion.cli --llm qwen        --bfcl simple_python --bfcl-per-category 100
python -m ganglion.cli --llm qwen-native --bfcl all           --bfcl-per-category 100
python -m ganglion.cli --llm qwen        --bfcl irrelevance   --bfcl-allow-empty-calls
python -m ganglion.cli --llm qwen        --bfcl callable      --repair
python -m ganglion.cli --llm qwen        --bfcl all --bfcl-output runs/bfcl/<name>_cases.jsonl \
                                                  > runs/bfcl/<name>_summary.json
```

`--bfcl` choices: `simple_python` | `multiple` | `parallel` | `parallel_multiple` | `irrelevance` | `callable` (the four non-irrelevance categories) | `all` (all five). `rules` client has no BFCL adapter.

### Selecting a Model from the Registry

`configs/models.yaml` is the checked-in list of models the CLI and the console
share. `--model <model_id>` addresses an entry directly and is mutually
exclusive with `--llm`, which stays as a legacy alias (`rules` → `rules`,
`qwen` → `qwen3.6-plus@dashscope`, `qwen-text` / `qwen-thinking` /
`qwen-native` → the matching `@dashscope` entries). With neither flag the run
uses `rules`.

```bash
# Registry id instead of --llm
python -m ganglion.cli --model rules --tier iot_light_5

# A different registry file
python -m ganglion.cli --model qwen3.6-plus@dashscope --models configs/models.yaml --limit 2
```

An entry's `kind` picks the client family:

| `kind` | Client | Notes |
|--------|--------|-------|
| `rules` | `RuleBasedJSONDSLClient` | Offline regex stand-in; the three scaling tiers (measured em 1.0 on each), not `home_assistant_4` |
| `openai_compat` | Qwen DSL / freeform / thinking / native | `provider`: `dashscope` \| `vllm` \| `openai` — only changes how the thinking switch is sent |
| `local_hf` | `LocalHFClient` | In-process transformers + optional PEFT LoRA; `base_model`, `adapter_dir`, `device`, `dtype`, `max_new_tokens`, `grammar_mask` |

`client` picks the prompt/decoding path: `json-dsl` | `freeform` | `thinking` |
`native`. String values support `${VAR:-default}` expansion; secrets are never
stored in the file — `api_key_env` names the environment variable. LoRA
adapter directories found under `runs/` are auto-discovered as `local:<dir>`
entries (a YAML entry with the same id wins).

### Persisting a Run

`--trace-store <dir>` turns a run into a run bundle under
`<dir>/<catalog_id>/<run_id>/` — `traces.jsonl` (one `Trace` per invocation,
with raw output preserved even on failure), `manifest.json`, `summary.json`,
`report.md`, `describe.json` and `events.jsonl`. `--run-id` (default
`<model_id>-<YYYYmmdd-HHMMSS>`), `--iteration` and `--parent-run` fill the
manifest fields that the console's Loops page and `compare_runs` read. A BFCL
run writes one bundle per category (`catalog_id = bfcl/<category>`,
`metric_kind = ast_match`) and skips `report.md` / `describe.json`, because
BFCL catalogs are per-case and cannot be resolved back from a `catalog_id`.

```bash
python -m ganglion.cli --model rules --trace-store runs/traces --run-id rules-0 --iteration 0
python -m ganglion.cli --model rules --trace-store runs/traces --run-id rules-1 \
                       --iteration 1 --parent-run rules-0
```

### Operator Console

A local web UI over those run bundles: chat with a model, label what came
back, look at the patches the analyzer proposes and at the iteration-to-
iteration deltas. Standard library only — no dependency beyond the two the
package already has.

```bash
# Two offline runs (no API key, no GPU) plus their analysis
python -m ganglion.console seed  --runs runs/traces --limit 100

# Pages + /api/* on http://127.0.0.1:8766 ($GANGLION_CONSOLE_PORT overrides)
python -m ganglion.console serve --runs runs/traces --web web

# The same surface without HTTP
python -m ganglion.console analyze iot_light_5 rules-degraded-seed --runs runs/traces
python -m ganglion.console export-labels iot_light_5 --runs runs/traces --zones train
python -m ganglion.console compare iot_light_5 rules-seed rules-degraded-seed --runs runs/traces
```

Live pages: `chat.html` (prompt → Action IR → tool calls, F⁰ vs Fᴷ, verdict
bar), `catalog.html` (original schema ⇄ Action IR renders ⇄ canonicalisation,
plus compiling a pasted tool list), `traces.html` (label queue with filters and
keyboard labelling), `rules.html` (patch proposals → blind decision → ported;
hook attribution → retire candidates), `loops.html` (per-run manifest, KPIs,
failure histogram, transition matrix vs. parent, training provenance). The
other sheets are still mockups and say so with a "mock" chip.

Every command in this file assumes the repo root as cwd: datasets, `configs/`
and the console's `--runs` / `--web` defaults are all relative paths.

### Dataset Generation

```bash
# Regenerate 500-case IoT dataset
python examples/iot_light/generate_dataset.py
```

### Catalog Size Measurement

```bash
# Measure DSL vs native schema sizes across tiers
python -m ganglion.benchmarks.iot.scaling
```

### Environment Variables

```bash
export DASHSCOPE_API_KEY=your_api_key
export GANGLION_MODEL=qwen3.6-plus      # Expanded inside configs/models.yaml
export DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
export GANGLION_MODELS=configs/models.yaml   # Registry path (--models overrides)
export GANGLION_VLLM_MODEL=Qwen/Qwen3-1.7B   # The local@vllm entry
export GANGLION_VLLM_BASE_URL=http://127.0.0.1:8000/v1
export GANGLION_CONSOLE_PORT=8766            # Console default
export GANGLION_LABELER=operator             # Author stamped on POST /api/labels
```

`GANGLION_ENABLE_THINKING` is now only read by `QwenConfig.from_env()`, i.e.
when a client is constructed directly. Everything that goes through the
registry — the CLI and the console — ignores it; thinking is the
`client: thinking` registry entry instead.

Dependencies: the package itself needs only `openai` and `pyyaml`, and the
console adds nothing on top. `kind: local_hf` models need the `[factory]`
extra (torch + transformers + peft); `xgrammar` is optional and a missing one
downgrades `grammar_mask: true` to a one-time warning.

## Development Conventions

### Code Style

- **Type hints:** Use `from __future__ import annotations` and modern type hints
- **Dataclasses:** Prefer `@dataclass(frozen=True)` for immutable configs
- **Naming:** snake_case for functions/variables, PascalCase for classes
- **Docstrings:** Minimal; focus on "why" not "what"

### Testing Practices

- **Test location:** `tests/` directory, mirroring package structure
- **Naming:** `test_*.py` files with `test_*` functions
- **Fixtures:** Use pytest fixtures for shared setup
- **Determinism:** Offline tests use `--llm rules` for reproducibility
- **Coverage:** Test validator, repair loop, catalog tiers, JSON extraction

### Architecture Patterns

1. **Catalog-driven design:** All tool definitions derive from `ToolSpec` in `ganglion/contract/tool_spec.py`
2. **Validator first:** JSON DSL is validated before emission to tool executor
3. **Repair loop:** Optional retry mechanism for validation failures
4. **Tier system:** Three tool tiers (5, 20, 50 tools) for scaling experiments, plus `home_assistant_4` — a projection of `iot_light_5` onto Home Assistant's Assist API tools with its own derived dataset (`examples/home_assistant/`)
5. **Three renders from one traversal:** `render_json_dsl()` is what the model sees, `render_openai_tools()` is the native baseline, and `describe()` is the complete JSON snapshot the `cf-<sha12>` catalog fingerprint is taken over
6. **Files are the seam:** no in-process bus. Runs, traces, labels, events, classifications, patches, decisions and comparisons are append-only sidecars in the run directory; `list_runs` scanning `**/manifest.json` is the source of truth for what runs exist
7. **Propose, never apply:** the analyzer emits `RulePatch` proposals and `apply_patch` is a pure preview; a patch reaches a builtin catalog only through a reviewed code edit recorded with its `commit_sha`

### Key Design Decisions

- **Structured output:** Default path uses Qwen's `response_format={"type": "json_object"}`
- **Thinking mode:** Disabled by default (high cost, no benefit for simple DSL conversion)
- **Normalization:** Validator normalizes aliases (e.g., "주방" → "kitchen", "영화 모드" → "movie")
- **Exact match:** Evaluated after semantic normalization, not raw string comparison

### Substrate Invariants (keep these when extending)

- **A failed inference still produces a full trace.** The json-dsl path raises `RepairExhaustedError` (`ganglion/analyzer/repair.py`); the rules, freeform, native and local-HF paths raise `ModelOutputError` (`ganglion/lm/client.py`). Both carry `.raw` and `.attempts`, and runners record `raw=getattr(exc, "raw", None)` rather than re-raising. New clients and runners must preserve those attributes — failure classification, rule synthesis and F⁰/Fᴷ attribution all read the failed output.
- **`Trace.plan` means *validated* plan and is `None` on failure.** `Trace.raw_plan` holds the decoded-but-unvalidated JSON of the last attempt that parsed as an object. Never write an unvalidated dict into `plan`.
- **Trace ids changed shape.** `_derive_trace_id` now hashes `catalog_id` and the new `Trace.repeat_index` alongside `(case_id, model_id, run_id, attempts)`, so ids recorded before the console batch do not reproduce — and `--repeat N` (or a console re-asking the same prompt) yields distinct traces instead of collapsing into one.
- **`TraceStore` is lock-guarded and re-reads changed shards**, so a running console sees runs a CLI process wrote. Still one writer per file: inside the console every write is queued on `ganglion/console/writer.py`.

## JSON DSL Specification

### Structure

```json
{
  "calls": [
    {
      "action": "set_light",
      "args": {
        "room": "living",
        "state": "on",
        "brightness": 70
      }
    }
  ]
}
```

### Supported Actions (IoT Light Tier)

| Action | Args | Description |
|--------|------|-------------|
| `list_devices` | `{}` | List all light devices |
| `get_light_state` | `{room: str}` | Get current state of room light |
| `set_light` | `{room, state, brightness?, color_temp?}` | Set light state |
| `schedule_light` | `{room, at, state, brightness?}` | Schedule light action |
| `create_scene` | `{name, actions: [set_light]}` | Create named scene |

### Normalization Rules

- **Rooms:** Korean/English aliases → canonical (e.g., "거실", "living room" → "living")
- **States:** "켜", "on", "turn on" → "on"; "꺼", "off" → "off"
- **Brightness:** "70%", "70" → integer `70`
- **Color temp:** "따뜻하게", "warm" → "warm"; "중립" → "neutral"; "차갑게" → "cool"
- **Scene names:** "영화 모드", "movie mode" → "movie"
- **Time:** Various formats → `HH:MM` 24-hour format

## Tool Tiers

| Tier | Tools | DSL Chars | Native Chars | Native/DSL Ratio |
|------|-------|-----------|--------------|------------------|
| `iot_light_5` | 5 | 1,334 | 2,108 | 1.58x |
| `home_iot_20` | 20 | 2,552 | 6,842 | 2.68x |
| `smart_home_50` | 50 | 4,670 | 15,841 | 3.39x |
| `home_assistant_4` | 4 | 1,491 | 1,733 | 1.16x |

Regenerate the table with `python -m ganglion.benchmarks.iot.scaling`; the
numbers move whenever a `ToolSpec` changes.

Select tier via `--tier` flag:
```bash
python -m ganglion.cli --llm qwen --tier smart_home_50
```

## Milestones Summary

| Milestone | Status | Description |
|-----------|--------|-------------|
| M1 | ✅ Complete | IoT 500-case dataset, baseline validation |
| M2 | ✅ Complete | Tool scaling (5→50 tools), token efficiency |
| M3 | ✅ Complete | Repeat measurement infrastructure (n=250) |
| M4 | ✅ Complete | Repair loop implementation |
| M5 | ✅ Complete | External schema → Catalog compiler + BFCL adapter; `{"calls":[]}` null-action contract |
| M1'~M5' | ✅ Complete | BFCL v4 replay on qwen3.6-plus (86.2% AST, -62% input, -25% latency) and qwen3.6-flash |

## Related Documentation

- **Research Report:** `docs/poc_verification_report.md` (Korean, detailed analysis)
- **BFCL Reports:** `docs/bfcl_m1_m4_result_report.md`, `docs/bfcl_m5_abstention_report.md`, `docs/bfcl_flash_replay_report.md`
- **Project Goals:** `overview.md` (Korean, high-level vision)
- **Dataset:** `examples/iot_light/dataset.jsonl` (500 cases) and `examples/bfcl/v4/sample/*.jsonl`
- **Operator Console:** `docs/refine_proposal.md` (design rationale, Korean), `docs/tasks/console_operator.md` (spec), `ganglion/console/README.md` (implementation notes), `web/mockups/` (screen artboards)
- **Task specs:** `docs/tasks/README.md` — the index; every module's surface has a six-section spec there, and the spec is written before the code

## Known Limitations

1. **Synthetic IoT dataset:** Template-generated, not real user queries (BFCL covers the real-world side)
2. **Single-turn only:** BFCL multi-turn / Java / live categories are out-of-scope
3. **Validator complexity:** Alias rules may require maintenance as tools grow
4. **Provider coverage:** The registry accepts any OpenAI-compatible endpoint (`dashscope` / `vllm` / `openai`) and in-process HF models, but every measured result to date is Qwen on DashScope; BFCL's native-baseline path uses OpenAI-compatible `tools=[...]`
5. **Latency variance:** Single-region API calls, no distributed statistics
6. **Console scope:** loopback-only, single writer thread, no auth; `bfcl/<category>` runs are read-only there because per-case catalogs cannot be resolved back from a `catalog_id`

## Future Work

1. No-call prompt tuning: tighten `irrelevance` semantic gating (current DSL 90% vs target ≥native)
2. Semantic abstention classifier: gate empty-plan emission when tool/request match is weak
3. M6 value/unit canonicalization in compiler/validator layer
4. MCP schema → DSL catalog auto-generation beyond compile-time `RawArg` fallback
5. Explore fine-tuning/LoRA for small model optimization (see `ganglion/lm/finetune/` and `ganglion/lm/synth/`)
6. Close the console loop end to end: labels → corrected-SFT export → LoRA run → a new run bundle compared against its parent
