# factory_bfcl — Phase 1+2 0.6B optimization on BFCL v4

## Role
Replays the Qwen3-0.6B Phase 1 + Phase 2 optimization pipeline (baseline → repair → grammar masking → per-category SFT → post-correction → self-bootstrap/DPO) on the BFCL v4 single-turn benchmark, producing per-category metrics comparable to the IoT-light/smart-home phase-2 numbers.

## Scope
- in-scope:
  - `runs/factory_bfcl/phase1/{baseline,repair}/<category>_{dsl,native}_{summary.json,cases.jsonl,log}` — Phase 1 outputs.
  - `runs/factory_bfcl/phase2/grammar/` — grammar-mask on/off ablation, per category.
  - `runs/factory_bfcl/phase2/sft/<category>/{train.jsonl,holdout.jsonl,adapter/,eval_report.{json,md}}` — five per-category LoRA adapters (one per BFCL category) over an 80/20 train/holdout split of the deterministic 100-case sample.
  - `runs/factory_bfcl/phase2/post_corr/<category>_summary.json` — deterministic post-correction uplift (BFCL-shape `defaults_when_missing` analogue).
  - `runs/factory_bfcl/phase2/bootstrap_dpo/` — optional self-bootstrap (N=4, T=0.7) and DPO graded-reward stage.
  - `docs/factory_bfcl_report.md` — final report (stage × category table, comparison with `docs/factory_phase2_plan.md` §12).
- out-of-scope:
  - BFCL multi-turn categories. v4 single-turn only.
  - `tests/`, `ganglion/dsl/`, `ganglion/bfcl/` source code. The schema compiler + grader are already validated by `tests/test_bfcl_smoke.py` and the M1'~M5' reports; this task consumes them, it does not change them.
  - `qwen3.6-plus` / `qwen3.6-flash` configurations. This task is 0.6B only.
  - IoT tiers (`iot_light_5`, `home_iot_20`, `smart_home_50`). Already covered by Phase 2 on dataset.jsonl.
  - Modifying `examples/bfcl/v4/sample/*.jsonl`. Read-only SSOT.
- on violation:
  - If grammar masking requires changes to `ganglion/factory/grammar/`, branch a separate task; do not patch in-line.
  - If BFCL grader produces ambiguous failures, escalate to a `docs/tasks/` follow-up rather than soft-fixing inside this task.

## Procedure
trigger: user invocation (one-shot research run, not scheduled).
  1. Phase 1 baseline. `GANGLION_MODEL=qwen3-0.6b` × `--bfcl {simple_python,multiple,parallel,parallel_multiple,irrelevance}` × {DSL, native (4 callable cats only)} × `--bfcl-per-category 100`. Write summary+cases under `phase1/baseline/`.
  2. Phase 1 repair. Same DSL grid + `--repair --repair-max-attempts 1`. Write under `phase1/repair/`.
  3. Phase 2 grammar masking. Local `Qwen/Qwen3-0.6B` (HF) on RTX 4080 with xgrammar mask {on, off}. Output `phase2/grammar/<category>_{mask_on,mask_off}_summary.json`.
  4. Phase 2 SFT per category. For each BFCL category build a deterministic 80-train / 20-holdout split (seed=42, in-category split — not cross-category). Train `Qwen/Qwen3-0.6B` LoRA r=32, lr=2e-4, 3 epochs, max_seq=2048 (BFCL prompts longer than IoT). Eval each adapter on its own holdout AND on the full 100-case grader sample (for direct comparison with phase 1 numbers).
  5. Phase 2 post-correction. Inspect failure cases from S2a; design `defaults_when_missing`-style deterministic rules tailored to BFCL shape (e.g. boolean default, empty-list default for optional list args). Apply offline to S2a outputs.
  6. Phase 2 self-bootstrap + DPO (best-effort). With the v1 adapter sample N=4 at T=0.7 per train prompt → grader-passing samples become v2 train rows. Re-train. If wall-clock permits, build DPO chosen/rejected pairs and run one DPO epoch.
on per-category SFT divergence (eval_loss > 1.0 mid-epoch): abort that category, log, continue with remaining categories.
on grammar-mask regression vs. SFT'd adapter (≥3pp drop): record but do not block; matches the Phase 2 finding that mask hurts post-SFT.

## Contract
- in:
  - `examples/bfcl/v4/sample/<category>.jsonl` (read-only).
  - `Qwen/Qwen3-0.6B` (HF Hub, cached locally).
  - `DASHSCOPE_API_KEY` env for Phase 1.
  - `GANGLION_MODEL=qwen3-0.6b` env for Phase 1 (DashScope-served 0.6B).
- out:
  - All artifacts under `runs/factory_bfcl/` listed in `in-scope`.
  - `docs/factory_bfcl_report.md`.
- event:  consume — none (one-shot). emit — none.
- failure:
  - DashScope error → retry up to 3 times with jitter; on persistent failure, mark category failed in summary and continue.
  - GPU OOM in SFT → drop `per_device_batch_size` to 2 with grad-accum 4; if still OOM, log and skip that category's SFT.
  - HF model download failure → fail loud; do not fall back to a different base.
- success: `runs/factory_bfcl/phase1/baseline/<cat>_dsl_summary.json` exists and parses as JSON for all 5 categories; `runs/factory_bfcl/phase2/sft/<cat>/eval_report.json` exists for all 5 categories OR has a recorded skip reason; `docs/factory_bfcl_report.md` exists and contains a comparison table.

## Observation
- `bfcl_baseline_exact_match[cat]` = `phase1/baseline/<cat>_dsl_summary.json:exact_match_rate`.
- `bfcl_repair_uplift[cat]` = repair − baseline exact_match (DSL).
- `bfcl_mask_uplift[cat]` = mask_on − mask_off exact_match (Phase 2 §S1b analogue).
- `bfcl_sft_uplift[cat]` = sft_v1 − repair exact_match.
- `bfcl_post_corr_uplift[cat]` = post_corr − sft_v1 exact_match.
- Failure decomposition per stage: `syntax_invalid_rate`, `action_match_rate − exact_match_rate` (right tool, wrong args), `empty_calls_rate` (irrelevance only).

Related: [[factory_phase2_plan]], [[external_benchmark_bfcl]], [[null_action_contract]], [[tool_schema_compiler]].
