[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Companions: [external_benchmark_bfcl](./external_benchmark_bfcl.md), [tool_schema_compiler](./tool_schema_compiler.md)

# factory_bfcl_arc

**STATUS: PLAN — no implementation yet. This doc is the spec under which BFCL-targeted factory work proceeds.**

Apply the same factory pipeline that lifted Qwen3-0.6B from 38% to 99.6% on `iot_light_5` (synth → SFT → self-bootstrap → post-correction → DPO) to the BFCL v4 single-turn benchmark, producing a Qwen3-0.6B adapter whose BFCL AST match is the new headline. The factory pipeline must be re-derived around **per-case catalogs** — BFCL's defining property — rather than the fixed-catalog tier model the existing factory was written against.

## Role

Re-derive each factory stage for BFCL's case-bound catalogs, train a Qwen3-0.6B LoRA on the resulting synthetic + bootstrapped data, run inference under the BFCL grader, and produce a head-to-head report against the existing `qwen3.6-plus / qwen3.6-flash` DashScope-API BFCL numbers.

## Scope

- **in-scope**:
  - Data synthesis (S1') — generate `(case_tools, user_message, expected_calls)` triples whose distribution matches BFCL v4 single-turn categories. Source = upstream BFCL v4 train split (NOT our 500-case sample, which is the evaluation set).
  - SFT (S2a') — Qwen3-0.6B LoRA on the synth output. Training rendering MUST be the **per-case** DSL prompt (the same `Catalog.render_json_dsl()` that BFCL inference uses); each row carries its own catalog text in the system prompt.
  - Self-bootstrap (S2c') — sample the SFT-trained adapter on the training intents, keep `ast_match`-passing rollouts (BFCL AST grader, not exact match), augment train.jsonl.
  - Post-correction (S2a+') — port BFCL-applicable rules into `Catalog.parse_json_dsl`-path. Candidate rules: `strip_unknown_args` (already exists; flip default to True for compiled BFCL catalogs), BFCL-grader-aware string normalization (`_standardize_string` from `ganglion/bfcl/grader.py`), `tuple→list` coercion, numeric int↔float promotion. Each rule lands behind a flag and a unit test against the upstream AST checker semantics.
  - DPO (S3') — verifier-graded preference pairs scored by `bfcl.grader.ast_match` (not `eval/metrics.graded_score`, which is IoT-shaped).
  - Held-out evaluation — the 500-case `examples/bfcl/v4/sample/*.jsonl` (the same SSOT eval that `bfcl_m5_abstention_report.md` uses), driven via `python -m ganglion.eval.runner --bfcl all --llm bfcl-0.6b-lora`.
  - One new `--llm bfcl-0.6b-lora` client adapter wrapping a local Qwen3-0.6B + LoRA inference loop, mirroring `QwenJSONDSLClient` so it goes through the same validator path.
- **out-of-scope**:
  - Multi-turn / Java / live BFCL categories — same boundary as [external_benchmark_bfcl](./external_benchmark_bfcl.md). BFCL multi-turn requires conversation-state synthesis which this arc is not designed around.
  - 1.7B / larger base models — Arc A's thesis is *bounded specialization at sub-1B*. 1.7B comparisons stay in factory_phase1 reports.
  - Editing `examples/bfcl/v4/sample/*.jsonl` — that is the eval SSOT and is regenerated only via `subsample.py` with the pinned seed.
  - DashScope-API replays — qwen3.6-plus/flash numbers are frozen in their respective reports; this arc does not re-run them.
  - Catalog edits in `ganglion/schema/*.py` — irrelevant; BFCL never uses the IoT tiers.
  - Inference-time grammar masking (XGrammar) on BFCL — deferred. `ganglion/factory/grammar/catalog_to_xgrammar.py` was authored against fixed-shape catalogs; BFCL's per-case schemas need a compile-per-case grammar path that is its own task (`bfcl_grammar_masking.md`, TBD).
  - "Universal" base-model training (Tier 0) — explicitly deferred per `factory/__init__.py`.
- **on violation**: any work that requires touching IoT-tier code paths or BFCL multi-turn data stops and escalates with `factory_bfcl_arc_scope_breach(area)`. The post-correction rules must each have an upstream-grader test before merging; if a candidate rule changes BFCL leaderboard AST behaviour in a way the AST checker doesn't already accept, the rule does **not** land — escalate.

## Procedure

```
S0  Acquire BFCL v4 train data
    fetch upstream BFCL v4 simple_python / multiple / parallel / parallel_multiple /
        irrelevance splits, EXCLUDING the 500 evaluation case ids in
        examples/bfcl/v4/sample/*.jsonl (use the `id` field for the exclusion set).
    write examples/bfcl/v4/train/<category>.jsonl
        (or pull from upstream-mirrored HF dataset; same SOURCE.md pinning rule)
    target volume: 2000–4000 cases total, weighted toward categories with
        the weakest M5 DSL score (parallel_multiple, irrelevance).

S1' Per-case synth augmentation
    for each train case:
        catalog ← compile_tool_calling_schema(case.function)
        # Tool-anchored synth is poorly aligned with BFCL; the per-case
        # catalog already gives one ground-truth call. Instead, use a
        # *paraphrase-anchored* prompt: ask the teacher for K paraphrases
        # of case.user_message that preserve the same expected call.
        for paraphrase in teacher.paraphrase(case.user_message, k=K):
            keep if catalog.parse_json_dsl(case.expected_dsl).calls
                   match ast_match against paraphrase under case.ground_truth
    write examples/bfcl/v4/train/synth.jsonl in SynthExample format
        with the addition of a `tools` column carrying the case schema.

S2a' SFT (Qwen3-0.6B + LoRA)
    extend ganglion.factory.customer.train_lora.build_messages to consume
        per-row catalog (currently takes a single Catalog kwarg).
    training format: same system/user/assistant triplet as runtime, with
        the per-row catalog rendered into the system prompt.
    base = Qwen/Qwen3-0.6B, LoRA all-linear, r=32 α=64, bf16, gc on,
        epochs=3, lr=2e-4, cosine, warmup 5%, seed=42.
    output: runs/factory_phase2/sft_0.6B_bfcl/<adapter>

S2a+ Post-correction port
    for each candidate rule:
        write a regression test that pre-corrects a known BFCL failure
            row from runs/bfcl/m5_full_run.jsonl
        gate via tool spec or Catalog default flag
        confirm `pytest tests/test_bfcl_grader.py + test_bfcl_runner.py`
            still passes on the existing 500-case fixtures
    rules to land first (each is its own commit):
        - strip_unknown_args default-True for compiler-generated catalogs
          (already exists; flag flip on `compile_tool_calling_schema`)
        - tuple→list coercion at validator level (already partial in
          ganglion/bfcl/grader.py — promote into Catalog.parse_json_dsl
          so SFT can learn the canonical form)
        - int→float promotion for `number`-typed args (mirrors grader)
        - string canonicalization for enum aliases derived from BFCL
          enum + description hints (NOT the IoT alias map)

S2c' Self-bootstrap
    fork runs/factory_phase2/self_bootstrap.py → self_bootstrap_bfcl.py
    differences from IoT version:
        - source pool = examples/bfcl/v4/train/synth.jsonl (per-row catalog)
        - grader = bfcl.grader.ast_match instead of ActionPlan equality
        - keep iff grade.valid AND not previously seen (id-level dedupe)
    augment train.jsonl → train.augmented.jsonl; retrain with same config

S3'  DPO with BFCL-graded pairs
    fork runs/factory_phase2/dpo_pairs.py → dpo_pairs_bfcl.py
    score each completion via:
        plan ← catalog.parse_json_dsl(out)
        grade ← bfcl.grader.ast_match(plan.calls, case)
        score ← 1.0 if grade.valid
                else 0.5 if grade.error_type starts with "value_error"
                else 0.0
    keep pair iff (winner_score - loser_score) >= 0.5
    train DPO on top of S2c' adapter

Eval
    ganglion/eval/runner.py: register --llm bfcl-0.6b-lora
        loads Qwen3-0.6B + adapter once, runs invoke(prompt) per case
    python -m ganglion.eval.runner --bfcl all \
        --llm bfcl-0.6b-lora --bfcl-allow-empty-calls \
        --bfcl-output runs/bfcl/0.6b_lora_cases.jsonl \
        > runs/bfcl/0.6b_lora_summary.json

Reporting
    docs/bfcl_0.6b_factory_report.md — same template as
    bfcl_m5_abstention_report.md, head-to-head against:
        qwen3.6-plus (M5 full run, 86.2%)
        qwen3.6-flash (M5 full run, 80.8%)
        untuned Qwen3-0.6B BFCL baseline (must be measured first)
    Decision rule: SFT-only number is mandatory before any S2c'/S3' work.
        If untuned 0.6B BFCL is far below 38% (IoT untuned baseline),
        reassess feasibility before committing GPU time.
```

## Contract

- **in**:
  - Upstream BFCL v4 train data with the 500 eval ids excluded.
  - Existing `compile_tool_calling_schema` (per-case catalog) and `ast_match` grader.
  - GPU access (CUDA preferred per `runs/factory_phase2/train_v2_cuda.py` pattern).
- **out**:
  - `runs/factory_phase2/sft_0.6B_bfcl/{v1,v2,v3}/adapter/` — three checkpoints (SFT, +bootstrap, +DPO).
  - `runs/bfcl/0.6b_lora_summary.json` and `..._cases.jsonl` — final eval artifacts in the same schema as existing BFCL summaries.
  - `docs/bfcl_0.6b_factory_report.md` — head-to-head report.
  - `examples/bfcl/v4/train/{synth,augmented}.jsonl` — checked-in synth corpus, deterministic seed.
  - `examples/bfcl/v4/train/SOURCE.md` — upstream commit SHA and exclusion-list method, identical convention to `examples/bfcl/v4/sample/SOURCE.md`.
- **event**: consume `bfcl.run.requested(phase=0.6b_factory, …)`; emit `bfcl_factory_arc_completed(adapter_sha, ast_match_rate) | bfcl_factory_arc_paused(reason, decision_point)`.
- **failure**:
  - Untuned 0.6B BFCL AST < 10% → emit `bfcl_factory_arc_paused(reason=base_too_weak)`; do not proceed to SFT. Decision: either pivot to 1.7B (out-of-scope of this arc) or accept that BFCL is not a sub-1B-feasible benchmark.
  - SFT v1 fails to improve over untuned by ≥ +20pp → emit `bfcl_factory_arc_paused(reason=sft_below_floor)`. Debug train/inference distribution mismatch BEFORE adding bootstrap or DPO.
  - Self-bootstrap kept ratio < 5% of pool → emit `bfcl_factory_arc_paused(reason=bootstrap_yield_too_low)`. Bootstrap below this threshold doesn't change the adapter measurably; do not retrain.
  - Post-correction rule breaks `tests/test_bfcl_grader.py` against the upstream AST semantics → rule does not land. No exceptions, no flag gating around grader correctness.
  - Training/inference catalog rendering drift detected (system prompt at train != at inference) → hard stop; this is the one invariant that always sinks the whole arc.
- **success**:
  - Untuned 0.6B BFCL AST measured and stamped (the entry-gate number).
  - Final adapter reaches AST ≥ qwen3.6-flash (~80%) on the 500-case eval → "Arc A objective met" (sub-1B specialization matches a flagship-class model on the same external benchmark).
  - Soft target: AST ≥ 86% (qwen3.6-plus parity) — at parity, the framework-on-small-model claim is publishable.
  - All checkpoints, summaries, and the head-to-head report are committed.

## Decision points (gating questions the arc must answer in order)

1. **Is BFCL feasible for 0.6B at all?** — *untuned 0.6B baseline*. If < 10% AST, stop the arc.
2. **Does the IoT-style SFT lift transfer to BFCL?** — *0.6B + SFT-only*. The IoT result was +35pp from a 38% base. BFCL has thousands of unique tool schemas; the floor and ceiling are unknown. If SFT lift is < 20pp, do not proceed.
3. **Is the IoT-style post-correction lift achievable on BFCL?** — *0.6B + SFT + post-correction*. IoT got +6pp from `defaults_when_missing` + `strip_unknown_args` + KR-time normalization. BFCL's analogues are different and unproven (strip_unknown_args, tuple→list, int→float, string standardization). If lift is < 2pp, the post-correction track is not load-bearing on BFCL.
4. **Does self-bootstrap deliver lift on a heterogeneous catalog distribution?** — *0.6B + SFT + bootstrap*. IoT had 5 tools across the entire dataset; BFCL has hundreds across cases. Bootstrap may dedupe to a much smaller useful set.
5. **Does DPO close the remaining gap?** — *0.6B + SFT + bootstrap + DPO*. Only run if (3) and (4) cleared their floors.

Each decision point is a stop-the-arc-and-write-a-paragraph moment. The session report (analogous to `docs/factory_phase2_session_2026-05-08.md`) captures the answers before proceeding to the next stage. **No silent skipping of stages.**

## Observation

- `untuned_0.6b_bfcl_ast` — the entry-gate number. Reported per category and aggregate.
- `sft_lift_pp` = (SFT AST − untuned AST). Compare with IoT-light +35pp benchmark.
- `post_correction_lift_pp` = (SFT+pc − SFT). Compare with IoT-light +6pp benchmark.
- `bootstrap_kept_ratio` = bootstrap-passed samples ÷ pool. IoT showed ~10% useful yield (S2c v2).
- `bootstrap_lift_pp` = (SFT+pc+bootstrap − SFT+pc).
- `dpo_lift_pp` = (SFT+pc+bootstrap+DPO − SFT+pc+bootstrap).
- `gap_to_qwen36_plus` = (qwen3.6-plus AST 86.2% − final adapter AST). Negative means parity reached.
- `train_inference_prompt_drift` — deterministic check: render the system prompt at train and at inference for one case, byte-diff. Must be 0. Enforced as a regression test, not just observation.

## Status

| Stage | Status | Artifact |
|---|---|---|
| S0 — train data acquisition | ✅ landed | `examples/bfcl/v4/build_train.py`, `examples/bfcl/v4/train/{*.jsonl, stats.json}` (740 cases, zero `id` overlap with `sample/`) |
| S1' — paraphrase synth | ☐ not started | — |
| S2a' — SFT Qwen3-0.6B + LoRA | ☐ not started | — |
| S2a+ — post-correction port | ☐ not started | — |
| S2c' — self-bootstrap | ☐ not started | — |
| S3' — DPO | ☐ not started | — |
| Eval + report | ☐ not started | — |

Decision-point numbers will be filled in here as each stage lands. The first stage requiring measurement (entry-gate untuned-0.6B BFCL baseline) belongs to S2a' as a prerequisite check, not S0.
