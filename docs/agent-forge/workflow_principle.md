**[English](workflow_principle.md)** · [한국어](workflow_principle.ko.md)

[← Home](Home.md) · [Task delegation principles](task_principle.md) · [Agent / skill-set authoring principles](agent_skill_principle.md)

# Workflow composition principles

Operational extension of [`agent_skill_principle`](agent_skill_principle.md)'s *composition* pillar. **Build complex capabilities by wiring small atomic task docs into composite task docs — like Unix pipes.** `grep | sort | uniq -c | head` is more powerful than any of its parts; saved as a script it becomes a first-class tool. The same idea applied to agentic workflows: a composite task doc *names* a useful pipeline and carries its own contract.

## Premise

**Composite task docs are first-class outputs**, not file-system clutter to avoid. Authoring one is how you turn ad-hoc orchestration into a named, contracted, observable capability that future workflows can themselves compose against.

The job of this doc: tell you (a) when to compose vs. extend vs. author atomic, (b) how a composite doc differs structurally from an atomic one, (c) what makes a composite earn its keep.

## Decision rule

When new work arrives:

1. **Decompose** it into atomic work units (responsibilities with one role / one contract each).
2. For each unit, pick the lightest fit:
   - (a) An existing task doc covers it → use as a primitive, no new file.
   - (b) Gap lives in an existing doc's **data** section (e.g., `spec_sync.md`'s `## Pairs`, `forge_pr_review.md`'s `## Registered forge bots`, `wiki_sync.md`'s `MD_FILES`) → extend the data, no new file.
   - (c) Genuinely new atomic responsibility → write a **new atomic task doc** ([`task_principle`](task_principle.md) template).
3. For the work *as a whole*:
   - If it's a single primitive's job → invoke it directly, no composite.
   - If it wires *multiple* primitives into a named capability → write a **composite task doc** with its own outer contract.
4. Connect every unit via `Contract.event` clauses — no direct task-to-task calls ([`agent_skill_principle`](agent_skill_principle.md) §3).

## Composition — how to write a composite task doc

A composite is a [`task_principle`](task_principle.md)-templated doc whose *Procedure* delegates to atomic primitives via events, and whose *Contract* declares an outer interface that no single primitive alone provides.

Shape (illustrative):

```
# docs_health_check (composite)

## Role
Aggregate the per-edge doc-health verdicts from spec_sync and wiki_e2e
into one repo-level "docs healthy" signal.

## Scope
- in-scope: subscribe to wiki_e2e_passed and spec_sync_no_drift; aggregate;
  emit a single repo-level verdict per source SHA.
- out-of-scope: re-running the underlying checks; editing their docs;
  defining new check predicates.

## Procedure
on `wiki_e2e_passed(sha)`        → record signal A(sha)
on `spec_sync_no_drift(sha)`     → record signal B(sha)
on A(sha) ∧ B(sha) within W      → emit `docs_healthy(sha)`
on window W elapsed without both → emit `docs_stale(sha, missing=[…])`

## Contract
- in:      events from primitives, source SHA
- out:     exactly one verdict event per source SHA
- event:   consume wiki_e2e_passed, spec_sync_no_drift; emit docs_healthy | docs_stale
- failure: primitive failure → propagate as docs_stale with cause; W elapsed → docs_stale
- success: every SHA produces exactly one verdict (idempotent across re-emits)
```

What changed vs. an atomic doc:

| Section | Atomic doc | Composite doc |
|---|---|---|
| Procedure | own algorithm | delegates to primitives via events |
| Contract.in | direct inputs | mostly events from primitives |
| Contract.failure | own branches | usually wraps primitive failure events |
| Observation | own metrics | usually aggregates primitives' metrics |

**Same template, different fill.** That is the point — composites and atomics are interchangeable inside larger pipelines.

## Inheritance — what primitives expose to composites

Four reuse mechanisms, lightest → heaviest:

| Mechanism | What's inherited | Example |
|---|---|---|
| **Pointer** | Link to parent principle at the doc's bottom | every task doc closes with `General principle: [task_principle](task_principle.md)` |
| **Template** | The 6-section structure (Role / Scope / Procedure / Contract / Observation) | every doc here |
| **Pattern** | Shape of an analog module | `wiki_e2e` borrowed `test_agent`'s E2E-verifier shape |
| **Data** | A row added to an existing doc's data section | a pair in `spec_sync.md` `## Pairs`; a bot in `forge_pr_review.md` `## Registered forge bots` |

A composite typically uses Pointer + Template + (often) Pattern; primitives expose Data slots to be appended to.

## When to write a composite (positive signals)

- The wiring repeats — you'd otherwise script the same orchestration each time.
- The composite's contract is **not reducible** to any single primitive's contract.
- The composite names a *capability*, not just a sequence — future workflows will want to compose against the composite as if it were a primitive.

## When NOT to write a composite (negative signals)

- One-shot orchestration — a script or a workflow run suffices.
- The composite would just rename one primitive → collapse it.
- The composite would have no contract of its own, only a topology diagram → delete (this is exactly why `UX_E2E_CI_plan.md` was removed).

## Worked examples (this repo)

- ✓ **`wiki_e2e`** — composite of `wiki_sync` completion (event) + `wiki_sync.md`'s `MD_FILES` (data inheritance) + curl/grep verification primitives. Its outer contract (per-predicate PASS/FAIL with redirect-tolerant HTTP) is not reducible to any single primitive.
- ✓ **`forge_pr_review`** — partial composite: consumes GitHub-native `pull_request` event + reads `spec_sync.md`'s `## Pairs` data; the predicate-evaluation logic is the new atomic responsibility it adds; `forge_pr_approved` is the composed output.
- ✗ **`UX_E2E_CI_plan.md` deleted** — tried to be a composite (`ux_agent | test_agent | ci_trigger`) but had only a topology diagram, no `in / out / event / failure / success` of its own. **A composite without a contract is the anti-pattern.** The wiring it described still exists; it now lives inline in Home's diagram.
- ✗ **`cp → sed` in `wiki_sync`** — in-place evolution of an atomic doc, not composition. No new doc needed.

## Anti-patterns

- **Contract-less composite** — topology diagram with no `in / out / event / failure / success`. Future workflows can't compose against it. (Deleted: `UX_E2E_CI_plan.md`.)
- **Wrapper composite** — composite that just renames one primitive. Collapse.
- **Scope-creeping composite** — composite that mutates a primitive's `in-scope` to fit. Restructure responsibilities first.
- **Atomic gluttony** — refusing to compose, re-authoring every new use case as a fresh atomic doc. Primitives never accumulate; the doc set bloats without gaining leverage.

General principle this composition rule extends: [`agent_skill_principle`](agent_skill_principle.md) — especially the *composition* pillar.
