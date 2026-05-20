# Ganglion factory design

> Cornerstone design narrative for the Ganglion redesign. This is **not** a task
> document — it does not follow the six-section template defined in
> [[task_principle]]. It is the anchor narrative that every task doc references
> and from which the module-level decomposition is derived.
>
> Companion documents:
> - [[redesign_plan]] — the migration plan / batch ordering that operationalises
>   this design.
> - [[task_principle]], [[workflow_principle]] — imported principles from
>   `docs/agent-forge/`.
> - The 21 sibling task docs under `docs/tasks/` (see §2 and §8 below).

## 1. Goal (from `docs/goal/goal.md`)

The original goal, verbatim from `docs/goal/goal.md`:

> # 목표
> 스펙 기반 toolcalling 최적화 모델 팩토리 설계.
> # 모듈화
>     1.언어 모델 생산파트
>     2. 1의 추론값의 통계적 분석을 통한 호보정 프로세스(error correction, compiler) 최적화 파트
>     3. 호환, 통신을 위한 공통계약(스키마, DSL)
> 의 모듈로 구성. 각 모듈은 독립적으로 동작가능하고 때론는 상호보완적으로 연결되어 데이터를 증강시키고 최적화하는데 기여함

Working translation (Korean terms preserved where they are load-bearing):

> **Goal.** Design a spec-based tool-calling optimisation **model factory**.
>
> **Modularisation.** The factory is composed of three modules:
> 1. The **language-model production** part.
> 2. A **statistical analysis** part that drives the **호보정 프로세스
>    (calibration/correction process, i.e. error correction, compiler)**
>    optimisation, derived from the inference outputs of (1).
> 3. The **common contract (schema, DSL)** that provides compatibility and
>    inter-module communication.
>
> Each module must be able to operate **independently**, and must also be able
> to connect **complementarily** so that, through their interaction, they
> **augment and optimise the data** each consumes.

Three load-bearing claims are baked into this goal:

- **G1 — Independent operation.** Any one of the three modules must remain
  useful on its own. A user who only wants to validate spec files should be
  able to depend on `contract/` without pulling in inference or analysis. A
  user who wants to serve inference against a hand-written catalog should not
  need the analyser. A user who wants to grade externally-produced traces
  should not need an inference client.
- **G2 — Complementary interaction.** When the modules **are** wired together,
  they must connect through a clean seam — not via hidden direct calls. The
  design uses an event namespace (§3, §7) as that seam.
- **G3 — Data augmentation through interaction.** The interaction itself must
  *produce* data: each turn of the feedback loop should leave a richer dataset
  behind than the previous turn. §3 walks the loop; §4.3 names the four
  successive datasets that grow across iterations.

The rest of this document explains the three-module decomposition (§2), shows
the loop that closes G3 (§3), argues each goal requirement is satisfied
(§4), names what the design replaces (§5), and lists what is **not** in scope
for this batch (§6). §7 prints the event namespace; §8 gives a reading order
for new contributors.

## 2. The three-module triad

`goal.md` enumerates the modules in narrative order (LM → analyser → contract).
The implementation order is the reverse of the narrative order, because the
dependency DAG is `contract ← lm` and `contract ← analyzer` with no edge
between `lm` and `analyzer` except via events. We therefore introduce the
modules in **dependency order** here: contract first, then `lm`, then
`analyzer`, then the non-peer benchmark consumers.

```
                   ┌─────────────────────┐
                   │  contract (Mod. 3)  │   ◄─── leaf of the DAG
                   │  schema + DSL       │
                   └────┬─────────────┬──┘
                        │             │
              renders   │             │   parses + validates
                        ▼             ▼
              ┌──────────────┐   ┌──────────────┐
              │ lm (Mod. 1)  │   │ analyzer (M2)│
              │ produce      │   │ measure +    │
              │ inference    │   │ propose      │
              └──────┬───────┘   └──────┬───────┘
                     │  traces (via events)  ▲
                     └──────────────────────►│
                                             │
                              rule.proposed  │
                                             ▼
                   ┌─────────────────────┐
                   │  contract (Mod. 3)  │   ◄─── feedback edge
                   │  Catalog v_{n+1}    │
                   └─────────────────────┘
```

