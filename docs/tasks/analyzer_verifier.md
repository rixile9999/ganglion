[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_verifier

Catalog-bound, deterministic, continuous reward function over LM outputs. Used by [[lm_data_synth]] as a synthesis-gate predicate and by [[lm_finetune]] as the DPO/RL reward signal. The shape is fixed by contract: `0.0` on parse failure, `0.3` on parse-only (no gold), `0.3 + 0.4·action_match_ratio + 0.2·arg_match_ratio` on partial match against gold, `1.0` on exact-match. Catalog-bound by construction — one verifier per [[contract_catalog]], never pluggable across catalogs.

## Role

Compute a continuous, deterministic reward in `[0, 1]` for a raw LM output, given an optional gold `ActionPlan`, against a fixed `Catalog`.

## Scope

- **in-scope**:
  - `VerifierFn = Callable[[raw_output: str, gold: ActionPlan | None, prompt: str | None], float]` bound to a single `Catalog`.
  - `make_verifier(catalog: Catalog) -> VerifierFn` factory — one verifier per [[contract_catalog]]; the catalog is closed over at construction time.
  - Reward shape (deterministic, continuous, `[0, 1]`):
    - `0.0` — `catalog.parse_json_dsl(raw_output)` raises.
    - `0.3` — parses OK, `gold is None` (structural validity only; no semantic ground truth).
    - `0.3 + 0.4·action_match_ratio + 0.2·arg_match_ratio` — gold supplied, partial match.
    - `1.0` — `predicted_plan == gold` (ActionPlan value equality).
  - Per-call decomposition for multi-call plans: each call scored independently and averaged. Position-aligned (call `i` in predicted vs call `i` in gold); a length mismatch contributes `0.0` for the missing positions, not a reordering search. Reorder-tolerance is a [[benchmark_bfcl]] grader concern (parallel categories), not a reward concern. `action_match_ratio` = positions where action names agree / `max(len(predicted), len(gold))`. `arg_match_ratio` = per-call Jaccard of `(k, v)` arg pairs, averaged over gold calls; calls whose action does not match contribute `0.0`.
  - Determinism: identical `(raw, gold, prompt, catalog)` always yields the same float. No clocks, no RNG, no I/O.
  - Consumers:
    - [[lm_data_synth]] — synth-gate predicate `reward >= 0.95` (effectively exact match) to keep a synthesised `(prompt, expected)` pair.
    - [[lm_finetune]] — DPO pair construction reads verifier scores to order chosen vs rejected; GRPO/PPO use the raw `[0, 1]` value as the reward signal.
  - Target implementation path: `ganglion/analyzer/verifier.py`. Existing prototype at `ganglion/factory/customer/verifier.py:make_verifier` is the source of the contract; the analyzer module is its principled relocation.
- **out-of-scope**:
  - Training a learned reward model. The verifier is deterministic by definition; learned reward is a separate task surface.
  - Discrete match-shaped graders. Benchmark graders own their own scoring: [[benchmark_iot]] exact_match (boolean) and [[benchmark_bfcl]] ast_match (boolean over AST checker). The verifier is reward-shaped (continuous, gradient-friendly), not match-shaped.
  - Discrete graded scoring. `ganglion/eval/metrics.py:graded_score` (0 / 0.25 / 0.5 / 0.75 / 1.0) is a *different* signal for a *different* consumer; do not collapse the two.
  - Trace / failure classification — see [[analyzer_failure_taxonomy]] for the `syntax_invalid | wrong_action | wrong_arg | hallucinated_tool` partition.
  - Cross-catalog reward transfer. One verifier per `Catalog`; not pluggable. If a different catalog is in play, construct a different verifier.
  - Asynchronous / streaming reward computation. The verifier is synchronous and pure.
  - Reward calibration / scaling per RL algorithm (e.g. advantage normalisation, KL coefficients). Defer to downstream training in [[lm_finetune]] — DPO/GRPO handle their own scaling.
- **on violation**: if a caller wants reward beyond `[0, 1]` (e.g. negative reward for severe errors, or `> 1` for "better than gold"), open a separate task and a separate function. Do not stretch the interval inline — the closed interval is contract.

## Procedure

```
construct:
    fn ← make_verifier(catalog)        # catalog closed over by reference

per-invocation fn(raw, gold, prompt):
    try:
        plan ← catalog.parse_json_dsl(raw, prompt=prompt)
    except DSLValidationError | json.JSONDecodeError | TypeError:
        return 0.0                     # parse failure path

    if gold is None:
        return 0.3                     # structural validity only

    if plan == gold:
        return 1.0                     # ActionPlan value equality

    action_ratio ← _action_match_ratio(plan, gold)
        # per-call action equality, averaged over max(len(plan), len(gold)) positions
    arg_ratio    ← _arg_match_ratio(plan, gold)
        # per-call: Jaccard of (k, v) arg pairs, averaged over gold calls;
        # calls whose action does not match contribute 0.0

    return 0.3 + 0.4 * action_ratio + 0.2 * arg_ratio

on malformed catalog at construction:
    raise (fail loud — never silently degrade)
```

## Contract

- **in**:
  - Construction: `Catalog` instance.
  - Per-invocation: `raw_output: str`, `gold: ActionPlan | None`, `prompt: str | None`.
- **out**: `float ∈ [0, 1]`. Pure function; no files, no logs, no side effects.
- **event**: none. Consumed by direct call from [[lm_data_synth]] (synth-gate predicate) and [[lm_finetune]] (DPO pair construction).
- **failure**:
  - Malformed `Catalog` at construction time → raise (e.g. `TypeError` if not a `Catalog`).
  - Parse error at runtime (`DSLValidationError`, `json.JSONDecodeError`, malformed mapping, missing `calls`) → return `0.0` (the parse-failure reward path).
  - Invalid gold (`gold` is a string that doesn't parse, or an `ActionPlan` constructed against a different catalog) → raise `ValueError`. Bad gold is a programmer error, not a reward branch.
- **success**: against the fixture set in `tests/factory/test_verifier.py` (parse-fail / parse-only / partial / exact, plus multi-catalog smoke), reward values match expected values within `±1e-9`. When the implementation relocates to `ganglion/analyzer/verifier.py`, the same fixtures pass against the new path.

## Observation

- `verifier_invocation_count` — total calls per consumer (synth-gate vs DPO-pair). Lets us spot accidental hot-path use in eval (the verifier is for training-time scoring; benchmark graders own evaluation-time scoring).
- `reward_distribution` — histogram of returned values per consumer. A bimodal `{0.0, 1.0}` distribution at the synth-gate is healthy (clear keep/drop); a heavy mass at `0.3` on a DPO consumer means goldless invocations are leaking into RL.
- `parse_fail_rate` — share of inputs returning `0.0`. Correlates with [[analyzer_failure_taxonomy]] `syntax_invalid`. A sudden rise without a model change implicates the LM decoding path or catalog drift.
- `gate_keep_rate` — synth-gate-only metric: share of `reward >= 0.95` over total verifier calls in the synthesis loop. Drives [[lm_data_synth]]'s effective throughput.

## Status

Prototype lives at `ganglion/factory/customer/verifier.py` with test fixtures at `tests/factory/test_verifier.py`. This spec relocates the contract to `ganglion/analyzer/verifier.py` under the analyzer/ module surface (one of the three peer modules in [docs/goal/goal.md](../goal/goal.md): lm/, analyzer/, contract/). The signature also tightens from the current `(prompt_mapping, output_str) → float` (mapping carries `expected`) to the spec'd `(raw_output, gold, prompt) → float` triple so the gold path is explicit at call-sites and synthesis-gate vs DPO-pair-construction read identically.

See [[contract_catalog]] for the catalog binding, [[lm_data_synth]] and [[lm_finetune]] for the two consumers, [[analyzer_failure_taxonomy]] for the qualitative companion to this quantitative reward, and [[benchmark_iot]] / [[benchmark_bfcl]] for the match-shaped grader surfaces that this verifier deliberately does not replace.
