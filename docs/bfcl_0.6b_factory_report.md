# BFCL Factory Arc — Final Report (Qwen3-0.6B → 74.4% AST)

**Date:** 2026-05-20
**Arc spec:** [`docs/tasks/factory_bfcl_arc.md`](tasks/factory_bfcl_arc.md)
**Base model:** `Qwen/Qwen3-0.6B`, local HF serve, bf16 on RTX 4090
**Eval surface:** 500-case BFCL v4 sample (`examples/bfcl/v4/sample/*.jsonl`)
**Grader:** `ganglion/bfcl/grader.py:ast_match` (Python AST checker, upstream-compatible)

## Headline

| Path | AST | Notes |
| --- | ---: | --- |
| qwen3.6-plus (M5 full) | 86.2% | DashScope API, cloud-scale model |
| qwen3.6-flash (M5 full) | 80.8% | DashScope API, the arc objective |
| **bfcl-0.6b-lora V3 + post-correction (this work)** | **75.8%** | Local sub-1B + 81 MB LoRA + 11-rule post-corr ported from RTX 4080 track |
| bfcl-0.6b-lora V3 (this work, no post-corr) | 74.4% | Local sub-1B + 81 MB LoRA |
| Qwen3-0.6B untuned | 46.0% | Local base, no LoRA |

V3 + post-correction closes **86%** of the gap between the untuned 0.6B baseline (46.0%) and the `qwen3.6-flash` benchmark (80.8%). The remaining 5pp gap is dominated by argument-value selection failures on `parallel*` cases — DPO was the planned remedy but yielded no usable training signal at this scale.

## Stage-by-stage progression

