**[English](task_principle.md)** · [한국어](task_principle.ko.md)

[← Home](Home.md) · [Parent principle](agent_skill_principle.md) · [ux_agent](ux_agent.md) · [test_agent](test_agent.md) · [ci_trigger](ci_trigger.md)

# Principles for delegating arbitrary tasks

A generalization of the [agent / skill-set authoring principles](agent_skill_principle.md). The rules followed when delegating *any task* to an agent — not limited to a particular domain (UX / test / CI).

## Premise

LLMs tend toward non-determinism, interface drift, and **scope creep**. Minimize degrees of freedom and absorb them via *verifiable artifacts, explicit contracts, and explicit role / scope*. If the contract and scope cannot be enforced, the task is not a candidate for automated delegation.

## Principles

### 1. Simplicity

A brief carries the minimum sufficient information. No boilerplate, no verbose context. It must fit on one screen.

### 2. Modularity

One task = one responsibility. Decompose composite tasks. If decomposition is impossible, redraw the responsibility boundary.

### 3. Role and scope

Scope creep is the **chronic failure mode** of LLMs — not a simple mistake, but a tendency of the model to "helpfully" touch adjacent areas. Therefore every task declares its *positive scope* and *negative scope* with equal weight.

- `role` — *who* this task is. One sentence. Starts with a verb.
- `in-scope` — artifacts and areas it explicitly owns. Must be enumerable.
- `out-of-scope` — areas adjacent to but *not touched by* this task. **Must not be empty** — an empty `out-of-scope` is a signal of "scope undefined", which is itself the surface area for scope creep.
- `on violation` — if the task judges it must touch outside the boundary, *do not handle directly*. Stop and escalate, or branch to a responsible task.

> Role and scope are defined **before** the contract. The contract (in / out / event / failure) specifies *how*; role and scope specify *what to do / what not to do*. Without the latter, no matter how precise the contract, creep cannot be prevented.

### 4. Composition

Tasks connect to other tasks through `in / out / event`. Do not call another task directly — connect *via events only*. (Origin: the [Actor model · pub/sub](agent_skill_principle.md) influence. The sender knows nothing of the receiver's existence, implementation, or state.)

### 5. Explicit contract

Every task declares:

- `in` — inputs: sources, arguments, prior artifacts
- `out` — observable artifacts: files, responses, state changes (natural-language reports are ✗)
- `event` — names of events to consume / emit
- `failure` — branches: behavior per undefined signal, partial failure, external-dependency failure
- `success` — a predicate that can be auto-verified: assertions / file existence / `status == pass`

### 6. Lazy evaluation

If there is no change signal, do not run. Wasted-call rate = (runs ending in "no change") / (total runs). See [ci_trigger](ci_trigger.md).

### 7. Fail loud

Do not guess in undefined states. Log + escalate. Automatic recovery only along branches defined in the contract.

### 8. Idempotency

Same input + same event = same artifact. Concentrate side effects in one place so that external systems make them idempotent. If duplicate triggers accumulate damage, the contract is violated.

### 9. Observability

Anything outside the contract is assumed invisible from the outside. Signals that need to be observed must be exposed as explicit metric / log.

## Task document template

```
# <task name>

## Role
<who this task is — one sentence, starts with a verb>

## Scope
- in-scope:      <areas it owns — enumerated>
- out-of-scope:  <adjacent areas not touched — must not be empty>
- on violation:  <escalation branch when work outside the boundary is required>

## Procedure
<trigger signal or precondition>:
    <step 1>
    <step 2>
on <failure mode>:
    <branch>

## Contract
- in:       <...>
- out:      <observable artifacts>
- event:    consume <...>, emit <...>
- failure:  <branch table>
- success:  <auto-verifiable predicate>

## Observation
<metric> = <how it is computed>
```

## Applied examples

- [ux_agent](ux_agent.md) — UI/UX doc synchronization
- [test_agent](test_agent.md) — E2E script maintenance and execution
- [ci_trigger](ci_trigger.md) — event routing and observation

Examples of "arbitrary tasks": dependency upgrades, schema migrations, security review, refactoring, data backfill … all should be definable with the same template. If the template is hard to fill, the task is too large — *decompose*.

How to wire these atomic tasks into more powerful **composite** task docs (Unix-pipe style): [Workflow composition principles](workflow_principle.md).

## Anti-patterns

- "Just do whatever's reasonable" — open-ended delegation → no `success` predicate, no verifiability.
- Stating `in / out` only vaguely in natural language, with no contract → accumulating drift.
- **Leaving `out-of-scope` blank or omitted** → "undefined area" = scope-creep surface. A blank `out-of-scope` is an incomplete spec.
- **"While I'm here, let me also fix the adjacent thing"** — automatic side-corrections → if the boundary is touched, *stop and escalate*. No helpful autocorrects.
- Arbitrary fallbacks on failure → accumulating undefined states. Branches only as defined in the contract.
- One task owning multiple artifacts → responsibility boundary collapses. Decompose.
- Calling another task directly without events → tight coupling, no observability.
- `out` is a natural-language "report" → auto-verification fails. Convert to a machine-verifiable form.
