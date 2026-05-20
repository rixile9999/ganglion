[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Supersedes: [legacy/null_action_contract](./legacy/null_action_contract.md)

# contract_null_action

Catalog-level contract that promotes `{"calls": []}` to a first-class Action IR value — `ActionPlan(calls=())` — gated by an explicit per-catalog `allow_empty_calls` flag. Refresh of the legacy [`null_action_contract`](./legacy/null_action_contract.md) spec for Module 3 (`contract/`) of the redesigned [[goal]] layout. Closes the BFCL `irrelevance` abstention gap measured in M5' (see `docs/bfcl_m5_abstention_report.md`).

## Role

Define when an empty `calls` array is a valid Action IR value, how the [[contract_catalog]] surface advertises that capability to the LM, and how downstream graders interpret it — without inventing model-side abstention semantics.

## Scope

- in-scope:
  - `Catalog.allow_empty_calls: bool` on `ganglion/contract/catalog.py` — defaults to `False`; opt-in only.
  - Validator behaviour in `ganglion/contract/parse.py`: when `allow_empty_calls=True`, `{"calls": []}` returns `ActionPlan(calls=())`; when `False`, raises `DSLValidationError("calls must not be empty")`.
  - Prompt rendering branch: when the flag is set, `Catalog.render_json_dsl()` appends the literal line `If no tool call is needed, return exactly {"calls":[]}.` immediately after the JSON shape line; when unset, the line is absent (preserving pre-M5' prompt parity for legacy IoT-light callers).
  - Native baseline behaviour: when the flag is set, `Catalog.render_openai_tools()` continues to expose all tools — abstention is left to the model, not coerced via `tool_choice="none"` or tool-list pruning.
  - AST-grader handling for [[benchmark_bfcl]]: the `irrelevance` category is `valid=True` iff the predicted plan is empty (`ActionPlan(calls=())`).
  - Flag propagation through the schema compiler so per-case BFCL catalogs inherit the opt-in.
- out-of-scope:
  - Semantic abstention classifiers (model-side intent detection that decides "should I call?") — the contract is purely about *whether* an empty plan is accepted, not whether the model produces one.
  - Prompt-engineering work beyond the literal "no call" instruction line (e.g. negative examples, "tools are unavailable unless they exactly satisfy the request" framing) — left to the catalog author or to synth-strategy choices.
  - Native baseline `tool_choice="none"` semantics or any transport-level coercion — a distinct abstention mechanism, separate concern.
  - False-abstention recovery (model abstains when it should have called) — that is [[analyzer_failure_taxonomy]]'s `abstention_miss_should_call` bucket plus any retry behaviour from [[analyzer_repair_policy]].
  - Default-on transition. The flag stays opt-in to preserve backwards compatibility with the IoT-light tiers and any existing M1–M4 reports.
- on violation: if a consumer wants empty-call semantics without setting the flag, it must explicitly construct a `Catalog` with `allow_empty_calls=True` (directly or via the schema compiler's `allow_empty_calls=` kwarg). Do **not** infer the flag from input shape, silently flip the default, or auto-promote `{"calls": []}` to valid because the model produced it. Stop and require the caller to opt in.

## Procedure

```
construct catalog:
    Catalog(..., allow_empty_calls=<bool>)   # default False

render prompt (Catalog.render_json_dsl):
    lines  ← ["Return JSON only.", 'JSON shape: {"calls":[...]}']
    if allow_empty_calls:
        lines.append('If no tool call is needed, return exactly {"calls":[]}.')
    lines += "Allowed actions:" + per-tool lines + rules

render native (Catalog.render_openai_tools):
    return [...full tool schemas...]                # unaffected by flag
    # no tool_choice="none" injection — abstention is a model decision

parse payload (parse.parse_json_dsl):
    obj ← json.loads(payload) if isinstance(payload, str) else payload
    if "calls" not in obj:
        raise DSLValidationError("'calls' missing")
    if obj["calls"] == []:
        if catalog.allow_empty_calls:
            return ActionPlan(calls=())
        else:
            raise DSLValidationError("calls must not be empty")
    else:
        validate each call as usual

ast_match against BFCL case (benchmark.bfcl.grader):
    if case.ground_truth is None:                   # irrelevance row
        return predicted == ()  ?  valid  :  "irrelevance:unexpected_call"
    else: …standard match…
```

## Contract

- in: a `Catalog` instance constructed with `allow_empty_calls=<bool>` (directly or via `compile_tool_calling_schema(..., allow_empty_calls=<bool>)`), plus a candidate JSON DSL string or pre-parsed mapping.
- out:
  - `parse_json_dsl('{"calls":[]}')` against a flag-on catalog returns `ActionPlan(calls=())`.
  - Same input against a flag-off catalog raises `DSLValidationError("calls must not be empty")`.
  - `Catalog.render_json_dsl()` deterministically contains the no-call line iff the flag is set.
  - `ActionPlan(calls=())` is constructible, immutable, and equality-comparable (`ActionPlan(calls=()) == ActionPlan(calls=())`).
- event: none — the contract surface flows through `Catalog` instances, not events. Consumers: [[benchmark_bfcl]] (uses the contract for the `irrelevance` category), [[analyzer_failure_taxonomy]] (uses the empty-plan return value to bucket `abstention_miss_*` failures), [[contract_catalog]] (carries the flag on the dataclass).
- failure:
  - Malformed input (non-JSON, missing `calls`, wrong shape) → `DSLValidationError` per normal validator rules; the flag does not relax other validation.
  - Non-empty `calls` containing invalid entries → normal per-call validation rules; the flag only affects the empty case.
  - Flag is set but the BFCL grader rejects the empty plan against a callable ground truth → that's a *grader* outcome (`false_abstention`), not a contract failure; the contract still produced a well-formed `ActionPlan(calls=())`.
  - Non-bool passed to `allow_empty_calls` → consumer responsibility; the dataclass stores whatever it is given and any subsequent truthy check still resolves, but downstream parity assertions in [[contract_catalog]] should reject it.
- success:
  - `tests/test_bfcl_smoke.py` irrelevance assertions pass (empty plan → `valid=True` when ground truth is `None`).
  - `tests/test_validator.py` exercises both `allow_empty_calls={True, False}` branches on the same payload.
  - `docs/bfcl_m5_abstention_report.md` numbers (irrelevance DSL 90% / native 86% on plus; 0/400 false-abstention on callable categories) reproducible by replaying the recorded BFCL cases against the refreshed contract.

## Observation

- `empty_plan_count` — number of cases per run whose validated `ActionPlan` is `()`. Reported alongside the standard syntax/exact-match metrics.
- `abstention_correct_rate` = correctly-empty plans ÷ ground-truth-abstain cases (i.e. BFCL `irrelevance` rows). Target on the M5' replay: ≥ native baseline.
- `abstention_false_positive_rate` = wrongly-empty plans ÷ ground-truth-call cases (the four BFCL callable categories). Regression guard: must stay at the M5' floor (0/400 on plus) for the same model + prompt.
- `prompt_delta_chars` = length difference between `render_json_dsl()` with the flag on vs off for a fixed catalog. Expected to be a single constant line (~60 chars); used by [[contract_catalog]]'s parity assertions.
