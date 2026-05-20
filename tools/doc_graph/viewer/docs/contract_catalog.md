[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[contract_schema_compiler]] · [[contract_null_action]]

# contract_catalog

The **Catalog / ToolSpec / ArgSpec** contract — the load-bearing surface of Module 3 ([`contract/`](../goal/goal.md)) that Modules 1 (`lm/`) and 2 (`analyzer/`) speak through. A single `Catalog` instance is the source-of-truth: it renders the compact DSL prompt **and** the OpenAI native tool schema **and** validates model output back into an `ActionPlan`. This dual rendering is what makes the IR-vs-native comparison apples-to-apples.

## Role

Define the immutable Catalog contract (Catalog, ToolSpec, ArgSpec taxonomy, ActionPlan) that every other module imports — never mutates — to render prompts, validate outputs, and compare plans by value.

## Scope

- **in-scope**:
  - `Catalog` frozen dataclass — fields `name`, `tools: tuple[ToolSpec, ...]`, `examples`, `extra_rules`, `allow_empty_calls: bool`, `default_strip_unknown_args: bool`. Methods `get_tool`, `render_json_dsl()`, `render_openai_tools()`, `parse_json_dsl()`, `validate()`, `validate_call()`.
  - **Dual rendering** from one SSOT: `render_json_dsl()` produces the short text appended to the system prompt; `render_openai_tools()` produces the full OpenAI `tools=[...]` list. Adding a tool updates both.
  - `ToolSpec` frozen dataclass — `name`, `description`, `args: tuple[ArgSpec, ...]`, `dsl_args_override`, `custom_validator`, plus the **post-correction hooks**: `defaults_when_missing: tuple[DefaultRule, ...]`, `strip_unknown_args: bool | None`, `prompt_correction: PromptCorrection | None`.
  - ArgSpec taxonomy (`EnumArg`, `IntArg`, `NumberArg`, `StringArg`, `BoolArg`, `TimeArg`, `RawArg`) with the locale/domain canonicalisation hooks `EnumArg.aliases` and `StringArg.aliases` (e.g. `"거실" → "living"`, `"movie mode" → "movie"`). `RawArg` is the escape hatch for shapes the generic renderer cannot express, paired with `ToolSpec.custom_validator`.
  - `parse_json_dsl()` pipeline: `str | Mapping → strict JSON → fenced ```json``` fallback → first decodable `{...}` → `validate()` → `ActionPlan(calls=tuple[ToolCall, ...])`. Frozen `ToolCall(name, args)`; value equality semantics so `result.plan == expected` is the exact-match metric.
  - `allow_empty_calls` opt-in flag — linkage point with [[contract_null_action]] (does not own the abstention semantics, only carries the boolean).
  - `DSLValidationError` taxonomy with field-path information for unknown tool, missing required arg, alias miss, enum miss, type miss.
  - Target post-redesign paths: `ganglion/contract/catalog.py`, `ganglion/contract/tool_spec.py`, `ganglion/contract/arg_spec.py`, `ganglion/contract/types.py`, `ganglion/contract/parse.py`, `ganglion/contract/builtins/{iot_light,home_iot_20,smart_home_50}.py` ([[benchmark_iot]] hands these out).
- **out-of-scope**:
  - External schema ingestion (OpenAI / MCP / bare function schemas / BFCL `function` entries) → [[contract_schema_compiler]].
  - Benchmark-specific per-case Catalog construction (e.g. one Catalog per BFCL row) → [[benchmark_iot]], [[benchmark_bfcl]].
  - Automated alias / default discovery from failure traces → [[analyzer_rule_synthesis]] *proposes* patches in the `DefaultRule` / `PromptCorrection` shape, but this doc only defines that shape — never produces patches itself.
  - Provider-specific output adapters beyond the neutral `{name, arguments}` shape (Anthropic `tool_use` blocks, Gemini `functionCall`, etc.) — deferred.
  - Direct execution of tool calls — that boundary belongs to the runtime executor, not the contract.
  - Repair loop control flow → [[analyzer_repair_policy]]; the contract only raises `DSLValidationError`, it does not retry.
- **on violation**: if a Catalog change requires touching `lm/`, `analyzer/`, or `benchmarks/` consumers in the same patch, **stop**. Open a separate task for the consumer-side change and connect via the events listed below — never inline-edit consumers from this task.

## Procedure

```
construct (user-authored, e.g. ganglion/contract/builtins/iot_light.py):
    args = (
        EnumArg(name="room", values=("living","bedroom"), aliases={"거실":"living"}),
        IntArg(name="brightness", min=0, max=100, required=False),
    )
    tool = ToolSpec(
        name="set_light",
        description="…",
        args=args,
        defaults_when_missing=(DefaultRule(arg="brightness", value=70),),
        strip_unknown_args=True,
        prompt_correction=PromptCorrection(when=…, suggest=…),
    )
    catalog = Catalog(
        name="iot_light_5",
        tools=(tool, …),
        allow_empty_calls=False,
        default_strip_unknown_args=False,
    )
    register(catalog)                              # → emits contract.catalog.published