### 2.1 Module 3 — `contract/`

> *Common schema/DSL surface. The other two modules speak through this.*

**Responsibility.** Own the canonical shape of a tool catalog and the
serialisation surfaces that other modules consume. Specifically:

- The `Catalog`, `ToolSpec`, `ArgSpec` (and variants `EnumArg`, `IntArg`,
  `StringArg`, `TimeArg`, `RawArg`) data structures.
- The DSL parser/validator (`Catalog.parse_json_dsl`) — the single place
  output strings are turned into `ActionPlan` values.
- The external-schema compiler (`compile_tool_calling_schema`) that converts
  OpenAI / MCP / bare-function / BFCL schemas into a `Catalog` at runtime.
- The built-in tier catalogs (`iot_light_5`, `home_iot_20`, `smart_home_50`).
- The two **rendering surfaces** that the LM module consumes:
  `render_json_dsl()` (compact text prepended to the system prompt) and
  `render_openai_tools()` (the full native `tools=[...]` array).

**In-scope.** Anything that defines what a valid tool-call looks like or how a
tool-call is communicated between LM and analyser. This includes the
**null-action contract** (`allow_empty_calls`) used by the BFCL irrelevance
category.

**Out-of-scope.** Inference, training, statistics, repair. The contract module
is a pure-Python library with no I/O beyond reading schema files.

**Why it is listed *third* in `goal.md` but built *first*.** It is a leaf in
the dependency DAG: every module imports from `contract/`, but `contract/`
imports from nothing else in the project. Building it first guarantees the
other two modules can be developed independently against a stable seam.

**Task docs.** [[contract_catalog]] (the `Catalog`/`ToolSpec` surface),
[[contract_schema_compiler]] (the external-schema → `Catalog` compiler),
[[contract_null_action]] (the `{"calls":[]}` abstention contract).

**Owned event.** `contract.catalog.published(catalog_id, version)` — emitted
when a new catalog version is registered, whether hand-authored, generated
from an external schema, or produced by [[analyzer_rule_synthesis]] applying a
rule patch.

### 2.2 Module 1 — `lm/`

> *Language-model production: synth, train, infer.*

**Responsibility.** Given a `Catalog` (and a prompt / dataset / adapter),
produce inference outputs in the DSL shape that `Catalog.parse_json_dsl` can
consume. Today this responsibility is split between `ganglion/runtime/qwen.py`
(inference clients) and `ganglion/factory/customer/{synth,train_lora}.py`
(dataset synthesis + LoRA fine-tuning); the redesign consolidates both under
`lm/`.

**In-scope:**
- Inference clients (the current `QwenJSONDSLClient`,
  `QwenFreeformJSONDSLClient`, `QwenNativeToolClient`, plus the
  `RuleBasedJSONDSLClient` stub) refactored against a uniform `LMClient`
  interface that takes `(prompt, catalog)` and emits an `lm.inference.*`
  event.
- Grammar-constrained decoding masks built from a `Catalog` ([[lm_grammar_mask]]).
- Dataset synthesis ([[lm_data_synth]]) — produces (prompt, expected DSL) pairs
  from a `Catalog`. Replaces the ad-hoc `factory/customer/synth.py` +
  `runs/factory_bfcl/teacher_augment.py` pair.
- Fine-tuning ([[lm_finetune]]) — LoRA / SFT / DPO pipelines that consume a
  synthesised dataset and produce an adapter. Replaces
  `runs/factory_bfcl/bfcl_sft.py`, `bfcl_sft_v2.py`, `bfcl_dpo.py`,
  `bfcl_bootstrap.py`, `train_lora.py`.
- Prompt construction (implicit [[lm_prompts]]) — the templating layer that
  splices `catalog.render_json_dsl()` into a system prompt.

**Out-of-scope.** Trace storage, failure classification, metric aggregation,
repair loops, reward shaping. These are owned by `analyzer/`. The `lm` module
emits raw `lm.inference.{completed,failed}` events and stops.

