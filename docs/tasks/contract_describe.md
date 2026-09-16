[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[contract_catalog]] · [[contract_patch_apply]]

# contract_describe

Deterministic, JSON-able **description** of a `Catalog` and the **content fingerprint** derived from it. `Catalog` already has two renderers — `render_json_dsl()` and `render_openai_tools()` — but neither exposes aliases, post-correction hooks, argument descriptions or `bool_true` / `bool_false`, so two catalogs that differ only in an alias render byte-identical DSL and nothing downstream can tell which one produced a record. This task adds the third renderer, `describe()`, and hashes it into `catalog_fingerprint` — the first element of the identity triple (`catalog_fingerprint`, `model_id`, `dataset_sha256`) that [[analyzer_run_manifest]], [[analyzer_label_store]], [[analyzer_patch_decision]] and [[console_operator]] stamp on every new record.

## Role

Serialise a `Catalog` into one deterministic JSON-able dict that covers every field the validator reads, and derive a stable `cf-` fingerprint from that dict.

## Scope

- **in-scope**:
  - `ganglion/contract/describe.py` (new):
    - `describe(catalog: Catalog) -> dict` — pure; JSON-able (`json.dumps(..., sort_keys=True, ensure_ascii=False)` succeeds); byte-identical for equal catalogs.
    - `catalog_fingerprint(catalog: Catalog) -> str` — `"cf-" + sha256(json.dumps(describe(catalog), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]`.
  - Output shape (top level → tool → arg):
    ```
    {"name": str, "allow_empty_calls": bool, "default_strip_unknown_args": bool,
     "examples": [[prompt, response], ...],          # Catalog.examples, declared order
     "extra_rules": [str, ...],                       # declared order
     "tools": [{"name": str, "description": str, "dsl_args_override": str | null,
                "strip_unknown_args": bool,
                "custom_validator": bool,             # presence only — callables are never serialised
                "prompt_correction": bool,            # presence only
                "defaults_when_missing": [{"arg": str, "value": <json>, "predicate_present": true}],
                "args": [<arg entry>, ...]}]}         # declared order
    ```
    Every arg entry carries `{"name", "kind", "required", "description"}` with `"description": getattr(spec, "description", "")` (`RawArg` has no `description` field), plus per-kind keys read from `ganglion/contract/tool_spec.py`:

    | `kind` | extra keys | source fields |
    |---|---|---|
    | `enum` | `values` (declared order), `aliases` (sorted dict), `bool_true`, `bool_false` | `EnumArg.values / aliases / bool_true / bool_false` |
    | `integer` | `min`, `max`, `allow_percent` | `IntArg.min_value / max_value / allow_percent` |
    | `number` | `min`, `max` | `NumberArg.min_value / max_value` |
    | `string` | `aliases` (sorted dict), `pattern` | `StringArg.aliases / pattern` |
    | `boolean`, `time` | — | — |
    | `raw` | `json_schema` (the schema object; key order fixed by `sort_keys` at dump time), `dsl_description` | `RawArg.json_schema / dsl_description` |
  - `Catalog.describe()` and `Catalog.fingerprint()` on `ganglion/contract/catalog.py` — thin delegates. The import is **lazy, inside the method body** (`def describe(self): from ganglion.contract.describe import describe as _d; return _d(self)`, same for `fingerprint`): `ganglion/contract/__init__.py` imports `catalog` first and `describe.py` needs `Catalog`, so a module-level import would be a circular import at package init that takes the whole test suite down. `describe.py` imports `tool_spec` at top level and type-imports `Catalog` only under `TYPE_CHECKING`.
  - Exports `describe`, `catalog_fingerprint` from `ganglion/contract/__init__.py`.
  - Determinism rules: sequences keep declared order (tools, args, enum values, examples, extra rules); mappings are emitted key-sorted; callables (`custom_validator`, `prompt_correction`, `DefaultRule` predicates) reduce to presence booleans. Consequence, stated once: **changing the body of a callable does not move the fingerprint.** Hook bodies are reviewed at the source level and their effect is measured empirically by [[analyzer_correction_attribution]], not by hash.
  - Tests: `tests/test_catalog_describe.py`.
- **out-of-scope**:
  - DSL / OpenAI rendering, validation, `ActionPlan` equality — [[contract_catalog]].
  - Applying patches or stripping hooks (which *consume* `fingerprint()` to prove a change happened) — [[contract_patch_apply]].
  - Persisting `describe.json` into a run directory — written by the run owner ([[console_operator]] sessions, the `cli --trace-store` bundle of [[benchmark_iot]] / [[benchmark_bfcl]]); this task returns a dict.
  - Comparing fingerprints across runs, `manifest_diff` — [[analyzer_compare]].
  - The compiled-catalog id `compiled/<sha12>` (a hash of the *source* tool list, not of `describe()`) — `ganglion/analyzer/catalogs.py`; `source_tools` preservation — [[contract_schema_compiler]].
  - Model identity (`mf-` fingerprint) — [[lm_model_registry]].
  - Serialising callable bodies (AST / bytecode hashing) — deliberately excluded; see the determinism rule above.
