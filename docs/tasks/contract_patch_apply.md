[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[contract_catalog]] · [[contract_describe]]

# contract_patch_apply

Pure, in-memory application of a `RulePatch` — the `rule_patch` payload of `analyzer.rule.proposed`, produced by [[analyzer_rule_synthesis]] — to a `Catalog`, plus the inverse: **stripping the post-correction hooks** so a stored raw output can be re-parsed as F⁰ (model alone) next to Fᴷ (model + hooks). Today `ganglion/analyzer/rules.py` "NEVER mutates a Catalog or ToolSpec" and nothing else applies a patch either: accepted proposals are hand-ported into `ganglion/contract/builtins/*.py`. This task lets a patch be *previewed* on stored raw before a human ports it, without moving the "analyzer proposes, human applies" boundary — `apply_patch` never writes a file and never publishes a catalog.

## Role

Return a new `Catalog` with exactly one `RulePatch` applied, or with named hook kinds removed from every tool, never mutating the input and raising `PatchNotApplicableError` for every payload that cannot be applied mechanically.

## Scope

- **in-scope**:
  - `ganglion/contract/patch.py` (new):
    - `class PatchNotApplicableError(ValueError)`.
    - `ALL_HOOK_KINDS = ("defaults_when_missing", "strip_unknown_args", "prompt_correction")`.
    - `apply_patch(catalog: Catalog, patch: Mapping[str, Any]) -> Catalog` — `patch` is a `RulePatch.to_dict()` mapping (`patch_id`, `catalog_id`, `target_tool`, `operation`, `payload`, `evidence`, `source_failure_type`, `created_at`); only `catalog_id`, `target_tool`, `operation`, `payload` are read.
    - `strip_hooks(catalog: Catalog, kinds: Iterable[str]) -> Catalog`.
  - Preconditions for every operation: `catalog.get_tool(patch["target_tool"])` exists; when `patch["catalog_id"]` is present it equals `catalog.name` (patching the wrong catalog fails loud rather than silently — `synthesize_rules` stamps `catalog_id = catalog.name`, and compiled catalogs are compiled with `name=catalog_id` so the two agree).
  - Operation table — payload keys are the ones `ganglion/analyzer/rules.py` really emits (SSOT: [[analyzer_rule_synthesis]]); this doc mirrors them and must be revised with it:

    | `operation` | `source_failure_type` | `payload` | effect on `target_tool` |
    |---|---|---|---|
    | `add_alias` | `value_out_of_enum` \| `alias_unrecognised` | `{"arg": str, "aliases": {observed: gold}, "kind": "enum" \| "string"}` — keys already `strip().lower()`-normalised | `replace(spec, aliases={**spec.aliases, **new})` on the `EnumArg` / `StringArg` named `arg`. `kind` must match the spec class; for `enum` every gold must be in `values`. A key already mapped to a *different* gold → `PatchNotApplicableError`; an identical mapping is a no-op (idempotent). |
    | `set_default` | `missing_required_arg` | `{"arg": str, "default": Any, "predicate_hint": {"requires_args": [sorted str]}}` | `req = tuple(payload.get("predicate_hint", {}).get("requires_args", ()))`; append `(payload["arg"], payload["default"], lambda args, req=req: all(k in args for k in req))` to `defaults_when_missing` — an empty `requires_args` degenerates to always-True, matching `Catalog.validate_call`'s `predicate(args)` gate. `arg` must be declared on the tool; an existing rule for the same `arg` → `PatchNotApplicableError` (retire it first). |
    | `enable_strip_unknown_args` | `unknown_arg` | `{"strip_unknown_args": true}` — no `arg` key | `replace(tool, strip_unknown_args=True)`; already `True` → no-op. |
    | `extend_argspec` | `unknown_arg` (shape A) / `type_mismatch` (shape B) | A: `{"arg", "observed_types": {py_type: count}, "spec_hint": "RawArg"}`; B: `{"arg", "spec_hint": "IntArg" \| "IntArg(allow_percent=True)" \| "RawArg", "transform": "int_from_string" \| "percent" \| "strip_unit"}` | Applicable **iff** `payload.get("transform") == "percent"` (equivalently `spec_hint == "IntArg(allow_percent=True)"`) **and** `tool.get_arg(payload["arg"])` is an `IntArg` → `replace(spec, allow_percent=True)`. Everything else → `PatchNotApplicableError`: shape A is an *add-a-new-arg* `ToolSpec` shape change; `int_from_string` is already accepted by the validator (not a widening); `strip_unit` has no mechanical `ArgSpec` equivalent. |
    | `retire_rule` | `no_failure` (emitted by `analyzer.corrections.retire_candidates_as_patches`, [[analyzer_correction_attribution]]) | `{"hook_kind": "defaults_when_missing" \| "strip_unknown_args" \| "prompt_correction", "arg": str \| null}` | `defaults_when_missing`: drop entries whose arg equals `payload["arg"]` (all entries when `null`); `strip_unknown_args`: `replace(tool, strip_unknown_args=False)`; `prompt_correction`: `replace(tool, prompt_correction=None)`. Nothing to retire → `PatchNotApplicableError`. |
    | `add_prompt_correction` | `abstention_miss_should_call` | `{"system_nudge": str, "trigger_tool": str}` | always `PatchNotApplicableError` — a prompt nudge is not a `ToolSpec` field. |
    | `ESCALATE` | `type_mismatch` | `{"arg": str, "blocked_reason": str}` | always `PatchNotApplicableError`. |
    | any other string | — | — | `PatchNotApplicableError`. |
  - `strip_hooks(catalog, kinds)`: returns a copy with the named kinds removed from **every** tool — `defaults_when_missing → ()`, `strip_unknown_args → False` **and** `Catalog.default_strip_unknown_args → False`, `prompt_correction → None`. `custom_validator` is **never** stripped (it is the validator for `RawArg` shapes, not a correction). A kind outside `ALL_HOOK_KINDS` → `ValueError`.
  - Purity: `dataclasses.replace` all the way down (`Catalog` → `ToolSpec` → `ArgSpec` are frozen); the input object is unchanged; `result.fingerprint() != catalog.fingerprint()` iff a field changed ([[contract_describe]]). The predicate installed by `set_default` is the only new callable and closes over the immutable `req` tuple, so equal patches yield behaviourally equal catalogs.
  - Exports `PatchNotApplicableError`, `apply_patch`, `strip_hooks`, `ALL_HOOK_KINDS` from `ganglion/contract/__init__.py`.
  - Tests: `tests/test_contract_patch.py`.