**Task docs.** [[lm_client]], [[lm_grammar_mask]], [[lm_finetune]],
[[lm_data_synth]], [[lm_prompts]] (implicit — prompt templating ships inside
the client task).

**Owned events.**
- `lm.inference.completed(case_id, catalog_id, raw, parsed, latency_ms,
   tokens_in, tokens_out)`
- `lm.inference.failed(case_id, catalog_id, error_kind, raw, attempt)`
- `lm.synth.completed(dataset_id, catalog_id, n_cases)`
- `lm.finetune.completed(adapter_id, dataset_id, base_model, metrics)`

### 2.3 Module 2 — `analyzer/`

> *Statistical analysis driving the 호보정 프로세스
> (calibration/correction process) — the `goal.md` §2 module.*

**Responsibility.** Ingest traces, classify failures, aggregate statistics,
propose `ToolSpec` patches, configure repair policy, supply reward signal.
This module is the **central act** of the redesign: today it does not exist
as a module. Its concerns are scattered across:

- `ganglion/eval/metrics.py` — `summarize()`: aggregate match rates, latency
  percentiles, parse-strategy counts.
- `ganglion/runtime/qwen.py:run_dsl_with_repair` — inline repair loop tied to
  one client class.
- `ganglion/factory/customer/verifier.py` — per-customer verifier with its own
  ad-hoc rubric.
- `runs/factory_bfcl/post_correction.py` — hand-coded R1–R11 post-correction
  rules.
- `runs/factory_bfcl/analyze_failures.py` — one-off failure-clustering script.
- `runs/factory_bfcl/apply_post_corr_holdout.py` and
  `apply_post_corr_to_phase3.py` — patch-application scripts.

Consolidating these into one `analyzer/` package, with a uniform event-driven
interface, is what makes the goal's "feedback loop" actually closable in code.

**In-scope:**
- A trace store ([[analyzer_trace_store]]) — the substrate. Every other
  analyzer task reads from it.
- Failure taxonomy ([[analyzer_failure_taxonomy]]) — names the failure classes
  (`schema_violation`, `arg_alias_miss`, `missing_required_arg`,
  `null_action_required`, `parse_strategy_fallback`, …) and assigns each
  trace exactly one.
- Metric aggregation ([[analyzer_metrics]]) — what today's
  `eval/metrics.py:summarize` does, but driven by `analyzer.trace.recorded`
  rather than an in-memory list owned by the runner.
- **Rule synthesis ([[analyzer_rule_synthesis]]) — this is the goal §2
  feedback edge.** Consumes failure histograms, proposes `ToolSpec` patches
  (e.g. new aliases, tightened enums, added `custom_validator` hooks), emits
  `analyzer.rule.proposed`.
- Repair policy ([[analyzer_repair_policy]]) — a policy interface that wraps
  what today is the inline `run_dsl_with_repair` loop, with replay support so
  that repair behaviour can be measured against historical traces without
  re-running inference.
- Verifier ([[analyzer_verifier]]) — the grading layer that today lives in
  `factory/customer/verifier.py` and `bfcl/grader.py`. After consolidation it
  becomes a consumer of trace + classifier output, not a separate evaluation
  loop.

**Out-of-scope.** Inference, training, dataset synthesis (those are `lm/`).
Catalog mutation: `analyzer/` *proposes* patches via events; it does not edit
`Catalog` objects directly.

**Task docs.** [[analyzer_trace_store]] (the substrate),
[[analyzer_failure_taxonomy]] (classification), [[analyzer_metrics]]
(aggregation), [[analyzer_rule_synthesis]] (the goal §2 feedback edge),
[[analyzer_repair_policy]] (replay-capable repair), [[analyzer_verifier]]
(grading).

**Owned events.**
- `analyzer.trace.recorded(trace_id, case_id, source, payload)`
- `analyzer.failure.classified(trace_id, taxonomy_label, confidence)`
- `analyzer.metrics.summarized(window_id, metrics)`
- `analyzer.rule.proposed(catalog_id, patch, evidence_trace_ids,
   estimated_lift)`
- `analyzer.repair.replayed(trace_id, repair_strategy, success, attempts)`

### 2.4 Consumers — `benchmarks/`

