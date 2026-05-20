[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_repair_policy

Specify the repair-loop as a **configurable policy** rather than a fixed retry. Today, `ganglion/runtime/qwen.py:run_dsl_with_repair` (lines 72-124) hard-codes one corrective user message and one budget knob (`RepairConfig(enabled, max_attempts)`). This task lifts that into a `RepairPolicy` protocol so different policies — per-`FailureType` retry messages, custom budgets, A/B ablations — can be tried live or replayed against recorded traces without making new API calls.

## Role

Decide, given the conversation-so-far and the most recent validation error, whether the [[lm_client]] should retry (and with what corrective message) or give up.

## Scope

- **in-scope**:
  - `RepairPolicy` protocol with a single method:
    - `decide(attempts: list[dict], last_error: DSLValidationError) -> RepairAction`
    - `RepairAction` = `Retry(retry_message: list[dict])` | `GiveUp(reason: str)`. Policies are *stateless* — input is the recorded attempts log, output is the next decision.
  - Concrete policies, all in `ganglion/analyzer/repair.py`:
    - `NoRepairPolicy()` — always returns `GiveUp("repair_disabled")`. Reproduces today's `RepairConfig(enabled=False)`.
    - `FixedRetryPolicy(max_attempts=1, message_template=DEFAULT)` — reproduces today's `RepairConfig(enabled=True, max_attempts=1)` byte-for-byte. **Drop-in default.**
    - `PerFailureTypePolicy(rules: Mapping[FailureType, PolicyRule])` — branches on the [[analyzer_failure_taxonomy]] classification of `last_error` and applies a per-type budget + retry-message template.
    - `AblationPolicy(policies: Sequence[RepairPolicy], branch_fn: Callable[[list[dict]], int])` — routes between sibling policies for offline A/B comparison (e.g. by `trace_id % n`, by catalog tag).
  - Retry-message templates:
    - Default — `"Your previous JSON failed validation: {error}. Return only valid JSON conforming to the catalog."` (verbatim with today's `run_dsl_with_repair` message at `qwen.py:117-120`).
    - Per-`FailureType` specialisations (consumed by `PerFailureTypePolicy`):
      - `syntax_invalid` → restate the catalog headers (the `Return JSON only.` + JSON-shape lines from `Catalog.render_json_dsl()`).
      - `unknown_action` → list the allowed action names from the catalog, framed as `"Use one of: …"`.
      - `unknown_arg` → enumerate the allowed arg names for the predicted tool.
      - `value_out_of_enum` → list the allowed enum values for the offending arg.
      - `wrong_action` (catalog-validated but semantically off, surfaced via grader) → retry with a corrected tool subset.
    - Templates are pure functions of `(last_error, catalog)`; no state is captured. This keeps `decide` cheap to replay.
  - **Replay support.** Given a recorded `Trace` from [[analyzer_trace_store]], simulate "what would policy P have done?" by re-feeding `trace.attempts` to `policy.decide()` step-by-step, **without** any model call. Used to ablate policies offline against the historical corpus.
  - Integration surface: [[lm_client]] accepts an optional `RepairPolicy` (replacing today's `RepairConfig` knob). When set, after each `DSLValidationError` the client calls `policy.decide(attempts_so_far, exc)` and acts on the returned `Retry` / `GiveUp`.
  - Target path: `ganglion/analyzer/repair.py` (new module under the new `analyzer/` peer of `lm/` and `contract/` — see [docs/goal/goal.md](../goal/goal.md)).
- **out-of-scope**:
  - Actual model invocation, conversation-state mutation, token accounting — owned by [[lm_client]]. This task is the *brain* telling the client when to retry; it never opens a network connection.
  - Synthesising new validator rules / catalog aliases from observed failures — that feedback edge belongs to [[analyzer_rule_synthesis]] (rules flow back into `Catalog`, not into retry messages).
  - Training a learned / policy-gradient repair policy — defer. This primitive is deterministic; learning is a downstream consumer.
  - Persisting per-attempt artifacts — [[analyzer_trace_store]] records all attempts of a single case as one `Trace`. This task only *reads* `trace.attempts`.
  - Cross-attempt prompt mutation beyond the retry-message append (e.g. rewriting the system prompt, swapping the catalog mid-loop). One corrective user message per attempt; full prompt rewrites are a separate task.
  - Concurrency / parallel retries / speculative branching. Strictly single-threaded per case.
  - Native tool-call client (`QwenNativeToolClient`) — its retries follow the OpenAI tool API contract, not this policy. Wiring the policy into freeform / native paths is a follow-up.
- **on violation**: if a policy returns `Retry` after `max_attempts` is exceeded, [[lm_client]] **must fail loud** — raise `RepairBudgetExceeded(policy=…, attempt=…)` rather than silently honour the over-budget retry. Policies that miscount their own budget are bugs, not edge cases.

## Procedure

```
sync pipeline (live):
    lm_client invokes completer → response → catalog.parse_json_dsl(response)
    on DSLValidationError as exc:
        decision ← policy.decide(attempts_so_far, exc)
        match decision:
            Retry(message):
                if len(attempts_so_far) > policy.max_attempts:
                    raise RepairBudgetExceeded
                append (assistant: prev_response) + (user: message) to conversation
                continue loop
            GiveUp(reason):
                raise exc  # original DSLValidationError, with reason attached to attempts log

offline replay pipeline:
    consume analyzer.trace.recorded(trace_id) event from [[analyzer_trace_store]]
    load Trace, walk trace.attempts in order:
        for attempt_i in trace.attempts:
            reconstruct DSLValidationError from attempt_i["error"]
            decision ← policy.decide(attempts[:i+1], reconstructed_error)
            compare decision to recorded next-step (Retry vs GiveUp)
    succeeded ← (final attempt has no error)
    emit analyzer.repair.replayed(trace_id, policy_id, succeeded, attempts_used, disagreement_count)

on policy raises:
    log + treat as GiveUp("policy_error: {exc}"). Never let a policy bug crash the host loop.

on missing trace.attempts:
    emit analyzer.repair.replayed(..., status="degenerate") and skip.
```

Budget arithmetic is owned by the **caller** ([[lm_client]]), not the policy: the
client counts how many attempts have been made and refuses any `Retry` that
would push the count past `policy.max_attempts`. A policy may *suggest* `Retry`
indefinitely; the caller's invariant `len(attempts) ≤ max_attempts + 1` is the
authoritative budget check. This separation lets `PerFailureTypePolicy` declare
per-`FailureType` budgets without re-implementing the global cap.

## Contract

- **in**:
  - live mode: `attempts: list[dict]` (the in-progress conversation log, shape per `ModelResult.raw["attempts"]`) + `last_error: DSLValidationError` + a `RepairPolicy` instance held by [[lm_client]].
  - replay mode: a `Trace` loaded by [[analyzer_trace_store]] in response to `analyzer.trace.recorded`.
- **out**:
  - `RepairAction = Retry(retry_message: list[dict]) | GiveUp(reason: str)` returned per call to `policy.decide`.
  - One `analyzer.repair.replayed(trace_id, policy_id, succeeded: bool, attempts_used: int, disagreement_count: int)` event per replayed trace.
  - No file artifacts owned directly; replay results are aggregated by [[analyzer_trace_store]] consumers.
- **event**: consume `analyzer.trace.recorded` (replay trigger). Emit `analyzer.repair.replayed`.
- **failure**:
  - Policy raises during `decide` → caught, logged, coerced to `GiveUp("policy_error: …")`. Original call site sees normal `GiveUp`.
  - Policy returns `Retry` past its own declared budget → `RepairBudgetExceeded` (fail loud).
  - Replay over a `Trace` with no `attempts` key, or attempts missing the `error` field → emit `analyzer.repair.replayed(..., status="degenerate")`, do not crash.
  - Unknown `FailureType` in `PerFailureTypePolicy.rules` → fall through to the default template; do not raise.
- **success**:
  - `tests/test_repair_loop.py` continues to pass with `FixedRetryPolicy(max_attempts=1)` swapped in for `RepairConfig(enabled=True, max_attempts=1)` — byte-equal retry message, byte-equal `ModelResult.raw["attempts"]` shape.
  - A new `tests/test_repair_policy.py` covers: `NoRepairPolicy` returns `GiveUp` immediately; `PerFailureTypePolicy` picks the right template for each `FailureType`; replay over a recorded trace reproduces the recorded sequence under the same policy.

## Observation

- `repair_attempt_distribution[catalog_id, failure_type]` — histogram of attempts-until-success vs giveup, partitioned by catalog and by [[analyzer_failure_taxonomy]] type. Computed from `Trace.attempts` aggregated by [[analyzer_trace_store]].
- `repair_success_rate[policy]` — share of cases where a `Retry` chain ended in a valid parse, per policy id. Drop-in comparison metric for `PerFailureTypePolicy` vs `FixedRetryPolicy`.
- `replay_vs_recorded_disagreement_rate` — when replaying recorded traces under the **same** policy that was active at record time, decisions should match. Non-zero rate ⇒ either non-determinism in the policy or drift in the failure taxonomy classifier ⇒ alert.
- `repair_budget_exceeded_count` — count of `RepairBudgetExceeded` raises. Strict zero in healthy operation; non-zero ⇒ policy bug.
- `retry_message_token_cost[policy, failure_type]` — input-token delta added by the retry-message append, averaged per attempt. Lets us compare verbose `PerFailureTypePolicy` templates against the terse default and quantify the "smarter retry costs more prompt" tradeoff.
- `attempts_per_successful_case[policy]` — mean attempts across cases that ended successfully. Companion to `repair_success_rate` — a policy that achieves the same success rate in fewer attempts is strictly better.

## Notes for implementors

- The new module lives under `ganglion/analyzer/` (the third peer of `lm/` and `contract/` per [docs/goal/goal.md](../goal/goal.md)). It must not import from `ganglion/runtime/qwen.py`; the dependency runs the other way — lm depends on the analyzer protocol, never the reverse.
- `Trace` (from [[analyzer_trace_store]]) is consumed as an immutable record: read `trace.attempts`, do not mutate.
