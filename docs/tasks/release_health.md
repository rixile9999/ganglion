[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Composition rules: [workflow_principle](../agent-forge/workflow_principle.md)

# release_health (composite)

Aggregate the per-edge verdict events from [`dataset_integrity`](./dataset_integrity.md), [`eval_smoke_guard`](./eval_smoke_guard.md), and [`report_freshness`](./report_freshness.md) into a single SHA-keyed *release-readiness* signal. **Composite earns its keep because no single primitive's contract answers "is this SHA shippable?"**

## Role

Produce exactly one outer verdict per SHA — `release_ready(sha)` | `release_blocked(sha, cause)` | `release_stale(sha, missing=[...])` — by subscribing to primitive verdict events.

## Scope

- **in-scope**:
  - Subscribing to verdict events from the three primitives above, keyed by SHA.
  - Aggregating within a configurable window `W` (default 30 min) per SHA.
  - Emitting exactly one outer verdict per SHA observed.
- **out-of-scope**:
  - Re-running any primitive — composite only listens.
  - Defining or modifying primitive thresholds (e.g. tolerance for `eval_smoke_guard`).
  - Gating actual merges — callers (branch protection, release scripts) decide what to do with `release_ready`.
  - Catalog drift — `catalog_spec_sync` runs pre-merge as a *PR-opening* agent, not a verdict-emitter. (May be added to this composite if its emission shape stabilises.)
  - Any primitive not in the subscription list — silent on signals it does not consume.
- **on violation**: if a primitive emits a verdict event whose schema does not match the contracts above (e.g. missing `sha` field, unknown event name) → emit `release_health_failed(sha, cause=protocol, primitive)`; **do not coerce** the malformed event into a known shape. The fix is in the primitive's contract.

## Procedure

```
on dataset_integrity_passed(sha)   → record A(sha)
on eval_smoke_passed(sha)          → record B(sha)
on report_freshness_passed(sha)    → record C(sha)
on any *_failed | *_regressed(sha, cause)
                                   → record fail(sha, primitive, cause)

evaluate(sha):
    if any fail(sha, *) recorded within W:
        emit release_blocked(sha, causes=[fail entries])
    elif A(sha) ∧ B(sha) ∧ C(sha) recorded within W:
        emit release_ready(sha)
    elif W elapsed since first signal for sha:
        emit release_stale(sha, missing=[primitives without verdict])

on each primitive event for sha:
    evaluate(sha)
on window-W timer per sha:
    evaluate(sha)
```

## Contract

- **in**: verdict events from the three primitives; SHA carried in every event.
- **out**: exactly one outer verdict event per SHA observed.
- **event**:
  - consume: `dataset_integrity_{passed,failed}`, `eval_smoke_{passed,regressed,failed,bootstrap_required}`, `report_freshness_{passed,failed,bootstrap_required}`.
  - emit: `release_ready(sha) | release_blocked(sha, causes) | release_stale(sha, missing) | release_health_failed(sha, cause)`.
- **failure**:
  - Protocol violation in a consumed event → `release_health_failed(cause=protocol)`; outer verdict for that SHA suppressed.
  - Primitive emits `bootstrap_required` → counts as *neither passed nor failed*; SHA will land in `release_stale` if other primitives also do not pass.
  - Clock skew between primitives larger than `W` → `release_stale` is the correct outcome; do not extend the window adaptively.
- **success**: every SHA that any primitive emits an event for produces exactly one outer verdict; idempotent on duplicate primitive emissions for the same SHA.

## Inheritance (per [workflow_principle](../agent-forge/workflow_principle.md))

| Mechanism | What this composite inherits |
|---|---|
| Pointer | This file links back to [task_principle](../agent-forge/task_principle.md). |
| Template | Same 6-section structure as the primitives. |
| Pattern | Borrowed from agent-forge's *docs_health_check* worked example in `workflow_principle`. |
| Data | None — composite has no `## Pairs` / `## Subscribers` table yet; primitive list lives in the `event.consume` clause above. If a fourth primitive is added later, the right move is a `## Subscribers` data section here, not a parallel composite. |

## Observation

- `release_ready_rate` = `release_ready` events ÷ total SHAs evaluated (rolling 30d).
- `stale_rate` = `release_stale` ÷ total — high values indicate `W` is too short or a primitive is flaking.
- `blocked_cause_histogram` — counts of `release_blocked` causes by primitive — points at the weakest edge.
- `protocol_violation_count` — `release_health_failed(cause=protocol)` events; should be 0 once primitives stabilise.

## Negative checks (composite anti-patterns)

- ☐ This file declares its own `in / out / event / failure / success` — not just a topology diagram. (If it ever degenerates to "just a diagram", delete per `workflow_principle` §Anti-patterns.)
- ☐ Does not wrap a single primitive — three independent verdicts feed in.
- ☐ Does not mutate any primitive's `in-scope` — only listens to their declared event emissions.