> *Benchmark adapters; not a peer module but a load-bearing consumer.*

**Why not a peer?** `benchmarks/` does not own data structures the other
modules consume, and it does not own a policy. It is a thin orchestration
layer that *wires* `contract` + `lm` + `analyzer` together against a specific
dataset family.

**Responsibility.** For each supported benchmark (IoT tiers, BFCL v4):
- Load cases (today: `ganglion/eval/dataset.py`, `ganglion/bfcl/loader.py`).
- For each case, build / fetch a `Catalog` (via `contract.compile_*` or via
  the tier registry).
- Invoke an `lm.LMClient` per case; rely on the client to emit
  `lm.inference.*` events.
- Optionally emit a per-benchmark roll-up event when the run finishes.

Crucially, `benchmarks/` **does not** introduce new module-level state. It
does not own a trace store (that belongs to `analyzer/`); it does not own a
metric table (also `analyzer/`); it does not own a `Catalog` shape (that
belongs to `contract/`). It owns only the loop control and the dataset
iterator.

**Task docs.** [[benchmark_iot]], [[benchmark_bfcl]].

**Owned events.**
- `benchmark.iot.completed(tier, n_cases, started_at, finished_at)`
- `benchmark.bfcl.completed(category, n_cases, started_at, finished_at)`

## 3. The feedback loop (goal §1 + §2 + §3 in motion)

The three goal requirements — independent operation, complementary
interaction, and data augmentation through interaction — are mechanised as a
single cyclic event flow. This is the diagram every task doc should refer to
when explaining where it sits.

```
        ┌─────────────────────────────────────────────────────────┐
        │                  contract (Module 3)                    │
        │     Catalog v_n  ────────────────────────────────────►  │
        └────────────────┬─────────────────────────────┬──────────┘
                         │ render_json_dsl /           │ ToolSpec patches
                         │ render_openai_tools         │ (rule.proposed
                         ▼                             │  → catalog.published)
   ┌─────────────────────────────────┐                 │
   │            lm (Module 1)         │                │
   │  synth → finetune → infer       │                 │
   │  emits lm.inference.{completed, │                 │
   │  failed} per case               │  traces         │
   └────────────────┬────────────────┘                 │
                    │                                  │
                    ▼                                  │
        ┌─────────────────────────────┐                │
        │      benchmarks (consumer)   │                │
        │  emits benchmark.*.completed│                │
        └────────────────┬────────────┘                │
                         │                             │
                         ▼                             │
        ┌─────────────────────────────────────────────┴───────┐
        │                  analyzer (Module 2)                 │
        │  trace_store ← lm.inference.* + benchmark.*          │
        │  taxonomy → analyzer.failure.classified              │
        │  metrics → analyzer.metrics.summarized               │
        │  rule_synthesis → analyzer.rule.proposed ───────────►│
        │  repair / verifier (consumers of trace + classifier) │
        └──────────────────────────────────────────────────────┘
```

The cycle, step by step:

1. **Catalog v_n is published.** Either hand-authored (one of the tier
   catalogs, e.g. `iot_light_5`), compiled from an external schema (BFCL case
   `function` block), or — interestingly — produced by step 5 of the previous
   cycle. `contract.catalog.published(catalog_id, version=n)` fires.
2. **`lm.synth` produces a dataset against v_n.** [[lm_data_synth]] consumes
   the catalog and emits a (prompt, expected DSL) corpus, then fires
   `lm.synth.completed(dataset_id, catalog_id, n_cases)`.
3. **`lm.finetune` trains an adapter.** [[lm_finetune]] consumes the dataset
   and produces an adapter, firing `lm.finetune.completed(adapter_id, …)`.
   (Step 2 and 3 are skipped when running against a vanilla / API model — the
   loop is still well-defined.)
4. **`lm.client` invokes the model per case.** [[lm_client]] runs each case,
   passes the catalog through `render_json_dsl` or `render_openai_tools`,
   parses the response via `Catalog.parse_json_dsl`, and fires
   `lm.inference.completed` (or `failed`).
5. **Benchmarks orchestrate steps 1–4.** [[benchmark_iot]] /
   [[benchmark_bfcl]] iterate cases and emit `benchmark.*.completed` when
   done.
