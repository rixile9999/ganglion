# BFCL SFT on Qwen3-0.6B — V1 (mode collapse) and V2 (fixed)

**Date:** 2026-05-19
**Base model:** `Qwen/Qwen3-0.6B`, local HF serve, bf16 on RTX 4090
**Adapter:** LoRA all-linear, r=32, α=64, dropout 0.05
**Optimizer:** TRL SFTTrainer, AdamW, lr=2e-4 cosine, warmup 5%, bf16, gc on
**Schedule:** 3 epochs × 2,960 examples, effective batch 8 (4 × grad-accum 2), max_seq=2048
**Loss masking:** `assistant_only_loss=True` (system + user not in the gradient)
**Wall:** v1 = 606s, v2 = 621s on a single 4090
**Eval surface:** 500-case BFCL v4 sample, AST grader, `--bfcl-allow-empty-calls`

## V2 headline (decision gate #2 ✅ passed)

| Metric | Untuned 0.6B | **V2** | Δ |
| --- | ---: | ---: | ---: |
| AST aggregate | 46.0% | **73.4%** | **+27.4pp** |
| Syntax-valid | 94.0% | 97.6% | +3.6pp |
| Latency p50 (ms) | 835 | 1,391 | +66.6% |
| Output tokens (total) | 23,189 | 20,810 | −10% |

| Category | Untuned | **V2** | Lift |
| --- | ---: | ---: | ---: |
| simple_python | 76.0% | 77.0% | +1.0 |
| multiple | 71.0% | 80.0% | +9.0 |
| **parallel** | 10.0% | **63.0%** | **+53.0** |
| **parallel_multiple** | 9.0% | **60.0%** | **+51.0** |
| irrelevance | 64.0% | 87.0% | +23.0 |
| **aggregate** | **46.0%** | **73.4%** | **+27.4** |

Per the per-category gate rule in the arc spec, the result is meaningful: regression count is 0, and the lift is concentrated where the untuned model had headroom (`parallel*` +51–53pp). The model has not just shifted the mean — it has solved the specific failure mode (wrong call count) that the untuned baseline collapsed on.

vs framework baselines (DashScope `qwen3.6-*`):

| Path | AST | Δ vs V2 |
| --- | ---: | ---: |
| **bfcl-0.6b-lora V2 (this work)** | **73.4%** | — |
| qwen3.6-flash M5 (arc objective) | 80.8% | +7.4pp |
| qwen3.6-plus M5 (arc soft target) | 86.2% | +12.8pp |

V2 lands between the untuned 0.6B (46.0%) and the qwen3.6-flash baseline (80.8%), closing **77% of the gap** between them in a single SFT stage. The arc objective (≥ flash 80%) still needs S2a+, S2c', S3' to land on top.

## V1 (mode collapse) — preserved as failure evidence

V1 was the first attempt and produced a textbook mode collapse: the model emitted `{"calls": []}` on 488/500 cases. Irrelevance hit 100% (correct), every other category dropped to single digits or zero.

| Category | Untuned | V1 | Δ |
| --- | ---: | ---: | ---: |
| simple_python | 76.0% | 0.0% | −76.0 |
| multiple | 71.0% | 0.0% | −71.0 |
| parallel | 10.0% | 2.0% | −8.0 |
| parallel_multiple | 9.0% | 5.0% | −4.0 |
| irrelevance | 64.0% | 100.0% | +36.0 |
| aggregate | 46.0% | 21.4% | −24.6 |

### Why V1 collapsed

`build_sft_pool_bfcl.py` v1 set `allow_empty_calls=True` **only for the `irrelevance` category** (per the spec's S2a+' description of the null-action contract). This made the *system prompt* differ between categories: irrelevance rows carried the line

> If no tool call is needed, return exactly {"calls":[]}.

while every other category's prompt did not. The eval runner, however, passes `--bfcl-allow-empty-calls` as a global flag, so **every** inference-time system prompt carries that line.

Concretely:

| Phase | Catalog `allow_empty_calls` | System prompt has no-call clause |
| --- | --- | --- |
| Train (V1) — irrelevance | True | yes |
| Train (V1) — other 4 categories | False | **no** |
| Inference (any category) | True | **yes** |

The model learned a single, strong correlation: *"if the system prompt contains the no-call clause, the right answer is `{"calls":[]}`."* That is exactly the prompt shape every inference-time row has, so the model defaulted to the empty plan everywhere. `assistant_only_loss=True` meant nothing else in the system prompt was pushed against this rule. Training entropy collapsed to 0.001–0.003 and per-token accuracy hit 1.0 — by training-loss standards, V1 succeeded; by behaviour, it failed.

### V2 fix

