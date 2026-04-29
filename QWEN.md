# Ganglion — *spinal tool calling for LLMs*

## Project Overview

**Ganglion** is a research prototype testing whether a compact intermediate representation (JSON DSL) can replace full tool schemas in LLM prompts while preserving tool-call accuracy. The goal is to reduce token costs and latency for agent tool-calling workflows. The metaphor is a spinal reflex / insect ganglion — a fast, low-overhead reaction circuit that doesn't need the full "cortex" of native tool schemas.

**Core Hypothesis:** Instead of providing full tool schemas to the LLM on every request, have the LLM generate a short intermediate representation (IR) that a deterministic parser/validator converts into actual tool calls. This reduces token consumption and improves response latency.

**Key Results (M1-M4):**
- **46-69% token reduction** vs native tool calling (scales with tool count)
- **100% exact match** accuracy on 500-case IoT dataset
- **19% faster** mean latency vs native tool calling
- Validated across 3 tool tiers: 5, 20, and 50 tools

## Project Structure

```
reflex-language-model/
├── ganglion/                 # Main package
│   ├── dsl/                 # DSL definition & validation
│   │   ├── catalog.py       # Tool catalog, DSL rendering, OpenAI tools
│   │   ├── tool_spec.py     # ToolSpec, ArgSpec definitions (EnumArg, IntArg, etc.)
│   │   ├── validator.py     # JSON DSL validation & normalization
│   │   ├── emitter.py       # Deterministic tool-call emission
│   │   ├── json_extract.py  # Lenient JSON extraction from model output
│   │   └── types.py         # DSL type definitions (ActionPlan, ActionCall)
│   ├── runtime/             # Model runtime implementations
│   │   ├── qwen.py          # Qwen API clients (DSL, native, freeform)
│   │   ├── rules.py         # Deterministic rule-based client (testing)
│   │   ├── executor.py      # Mock tool executor
│   │   └── types.py         # Runtime types (ModelResult, etc.)
│   ├── schema/              # Tool schema definitions
│   │   ├── iot_light.py     # 5-tool IoT lighting domain
│   │   ├── home_iot.py      # 20-tool home automation domain
│   │   ├── smart_home.py    # 50-tool smart home domain
│   │   └── __init__.py      # get_catalog(tier) registry
│   └── eval/                # Evaluation infrastructure
│       ├── runner.py        # Offline/LLM evaluation runner
│       ├── metrics.py       # Exact match, token stats, latency
│       └── scaling.py       # Catalog size measurement
├── examples/
│   └── iot_light/
│       └── generate_dataset.py  # 500-case deterministic dataset
├── tests/                   # Pytest test suite
├── docs/
│   └── poc_verification_report.md  # Detailed Korean research report
└── runs/                    # Evaluation run outputs (generated)
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
python -m ganglion.eval.runner --llm rules --tier iot_light_5

# Qwen structured JSON DSL evaluation (default: 500 cases)
python -m ganglion.eval.runner --llm qwen --tier iot_light_5

# Qwen native tool calling baseline
python -m ganglion.eval.runner --llm qwen-native --tier iot_light_5

# Qwen freeform (no response_format)
python -m ganglion.eval.runner --llm qwen-text --tier iot_light_5

# Qwen thinking mode (no response_format)
python -m ganglion.eval.runner --llm qwen-thinking --tier iot_light_5

# With repair loop (auto-retry on validation failure)
python -m ganglion.eval.runner --llm qwen --tier iot_light_5 --repair --repair-max-attempts 1

# With repeated measurements (for latency statistics)
python -m ganglion.eval.runner --llm qwen --tier iot_light_5 --repeat 5

# Limit cases for quick testing
python -m ganglion.eval.runner --llm qwen --limit 10
```

### Dataset Generation

```bash
# Regenerate 500-case IoT dataset
python examples/iot_light/generate_dataset.py
```

### Catalog Size Measurement

```bash
# Measure DSL vs native schema sizes across tiers
python -m ganglion.eval.scaling
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

1. **Catalog-driven design:** All tool definitions derive from `ToolSpec` in `ganglion/dsl/tool_spec.py`
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
python -m ganglion.eval.runner --llm qwen --tier smart_home_50
```

## Milestones Summary

| Milestone | Status | Description |
|-----------|--------|-------------|
| M1 | ✅ Complete | IoT 500-case dataset, baseline validation |
| M2 | ✅ Complete | Tool scaling (5→50 tools), token efficiency |
| M3 | ✅ Complete | Repeat measurement infrastructure (n=250) |
| M4 | ✅ Complete | Repair loop implementation |
| M5 | ⏳ Pending | MCP schema → DSL catalog auto-generation |

## Related Documentation

- **Research Report:** `docs/poc_verification_report.md` (Korean, detailed analysis)
- **Project Goals:** `overview.md` (Korean, high-level vision)
- **Dataset:** `examples/iot_light/dataset.jsonl` (500 cases)

## Known Limitations

1. **Synthetic dataset:** Template-generated, not real user queries
2. **Single domain:** IoT lighting only (no multi-turn, no tool dependencies)
3. **Validator complexity:** Alias rules may require maintenance as tools grow
4. **Provider lock-in:** Currently tied to Qwen structured output
5. **Latency variance:** Single-region API calls, no distributed statistics

## Future Work

1. Expand to 20-50 tools with realistic usage patterns
2. Add repair loop escalation for native tool calling failures
3. Implement MCP schema → DSL catalog auto-generation
4. Test on external benchmarks (e.g., BFCL)
5. Explore fine-tuning/LoRA for small model optimization