6. **Trace store ingests.** [[analyzer_trace_store]] subscribes to
   `lm.inference.*` and persists each event. Crucially, the trace store does
   not require a benchmark run — any `lm.inference.*` emitter (e.g. a
   production serving deployment) feeds it.
7. **Taxonomy classifies.** [[analyzer_failure_taxonomy]] subscribes to
   `analyzer.trace.recorded` and emits `analyzer.failure.classified` for each
   failed trace.
8. **Metrics summarise.** [[analyzer_metrics]] subscribes to both
   `analyzer.trace.recorded` and `analyzer.failure.classified`, periodically
   emits `analyzer.metrics.summarized(window_id, metrics)`. This is the
   replacement for today's batch `eval/metrics.py:summarize`.
9. **Rule synthesis proposes patches** — *the feedback edge.*
   [[analyzer_rule_synthesis]] consumes the failure histogram (the joint of
   trace and classification streams) and proposes `ToolSpec` patches. It
   fires `analyzer.rule.proposed(catalog_id, patch, evidence_trace_ids,
   estimated_lift)`.
10. **Patches gate into contract.** Either a human reviews the proposed patch
    or, if [[contract_catalog]] is configured with `auto_apply=True`, the
    patch is applied directly. Either way, the resulting new catalog fires
    `contract.catalog.published(catalog_id, version=n+1)` — and the cycle
    re-enters step 1.

This loop is what `goal.md` means by "modules augmenting data through
interaction". Each turn produces:

- A new catalog version (`contract.catalog.published`).
- A new synthesised dataset against that catalog (`lm.synth.completed`).
- A new adapter trained on that dataset (`lm.finetune.completed`).
- A new trace corpus (`analyzer.trace.recorded` × N).
- A new metric summary (`analyzer.metrics.summarized`).

Two composite task docs orchestrate the loop:

- [[factory_pipeline]] — the closed-loop composite that runs steps 1–10.
- [[factory_evaluation]] — the open-loop composite that runs steps 1–8 only
  (measurement without acting on `analyzer.rule.proposed`).

Both composites consume primitive events declared by the modules; neither
invokes any task doc by name. This matches the
"connect via events, not direct calls" rule in [[task_principle]] §Contract.

## 4. Why the goal is satisfied

Three sub-sections, one per `goal.md` requirement.

### 4.1 Independent operation (G1)

Each module is useful on its own. Concretely:

- **`contract/` alone.** A `Catalog` can be constructed in-memory, rendered to
  DSL via `render_json_dsl`, rendered to native via `render_openai_tools`, and
  used to parse arbitrary strings via `parse_json_dsl`. This is exactly what
  the existing `tests/test_tool_schema_compiler.py` and
  `tests/test_compiler_bfcl_features.py` exercise. Use case: an external team
  validating their own tool-schema files against the Ganglion DSL contract
  without ever calling an LLM. No `lm.*` or `analyzer.*` event subscribers
  are required.
- **`lm/` alone.** Given a `Catalog` and a prompt, an `lm.LMClient`
  implementation emits `lm.inference.completed` events. The events can be
  consumed by anyone — a logger, a print sink, the analyzer trace store, or
  nothing at all (the events are still useful as side-effect-free returns).
  Use case: serving a fine-tuned adapter against production traffic where no
  evaluation is performed.
- **`analyzer/` alone.** [[analyzer_trace_store]] accepts traces from any
  source that adheres to the trace schema, not just from `lm/`. A user can
  pipe externally-produced traces (e.g. exported from a hosted LLM with tool
  use enabled) into the trace store and run [[analyzer_failure_taxonomy]] +
  [[analyzer_metrics]] over them. Use case: forensic analysis of an existing
  production deployment's tool-call quality, with no Ganglion-side inference
  ever taking place.

In each of the three cases above, the other two modules are absent **as
Python imports** — the `contract/`, `lm/`, and `analyzer/` packages do not
import each other. They share the event namespace (§7), which is itself owned
by `contract/` (since event payloads include catalog references).

### 4.2 Complementary interaction (G2)

