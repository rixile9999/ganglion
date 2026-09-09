[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[benchmark_bfcl]] · [[benchmark_iot]] · [[contract_tier_home_assistant]]

# benchmark_selection

Decision record for **which external tool-calling benchmarks Ganglion runs
against the local-model path** (vLLM-served open weights on a single H100
80GB replacing the DashScope API). Written 2026-09-09 after a survey of the
post-BFCL-v4 benchmark landscape. The decision is a table (`adopt / defer /
reject` per candidate) plus the adoption order; the adapters themselves are
separate task docs.

Motivating finding: BFCL v4 is still the current version (no v5 as of
2026-09), but its 2026-04 reweighting puts the single-turn AST categories this
repo runs (`simple_python`, `multiple`, `parallel`, `parallel_multiple`,
`irrelevance`) into the **Non-Live 10 %** bucket, and the 2026-07 validity
audit (arXiv 2607.02577) measured an 18.5 % evaluator-vs-human disagreement
across BFCL v4 / τ²-Bench / LiveMCPBench / MCP-Atlas, with LiveMCPBench
spreading 57.9 %–76.8 % across 23 reruns of one setup. A single BFCL number is
no longer a sufficient external claim.

## Role

Decide, and keep deciding, the external benchmark set that the local-model
evaluation runs — one status per candidate, one adoption order — so that
every adapter task has a documented reason to exist.

## Scope

- **in-scope**:
  - The candidate matrix below and the four selection criteria:
    (C1) deterministic, reproducible grading;
    (C2) runnable offline against an OpenAI-compatible endpoint (vLLM);
    (C3) exercises the catalog-size / tool-discovery axis the Action-IR
    hypothesis is about;
    (C4) domain proximity to the IoT-light tiers.
  - Status per candidate ∈ `{adopt, defer, reject}` with a one-line reason.
  - Adoption order and the trigger for re-evaluating a `defer`.
  - Pointers to the adapter task doc each `adopt` row requires.
- **out-of-scope**:
  - Implementing any adapter. `benchmark_bfcl` already exists; MCPMark and
    home-assistant-datasets adapters are future task docs
    (`benchmark_mcpmark`, `benchmark_home_assistant`) — not authored here.
  - Local model selection (which open weights to serve). That is a separate
    decision recorded outside this doc.
  - Hardware purchase for a physical Home Assistant test bench. Only the
    software benchmark is in scope; the device decision is the user's.
  - BFCL multi-turn / Live / Agentic categories. Still out of scope for the
    single-turn IR hypothesis; not re-opened by this doc.
  - Changing the BFCL grader or sample (`examples/bfcl/v4/sample/` is SSOT).
- **on violation**: if a candidate turns out to need a change to the
  `Catalog` contract or the grader to be runnable, stop and open the change
  under [[contract_catalog]] / [[benchmark_bfcl]] — do not fold contract
  edits into an adapter.

## Procedure

```
on lm.client.migrated(provider="local") or on annual review (next: 2027-03):
    for candidate in CANDIDATES:
        score C1..C4 (yes/no) from the candidate's own harness docs
        status ← adopt   if C1 ∧ C2 ∧ (C3 ∨ C4)
                 defer   if C2 ∧ ¬C1            # runnable but LLM-judged
                 reject  otherwise
    write the table below; bump `Decision date`
    for row in adopt: ensure a task doc named in `Adapter` exists or is listed
        under "Pending adapters"
on candidate harness unreachable / license change:
    keep previous status, add a `note`, do not silently drop the row.
```

## Contract

- **in**: candidate list (below), criteria C1–C4, the survey sources listed
  at the end of this doc.
- **out**: this file, with the `Decision` table filled and
  `Decision date: 2026-09-09`. Each `adopt` row names an adapter task doc.
- **event**:
  - consume `lm.client.migrated(provider)` — the trigger that made this
    decision necessary.
  - emit `benchmark.selection.decided(date, adopted=[...])` — consumed by
    [[factory_evaluation]] to know which `benchmark_id`s are legitimate.
- **failure**:
  - A candidate's public harness disappears → status stays, `note` records
    the date; adapter task doc is marked blocked.
  - An `adopt` row has no adapter doc after the next review → downgrade to
    `defer` with reason `no adapter`.
- **success**: `grep -c "^| .* | adopt |" docs/tasks/benchmark_selection.md`
  ≥ 1, and every adapter doc named on an `adopt` row either exists under
  `docs/tasks/` or is listed under "Pending adapters" with an owner.

## Decision

Decision date: **2026-09-09**

