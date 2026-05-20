[← New tasks](./README.md) · Composite principle: [workflow_principle](../agent-forge/workflow_principle.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# factory_pipeline (composite)

**This is a composite task doc.** It names the redesigned Ganglion *factory* — the feedback loop that wires `lm/`, `analyzer/`, and `contract/` (per [`goal.md`](../goal/goal.md)) into one event-driven optimisation cycle on a `Catalog`. Per [`workflow_principle`](../agent-forge/workflow_principle.md), a composite earns its keep when its outer contract is **not reducible** to any single primitive's contract — and "iterate a catalog until it converges" is exactly that: no primitive owns the loop.

Today this loop is a *manual* operator routine (run synth, eyeball failures, hand-edit post-correction R1-R11, retrain, re-bench). The legacy task docs [[factory_bfcl]] and [[factory_bfcl_phase3]] are atomic, one-shot research replays — they document *one trip around the loop*. The operator scripts that back them — `runs/factory_bfcl/post_correction.py`, `apply_post_corr_*.py`, `bfcl_sft*.py`, `bfcl_dpo.py`, `bfcl_bootstrap.py`, `teacher_augment.py` — are chained by hand. This composite is what they should have been: a named, contracted, observable capability that future workflows can compose against as if it were a primitive ([`workflow_principle` §Composition](../agent-forge/workflow_principle.md)).

Why a *composite* and not a single atomic doc:
- The outer interface ("iterate until convergence on a Catalog") is **not reducible** to any single primitive — no `lm_*` doc owns benchmarking; no `analyzer_*` doc owns retraining; no `contract_*` doc owns convergence detection.
- The wiring repeats — every catalog will want the same `synth → finetune → benchmark → trace → metrics → rule → patch` loop. Without a named composite, every caller re-implements the orchestration inline (the [`workflow_principle`](../agent-forge/workflow_principle.md) *atomic gluttony* failure mode).
- It is the cornerstone doc the [`goal.md`](../goal/goal.md) three-module redesign needs: it is *the* place where `lm/`, `analyzer/`, and `contract/` meet, and it must meet them via events, not direct calls.

## Role

Drive a `Catalog` through repeated `synth → finetune → benchmark → trace_store → taxonomy → metrics → rule_synthesis → catalog patch → next-iteration synth` cycles, emitting exactly one `factory.pipeline.iterated` per cycle and exactly one `factory.pipeline.aborted` on stop conditions — by subscribing to primitive events only.

## Scope

- **in-scope**:
  - Subscribing to `lm.synth.completed` and triggering `lm.finetune.request` for the catalog under iteration.
  - Subscribing to `lm.finetune.completed` and triggering `benchmark.iot.request` + `benchmark.bfcl.request` against the new adapter.
  - Subscribing to `benchmark.{iot,bfcl}.completed` and waiting on `analyzer.trace.recorded` events (counted) for trace-store ingestion.
  - Subscribing to `analyzer.failure.classified` and accumulating a per-iteration failure histogram.
  - Subscribing to `analyzer.metrics.summarized` and recording per-iteration `exact_match_rate` / `action_match_rate` / `ast_match_rate`.
  - Subscribing to `analyzer.rule.proposed` and accumulating proposed patches for the iteration.
  - Applying patches **only when** `auto_apply=True` (default `False` — humans gate by default) → emit `contract.catalog.published(new_catalog_id, version+1)` to begin the next iteration.
  - Iteration control: `max_iter` cap, plateau detection (no `exact_match_rate` improvement for `K` consecutive iterations), early termination on `exact_match_rate ≥ threshold`.
  - Idempotency: skip an iteration whose `catalog_id`/`version` pair matches a prior cycle.
  - Aggregate emit: `factory.pipeline.iterated(catalog_id, iteration, eval_summary)` per cycle; `factory.pipeline.aborted(catalog_id, reason)` on stop conditions.
  - Target implementation path: `ganglion/factory.py`.
- **out-of-scope**:
  - Re-running primitives. This composite *delegates fully* — it does not duplicate synth, finetune, benchmark, classify, metrics, or rule-synthesis logic. Those live in [[lm_data_synth]], [[lm_finetune]], [[benchmark_iot]], [[benchmark_bfcl]], [[analyzer_failure_taxonomy]], [[analyzer_metrics]], [[analyzer_rule_synthesis]].
  - Applying patches autonomously without an opt-in flag — patch application requires `auto_apply=True` OR a human approval signal observed on the event bus. Default behaviour holds patches and surfaces `pending_patches` in the iteration's eval summary.
  - Mutating any primitive's contract — composite reads events; it does not change what primitives do. Composite would-be-changes to primitive contracts must restructure responsibilities first (per [`workflow_principle` §Anti-patterns](../agent-forge/workflow_principle.md)).
  - Cross-catalog joint optimisation — one `catalog_id` per pipeline instance. Multi-catalog meta-optimisation, if ever needed, is a separate higher-order composite.
  - Concurrent pipelines on the same catalog — serialised by `catalog_id`; idempotency keyed off catalog version.
  - Real-time / streaming iteration — batch / event-driven only. There is no in-cycle dataflow; everything is `on event → action`.
  - Persistence of intermediate adapters, datasets, or trace records — defer to [[lm_finetune]] (adapter storage), [[lm_data_synth]] (dataset storage), and [[analyzer_trace_store]] (trace persistence). The composite only references their IDs.
  - Defining the `eval_summary` schema — that is owned by [[analyzer_metrics]] and [[factory_evaluation]]. This composite passes it through, not edits it.
- **on violation**: if the composite catches itself wanting to *call* a primitive directly (not via an event) — STOP. That is the [`workflow_principle`](../agent-forge/workflow_principle.md) anti-pattern (`Contract-less composite` / `Scope-creeping composite`). Restructure: either subscribe to an existing event in the shared namespace, or propose a *new* event on the shared bus (a separate doc PR), then resume.

## Procedure

```
on factory.pipeline.start(catalog_id, max_iter, threshold, auto_apply, plateau_K):
    state[catalog_id] = {iteration: 0, history: [], patches: [], em_curve: []}
    enqueue lm.synth.request(catalog_id, strategies=[...])     # primitive emits lm.synth.completed

on lm.synth.completed(catalog_id, dataset_id):
    state[catalog_id].dataset_id = dataset_id
    enqueue lm.finetune.request(catalog_id, dataset_id)

on lm.finetune.completed(adapter_id, catalog_id, eval_summary):
    state[catalog_id].adapter_id = adapter_id
    enqueue benchmark.iot.request(adapter_id, catalog_id)
    enqueue benchmark.bfcl.request(adapter_id, catalog_id)

on benchmark.iot.completed(catalog_id, summary_path):
    state[catalog_id].iot_summary = summary_path
on benchmark.bfcl.completed(catalog_id, summary_path):
    state[catalog_id].bfcl_summary = summary_path
    # both benchmarks fan-in expected before metrics summarise

on analyzer.trace.recorded(catalog_id, trace_id):
    state[catalog_id].traces_seen += 1
    # wait until expected_trace_count reached (counted from benchmark.*.completed)

on analyzer.failure.classified(catalog_id, label, trace_id):
    state[catalog_id].failure_hist[label] += 1

on analyzer.metrics.summarized(catalog_id, summary_path, n_traces):
    em = read(summary_path).exact_match_rate
    state[catalog_id].em_curve.append(em)
    if em >= threshold:
        emit factory.pipeline.iterated(catalog_id, iteration, eval_summary=summary_path)
        emit factory.pipeline.aborted(catalog_id, reason="threshold_reached")
        stop
    if state[catalog_id].iteration >= max_iter:
        emit factory.pipeline.iterated(catalog_id, iteration, eval_summary=summary_path)
        emit factory.pipeline.aborted(catalog_id, reason="max_iter_reached")
        stop
    if plateau(em_curve, K=plateau_K):
        emit factory.pipeline.iterated(catalog_id, iteration, eval_summary=summary_path)
        emit factory.pipeline.aborted(catalog_id, reason="plateau")
        stop
    # else: wait for rule.proposed events

on analyzer.rule.proposed(catalog_id, rule_patch, evidence):
    state[catalog_id].patches.append((rule_patch, evidence))

on (all_expected_rule_proposals_received(catalog_id)):
    if auto_apply:
        new_catalog_id = apply_patches(catalog_id, state[catalog_id].patches)  # version+1
        emit contract.catalog.published(new_catalog_id, version=current+1)
        emit factory.pipeline.iterated(catalog_id, iteration, eval_summary)
        state[catalog_id].iteration += 1
        enqueue lm.synth.request(new_catalog_id, strategies=[...])             # next cycle
    else:
        emit factory.pipeline.iterated(catalog_id, iteration, eval_summary, pending_patches=state[catalog_id].patches)
        # halts here; human approval signal resumes via factory.pipeline.start on new catalog_id

on primitive_failure(name, catalog_id, cause):
    emit factory.pipeline.aborted(catalog_id, reason=f"primitive_failed:{name}:{cause}")
    stop

on window_W_elapsed_without(expected_event, catalog_id):
    emit factory.pipeline.aborted(catalog_id, reason=f"primitive_timeout:{expected_event}")
    stop
```

## Contract

- **in**:
  - Initial `factory.pipeline.start(catalog_id, max_iter, threshold, auto_apply, plateau_K)` invocation.
  - Events from primitives — see `event.consume` below.
- **out**:
  - Exactly one `factory.pipeline.iterated(catalog_id, iteration, eval_summary, pending_patches?)` per completed cycle (idempotent across re-emits keyed by `(catalog_id, iteration)`). `pending_patches` is populated only when `auto_apply=False` and the iteration produced proposed patches awaiting human approval; absent / empty otherwise.
  - Exactly one `factory.pipeline.aborted(catalog_id, reason)` per pipeline instance, on any stop condition.
  - Pass-through artifact references: `dataset_id`, `adapter_id`, `summary_path` (the underlying files are owned by primitives, not this composite).
- **event**:
  - consume: `lm.synth.completed`, `lm.finetune.completed`, `benchmark.iot.completed`, `benchmark.bfcl.completed`, `analyzer.trace.recorded`, `analyzer.failure.classified`, `analyzer.metrics.summarized`, `analyzer.rule.proposed`, `contract.catalog.published` (when external — for human-gated patch resumption).
  - emit: `factory.pipeline.iterated`, `factory.pipeline.aborted`, `contract.catalog.published` (only when `auto_apply=True` and patches applied).
- **failure**:
  - Primitive failure (any consumed `*.failed` or out-of-band error) → `factory.pipeline.aborted(catalog_id, reason="primitive_failed:{name}")`.
  - Patch application failure (e.g. `[[contract_catalog]]` rejects the patch) → log + `factory.pipeline.aborted(catalog_id, reason="patch_apply_failed")`.
  - Window `W` elapsed without an expected event → `factory.pipeline.aborted(catalog_id, reason="primitive_timeout:{event_name}")`. Do not extend `W` adaptively.
  - Malformed primitive event (missing `catalog_id`, schema mismatch) → `factory.pipeline.aborted(catalog_id, reason="protocol_violation:{primitive}")`. Do not coerce.
- **success**: every pipeline instance yields **at least one** `factory.pipeline.iterated` *or* `factory.pipeline.aborted` event for its `catalog_id`; the iteration's `eval_summary` contains `exact_match_rate`, `action_match_rate`, `ast_match_rate` for each benchmark consumed; iteration order is monotone (`iteration_{n+1} > iteration_n`); re-emits with identical `(catalog_id, iteration)` are no-ops.

Stop conditions, ranked (first match wins):

1. `exact_match_rate ≥ threshold` → abort `reason="threshold_reached"`.
2. `iteration ≥ max_iter` → abort `reason="max_iter_reached"`.
3. Plateau: no `exact_match_rate` improvement above `ε` for `K` consecutive iterations → abort `reason="plateau"`.
4. Patch application failed → abort `reason="patch_apply_failed"`.
5. Primitive failure → abort `reason="primitive_failed:{name}"`.
6. Primitive timeout (window `W` elapsed) → abort `reason="primitive_timeout:{event}"`.
7. Protocol violation in a consumed event → abort `reason="protocol_violation:{primitive}"`.

## Inheritance (per [workflow_principle](../agent-forge/workflow_principle.md))

| Mechanism | What this composite inherits |
|---|---|
| Pointer | Links back to [`task_principle`](../agent-forge/task_principle.md) and [`workflow_principle`](../agent-forge/workflow_principle.md). |
| Template | Same 6-section structure as the primitives. |
| Pattern | Borrowed shape from [`release_health`](./release_health.md) — subscribe-aggregate-emit — extended to a *loop* (release_health is fan-in once per SHA; this is fan-in once per iteration, then re-entry). |
| Data | None today. If the loop ever needs per-stage configuration tables (e.g. `## Iteration profiles`), add as a data section here, not in primitives. |

## Observation

Composite aggregates primitives' metrics — no new metric primitives invented:

- `pipeline_iterations_total[catalog_id]` = count of `factory.pipeline.iterated` emitted for `catalog_id`.
- `pipeline_uplift_per_iteration[catalog_id, iteration]` = `exact_match_rate_i − exact_match_rate_{i-1}` (read from `[[analyzer_metrics]]` summaries).
- `patches_applied_per_iteration[catalog_id, iteration]` = `len(applied_patches)` (0 when `auto_apply=False`).
- `pipeline_wall_hours[catalog_id]` = wall-clock from `factory.pipeline.start` to terminal `factory.pipeline.aborted` (or last `factory.pipeline.iterated` if still running).
- `pipeline_abort_rate` = `factory.pipeline.aborted` ÷ `factory.pipeline.start` (rolling 30d).
- `plateau_rate` = aborts with `reason="plateau"` ÷ total aborts — signals threshold/`K` mis-tuning.
- `protocol_violation_count` = aborts with `reason="protocol_violation:*"` — should be 0 once primitive contracts stabilise.

## Negative checks (composite anti-patterns — per [workflow_principle](../agent-forge/workflow_principle.md))

- [ ] This file declares its own `in / out / event / failure / success` — not just a topology diagram. (If it ever degenerates to "just a diagram", delete — see `workflow_principle` §Anti-patterns.)
- [ ] Does not wrap a single primitive — wires *eight* event sources into a loop no primitive owns.
- [ ] Does not mutate any primitive's `in-scope` — only listens to declared event emissions; new behaviour requires new events on the shared bus, not primitive contract edits.
- [ ] Procedure uses event-handler shape (`on <event(payload)> → <action>`); no direct calls to other task docs by name.

Related: [[lm_data_synth]], [[lm_finetune]], [[benchmark_iot]], [[benchmark_bfcl]], [[analyzer_trace_store]], [[analyzer_failure_taxonomy]], [[analyzer_metrics]], [[analyzer_rule_synthesis]], [[contract_catalog]], [[factory_evaluation]], [[factory_bfcl]], [[factory_bfcl_phase3]].
