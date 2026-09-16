[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[contract_schema_compiler]] · [[contract_describe]] · [[analyzer_trace_store]]

# analyzer_catalogs

Every record the analyzer writes — a trace, a label, a manifest, a patch — is stamped with a `catalog_id` string, but nothing turned that string back into the `Catalog` it names. Module 3 cannot do it: [[contract_catalog]] owns builtin tiers and [[contract_schema_compiler]] compiles a tool list handed to it, and neither knows the runs dir a console-submitted tool list has to survive in. This task is that missing inverse — the `catalog_id` → `Catalog` resolver, plus the persistence layer that makes an externally submitted tool list re-resolvable after a restart. It sits in `analyzer/` because the id vocabulary it decodes is [[analyzer_trace_store]]'s and the base dir it reads is the run-bundle root.

Status: implemented at `ganglion/analyzer/catalogs.py`, tests `tests/test_analyzer_catalogs.py`. This doc was written after the code (2026-09-16) — the one module of the console batch whose spec lagged its implementation; it describes the landed surface, not a plan.

## Role

Resolve a `catalog_id` into the exact `Catalog` that produced the records carrying it, persisting externally submitted tool lists under a content-addressed id so the resolution is stable across processes.

## Scope

- **in-scope**:
  - The three `catalog_id` namespaces and their resolution rule:
    - **builtin tier name** (`iot_light_5`, `home_iot_20`, `smart_home_50`, `home_assistant_4`) → `ganglion.contract.builtins.get_catalog(tier)`. All four tiers of `TIERS`, not only the three `SCALING_TIERS` — `home_assistant_4` ([[contract_tier_home_assistant]]) is off the scaling curve but is a first-class run target. The *shared* module-level object is returned, not a copy: callers treat it as immutable (patch previews go through [[contract_patch_apply]], which never mutates).
    - **`compiled/<sha12>`** → `<base>/compiled/<sha12>/catalog.json`, recompiled through `compile_tool_calling_schema(tools, name=catalog_id, allow_empty_calls=…)`.
    - **`bfcl/<category>`** → **not resolvable**: BFCL cases each ship their own tool list ([[benchmark_bfcl]]), so no single catalog corresponds to the id. Raises `CatalogNotResolvable`; such runs are read-only in the console.
  - Public API of `ganglion/analyzer/catalogs.py` (`__all__`): `COMPILED_PREFIX = "compiled/"`, `CatalogNotResolvable`, `resolve_catalog`, `register_compiled`, `list_catalogs`, `source_tools_for`.
    - `resolve_catalog(catalog_id, *, base_dir) -> Catalog`.
    - `register_compiled(base_dir, name, tools, *, allow_empty_calls=False) -> (catalog_id, CompiledToolMapper)` — persist + compile; idempotent.
    - `list_catalogs(base_dir) -> list[dict]` — builtin rows in `TIERS` order, then persisted compiled rows in `sha12` order. Row: `{"catalog_id", "n_tools", "allow_empty_calls", "fingerprint", "source": "builtin" | "compiled"}`, compiled rows additionally `"label"`.
    - `source_tools_for(catalog_id, base_dir) -> list[dict] | None` — the persisted submission for a compiled id; `None` for every other id and for an unreadable one.
  - `CatalogNotResolvable(ValueError)` — the single failure type for `bfcl/*`, an unknown tier, a malformed `compiled/<not-sha12>` id, a missing `catalog.json` and an unreadable / shapeless one.
  - **Identity, hashed canonically.** `catalog_id = COMPILED_PREFIX + sha256(json.dumps([name, tools, allow_empty_calls], sort_keys=True, ensure_ascii=False, default=str))[:12]`. Canonical *for the id only*, so two logically identical submissions whose keys happen to be ordered differently collapse onto one id.
  - **Invariant — the persisted document keeps submission order and is never written with `sort_keys`.** `catalog.json` is `json.dumps(payload, ensure_ascii=False, indent=2)`. Key order is load-bearing: `compile_tool_calling_schema` builds `ToolSpec.args` in `parameters.properties` order, and that order is the line the model reads in `render_json_dsl()`, the property order `render_openai_tools()` re-emits, and the sequence `describe()` hashes into the `cf-` fingerprint ([[contract_describe]]). Sorting on write would hand the caller one catalog and rebuild a re-ordered twin in `resolve_catalog` — a different prompt and a different fingerprint for one id across a console restart. Regression: `tests/test_analyzer_catalogs.py::test_fingerprint_is_stable_across_persistence` and `::test_persisted_tools_keep_property_order`.
  - **Invariant — `Catalog.name == catalog_id` on every compiled path** (registration, resolution, re-registration). `ganglion/analyzer/rules.py:make_patch_id` stamps `catalog.name` into `rs-<catalog_id>-<hash>` and into `RulePatch.catalog_id`, so a compiled catalog carrying its human label instead would emit patches attributed to a name no consumer can resolve. The human-supplied name survives as `label`.
  - **Invariant — the persisted document wins.** For an id that already exists, the mapper is recompiled from disk rather than from the caller's list, so `register_compiled` → `resolve_catalog` → `register_compiled` all describe identically and the file's mtime does not move.
  - Persisted payload: `{"catalog_id", "name": catalog_id, "label": <human name>, "tools": <submission order>, "allow_empty_calls"}`.
  - Write discipline: compile *before* writing (a malformed schema raises `DSLValidationError` and leaves no `compiled/` tree at all), then `tmp` + `Path.replace` so no reader ever sees a half-written document. A corrupt or truncated `catalog.json` is rewritten from the caller's list on the next `register_compiled`.
  - Path safety: the `sha12` segment is matched against `^[0-9a-f]{12}$` before it reaches a path join, so `compiled/../etc` is a `CatalogNotResolvable`, never a traversal.