When the modules are wired together, they communicate through the event bus
declared in `contract/` and consumed by the composites in §3. There are no
module-to-module direct calls — i.e., `lm/` never imports `analyzer/`, and
`analyzer/` never imports `lm/`. Both depend on `contract/`, which is
acyclic.

The composite [[factory_pipeline]] is the *only* place where module wiring is
performed. It subscribes `analyzer/` consumers to `lm/` and `benchmarks/`
events, subscribes `contract/`'s catalog publisher to
`analyzer.rule.proposed`, and starts the loop. Tests for [[factory_pipeline]]
exercise the integration; per-module tests exercise each module in isolation.

This satisfies `goal.md`'s **상호보완적으로 연결** (complementary connection):
the modules complement each other when wired, but the wiring is itself a
discrete, optional, testable composite — not a hidden side effect of import.

### 4.3 Data augmentation through interaction (G3)

The feedback loop in §3 *produces* data; it does not just consume it. Each
turn of the loop augments four distinct data surfaces:

1. **The catalog** — version `n+1` carries new aliases / tightened enums /
   added validators relative to version `n`. Driven by
   [[analyzer_rule_synthesis]] → [[contract_catalog]].
2. **The synthesis corpus** — [[lm_data_synth]] against catalog `n+1`
   produces examples covering the new aliases and validators. The corpus from
   turn `n+1` strictly subsumes turn `n`'s coverage of the catalog surface.
3. **The trace corpus** — [[analyzer_trace_store]] accumulates traces across
   turns, so turn `n+1`'s failure histogram is informed by both turn `n` and
   turn `n+1` data. Failure modes that have already been patched out drop to
   zero; new failure modes (long tail) surface above noise.
4. **The repair / verifier policies** — [[analyzer_repair_policy]] and
   [[analyzer_verifier]] are themselves policies that can be re-tuned across
   turns based on what the trace store says about repair effectiveness. This
   is the second-order feedback edge: the *correction process itself* is the
   subject of the calibration / 호보정.

Each of the four surfaces is strictly *more* data than it was the previous
turn — never less. This is what `goal.md` means by **데이터를 증강시키고
최적화**: not just "produce more data" but "produce more *useful* data, where
usefulness is measured by the next turn's metric summary".

## 5. How this differs from today

A bullet-list "before → after" diff. Each row references the task doc that
mechanises the change.

- **Today:** Module 2 is scattered across `eval/metrics.py`,
  `runtime/qwen.py:run_dsl_with_repair`, `factory/customer/verifier.py`, and
  the ad-hoc scripts under `runs/factory_bfcl/` (`analyze_failures.py`,
  `post_correction.py`, `apply_post_corr_holdout.py`,
  `apply_post_corr_to_phase3.py`).
  **After:** One `analyzer/` package with the five task docs in §2.3.
  Consolidation is the central act of the redesign. See
  [[analyzer_trace_store]], [[analyzer_failure_taxonomy]],
  [[analyzer_metrics]], [[analyzer_rule_synthesis]],
  [[analyzer_repair_policy]], [[analyzer_verifier]].

- **Today:** Two parallel evaluation surfaces — `ganglion/eval/runner.py`
  (the IoT-tier loop) and `ganglion/factory/customer/eval.py` (the per-customer
  factory loop). They duplicate concerns: case loading, client invocation,
  metric collection.
  **After:** One event-wired loop in [[factory_pipeline]] / [[factory_evaluation]]
  where [[benchmark_iot]] and [[benchmark_bfcl]] emit traces and
  [[analyzer_metrics]] summarises. No duplicated loop code.

- **Today:** Hand-coded post-correction rules R1–R11 live in
  `runs/factory_bfcl/post_correction.py`, applied as one-shot scripts via
  `apply_post_corr_holdout.py` / `apply_post_corr_to_phase3.py`. Adding a
  rule means hand-editing the script and re-running it.
  **After:** [[analyzer_rule_synthesis]] proposes patches from failure
  histograms, emits `analyzer.rule.proposed`, and a gate (manual or
  `auto_apply=True`) lands them into the catalog. New rules are *discovered*
  by the analyser, not hand-coded.