- **out-of-scope**:
  - Proposing patches and their payload shapes — [[analyzer_rule_synthesis]] is the SSOT; a new operation lands there first, then here.
  - Deciding accept / hold / reject / retire, the blind-then-preview protocol, `patch_decisions.jsonl` — [[analyzer_patch_decision]]; the preview endpoint that calls `apply_patch` — [[console_operator]].
  - Porting an accepted patch into `ganglion/contract/builtins/*.py` — a human code edit under review; this task is in-memory only.
  - Persisting a patched catalog under `compiled/<sha12>` (`register_compiled`, `ganglion/analyzer/catalogs.py`).
  - F⁰ / Fᴷ **attribution** — per-kind ablation, rescue / regression counts, `cp95_upper` — [[analyzer_correction_attribution]]; only `strip_hooks` is provided here.
  - `ToolSpec` shape changes: new tools, new args, `RawArg` swaps, `custom_validator` edits — outside the mechanical envelope by design.
  - Grammar recompilation after a patch — [[lm_grammar_mask]].
  - Emitting `contract.catalog.published` — the `register()` path of [[contract_catalog]] and the `auto_apply` branch of [[factory_pipeline]].
- **on violation**: if a payload needs a shape change (new arg or tool, `RawArg` swap, validator edit), do **not** approximate it with a looser spec — raise `PatchNotApplicableError` and let the proposal stay visible as an escalation in [[console_operator]]. The change goes through a reviewed source edit and a fresh `contract.catalog.published`.

## Procedure