- **out-of-scope**:
  - Compiling a tool schema into a `Catalog` — [[contract_schema_compiler]] owns `compile_tool_calling_schema`, the type-alias normalisation and `CompiledToolMapper`; this task only chooses `name=` and `allow_empty_calls=`.
  - `describe()` / `fingerprint()` — [[contract_describe]]. Rows here *report* `fingerprint()`; the `cf-` format is not defined here.
  - Authoring builtin tiers — [[contract_catalog]], [[contract_tier_home_assistant]]. This task never edits `ganglion/contract/builtins/`.
  - Applying a `RulePatch` or publishing a patched catalog as a new builtin — [[contract_patch_apply]] previews in memory; porting stays a reviewed human edit ([[analyzer_patch_decision]]).
  - Run bundles, `manifest.json`, `describe.json`, `summary.json` — [[analyzer_run_manifest]]. This task owns exactly one file, `compiled/<sha12>/catalog.json`, which is not inside a run dir.
  - Emitting or transporting ledger rows — [[analyzer_event_ledger]]. `catalogs.py` imports no ledger.
  - HTTP routing, the 404 / 422 mapping, the single-writer thread and the `POST /api/catalogs/compile` body shape — [[console_operator]].
  - Per-case BFCL catalogs — [[benchmark_bfcl]] builds them per row; this task deliberately refuses to fabricate a stand-in.
  - Deleting, garbage-collecting or migrating `compiled/` entries, and any retention policy over them — no deletion path exists; an orphaned entry is inert.
  - The `catalog_id` vocabulary itself and the shard layout it indexes — [[analyzer_trace_store]].
  - Model identity (`mf-`, `ModelSpec`) — [[lm_model_registry]].
- **on violation**: if a consumer needs a resolvable catalog for a `bfcl/*` id, **stop** — do not synthesise a union catalog or fall back to a builtin tier; the correct move is to register the case's tool list as a `compiled/<sha12>` catalog and run against that id. If a reader is tempted to canonicalise, re-indent or key-sort `catalog.json` in place, **stop** — that silently moves the fingerprint of every record already stamped with the id. A fourth id namespace is a doc revision here first, never a new `startswith` branch in a consumer.

