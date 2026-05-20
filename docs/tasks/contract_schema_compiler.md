[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Design doc: [docs/tool_schema_compiler.md](../tool_schema_compiler.md) · Supersedes: [legacy/tool_schema_compiler](./legacy/tool_schema_compiler.md)

# contract_schema_compiler

Compile arbitrary external tool schemas (OpenAI / DashScope / MCP / bare function schemas / BFCL `function` entries) into a Ganglion `Catalog` so DSL and native baselines render from the same single source of truth.

## Role

Translate external tool-calling schemas into Ganglion `ToolSpec` / `Catalog` and expose a runtime mapper that bridges the compiled DSL output back to a provider-neutral `{name, arguments}` call. Lives under the redesigned `contract/` module alongside [[contract_catalog]] and [[contract_null_action]].

## Scope

- in-scope:
  - Public entry points `compile_tool_calling_schema(schema, *, name, examples, extra_rules, allow_empty_calls)` and `compile_openai_tools(tools, ...)` — the only public surface.
  - Target implementation path: `ganglion/contract/schema_compiler.py` (was `ganglion/dsl/compiler.py`).
  - Accepted input shapes:
    1. OpenAI-compatible `{"type":"function","function":{...}}` tools.
    2. Bare `{"name","description","parameters"}` function schemas.
    3. MCP-style `{"name","description","inputSchema"}` tools.
    4. Wrapper `{"tools":[...]}` containing any of the above.
    5. Sequence of any of the above.
  - BFCL v4 type-alias normalisation (`dict→object`, `float→number`, `tuple→array`) at every nesting level inside `_normalize_schema`.
  - `ArgSpec` mapping branch order in `_compile_arg`: **enum → boolean → integer → number → string(time?) → any → `RawArg`** fallback.
  - Pass-through of `allow_empty_calls`, `examples`, and `extra_rules` to the constructed `Catalog` so the null-action contract from [[contract_null_action]] is honoured for callers like [[benchmark_bfcl]] `irrelevance`.
  - `CompiledToolMapper` exposing:
    - `catalog` and `source_tools` (deep-copied input snapshot)
    - `render_json_dsl()` / `render_openai_tools()` — symmetric rendering for the two evaluation paths
    - `parse_json_dsl(raw)` — validator entry returning an `ActionPlan`
    - `emit_tool_calls(raw)` — provider-neutral `[{name, arguments}, ...]` output for downstream executors

- out-of-scope:
  - Hand-written `ToolSpec` modules under `ganglion/contract/builtins/` — human authoring surface, see [[contract_catalog]].
  - Domain aliasing / locale normalisation (e.g. `거실 → living`, `영화 모드 → movie`). Compiler does not invent aliases; catalog authors supply them via `EnumArg.aliases` / `StringArg.aliases`.
  - Dataset row generation for compiled catalogs — see [[lm_data_synth]] for synthesis and [[benchmark_bfcl]] for per-case construction.
  - Provider-specific output adapters beyond neutral `{name, arguments}` (e.g. Anthropic `tool_use` blocks) — deferred to a future `provider_adapter` task.
  - Direct execution of the resulting tool calls — that is the runtime executor boundary.
  - Full JSON Schema support; supported subset is enumerated in [docs/tool_schema_compiler.md §Current Limitations](../tool_schema_compiler.md).
  - Statistical correction or repair-rule synthesis informed by compiler output — see [[analyzer_rule_synthesis]].

- on violation: unsupported schema features (deeply nested `oneOf` with discriminators, recursive `$ref`, schema arrays without `type`) raise `DSLValidationError` at compile time. The compiler never guesses a type — fall through to `RawArg` only when a syntactically valid JSON Schema subset can be preserved verbatim.

## Procedure

```
input: schema = mapping | sequence[mapping]
tools ← _coerce_tool_list(schema)              # OpenAI / bare / MCP / wrapper / sequence
compiled ← []
for tool in tools:
    function   ← _extract_function(tool)       # unwrap OpenAI {"type":"function"}
    name       ← required non-empty string
    parameters ← _normalize_schema(_extract_parameters(function))
                # rewrite BFCL aliases dict/float/tuple → object/number/array at every level
    for (arg_name, arg_schema) in parameters.properties.items():
        compiled_arg ← _compile_arg(
            arg_name, arg_schema,
            required=(arg_name in parameters.required))
        # branch order: enum → boolean → integer → number → string(time?) → any → RawArg
    compiled.append(ToolSpec(name, description, args))
catalog ← Catalog(name, tools=tuple(compiled),
                  examples=examples,
                  extra_rules=extra_rules,
                  allow_empty_calls=allow_empty_calls)
return CompiledToolMapper(catalog, source_tools=tuple(deepcopy(tools)))
```

Synchronous transform: no event emission, no IO. Consumers (e.g. [[benchmark_bfcl]]) compose the call inline per case.

## Contract

- in: a single OpenAI / MCP / bare tool mapping, a sequence of them, or a `{"tools":[...]}` wrapper. Optional kwargs: `name`, `examples`, `extra_rules`, `allow_empty_calls`.
- out: a `CompiledToolMapper` instance exposing `catalog`, `source_tools`, `render_json_dsl()`, `render_openai_tools()`, `parse_json_dsl()`, `emit_tool_calls()`.
- event: none — synchronous transform; consumers compose directly (see [[benchmark_bfcl]]).
- failure:
  - Empty or non-mapping / non-sequence input → `DSLValidationError("tool schema must …")`.
  - `function.name` missing or empty → `DSLValidationError("tool.name must be a non-empty string")`.
  - `parameters` / `inputSchema` not an object schema → `DSLValidationError`.
  - Argument schema with no recognisable `type` and no `RawArg` fallback path → `DSLValidationError`.
- success: every shipped fixture in `tests/test_tool_schema_compiler.py` and `tests/test_compiler_bfcl_features.py` round-trips: `compile → render_json_dsl → parse_json_dsl(expected) → ActionPlan` equals the hand-authored expectation; `pytest -q tests/test_tool_schema_compiler.py tests/test_compiler_bfcl_features.py` exits 0.

## Observation

- `compile_success_rate` = tools successfully compiled ÷ tools attempted (per input batch). Below 1.0 surfaces schema-feature gaps that should drive new branches in `_compile_arg`.
- `raw_arg_share` = `RawArg`-typed arguments ÷ total arguments. High share weakens the IR-compression claim — the more `RawArg` fallbacks, the closer the DSL surface is to the underlying JSON Schema, and the smaller the input-token saving over native tool schemas.
- `bfcl_alias_normalization_count` = number of `dict | float | tuple` rewrites per batch. Zero on non-BFCL inputs; non-zero confirms the normalisation layer is being exercised by external benchmark traffic from [[benchmark_bfcl]].

Status: live implementation under migration from `ganglion/dsl/compiler.py` → `ganglion/contract/schema_compiler.py`. Spec is post-hoc reconciliation with `task_principle`; see [docs/tool_schema_compiler.md](../tool_schema_compiler.md) for the long-form design (pipeline diagram, supported JSON Schema subset, generated DSL contract, current limitations, research role).