```
apply_patch(catalog, patch):
    tool ← catalog.get_tool(patch["target_tool"])                  # None → PatchNotApplicableError
    if "catalog_id" in patch and patch["catalog_id"] != catalog.name → PatchNotApplicableError
    op, payload ← patch["operation"], patch["payload"]
    match op:                                                      # table above
        "add_alias"                 → new_tool ← tool with spec.aliases extended
        "set_default"               → new_tool ← tool with one DefaultRule appended
        "enable_strip_unknown_args" → new_tool ← replace(tool, strip_unknown_args=True)
        "extend_argspec"            → percent on IntArg only, else PatchNotApplicableError
        "retire_rule"               → new_tool ← tool with the named hook removed
        "add_prompt_correction" | "ESCALATE" | _ → raise PatchNotApplicableError
    tools ← tuple(new_tool if t.name == tool.name else t for t in catalog.tools)
    return replace(catalog, tools=tools)

strip_hooks(catalog, kinds):
    kinds ← set(kinds); kinds ⊄ ALL_HOOK_KINDS → ValueError
    tools ← tuple(replace(t,
                 defaults_when_missing=() if "defaults_when_missing" in kinds else t.defaults_when_missing,
                 strip_unknown_args=False if "strip_unknown_args" in kinds else t.strip_unknown_args,
                 prompt_correction=None if "prompt_correction" in kinds else t.prompt_correction)
                 for t in catalog.tools)                           # custom_validator untouched
    return replace(catalog, tools=tools,
                   default_strip_unknown_args=False if "strip_unknown_args" in kinds
                                              else catalog.default_strip_unknown_args)

on PatchNotApplicableError: propagate — the caller records it (console preview → "not applicable"); never fall back to a partial apply
```

## Contract

- **in**: `catalog: Catalog`; `patch: Mapping` shaped as `RulePatch.to_dict()`; `kinds: Iterable[str] ⊆ ALL_HOOK_KINDS`.
- **out**: a new `Catalog` (input untouched); or `PatchNotApplicableError` / `ValueError`. No files, no events.
- **event**: none emitted, none consumed directly. The `patch` mapping is the `rule_patch` payload of `analyzer.rule.proposed`; callers apply it only after `analyzer.patch.decided(patch_id, stage, decision="accept")` ([[analyzer_patch_decision]]) or under [[factory_pipeline]]'s `auto_apply=True`, which is the one place that then emits `contract.catalog.published`.
- **failure**:
  - Unknown `target_tool`, `catalog_id` mismatch, unknown `operation` → `PatchNotApplicableError`.
  - `add_alias`: arg missing / not enum-or-string / `kind` mismatch / gold not in `values` / conflicting existing alias → `PatchNotApplicableError`.
  - `set_default`: arg not declared / rule already present for `arg` → `PatchNotApplicableError`.
  - `extend_argspec`: any payload other than `transform == "percent"` on an `IntArg` → `PatchNotApplicableError`.
  - `retire_rule`: unknown `hook_kind` or nothing to retire → `PatchNotApplicableError`.
  - `add_prompt_correction`, `ESCALATE` → `PatchNotApplicableError` (by design, not a bug).
  - `strip_hooks` with a kind outside `ALL_HOOK_KINDS` → `ValueError`.
- **success**: `pytest tests/test_contract_patch.py` passes, asserting at least: (1) `add_alias` `{"arg": "room", "aliases": {"안방": "bedroom"}, "kind": "enum"}` on `set_light` of `iot_light_5`, then `parse_json_dsl({"calls":[{"action":"set_light","args":{"room":"안방","state":"on"}}]})` yields `room == "bedroom"`, while the original catalog still raises; (2) `strip_hooks(iot_light_5, ALL_HOOK_KINDS).parse_json_dsl({"calls":[{"action":"set_light","args":{"room":"living","brightness":70}}]})` raises `DSLValidationError` (the `state` default no longer fires) and the unstripped catalog accepts it; (3) `retire_rule` `{"hook_kind": "defaults_when_missing", "arg": "state"}` on `set_light` changes `fingerprint()`; (4) `extend_argspec` with `transform == "percent"` on an `IntArg` sets `allow_percent=True`, while `spec_hint == "RawArg"` and `transform == "int_from_string"` raise; (5) `add_prompt_correction` and `ESCALATE` raise; (6) the input catalog's `fingerprint()` is unchanged after every call.

## Observation

- `patch_apply_count{operation}` / `patch_not_applicable_count{operation}` — from [[console_operator]] preview attempts; a high not-applicable share for `extend_argspec` is expected (only `percent` is mechanical) and is the escalation backlog.
- `mechanical_share{run_id}` = applicable proposals ÷ proposals — the fraction of the proposer's output the loop can preview without a human edit.
- `fingerprint_moved_rate` = applies whose `fingerprint()` changed ÷ non-no-op applies — must be 1.0.
- `strip_hooks_removed{kind}` = hooks removed per `strip_hooks` call — sanity check that F⁰ really is hook-free.

Status: spec, implementation in progress (2026-09-16). Target files `ganglion/contract/patch.py` (new), `ganglion/contract/__init__.py` (exports), `tests/test_contract_patch.py` (new).