render (consumed by lm/ and benchmarks/):
    dsl_text     = catalog.render_json_dsl()       # short, system-prompt-appended
    openai_tools = catalog.render_openai_tools()   # full OpenAI tools=[...]

validate (consumed by lm/ post-inference and analyzer/ on replay):
    raw: str | Mapping
    plan: ActionPlan = catalog.parse_json_dsl(raw)
    #   str path: strict JSON → fenced ```json``` → first decodable {...}
    #   then validate(): unknown tool / missing arg / alias miss / enum miss → DSLValidationError(field_path=…)
    #   value equality: plan == expected_plan is the exact-match metric

on DSLValidationError:
    surface to caller — never auto-repair here. analyzer_repair_policy decides.
```

## Contract

- **in**: a `Catalog` instance constructed from `ToolSpec`s; a raw model output `str | Mapping`; an optional source `prompt: str` for `prompt_correction` hooks.
- **out**:
  - `render_json_dsl() -> str` — deterministic, suffix newline, contains the no-call line iff `allow_empty_calls=True`.
  - `render_openai_tools() -> list[dict]` — OpenAI `tools=[...]` schema list (one entry per `ToolSpec`).
  - `parse_json_dsl(raw) -> ActionPlan` — frozen `ActionPlan(calls=tuple[ToolCall, ...])` with value equality.
  - `validate_call(name, args) -> ToolCall` — single-call validation surface for analyzer replay.
- **event**:
  - emit `contract.catalog.published(catalog_id, version)` on `register()` of a Catalog (built-in or compiler-produced).
  - consume `analyzer.rule.proposed(catalog_id, rule_patch, evidence)` only as a *proposal* — patches enter the codebase via human review, never auto-applied.
- **failure**:
  - Malformed JSON, no parseable object found → `DSLValidationError("could not parse JSON DSL output")`.
  - `calls` missing → `DSLValidationError("missing 'calls' field")`.
  - `calls` empty against `allow_empty_calls=False` → `DSLValidationError("'calls' must not be empty")` (see [[contract_null_action]]).
  - Unknown tool name → `DSLValidationError(f"unknown tool: {name}", field_path="calls[i].action")`.
  - Missing required arg / enum miss / type miss → `DSLValidationError(..., field_path="calls[i].args.<arg>")`.
- **success**: every shipped fixture in `tests/test_validator.py`, `tests/test_catalog_tiers.py`, and `tests/test_dataset_integrity.py` round-trips (`Catalog.parse_json_dsl(row["expected"]) == ActionPlan(...)`). `pytest -q` green on the contract test set is the machine-verifiable predicate.

## Observation

- `catalog_publish_count` = count of `contract.catalog.published` events per process.
- `parse_failure_rate` = `DSLValidationError`-raising calls / total `parse_json_dsl()` calls, partitioned by `failure.field_path` head segment.
- `dsl_render_chars` = `len(catalog.render_json_dsl())` per catalog — the IR-compression headline number.
- `openai_render_chars` = `len(json.dumps(catalog.render_openai_tools()))` per catalog — the native-baseline comparator. Ratio `dsl_render_chars / openai_render_chars` is the evidence the POC publishes.
- `parse_strategy_counts` = breakdown by `strict | fenced | embedded` from the lenient parser path, surfaced for [[analyzer_metrics]] consumption.