- **Today:** Repair is a fixed function `run_dsl_with_repair` in
  `runtime/qwen.py`, tied to one client class and not measurable in
  isolation.
  **After:** [[analyzer_repair_policy]] is a policy interface with replay
  support. Different repair strategies (single-shot, multi-shot with
  back-off, learned policies) can be benchmarked against the same trace
  corpus without re-running inference.

- **Today:** The word "factory" is overloaded — `ganglion/factory/customer/`
  treats each *customer* as a factory pipeline (synth → train → eval), with
  the implicit assumption that one customer = one pipeline = one model.
  **After:** The factory is the composite [[factory_pipeline]], a *system*
  composed of three peer modules. Per-customer specialisation becomes a
  parameter of the catalog (`contract.catalog.published(catalog_id=customer,
  version=n)`), not a fork of the pipeline code.

- **Today:** The grammar mask, prompt templating, and decoding parameters all
  live alongside the inference client. There is no clean seam between
  "produce a prompt" and "produce a constrained-decoded output".
  **After:** [[lm_grammar_mask]] is its own task that consumes a `Catalog`
  and produces a mask; [[lm_prompts]] is its own task that consumes a
  `Catalog` and a user message and produces a prompt; [[lm_client]] is the
  thin glue.

- **Today:** Benchmark adapters (`ganglion/bfcl/`, `ganglion/eval/dataset.py`)
  carry both case-loading **and** result-summarisation concerns.
  **After:** Benchmark adapters carry case-loading only. Summarisation moves
  to [[analyzer_metrics]]. See [[benchmark_iot]] and [[benchmark_bfcl]].

## 6. Out-of-scope for this batch

This document is the *design narrative*. It declares no code. The following
are explicitly **out of scope** for the current batch (which lands the
design + task docs only):

- **Module 1 code migration** — `ganglion/runtime/` → `ganglion/lm/`. The
  refactor itself is a separate batch driven by [[lm_client]] and friends.
- **Module 2 code migration** — consolidating `ganglion/eval/metrics.py` +
  `ganglion/runtime/qwen.py:run_dsl_with_repair` +
  `ganglion/factory/customer/verifier.py` + the `runs/factory_bfcl/*.py`
  scripts into `ganglion/analyzer/`. Separate batch driven by
  [[analyzer_trace_store]] and friends.
- **Benchmark code migration** — `ganglion/bfcl/` + the IoT loop in
  `ganglion/eval/runner.py` → `ganglion/benchmarks/`. Separate batch driven by
  [[benchmark_iot]] / [[benchmark_bfcl]].
- **Event-bus implementation** — there is currently no in-process event bus.
  One needs to be authored; see [[redesign_plan]]. The shape of the events
  is fixed here in §7, but the dispatcher is deferred.
- **Deletion of compatibility shims** — old import paths
  (`ganglion.runtime.qwen.QwenJSONDSLClient`, `ganglion.eval.metrics.summarize`,
  etc.) will remain as thin re-export shims until all internal callers
  migrate. Removal happens after all benchmark + test code has been moved.
- **CI workflows for the new task docs** — per [[task_principle]] "spec
  first, impl after": every `.github/workflows/*` is written after the
  declaring task doc lands. No workflow is authored in this batch.
- **Renaming of `ganglion/`** — the package name stays. The redesign is
  internal restructuring.

## 7. Glossary of events

The shared event namespace. Each event is named `<module>.<noun>.<verb_past>`
and carries a frozen-dataclass payload. The full payload schemas live in
[[contract_catalog]] (event types are part of the contract surface); this
section lists names and roles only. The grouping below should also appear in
[[redesign_plan]] §migration-map.

**`contract.*`** — owned by Module 3.

- `contract.catalog.published(catalog_id, version)` — new catalog version is
  available. Fired by `Catalog` construction, by the external-schema compiler,
  or by a rule patch being applied.

**`lm.*`** — owned by Module 1.

- `lm.inference.completed(case_id, catalog_id, raw, parsed, latency_ms,
   tokens_in, tokens_out)` — successful inference, including the parsed
   `ActionPlan`.