## Procedure

```
resolve_catalog(catalog_id, base_dir):
    if catalog_id starts with "bfcl/":      raise CatalogNotResolvable      # per-case; by design
    if catalog_id starts with "compiled/":
        sha ← catalog_id[len("compiled/"):]; assert ^[0-9a-f]{12}$ else raise
        payload ← json <base>/compiled/<sha>/catalog.json                   # missing/unreadable/no "tools" → raise
        return compile_tool_calling_schema(payload["tools"], name=catalog_id,
                                           allow_empty_calls=payload["allow_empty_calls"]).catalog
    if catalog_id in TIERS:                 return get_catalog(catalog_id)  # shared object; do not mutate
    raise CatalogNotResolvable(unknown id, list TIERS)

register_compiled(base_dir, name, tools, allow_empty_calls=False):
    tools ← [dict(t) for t in tools]                                        # submission order preserved
    sha   ← sha256(json.dumps([name, tools, allow_empty_calls], sort_keys=True))[:12]
    id    ← "compiled/" + sha; path ← <base>/compiled/<sha>/catalog.json
    if path exists and readable:  return id, compile(persisted payload)     # disk wins; file untouched
    mapper ← compile(id, tools, allow_empty_calls)                          # BEFORE any write
    write {catalog_id: id, name: id, label: name, tools, allow_empty_calls}
          with indent=2 and NOT sort_keys → tmp → replace
    return id, mapper

list_catalogs(base_dir):
    rows ← [row(tier, catalog, "builtin") for tier, catalog in TIERS.items()]
    for entry in sorted(<base>/compiled/*) where entry.name matches ^[0-9a-f]{12}$:
        rows.append(row(id, resolved catalog, "compiled") + {"label": payload["label"]})
    return rows

source_tools_for(catalog_id, base_dir):
    non-compiled id, or unreadable catalog.json → None; else the persisted "tools" list

on DSLValidationError during register_compiled: propagate; nothing is written
on corrupt catalog.json:   resolve_catalog raises; the next register_compiled rewrites it
on a broken compiled entry during list_catalogs: skip that row (one bad entry must not hide the rest)
```

## Contract

- **in**: a `catalog_id` string ([[analyzer_trace_store]]'s vocabulary) and a runs base dir; for registration, a human label plus an external tool list in any shape [[contract_schema_compiler]] accepts (OpenAI `tools`, bare function schemas, MCP `inputSchema`, a `{"tools": […]}` wrapper).
- **out**:
  - `<base>/compiled/<sha12>/catalog.json` — exactly one per compiled id; keys `{catalog_id, name, label, tools, allow_empty_calls}` with `name == catalog_id`; `indent=2`, `sort_keys` never applied; written atomically and not rewritten unless corrupt.
  - `resolve_catalog(...) -> Catalog` with `.name == catalog_id` for compiled ids and `is get_catalog(tier)` for builtin ones.
  - `register_compiled(...) -> (catalog_id, CompiledToolMapper)` with `catalog_id` matching `^compiled/[0-9a-f]{12}$`.
  - `list_catalogs(...)` rows and `source_tools_for(...)` — the JSON-able projections `GET /api/catalogs`, `GET /api/catalogs/{id}` and the catalog page render.
  - One `contract.catalog.compiled` row in `<base>/ledger/global.jsonl` per successful registration through the console — payload `{catalog_id, fingerprint, n_tools, label}`, correlation `{catalog_id}` (no `run_id`, hence the global ledger), `refs.catalog = <base>/<catalog_id>/catalog.json`.
- **event**: consume none. Emit none *directly* — `catalogs.py` imports no ledger; the registration's caller ([[console_operator]]'s `POST /api/catalogs/compile`, through the single writer) emits `contract.catalog.compiled` from the returned `(catalog_id, mapper)`, per [[analyzer_event_ledger]]'s closed `EVENT_NAMES`. Downstream, that id travels as `catalog_id` inside `analyzer.run.recorded`, `analyzer.trace.recorded`, `analyzer.label.recorded` and `analyzer.rule.proposed`.
- **failure**:

  | signal | branch |
  |---|---|
  | `bfcl/<category>` id | `CatalogNotResolvable`; console answers `404 unknown_catalog`, `analyze_run` raises before writing any sidecar |
  | unknown builtin tier | `CatalogNotResolvable` naming `sorted(TIERS)` |
  | `compiled/<not 12 hex>` (incl. `compiled/../etc`) | `CatalogNotResolvable` before any path join |
  | `compiled/<sha12>` with no `catalog.json` | `CatalogNotResolvable` naming the path |
  | `catalog.json` unparseable, not a mapping, or without a `tools` list | `CatalogNotResolvable`; `source_tools_for` → `None`; `list_catalogs` skips the row; the next `register_compiled` rewrites the file |
  | tool list `compile_tool_calling_schema` rejects | `DSLValidationError` propagates; no file, no directory, no event (console maps it to `422 invalid_tools`) |
  | re-registration of an existing id | not a failure — disk wins, mtime unchanged, same `(id, fingerprint)` returned |

