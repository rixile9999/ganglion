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
│   │   └── builtins/{iot_light,home_iot,smart_home}.py + get_catalog
│   ├── lm/                        # Module 1 — language-model production
│   │   ├── client.py              # ModelClient protocol + ModelResult
│   │   ├── dashscope.py           # QwenJSONDSL / Freeform / Native clients + QwenConfig
│   │   ├── rules.py               # Deterministic rule-based client (iot_light_5)
│   │   ├── local_hf.py            # Local HF + PEFT inference (generate_dsl, evaluate_lora)
│   │   ├── grammar.py             # catalog → JSON Schema → XGrammar LogitsProcessor
│   │   ├── prompts.py             # SYSTEM_PROMPT_TEMPLATE + _dsl_messages (parity SSOT)
│   │   ├── synth/{ingest,pipeline,strategies}.py     # Teacher-driven synthesis
│   │   └── finetune/sft.py        # LoRA SFT (TRL SFTTrainer, assistant_only_loss)
│   ├── analyzer/                  # Module 2 — statistical-analysis + compiler-correction (goal §2)
│   │   ├── trace.py               # Trace + TraceStore (append-only JSONL substrate)
│   │   ├── taxonomy.py            # FailureType enum (14 buckets) + classify()
│   │   ├── metrics.py             # summarize, CaseResult, RunResult, graded_score
│   │   ├── rules.py               # RulePatch proposals from failure histograms (R1-R11 promoted)
│   │   ├── repair.py              # RepairConfig + run_dsl_with_repair
│   │   ├── verifier.py            # Continuous reward fn make_verifier(catalog)
│   │   └── reports.py             # Markdown renderer over summary JSON
│   └── benchmarks/                # Consumers — emit traces
│       ├── iot/{dataset,runner,scaling,executor}.py
│       └── bfcl/{loader,grader,case_catalog,runner}.py
├── examples/
│   ├── iot_light/
│   │   └── generate_dataset.py     # 500-case deterministic dataset
│   └── bfcl/v4/
│       ├── sample/                  # Deterministic seed=42 subsample (5×100)
│       ├── subsample.py             # Regenerate the sample
│       └── SOURCE.md                # Pinned upstream commit SHA
├── tests/                          # Pytest test suite (232 tests)
├── docs/
│   ├── goal/goal.md                       # Original goal (Korean)
│   ├── factory_design.md                  # Cornerstone design narrative
│   ├── redesign_plan.md                   # Old→new path migration map
│   ├── poc_verification_report.md         # Research report
│   ├── bfcl_m1_m4_result_report.md        # BFCL M1'~M4' results
│   ├── bfcl_m5_abstention_report.md       # M5 null-action contract
│   ├── bfcl_flash_replay_report.md        # qwen3.6-flash replay
│   └── tasks/                              # 6-section task specs (+ legacy/)
└── runs/
    ├── m{2,3,4}/                           # IoT scaling/repeat/repair runs
    └── bfcl/[flash/]                       # BFCL per-phase summaries + cases
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
export GANGLION_MODEL=qwen3.6-plus      # Default model
export DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
export GANGLION_ENABLE_THINKING=false   # Set to true to enable thinking mode
```

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
4. **Tier system:** Three tool tiers (5, 20, 50 tools) for scaling experiments

### Key Design Decisions

- **Structured output:** Default path uses Qwen's `response_format={"type": "json_object"}`
- **Thinking mode:** Disabled by default (high cost, no benefit for simple DSL conversion)
- **Normalization:** Validator normalizes aliases (e.g., "주방" → "kitchen", "영화 모드" → "movie")
- **Exact match:** Evaluated after semantic normalization, not raw string comparison

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
| `iot_light_5` | 5 | 1,307 | 2,062 | 1.58x |
| `home_iot_20` | 20 | 2,525 | 6,796 | 2.69x |
| `smart_home_50` | 50 | 4,643 | 15,795 | 3.40x |

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

## Known Limitations

1. **Synthetic IoT dataset:** Template-generated, not real user queries (BFCL covers the real-world side)
2. **Single-turn only:** BFCL multi-turn / Java / live categories are out-of-scope
3. **Validator complexity:** Alias rules may require maintenance as tools grow
4. **Provider lock-in:** Currently tied to Qwen DashScope; BFCL native-baseline path uses OpenAI-compatible `tools=[...]`
5. **Latency variance:** Single-region API calls, no distributed statistics

## Future Work

1. No-call prompt tuning: tighten `irrelevance` semantic gating (current DSL 90% vs target ≥native)
2. Semantic abstention classifier: gate empty-plan emission when tool/request match is weak
3. M6 value/unit canonicalization in compiler/validator layer
4. MCP schema → DSL catalog auto-generation beyond compile-time `RawArg` fallback
5. Explore fine-tuning/LoRA for small model optimization (see `ganglion/factory/`)