`build_sft_pool_bfcl.py` now defaults to `--allow-empty-mode always`, which sets `allow_empty_calls=True` on **every** training-row catalog. The no-call clause now appears in the system prompt of every training row (callable and irrelevance alike), matching the global eval setting exactly.

The signal the model must learn shifts: not "*does the prompt allow empty calls?*" (always yes), but "*does the user message describe a request that can be served by one of the listed tools?*". This is the right learning target.

V2 training entropy is 0.007–0.011 (3–10× v1), and per-token accuracy 0.997–0.999 — still a near-fit on the augmented pool, but with the diversity needed to distinguish callable from abstain. The output-token total dropped only 10% (vs 88% for v1), confirming the empty-plan output is no longer dominant.

The buggy V1 setting is preserved as `--allow-empty-mode irrelevance-only` and the V1 adapter is preserved (artifact gitignored, train_metrics.json + summary + cases committed) so the collapse is reproducible.

## Failure mode distribution (V2)

Of the 133 V2 failures:

| Count | Error type | Direction for S2a+/S2c'/S3' |
| ---: | --- | --- |
| 45 | `parallel_function_checker_no_order:cannot_find_match` | Right call count, wrong argument values — DPO graded reward |
| 32 | `parallel_function_checker_no_order:wrong_count` | Residual call-count miscounts in `parallel*` | self-bootstrap can target these |
| 16 | `value_error:string` | String standardisation — post-correction port (S2a+) |
| 13 | `irrelevance:unexpected_call` | False-callable on irrelevance — more irrelevance pool or DPO |
| 6 | `simple_function_checker:wrong_count` | Bootstrap |
| 6 | `multiple_function_checker:wrong_count` | Bootstrap |
| 5 | `simple_function_checker:missing_optional` | `defaults_when_missing` port |
| 3 | `value_error:others` | DPO graded reward |
| 4 | `*_function_checker_no_order:cannot_find_match` (single-call) | Argument-value DPO |
| 3 | Other | — |

**Most actionable next stages:**
1. S2a+ (post-correction) — port `_standardize_string` from `ganglion/bfcl/grader.py` into `Catalog.parse_json_dsl` so SFT-emit gets canonicalised at parse time. Targets the 16 `value_error:string` failures directly. Predicted lift: +1–3pp.
2. S2c' (self-bootstrap) — sample V2 on the augmented pool, keep AST-passing emissions, retrain. Should especially help the 38 `wrong_count` residuals in `parallel*`. Predicted lift: +2–4pp.
3. S3' (DPO with verifier-graded reward) — final layer for argument-value selection (the 45 `cannot_find_match` failures in `parallel*`). Predicted lift: +2–4pp.

If the predicted lifts compose linearly, V2 (73.4%) → V5 (~80%) reaches the arc objective (flash parity). The plus-parity soft target (86.2%) is +13pp above V2 and likely requires either a larger base or a richer DPO loop.

## Artifacts

```
examples/bfcl/v4/train/
  sft_pool.jsonl, sft_pool_stats.json           # V1 pool (irrelevance-only allow_empty)
  sft_pool_v2.jsonl, sft_pool_v2_stats.json     # V2 pool (uniform allow_empty=True)
runs/factory_phase2/sft_0.6B_bfcl/
  v1/train.log, train_metrics.json               # V1 train (collapse)
  v2/train.log, train_metrics.json               # V2 train (fixed)
  v{1,2}/adapter/                                # gitignored binaries
runs/bfcl/
  sft_v1_0.6b_summary.json, sft_v1_0.6b_cases.jsonl
  sft_v2_0.6b_summary.json, sft_v2_0.6b_cases.jsonl
  baseline_0.6b_untuned_summary.json, baseline_0.6b_untuned_cases.jsonl
runs/factory_phase2/
  build_sft_pool_bfcl.py, train_sft_bfcl.py
```

## Reproducing V2

```bash
# Build V2 pool (uniform allow_empty_calls=True)
python runs/factory_phase2/build_sft_pool_bfcl.py \
    --out examples/bfcl/v4/train/sft_pool_v2.jsonl

# Train V2 (~10 min on RTX 4090)
python runs/factory_phase2/train_sft_bfcl.py \
    --pool examples/bfcl/v4/train/sft_pool_v2.jsonl \
    --out  runs/factory_phase2/sft_0.6B_bfcl/v2

# Evaluate on the 500-case BFCL subsample
python -m ganglion.eval.runner \
    --bfcl all --bfcl-per-category 100 \
    --llm bfcl-0.6b-lora \
    --adapter runs/factory_phase2/sft_0.6B_bfcl/v2/adapter \
    --bfcl-allow-empty-calls \
    --bfcl-output runs/bfcl/sft_v2_0.6b_cases.jsonl \
    > runs/bfcl/sft_v2_0.6b_summary.json
```