- **on violation**: if a consumer needs a field `describe()` does not expose (a predicate body, a validator's accepted keys), **stop** — do not hash something outside `describe()` into `catalog_fingerprint` from the consumer side. Extend the shape here in a doc revision and treat it as a fingerprint-format bump (every stored `cf-` value moves).

## Procedure

```
describe(catalog):
    tools ← []
    for tool in catalog.tools:                                   # declared order
        args ← []
        for (name, spec) in tool.args:                           # declared order
            entry ← {"name": name, "kind": spec.kind, "required": spec.required,
                     "description": getattr(spec, "description", "")}
            entry += per-kind keys (table above); aliases ← dict(sorted(spec.aliases.items()))
            args.append(entry)
        tools.append({"name": tool.name, "description": tool.description,
                      "dsl_args_override": tool.dsl_args_override,
                      "strip_unknown_args": tool.strip_unknown_args,
                      "custom_validator": tool.custom_validator is not None,
                      "prompt_correction": tool.prompt_correction is not None,
                      "defaults_when_missing": [{"arg": a, "value": v, "predicate_present": True}
                                                for (a, v, _pred) in tool.defaults_when_missing],
                      "args": args})
    return {"name": catalog.name, "allow_empty_calls": catalog.allow_empty_calls,
            "default_strip_unknown_args": catalog.default_strip_unknown_args,
            "examples": [list(pair) for pair in catalog.examples],
            "extra_rules": list(catalog.extra_rules), "tools": tools}

catalog_fingerprint(catalog):
    blob ← json.dumps(describe(catalog), sort_keys=True, ensure_ascii=False)
    return "cf-" + sha256(blob.encode("utf-8")).hexdigest()[:12]

Catalog.describe(self):     lazy-import describe.describe;            return describe(self)
Catalog.fingerprint(self):  lazy-import describe.catalog_fingerprint;  return catalog_fingerprint(self)

on ArgSpec of an unknown class:             raise TypeError            # fail loud — no "raw" fallback
on a default value that json.dumps rejects: propagate TypeError       # the builtin is mis-declared; fix the ToolSpec
```

## Contract

- **in**: a `Catalog` — builtin (`ganglion/contract/builtins/*.py`) or compiled ([[contract_schema_compiler]]).
- **out**:
  - `describe(catalog) -> dict` with the shape above; `json.dumps(d, sort_keys=True, ensure_ascii=False)` succeeds.
  - `catalog_fingerprint(catalog) -> str` matching `^cf-[0-9a-f]{12}$`.
  - `Catalog.describe()` / `Catalog.fingerprint()` returning the same values.
- **event**: none emitted, none consumed — synchronous pure functions. The fingerprint travels as `catalog_fingerprint` inside the payloads of `analyzer.run.recorded` (`RunManifest`), `contract.catalog.compiled`, `analyzer.label.recorded` and `analyzer.patch.decided` ([[analyzer_event_ledger]]).
- **failure**:
  - Unknown `ArgSpec` class → `TypeError`; never silently serialised as `raw`.
  - Non-JSON-serialisable `defaults_when_missing` value → `TypeError` from `json.dumps` (propagated).
  - Module-level import of `describe` from `catalog.py` → circular import at `import ganglion.contract`; forbidden by the lazy-import rule above and guarded by the success predicate.
- **success**: `pytest tests/test_catalog_describe.py` passes, asserting at least: (1) `fingerprint()` is identical across two calls and across two equal `Catalog` constructions; (2) `replace(catalog, allow_empty_calls=not catalog.allow_empty_calls).fingerprint() != catalog.fingerprint()`; (3) adding one alias to one `EnumArg` changes the fingerprint; (4) every catalog in `ganglion.contract.builtins.TIERS` describes and dumps with `sort_keys=True`; (5) `iot_light_5` → `set_light.defaults_when_missing == [{"arg": "state", "value": "on", "predicate_present": true}]` and every `RawArg` entry carries `json_schema` + `dsl_description`; (6) `python -c "import ganglion.contract"` exits 0 (no circular import).

## Observation

- `describe_bytes{catalog_id}` = `len(json.dumps(describe(catalog), sort_keys=True))` — grows with aliases and hooks; a jump without a `ToolSpec` edit signals an unintended hook.
- `fingerprint_distinct{catalog_id}` = distinct `catalog_fingerprint` values across `runs/traces/<catalog_id>/*/manifest.json` — the number of catalog versions the loop has actually been run against.
- `fingerprint_collision_count` = run pairs with equal `catalog_fingerprint` but unequal `describe.json` — must be 0.
- `callable_blind_share{catalog_id}` = tools with `custom_validator or prompt_correction` ÷ tools — the share of catalog behaviour the fingerprint cannot see.

Status: spec, implementation in progress (2026-09-16). Target files `ganglion/contract/describe.py` (new), `ganglion/contract/catalog.py` (two delegating methods), `ganglion/contract/__init__.py` (exports), `tests/test_catalog_describe.py` (new).
