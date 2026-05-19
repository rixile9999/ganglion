# Untuned Qwen3-0.6B on BFCL v4 — Decision Gate #1

**Date:** 2026-05-19
**Model:** `Qwen/Qwen3-0.6B`, local HF serve, bf16 on RTX 4090
**Dataset:** 500-case BFCL v4 single-turn subsample (`examples/bfcl/v4/sample/*.jsonl`)
**Path:** DSL JSON (Action IR) emitted under same system prompt as DashScope `qwen` path
**No LoRA, no repair, no grammar masking.**

## Headline

| Metric | Value |
| --- | ---: |
| **AST match rate** | **46.0%** |
| Syntax-valid rate | 94.0% |
| Latency mean (ms) | 1,059.15 |
| Latency p50 (ms) | 835.32 |
| Latency p95 (ms) | 3,198.92 |
| Output tokens (total) | 23,189 |

Reproducing:

```bash
python -m ganglion.eval.runner \
    --bfcl all --bfcl-per-category 100 \
    --llm bfcl-0.6b-base --bfcl-allow-empty-calls \
    --bfcl-output runs/bfcl/baseline_0.6b_untuned_cases.jsonl \
    > runs/bfcl/baseline_0.6b_untuned_summary.json
```

## Category breakdown

| Category | AST | Syntax-valid |
| --- | ---: | ---: |
| simple_python | **76.0%** | 92.0% |
| multiple | **71.0%** | 96.0% |
| irrelevance | 64.0% | 97.0% |
| parallel | **10.0%** | 89.0% |
| parallel_multiple | **9.0%** | 96.0% |

The model is competent at every category that produces **one** tool call. Where it has to emit **multiple** calls inside a single JSON `calls` array, it collapses.

## Failure mode distribution

| Count | Error type |
| ---: | --- |
| 166 | `parallel_function_checker_no_order:wrong_count` |
| 36 | `irrelevance:unexpected_call` |
| 16 | `value_error:string` |
| 15 | `parallel_function_checker_no_order:cannot_find_match` |
| 9 | `value_error:others` |
| 8 | `simple_function_checker:wrong_count` |
| 7 | `simple_function_checker:missing_optional` |
| 7 | `simple_function_checker:wrong_func_name` |
| 5 | `multiple_function_checker:wrong_count` |
| 1 | `type_error:nested` |

**67% of all failures are "wrong number of calls."** This is the single highest-leverage target for SFT.

## Comparison

| Model | Path | BFCL AST | IoT AST | Source |
| --- | --- | ---: | ---: | --- |
| qwen3.6-plus | DSL (M5) | 86.2% | n/a (different domain) | `docs/bfcl_m5_abstention_report.md` |
| qwen3.6-flash | DSL (M5) | 80.8% | n/a | `docs/bfcl_flash_replay_report.md` |
| **Qwen3-0.6B untuned (local HF)** | **DSL** | **46.0%** | **38.6%** | this note + `factory_phase2_plan.md` §10 |

Two notable points:

1. **BFCL untuned-0.6B starts higher than IoT untuned-0.6B** (46.0% vs 38.6%). The IoT eval is bilingual (Korean ↔ English aliasing) and uses a fixed catalog the model has not seen; BFCL is English-only and ships its tool spec inline in the prompt. The model exploits the inline spec better than it handles cross-lingual canonicalization.
2. **The gap to qwen3.6-flash is wide (–34.8pp) but mostly in parallel categories.** If SFT can lift parallel/parallel_multiple from ~10% to roughly the level of the single-call categories (70–76%), the aggregate moves to ~70% even before post-correction. That gives this arc real headroom.

## Decision

**Decision gate #1 — "Is BFCL feasible for 0.6B?" — PASSED.** 46.0% AST is 4.6× the 10% floor in `docs/tasks/factory_bfcl_arc.md`. Arc proceeds to S1' (paraphrase synth) and S2a' (SFT).

**Decision gate #2 sharpens.** The arc's `sft_lift_pp ≥ +20pp` rule is meaningful here because callable-category headroom is mixed: simple/multiple are already at 70%+ (low ceiling), parallel is at 9–10% (huge headroom). SFT should be evaluated **per-category** at decision gate #2, not only on the aggregate — if it lifts parallel by +50pp while leaving simple flat, the aggregate still moves but the arc's value is real.

## What the SFT design should specifically target

- The `calls` array length. Whatever paraphrase-synth data ships in S1' must include **parallel and parallel_multiple cases at roughly upstream weight** (200/740 train cases = 27%) so the SFT signal includes "emit N calls" as a learnable pattern.
- Argument value canonicalization for value-error cases (16 string + 9 others = 25 cases). Post-correction port (S2a+') should pick up the BFCL-grader-aware string standardization rule.
- Irrelevance abstention. Untuned hits 64% which is below the `qwen3.6-flash` 78% and `qwen3.6-plus` 90% — `allow_empty_calls` is already on; the gap is the model's tendency to invent a tool call when none fits. SFT on the 140 irrelevance training cases should narrow this directly.

## Artifacts

- `runs/bfcl/baseline_0.6b_untuned_summary.json` — full summary including `failures[]` for inspection
- `runs/bfcl/baseline_0.6b_untuned_cases.jsonl` — per-case detail
- `runs/bfcl/baseline_0.6b_untuned.log` — run log (HF weights load only)
- `ganglion/runtime/local_hf.py` — `LocalQwenDSLClient` used to produce this number
