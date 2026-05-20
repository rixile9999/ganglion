[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Consumer: [external_benchmark_bfcl](./external_benchmark_bfcl.md)

# null_action_contract

Catalog-level contract that lets Ganglion's Action IR represent **"no tool call needed"** as a first-class value (`{"calls": []}` → `ActionPlan(calls=())`). Closing the BFCL `irrelevance` gap measured in M1'–M4' depended on this — without an empty-plan representation the DSL path could not abstain symmetrically with the native baseline.

## Role

Promote the empty Action IR to a valid Catalog output and prompt-level instruction, gated by an explicit per-catalog flag.

## Scope

- **in-scope**:
  - `Catalog.allow_empty_calls: bool` (defaults `False`) — opt-in surface that callers set when their evaluation includes abstention cases.
  - `Catalog.validate` / `Catalog.parse_json_dsl` accepting `{"calls": []}` as a valid payload **iff** `allow_empty_calls=True`; returning `ActionPlan(calls=())`.
  - `Catalog.render_json_dsl()` adding the line `If no tool call is needed, return exactly {"calls":[]}.` immediately after the JSON shape line **iff** `allow_empty_calls=True`.
  - `compile_tool_calling_schema(..., allow_empty_calls=True)` propagating the flag to the generated `Catalog`.
  - `ast_match(predicted_calls=(), case)` returning `valid=True` whenever `case.ground_truth is None` (irrelevance semantics).
- **out-of-scope**:
  - Semantic abstention classifiers / gating logic — deferred to a future M6 task.
  - Prompt-engineering work to *reduce* false tool calls in `irrelevance` beyond the literal instruction line (e.g. "listed tools are unavailable unless they exactly satisfy the user request"). Tracked in [`bfcl_m5_abstention_report.md` §8](../bfcl_m5_abstention_report.md) as future work.
  - Native baseline (`QwenNativeToolClient`) behaviour — abstention there follows the OpenAI tool API contract, not this flag.
  - The default-on transition. Existing IoT-light tiers must not change behaviour; `allow_empty_calls` stays opt-in and defaults `False`.
  - False-abstention recovery on callable inputs — out-of-band; surfaced via `false_abstention_rate` for monitoring only.
- **on violation**: a payload with an empty `calls` array against a catalog with `allow_empty_calls=False` raises `DSLValidationError("'calls' must not be empty")`. The flag is never inferred from input shape.

## Procedure

```
construct catalog:
    Catalog(..., allow_empty_calls=<bool>)

render prompt:
    base_lines ← ["Return JSON only.", 'JSON shape: {"calls":[...]}']
    if allow_empty_calls:
        base_lines.append('If no tool call is needed, return exactly {"calls":[]}.')
    base_lines += "Allowed actions:" + per-tool lines + "Rules:" + …

validate payload:
    if "calls" missing → DSLValidationError
    if calls is empty:
        if allow_empty_calls → return ActionPlan(calls=())
        else                 → DSLValidationError("'calls' must not be empty")
    else                     → validate each call as usual

ast_match against case:
    if case.ground_truth is None:
        return predicted_calls == () ? valid : "irrelevance:unexpected_call"
    else: …
```

## Contract

- **in**: `allow_empty_calls: bool` at `Catalog` construction (directly or via `compile_tool_calling_schema`).
- **out**:
  - `Catalog.parse_json_dsl('{"calls":[]}')` returns `ActionPlan(calls=())` when the flag is set; raises `DSLValidationError` otherwise.
  - `Catalog.render_json_dsl()` deterministically contains the no-call line when the flag is set, and does not contain it when not.
  - `ActionPlan(calls=())` is constructible and equality-comparable (`ActionPlan(calls=()) == ActionPlan(calls=())`).
- **event**: no events emitted directly. Downstream tasks (notably [external_benchmark_bfcl](./external_benchmark_bfcl.md)) consume this contract.
- **failure**:
  - Empty `calls` against `allow_empty_calls=False` → `DSLValidationError("'calls' must not be empty")`.
  - Caller passes a non-bool to the flag → `TypeError` at dataclass construction.
- **success**:
  - `tests/test_validator.py` / `tests/test_bfcl_grader.py` exercise both `allow_empty_calls={True, False}` branches and `ast_match` on `case.ground_truth=None`.
  - `tests/test_bfcl_runner.py` confirms `irrelevance` smoke does not produce false abstentions on callable inputs.

## Observation

- `irrelevance_ast_match_rate` — share of irrelevance cases for which `predicted == ()`. BFCL M5 baseline target: ≥ native (current: DSL 90.0% vs native 86.0% on plus, DSL 78.0% vs native 83.0% on flash — see [`bfcl_m5_abstention_report.md`](../bfcl_m5_abstention_report.md) and [`bfcl_flash_replay_report.md`](../bfcl_flash_replay_report.md)).
- `false_abstention_rate` — share of callable cases (`simple_python|multiple|parallel|parallel_multiple`) for which the predicted plan is empty. Reported as a regression guard: M5 full run measured **0/400** on plus.
- `prompt_delta_chars` — length difference between `render_json_dsl()` outputs with the flag on vs off, for a fixed catalog. Should be a single constant line (~60 chars).

## Status

Implementation landed alongside BFCL M5'. Live artifacts:

- `Catalog.allow_empty_calls` — `ganglion/dsl/catalog.py:32` and validate/render call sites.
- Compiler propagation — `ganglion/dsl/compiler.py:compile_tool_calling_schema(..., allow_empty_calls=…)`.
- Grader irrelevance branch — `ganglion/bfcl/grader.py:57-64`.
- Runner integration — `ganglion/eval/bfcl_runner.py:build_case_catalog(... allow_empty_calls=…)`.
- Evidence — [`bfcl_m5_abstention_report.md`](../bfcl_m5_abstention_report.md) (irrelevance 74% → 90%, native 86% 역전; callable 400 false-abstention 0건).
