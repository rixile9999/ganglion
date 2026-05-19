# Self-maintenance task docs

Specs for Ganglion's self-maintenance loop. Each doc follows the [task delegation principles](../agent-forge/task_principle.md) (six-section template: `Role / Scope / Procedure / Contract / Observation`) and is the SSOT for its corresponding (eventual) `.github/workflows/*` impl.

**Docs are spec. Impls follow the docs, never the other way around.** Authoring a workflow without its declaring doc is the anti-pattern.

## Atomic primitives

| Doc | Kind | Purpose |
|---|---|---|
| [dataset_integrity](./dataset_integrity.md) | deterministic CI | Every dataset row parses against its tier's catalog and is uniquely identified. |
| [catalog_spec_sync](./catalog_spec_sync.md) | LLM agent | Detect `ToolSpec` ↔ rule client / dataset templates / report drift; open classified PR. |
| [eval_smoke_guard](./eval_smoke_guard.md) | deterministic CI | Block PRs whose offline (`rules + iot_light_5`) eval regresses below the pinned baseline. |
| [report_freshness](./report_freshness.md) | deterministic CI | Stamped numeric claims in `docs/*_report.md` match underlying `runs/**/*.json`. |

## Composite

| Doc | Aggregates | Outer signal |
|---|---|---|
| [release_health](./release_health.md) | `dataset_integrity` + `eval_smoke_guard` + `report_freshness` | `release_ready(sha) \| release_blocked(sha, cause) \| release_stale(sha, missing)` |

## Implementation status

- ☐ All five docs above — spec only, no `.github/workflows/*` impl yet. Impls are deliberately deferred to a follow-up PR per `task_principle` (spec first, impl after).
- ☐ `runs/baselines/iot_light_5_rules.json` — placeholder pending a chosen reference run from `runs/m{2,3,4}/`. Until pinned, `eval_smoke_guard` runs observe-only and emits `eval_smoke_bootstrap_required`.
- ☐ Marker convention `<!-- src:...#pointer -->` — not yet retrofitted into existing reports. `report_freshness` will emit `report_freshness_bootstrap_required` until a first report is stamped.

## Why these and not others

Each primitive targets a *drift surface* identified by reading the project against the seed principles in [`docs/agent-forge/`](../agent-forge/):

- `dataset_integrity` — drift between checked-in `expected` rows and current catalogs.
- `catalog_spec_sync` — drift between `ToolSpec` and its three dependents (rule client, dataset templates, report tool counts).
- `eval_smoke_guard` — silent regressions in offline accuracy that current tests do not gate.
- `report_freshness` — numbers in reports drifting from the `runs/` data that backs them.
- `release_health` — composite verdict; without it no single signal answers "is this SHA shippable?".

Explicit non-goals (per [task_principle §3](../agent-forge/task_principle.md), every doc has a non-empty `out-of-scope`):

- No KO mirror (`*.ko.md`) of these task docs — `ko_sync` from agent-forge was not imported.
- No catalog editing by any agent — `ToolSpec` is a human authoring surface.
- No automatic report rewriting — prose and number authorship stay with the author; only stamps are verified.