- `lm.inference.failed(case_id, catalog_id, error_kind, raw, attempt)` —
  inference attempt produced output the validator rejected. `error_kind`
  takes a value from [[analyzer_failure_taxonomy]] so that downstream
  classification can short-circuit on obvious cases.
- `lm.synth.completed(dataset_id, catalog_id, n_cases)` — a synthesis run
  finished. Useful for downstream training kickoff.
- `lm.finetune.completed(adapter_id, dataset_id, base_model, metrics)` — a
  fine-tune run finished. `metrics` is a small dict (loss, eval split match
  rate, etc.) that [[analyzer_metrics]] can promote to a window summary.

**`analyzer.*`** — owned by Module 2.

- `analyzer.trace.recorded(trace_id, case_id, source, payload)` — a trace
  has landed in the store. `source` is typically `"lm.inference.completed"`
  or `"lm.inference.failed"`, but external trace ingest is supported.
- `analyzer.failure.classified(trace_id, taxonomy_label, confidence)` — a
  trace has been assigned a taxonomy label.
- `analyzer.metrics.summarized(window_id, metrics)` — a metric window (e.g.
  one benchmark run, or one hour of production traffic) has been summarised.
- `analyzer.rule.proposed(catalog_id, patch, evidence_trace_ids,
   estimated_lift)` — a `ToolSpec` patch has been proposed against
   `catalog_id`. `evidence_trace_ids` lets a human reviewer audit the
   traces that motivated the patch.
- `analyzer.repair.replayed(trace_id, repair_strategy, success, attempts)` —
  a stored trace was replayed through a candidate repair strategy. Used by
  [[analyzer_repair_policy]] to compare strategies offline.

**`benchmark.*`** — owned by the `benchmarks/` consumer module.

- `benchmark.iot.completed(tier, n_cases, started_at, finished_at)` — an IoT
  tier run finished. Tier is one of `iot_light_5`, `home_iot_20`,
  `smart_home_50`.
- `benchmark.bfcl.completed(category, n_cases, started_at, finished_at)` —
  a BFCL category run finished. Category is one of `simple_python`,
  `multiple`, `parallel`, `parallel_multiple`, `irrelevance`.

Two non-events that are conspicuously absent:

- *There is no `lm.client.request_started`.* Inference is opaque to the bus
  until it completes (or fails). This keeps the event volume bounded by the
  number of cases, not the number of internal retries.
- *There is no `analyzer.metric.changed`.* Metrics are summarised on a
  window boundary; per-trace metric updates would be both expensive and
  meaningless given the noisy single-trace surface.

## 8. Reading order

For a new contributor approaching the redesign cold, in order:

1. `docs/goal/goal.md` — the four-line spec the redesign exists to satisfy.
2. **This document** (`docs/factory_design.md`) — the three-module
   decomposition and the feedback loop.
3. [[redesign_plan]] — the migration plan / batch ordering / event-bus
   substrate that makes this design land safely on the existing codebase.
4. [[contract_catalog]] — the leaf module's central data structure. Start
   here because every other module imports it.
5. [[lm_client]] — the inference seam. Once you understand `Catalog` and
   `LMClient`, the inference half of the loop is mechanical.
6. [[analyzer_trace_store]] — the analyser's substrate. Every other analyser
   task is a consumer of this.
7. [[factory_pipeline]] — the composite that wires the three modules.
   Reading this last is intentional: by the time you reach it, every event
   it subscribes to is already familiar from the previous docs.

Optional follow-on reading (each can be read independently after the above):

- Contract surface: [[contract_schema_compiler]], [[contract_null_action]].
- LM internals: [[lm_grammar_mask]], [[lm_data_synth]], [[lm_finetune]],
  [[lm_prompts]].
- Analyser internals: [[analyzer_failure_taxonomy]], [[analyzer_metrics]],
  [[analyzer_rule_synthesis]], [[analyzer_repair_policy]],
  [[analyzer_verifier]].
- Benchmarks: [[benchmark_iot]], [[benchmark_bfcl]].
- Measurement-only operation: [[factory_evaluation]].

That ordering also doubles as a dependency-walk: every doc references only
docs read earlier in the list, except the composites at the end which
reference everything.