- **success** (`pytest tests/test_analyzer_catalogs.py`): every tier in `TIERS` resolves and `resolve_catalog(tier) is get_catalog(tier)`; `bfcl/*`, an unknown tier, `compiled/000000000000` and `compiled/../etc` all raise `CatalogNotResolvable`, which is a `ValueError`; a round trip persists `payload["tools"] == TOOLS` with `name == catalog_id` and `label == "weather"`, re-registers to the same id without touching mtime, and differs by id when `name` or `allow_empty_calls` differs; a bad schema leaves no `compiled/` directory; `list_catalogs` returns `TIERS` order then the compiled row (a `notasha` directory ignored) with `fingerprint` equal to `get_catalog(...).fingerprint()`; register → resolve-from-disk → re-register yield **one** `cf-` fingerprint, one `render_json_dsl()` string and one `ToolSpec.args` order for deliberately non-alphabetical `properties`; a key-reordered but logically identical submission lands on the same id and describes like the document on disk; a corrupted `catalog.json` raises on resolve and is repaired by the next registration.

## Observation

- `compiled_catalogs` = `sha12` directories under `<base>/compiled/` = compiled rows from `list_catalogs`; a gap between the two counts is `orphan_compiled_dirs`.
- `orphan_compiled_dirs` = entries under `<base>/compiled/` that `list_catalogs` skips (non-`sha12` name, or a `catalog.json` that fails to compile) — must be 0; non-zero means a writer bypassed `register_compiled`.
- `fingerprint_stability{catalog_id}` = distinct `fingerprint()` values over {value returned at registration, value rebuilt by `resolve_catalog`, value after re-registration} — must be 1. The console-restart instability this module exists to prevent shows up here first.
- `unresolvable_rate{family}` = `CatalogNotResolvable` raises ÷ `resolve_catalog` calls, bucketed by `builtin` / `compiled` / `bfcl`. A non-zero `compiled` bucket means persistence broke; the `bfcl` bucket is expected and bounded by how many BFCL runs the console lists.
- `label_collisions` = distinct `catalog_id`s sharing a `label` — legitimate (the same name re-submitted with a changed tool list is a new id by design), but the console picker needs the count to disambiguate what it shows an operator.
- `unstamped_patch_ids` = `RulePatch.catalog_id` values that `resolve_catalog` rejects — must be 0; non-zero means a compiled catalog reached [[analyzer_rule_synthesis]] with a `Catalog.name` that is not its id.

Related: [[contract_catalog]], [[contract_schema_compiler]], [[contract_describe]], [[contract_patch_apply]], [[contract_tier_home_assistant]], [[analyzer_trace_store]], [[analyzer_run_manifest]], [[analyzer_rule_synthesis]], [[analyzer_analyze]], [[analyzer_event_ledger]], [[benchmark_bfcl]], [[console_operator]].
