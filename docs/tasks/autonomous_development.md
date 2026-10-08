# Ganglion autonomous development composite

Status: design proposal, runtime not implemented · 2026-10-08

This composite implements the development loop in [the system design](../autonomous_development_design.md). It composes planning, allocation, isolated implementation, verification and code integration through persisted commands, events and artifact references. It develops the project that implements the v2 factory; product bundle promotion remains owned by that factory.

## Role

Advance a versioned project objective by integrating independently verified code changes produced by bounded teams assigned to module and submodule cells.

## Scope

- in-scope: objective and module snapshots; proposal intake; dependency readiness; bounded role assignment; task attempts; verification dispatch; merge queue; research-branch promotion; crash recovery; costs and progress projections.
- out-of-scope: changing the meaning of the objective or quality thresholds; unrestricted worker spawning; product release approval; training implementation; releasing independent evaluation answers to builders; changing main or remote refs without the corresponding configured permission.
- on violation: preserve the candidate and failure evidence, reject the transition, and emit a scope or policy violation. A justified scope change becomes a revised proposal or dependent task.

## Procedure

```text
on dev.objective.registered or dev.proposal.created:
    validate objective, module ownership, acceptance, inputs and proposal key
    persist accepted task DAG and emit dev.task.ready for unblocked tasks

on dev.task.ready or dev.capacity.available:
    rank ready work using the frozen scheduling policy
    claim a candidate role slot, allocate fencing token, reserve budget/resources
    enqueue dev.workspace.prepare and dev.attempt.run commands in the outbox

on dev.attempt.submitted:
    require current unexpired lease, matching revisions and valid artifact hashes
    collect actual scope; enqueue independent verification of the candidate

on dev.verification.passed:
    enqueue integration candidate preparation against the current target SHA
    verify the exact integration SHA with required system and consumer gates

on dev.integration.verified:
    persist integration intent, then CAS the configured target reference
    reconcile the actual ref, complete the intent and publish dev.change.integrated

on dev.change.integrated:
    update cell revision and milestone evidence
    supersede affected stale tasks; unblock successors with refreshed inputs

on attempt failure or lease expiry:
    preserve artifacts; stop or quarantine the running resource holder
    reconcile unknown spend before releasing reservations
    create a new attempt only within the fixed retry and budget policy

on restart:
    reconcile leases, live processes, artifact references and integration intents
    replay pending outbox records through idempotent consumers

on objective completion or an episode stop condition:
    stop new admissions, settle active attempts and reservations
    emit one terminal dev.episode.finished record for the objective revision
```

## Contract

- in: ProjectObjective and ModuleSpec hashes; versioned TaskSpecs and access-filtered ContextPackets; configured resource and promotion policy; primitive events; immutable change and verification artifacts.
- out: persisted attempts and leases; ChangeSets; VerificationReports; IntegrationRecords; a terminal EpisodeReport with accepted and unfulfilled milestone predicates, full cost records and unresolved conditions.
- event consume: `dev.objective.registered`, `dev.proposal.created`, `dev.task.ready`, `dev.capacity.available`, `dev.attempt.submitted`, `dev.attempt.failed`, `dev.lease.expired`, `dev.verification.passed`, `dev.verification.failed`, `dev.integration.verified`, `dev.change.integrated`, `dev.input.updated`.
- event emit: `dev.task.ready`, `dev.attempt.claimed`, `dev.verification.requested`, `dev.integration.requested`, `dev.change.integrated`, `dev.task.superseded`, `dev.policy.violated`, `dev.episode.finished`.
- commands: `dev.workspace.prepare`, `dev.attempt.run`, `dev.verification.run`, `dev.integration.prepare`, `dev.integration.commit`. Primitives declare their own completion and failure artifacts; the composite does not implement those algorithms inline.
- failure: stale revisions invalidate the candidate; failed gates preserve evidence and permit bounded repair; missing evidence prevents promotion; expired leases reject late writes; uncertain external outcomes trigger reconciliation; budget/deadline/plateau exhaustion terminates the episode with unmet predicates.
- success: completed tasks reference integrated commits whose frozen acceptance predicates passed; every promotion has a verified exact SHA and reconciled CAS record; no stale token may register an accepted result; resource and budget reservations never exceed configured limits; one terminal report exists per episode id.

Delivery is at-least-once. Application is idempotent through consumer deduplication and transactionally persisted effects. Terminal output uniqueness uses `(episode_id, terminal_kind)`; episode identity fixes the objective revision and policy hash. Re-running the same objective is a new episode. Partial progress at exhaustion is reported as partial progress.

Attempts and fencing tokens are scoped to `(task_revision, candidate_id, role_slot)`, so replacing one worker does not invalidate an independent candidate. Verification is a separate job bound to a submitted SHA. When a candidate is integrated, competing attempts are cancelled and all child/candidate costs remain charged to the parent task and episode.

## Observation

- `milestone_completion_rate` = independently confirmed complete milestones / admitted milestones.
- `episode_cost` includes planning, worker, failed attempt, verification, integration and reconciliation costs; unknown values remain unknown or conservatively reserved.
- `ready_wait_seconds`, `verification_backlog`, `coordination_token_fraction`, `idle_session_seconds` identify allocation bottlenecks.
- `post_integration_regressions` counts regressions and reverts in a fixed observation window.
- `policy_violation_count`, `rejected_stale_submissions`, `reconciled_integration_intents`, `quarantined_resources` expose enforcement and recovery behavior.
- `no_change_session_rate` = sessions producing no new admissible artifact / all sessions.

General principles: [task contracts](../agent-forge/task_principle.md), [workflow composition](../agent-forge/workflow_principle.md).