| Candidate | Grading | C1 det. | C2 offline | C3 catalog axis | C4 domain | Status | Reason | Adapter |
|---|---|---|---|---|---|---|---|---|
| BFCL v4 single-turn (Non-Live) | AST match | yes | yes | partial (per-case catalog, ≤ ~20 tools) | no | **adopt** (retain) | Cheap, deterministic, continuity with M1'–M5' reports. Saturated bucket — report as *controlled* baseline, not headline. | [[benchmark_bfcl]] (exists) |
| MCPMark (eval-sys, 127 tasks, 5 MCP services) | per-task `verify.py` | yes | yes (LiteLLM → vLLM base_url; Docker) | yes (real MCP tool lists) | no | **adopt** | Only MCP-era benchmark with programmatic verification; open-weight results published (kimi-k2.7 81.1 %). | `benchmark_mcpmark` (pending) |
| home-assistant-datasets (allenporter; `assist`, `assist-mini`, `intents`) | expected entity-state diff on synthetic homes | yes | yes (Ollama / OpenAI-compatible model config) | partial (real HA Assist tool set) | **yes** | **adopt** | Uses the real HA Assist API tools; ground truth is device state. Bridges directly to [[contract_tier_home_assistant]]. | `benchmark_home_assistant` (pending) |
| MCP-Atlas (Scale, 1 000 tasks, 36 servers, 220 tools, 500 public) | claim-level rubric, LLM judge | no | partial (containerised harness; needs live API keys per server) | **yes** (strongest tool-discovery pressure) | no | **defer** | Best evidence for the input-token-cost claim, but LLM-judged and API-key heavy. Revisit once MCPMark numbers exist. | — |
| MCP-Universe (Salesforce) | mixed | partial | yes | yes | no | **defer** | Framework, not a fixed set; useful later for RL loops. Runs MCPMark internally, so MCPMark covers its fixed subset. | — |
| τ²-Bench v1.0.1 | policy + state, user simulator | partial | yes | no | no | **defer** | Multi-turn conversational agent evaluation; orthogonal to the one-shot IR hypothesis. | — |
| LiveMCPBench | LLM judge | no | yes | yes | no | **reject** | 18.9-point rerun spread in the validity audit; rankings not stable. | — |
| Tool-Veritas / Harness Lab (from arXiv 2607.02577) | deterministic + optional qualitative | yes | unknown | unknown | no | **defer** | Announced with the audit; harness maturity unverified as of 2026-09. Re-check at next review. | — |

Adoption order: (1) keep BFCL v4 single-turn running on the local path for
continuity; (2) MCPMark; (3) home-assistant-datasets `assist-mini` then
`assist`; (4) reconsider MCP-Atlas.

Pending adapters (owner: repo maintainer):

- `benchmark_mcpmark` — LiteLLM model entry pointing at the vLLM endpoint;
  per-task `verify.py` results mapped into [[analyzer_trace_store]].
- `benchmark_home_assistant` — synthetic-home fixtures + Assist tool set;
  expected-state diff mapped to `GradeResult`. Depends on
  [[contract_tier_home_assistant]] for the catalog shape.

## Observation

- `adopted_benchmark_count` = rows with `Status = adopt`.
- `adapter_coverage` = adopted rows whose adapter doc exists ÷
  `adopted_benchmark_count`. Target 1.0 by the next review.
- `headline_agreement` = for each adopted benchmark, |rank(DSL) − rank(native)|
  across the local models evaluated. Disagreement between benchmarks is the
  signal that one of them is measuring something other than the IR effect.

## Sources

- BFCL v4 leaderboard and changelog — <https://gorilla.cs.berkeley.edu/leaderboard.html>
- Validity audit: *Benchmarking the Benchmarks* — <https://arxiv.org/abs/2607.02577>
- MCPMark — <https://github.com/eval-sys/mcpmark>
- MCP-Atlas — <https://arxiv.org/abs/2602.00933>, <https://labs.scale.com/leaderboard/mcp_atlas>
- MCP-Universe — <https://github.com/SalesforceAIResearch/MCP-Universe>
- τ²-Bench — <https://github.com/sierra-research/tau2-bench>
- home-assistant-datasets — <https://github.com/allenporter/home-assistant-datasets>
- Home Assistant LLM API — <https://developers.home-assistant.io/docs/core/llm/>
- Home Assistant MCP Server integration — <https://www.home-assistant.io/integrations/mcp_server/>

Wikilinks: [[benchmark_bfcl]], [[benchmark_iot]],
[[contract_tier_home_assistant]], [[factory_evaluation]],
[[analyzer_trace_store]], [[contract_catalog]].