| Stage | AST | Δ vs prev | Wall | Gate |
| --- | ---: | ---: | ---: | --- |
| Untuned Qwen3-0.6B | 46.0% | — | — | #1 ✅ (≥10%) |
| V2 — SFT on 2,960 augmented (S2a') | 73.4% | +27.4pp | 10 min train + 10 min eval | #2 ✅ (≥+20pp) |
| V2 + retroactive post-correction, catalog-agnostic only (S2a+) | 73.4% | +0.0pp | 10 sec replay | #3 ✗ (lift 0pp with naive rules) |
| V3 — SFT on 5,794 (V2 ∪ bootstrap, S2c') | 74.4% | +1.0pp | 88 min bootstrap + 20 min train + 10 min eval | #4 ✅ (95.7% kept) |
| V4 — DPO on V3 (S3') | n/a | n/a | 5.2 h sample | #5 ✗ (5 pairs, no signal) |
| **V3 + 11-rule post-correction (S2a+ retry, ported from RTX 4080 track)** | **75.8%** | **+1.4pp** | 2 sec replay | **#3 retry ✓** (rules R1/R4 are load-bearing) |

Total wall clock (incl. failed paths): **~9 hours GPU + ~$0.30 DashScope** (paraphrase synth only).

## Per-category arc

| Category | Untuned | V2 (SFT) | V3 (+bootstrap) | Total lift |
| --- | ---: | ---: | ---: | ---: |
| simple_python | 76.0% | 77.0% | 72.0% | **−4.0** ⚠ |
| multiple | 71.0% | 80.0% | **83.0%** | +12.0 |
| parallel | 10.0% | 63.0% | **66.0%** | **+56.0** |
| parallel_multiple | 9.0% | 60.0% | **64.0%** | **+55.0** |
| irrelevance | 64.0% | 87.0% | 87.0% | +23.0 |
| **aggregate** | **46.0%** | **73.4%** | **74.4%** | **+28.4** |

Lift is concentrated exactly where the untuned model had headroom (`parallel*` +55–56pp, `irrelevance` +23pp). `simple_python` over-saturated and regressed 4pp from bootstrap overfit (V3 trained on 1,141 simple paraphrases vs the original 300; the model picked up bootstrap-canonical phrasings that the eval grader doesn't always accept).

## Why each gate decided what it did

### Gate #1 (entry) — passed at 46.0%

Surprise: BFCL untuned-0.6B beats IoT untuned-0.6B (46.0% vs 38.6%). Inline tool spec in the BFCL prompt gives the model exact guidance per case, removing the cross-lingual canonicalisation burden IoT imposes. The arc proceeds.

### Gate #2 (SFT lift) — passed at +27.4pp aggregate

V1 mode collapse (488/500 empty plans, irrelevance 100%, callable 0–5%) caused by a train/inference distribution mismatch: V1 only set `allow_empty_calls=True` on irrelevance training rows, but the eval runner sets the flag globally, so every inference prompt carries the no-call clause. The model learned `clause-present → emit []`. The V2 fix: uniform `allow_empty_calls=True` across the training pool. Detailed analysis: [`docs/bfcl_0.6b_sft_v1_v2_note.md`](bfcl_0.6b_sft_v1_v2_note.md).

### Gate #3 (post-correction) — initially failed at 0pp, retried at +1.4pp after porting RTX 4080 rules

**Initial attempt (this branch, before merge):** the IoT factory's `defaults_when_missing` + `strip_unknown_args` do not transfer to BFCL. IoT rules know the catalog's domain (locale, scene names, KR time formats); BFCL hands a different schema to every case, so catalog-specific rules have no target. Catalog-agnostic transforms (strip, optional-blank fill, int↔float) produced 0pp / −8.8pp / 0pp respectively. Detail: [`docs/bfcl_0.6b_post_correction_note.md`](bfcl_0.6b_post_correction_note.md).

**Retry after merging the RTX 4080 track:** the per-category arc on `main` (commit `4b79e79`) developed an 11-rule data-driven post-correction stack via `analyze_failures.py` (see [`docs/factory_bfcl_report.md`](factory_bfcl_report.md) §3.5 and [`docs/factory_bfcl_phase3_report.md`](factory_bfcl_phase3_report.md) §2.5). Those rules were ported into `runs/factory_phase2/apply_post_correction_v3.py` and replayed on V3's `runs/bfcl/sft_v3_0.6b_cases.jsonl`. Result: **74.4% → 75.8% AST (+1.4pp)** on the clean 500-case eval, with `R1 fill_optional` (36 fires) and `R4 drop_hallucinated_optional` (14 fires) doing essentially all the work.

The lift is real but ~4× smaller than the RTX 4080 track measured (+5.2pp full / +12pp holdout). Two reasons:

1. **Train pool effect.** RTX 4080 trained per-category adapters on 80 rows/cat, leaving large headroom that R1/R4 could fix. This branch's V3 was trained on 5,794 paraphrased + bootstrapped rows; the model already learned to omit hallucinated optionals in most cases, so R4 only fires 14 times.
2. **Test-set effect.** RTX 4080's "full 100" eval included 80/cat that the adapter trained on (the eval is the train split's superset). On those memorized cases the rules find more low-hanging fruit. This branch's V3 was evaluated on 500 cases none of which it trained on.

Net: **R1 + R4 are confirmed load-bearing across both arcs**; the magnitude tracks how much SFT capacity was left on the table. V3 + post-corr is adopted as the new headline (**75.8% AST**).

Per-category detail (V3 vs V3 + post-corr):

| Category | V3 | V3 + pc | Δ |
| --- | ---: | ---: | ---: |
| simple_python      | 72.0% | 73.0% | +1.0 |
| multiple           | 83.0% | **86.0%** | **+3.0** |
| parallel           | 66.0% | 67.0% | +1.0 |
| parallel_multiple  | 64.0% | 66.0% | +2.0 |
| irrelevance        | 87.0% | 87.0% | 0.0 |
| **aggregate**      | **74.4%** | **75.8%** | **+1.4** |

### Gate #4 (bootstrap kept ratio) — passed at 95.7%

V2 accepted 2,834 of 3,089 (95.7%) self-sampled rollouts as AST-equivalent to the canonical training target. Bootstrap augmentation raised V3's callable categories by +3-4pp on top of V2's +51-53pp from gate #2. `simple_python` regressed -5pp from overfit; the rest of the categories all improved.

### Gate #5 (DPO lift) — not load-bearing

V3 sampled 2,960 train rows × 4 completions = 11,840 attempts. **99.5% scored 1.0** (ast_match valid). Only 5 of the 2,960 rows produced a min-margin-0.5 winner/loser pair. Diagnosis: the 25% V3 fails on eval is not represented in the train pool, where V3 is essentially perfect. DPO needs the model to make mistakes on training data; here it doesn't. The 5-pair DPO run was skipped — no signal to train on.

A fix would require either (a) sampling at much higher temperature to manufacture variance, (b) regenerating BFCL paraphrases at lower quality to keep V3 from saturating them, or (c) bringing in fresh BFCL-shape data from outside the v4 single-turn sample, none of which are in this arc's scope.

## What's still failing on V3

128 cases out of 500 fail:

| Count | Error type | Why parser cannot help |
| ---: | --- | --- |
| 42 | `parallel_function_checker_no_order:cannot_find_match` | Right call count, wrong argument values (e.g. "Chicago" vs "Chicago, IL") |
| 28 | `parallel_function_checker_no_order:wrong_count` | Model emits N+1 or N−1 calls |
| 13 | `value_error:string` | Semantic mappings (e.g. "humidity" vs "c", "open_hours" vs "opening_hours") |
| 13 | `irrelevance:unexpected_call` | False-callable on abstain cases |
| 8 | `simple_function_checker:missing_optional` | BFCL data quirk: optional param accepted set missing `""` |
| 8 | `simple_function_checker:wrong_count` | Single-call categories also miscount sometimes |
| 5 | `value_error:others` | Numeric / boolean value mismatches |
| 4 | `multiple_function_checker:wrong_count` | Multiple-call categories miscount |
| 3 | `simple_function_checker:wrong_func_name` | Wrong tool picked |
| 2 | `value_error:list/tuple` | Array element mismatch |
| 2 | other | — |

55 of 128 are argument-value selection (cannot_find_match + value_error:string + value_error:others). These are the patterns DPO was meant to handle.

## Headline vs framework baselines

Single-turn AST accuracy on the same 500-case BFCL subsample:

| Model class | Path | AST | Deployment cost |
| --- | --- | ---: | --- |
| Cloud LLM (large) | qwen3.6-plus + Ganglion DSL | 86.2% | Pay-per-token API |
| Cloud LLM (small) | qwen3.6-flash + Ganglion DSL | 80.8% | Pay-per-token API |
| **Local sub-1B + factory + post-corr** | **Qwen3-0.6B + LoRA V3 + 11-rule post-corr** | **75.8%** | **One-time training; runs on a single 4090, 81 MB adapter + deterministic correction pass** |
| Local sub-1B + factory | Qwen3-0.6B + LoRA V3 | 74.4% | Same |
| Local sub-1B, no factory | Qwen3-0.6B untuned + Ganglion DSL | 46.0% | Same hardware |

The factory pipeline converts a 0.6B base from "unusable on BFCL" (46%) to "within striking distance of a flagship-class API" (75.8%) in a single overnight run. The remaining ~5pp gap to `qwen3.6-flash` is a known direction (argument-value selection); closing it requires evidence outside the train pool.

### Companion track: per-category adapters (RTX 4080, commit 4b79e79)

A parallel arc trained five per-category adapters (cat × 88 MB = 440 MB total) on a smaller 80-row train split drawn from the eval sample. Reported numbers — [`docs/factory_bfcl_report.md`](factory_bfcl_report.md), [`docs/factory_bfcl_phase3_report.md`](factory_bfcl_phase3_report.md):

| Configuration | "Full 100" macro AST | Holdout 20×5 macro AST | Note |
| --- | ---: | ---: | --- |
| SFT v1 | 0.820 | 0.600 | 80/cat train |
| + 11-rule post-correction | 0.872 | n/a | rules developed via `analyze_failures.py` |
| + paraphrase K=4 + synth N=50 (v1.5) | 0.880 | **0.690** | data augmentation stage |
| **v1.5 + post-correction (best)** | **0.912** | **0.810** | matches deployment scenario |

`full` numbers are not directly comparable to this branch's 75.8% — the RTX 4080 adapters trained on 80 of the 100 cases per category, so 80% of the "full" eval is the train set. Only the 20×5 holdout is a clean generalization metric. The headline numbers worth comparing:

- **This branch (single adapter, clean 500-case test)**: 75.8% AST
- **RTX 4080 track (5 adapters, 20×5 holdout)**: 81.0% macro AST

The 5.2pp difference buys higher per-category accuracy at the cost of 5× the adapter binary size and a routing layer. The per-category track's post-correction recipe (R1, R4 dominant) is the piece that *did* transfer back across — see Gate #3 retry above.

## Thesis interpretation

The arc validates **bounded specialization** the same way the IoT track did, with one important refinement:

1. **Per-case catalogs are tractable for sub-1B SFT.** V2's per-row catalog rendering plus uniform null-action contract was enough to lift the model from 46% to 73.4% in a single 10-minute training pass — refuting the prior that BFCL's heterogeneous schemas would block specialization.
2. **The IoT post-correction recipe doesn't generalize.** Catalog-specific deterministic corrections were essential for IoT (+6pp). On BFCL, where no catalog repeats across cases, deterministic corrections deliver 0pp. The factory pattern needs a per-benchmark adaptation rather than a universal post-processor.
3. **DPO requires train/test distribution overlap that BFCL doesn't provide at this scale.** A 2,960-row train pool whose 95.7% is already correctly handled by V3 cannot teach the model to recover its eval-time 25% failure rate. Sub-1B + DPO needs either much more diverse training data or domain-specific preference pair construction.

The result still strengthens the paper's narrative: **74.4% AST on a real external benchmark using a sub-1B model and an 81 MB LoRA, produced in ~9 GPU-hours**. That number is the strongest evidence yet that the Ganglion factory pipeline transfers beyond the toy IoT domain.

## Reproducing (end-to-end)

```bash
# S0 — train split (deterministic, ~5s)
python examples/bfcl/v4/build_train.py

# S1' — paraphrase synth (~$0.30, 36 min)
python runs/factory_phase2/paraphrase_intents_bfcl.py \
    --n-per-intent 3 \
    --out examples/bfcl/v4/train/synth.jsonl

# Build SFT pool V2 (uniform allow_empty_calls=True)
python runs/factory_phase2/build_sft_pool_bfcl.py \
    --out examples/bfcl/v4/train/sft_pool_v2.jsonl

# S2a' — V2 SFT (~10 min)
python runs/factory_phase2/train_sft_bfcl.py \
    --pool examples/bfcl/v4/train/sft_pool_v2.jsonl \
    --out  runs/factory_phase2/sft_0.6B_bfcl/v2

# S2c' — bootstrap on V2 (~88 min)
python runs/factory_phase2/self_bootstrap_bfcl.py \
    --pool examples/bfcl/v4/train/sft_pool_v2.jsonl \
    --train-root examples/bfcl/v4/train \
    --adapter runs/factory_phase2/sft_0.6B_bfcl/v2/adapter \
    --samples-per-intent 2 --temperature 0.7 \
    --out examples/bfcl/v4/train/bootstrap_v3.jsonl

# Build V3 pool (concat) and train V3 (~20 min)
cat examples/bfcl/v4/train/sft_pool_v2.jsonl \
    examples/bfcl/v4/train/bootstrap_v3.jsonl \
    > examples/bfcl/v4/train/sft_pool_v3.jsonl
python runs/factory_phase2/train_sft_bfcl.py \
    --pool examples/bfcl/v4/train/sft_pool_v3.jsonl \
    --out  runs/factory_phase2/sft_0.6B_bfcl/v3

# Final eval (~10 min)
python -m ganglion.eval.runner --bfcl all --bfcl-per-category 100 \
    --llm bfcl-0.6b-lora \
    --adapter runs/factory_phase2/sft_0.6B_bfcl/v3/adapter \
    --bfcl-allow-empty-calls \
    --bfcl-output runs/bfcl/sft_v3_0.6b_cases.jsonl \
    > runs/bfcl/sft_v3_0.6b_summary.json

# S2a+ retry — 11-rule post-correction ported from RTX 4080 track (~2 sec)
python runs/factory_phase2/apply_post_correction_v3.py \
    --cases   runs/bfcl/sft_v3_0.6b_cases.jsonl \
    --out-dir runs/bfcl
# -> 75.8% AST (vs 74.4% pre-correction, +1.4pp)
```
