[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Design doc: [docs/tool_schema_compiler.md](../tool_schema_compiler.md) · Consumer: [external_benchmark_bfcl](./external_benchmark_bfcl.md)

# tool_schema_compiler

Compile arbitrary external tool schemas (OpenAI / DashScope / MCP / bare function schemas) into a Ganglion `Catalog` so that DSL and native baselines render from the same source of truth.

## Role

Translate external tool-calling schemas into Ganglion `ToolSpec` / `Catalog` and expose a runtime mapper that bridges the compiled DSL output back to a provider-neutral `{name, arguments}` call.

## Scope

- **in-scope**:
  - `ganglion.dsl.compiler.compile_tool_calling_schema` and `compile_openai_tools` — the only public entry points.
  - Accepted input shapes:
    1. OpenAI-compatible `{"type":"function","function":{...}}` tools.
    2. Bare `{"name","description","parameters"}` function schemas.
    3. MCP-style `{"name","description","inputSchema"}` tools.
    4. Wrapper `{"tools":[...]}` containing any of the above.
  - BFCL v4 type-alias normalisation (`dict→object`, `float→number`, `tuple→array`) at every nesting level (`_normalize_schema`).
  - `ArgSpec` mapping: `EnumArg` (string enum), `IntArg`, `NumberArg`, `BoolArg`, `StringArg`, `TimeArg` (heuristic on name / `format: time` / `HH:MM` pattern), `RawArg` (fallback with preserved JSON Schema).
  - Passing through `allow_empty_calls`, `examples`, and `extra_rules` to the constructed `Catalog`.
  - `CompiledToolMapper`:
    - `render_json_dsl()` / `render_openai_tools()` — symmetric rendering for the two evaluation paths.
    - `parse_json_dsl(raw)` — validator entry.
    - `emit_tool_calls(raw)` — provider-neutral `[{name, arguments}, ...]` output for downstream executors.
- **out-of-scope**:
  - Hand-written `ToolSpec` modules under `ganglion/schema/` — those remain a human authoring surface (see [catalog_spec_sync](./catalog_spec_sync.md)).
  - Domain aliasing / locale normalisation (`거실 → living`, `영화 모드 → movie`). Compiler does not invent aliases; they must be supplied as catalog rules or custom validators.
  - Dataset row generation for compiled catalogs — `examples/<tier>/generate_dataset.py` owns that.
  - Provider-specific output adapters beyond the neutral `{name, arguments}` shape (e.g. Anthropic `tool_use` blocks). Defer to a future `provider_adapter` task.
  - Direct execution of the resulting tool calls — `ganglion/runtime/executor.py` boundary.
  - Full JSON Schema support; the supported subset is enumerated in [docs/tool_schema_compiler.md §Current Limitations](../tool_schema_compiler.md).
- **on violation**: unsupported schema features (deeply nested `oneOf` with discriminators, recursive `$ref`, schema arrays without `type`) raise `DSLValidationError` at compile time. The compiler never guesses a type — fall through to `RawArg` only when a syntactically valid JSON Schema subset is preserved verbatim.

## Procedure

```
input: schema = mapping | sequence[mapping]
tools ← _coerce_tool_list(schema)        # supports OpenAI / bare / MCP / wrapper
compiled ← []
for tool in tools:
    function   ← _extract_function(tool)         # unwrap OpenAI {"type":"function"}
    name       ← required string
    parameters ← _normalize_schema(_extract_parameters(function))
                # rewrite BFCL aliases dict/float/tuple → object/number/array
    for (arg_name, arg_schema) in parameters.properties.items():
        compiled_arg ← _compile_arg(arg_name, arg_schema, required=(arg_name in required))
        # branch order: enum → boolean → integer → number → string(time?) → any → RawArg
    compiled.append(ToolSpec(name, description, args))
catalog ← Catalog(name, tools=tuple(compiled),
                  examples, extra_rules, allow_empty_calls)
return CompiledToolMapper(catalog, source_tools=tuple(deepcopy(tools)))
```

## Contract

- **in**: a single OpenAI/MCP/bare tool, or a sequence of them, or a `{"tools":[...]}` wrapper.
- **out**: `CompiledToolMapper` exposing `catalog`, `source_tools`, `render_json_dsl()`, `render_openai_tools()`, `parse_json_dsl()`, `emit_tool_calls()`.
- **event**: none — compiler is a synchronous transform; consumers compose it directly inside their own tasks (see [external_benchmark_bfcl](./external_benchmark_bfcl.md)).
- **failure**:
  - Empty or non-mapping/non-sequence input → `DSLValidationError("tool schema must …")`.
  - `function.name` missing or empty → `DSLValidationError("tool.name must be a non-empty string")`.
  - `parameters`/`inputSchema` not an object schema → `DSLValidationError`.
  - Argument schema missing recognisable `type` and not falling through to `RawArg` → `DSLValidationError`.
- **success**: every shipped fixture in `tests/test_tool_schema_compiler.py` and `tests/test_compiler_bfcl_features.py` round-trips: `compile → render_json_dsl → parse_json_dsl(expected) → ActionPlan` equals the hand-authored expectation.

## Observation

- `compile_success_rate` = tools successfully compiled ÷ tools attempted (per input batch). Drops below 1.0 surface schema-feature gaps.
- `raw_arg_share` = `RawArg`-typed arguments ÷ total arguments. High share weakens the IR-compression claim — the more `RawArg` fallbacks, the closer the DSL surface is to the underlying JSON Schema.
- `bfcl_alias_normalization_count` = number of `dict|float|tuple` rewrites per batch. Zero on non-BFCL inputs; non-zero confirms the normalisation layer is being exercised.

## Status

Live implementation. Spec written post-hoc to reconcile with `task_principle`. See [docs/tool_schema_compiler.md](../tool_schema_compiler.md) for the long-form design doc (pipeline diagram, supported JSON Schema subset, generated DSL contract, current limitations, research role).
