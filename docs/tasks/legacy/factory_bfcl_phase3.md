# factory_bfcl_phase3 — data augmentation + DPO graded reward

## Role
Lifts Phase 2 SFT (sft_0.6B_bfcl_<cat> v1) from the 80-case-per-category
ceiling by (a) paraphrase + teacher-synthesized augmentation, (b) self-bootstrap
on v1, (c) DPO graded-reward training on v2. Target: macro AST 0.872 → ≥0.92
on full eval, ≥0.75 on holdout.

## Scope
- in-scope:
  - `runs/factory_bfcl/phase3/paraphrase/<cat>/{paraphrased.jsonl,stats.json}` —
    K=4 paraphrased intents per train case via DashScope teacher (qwen3.6-plus).
  - `runs/factory_bfcl/phase3/synth/<cat>/{synth.jsonl,stats.json}` — N≈100 new
    teacher-generated (intent, gt) pairs per category mimicking BFCL shape.
  - `runs/factory_bfcl/phase3/sft_v1_5/<cat>/{adapter,eval_*}` — SFT trained on
    train ∪ paraphrase ∪ synth.
  - `runs/factory_bfcl/phase3/bootstrap/<cat>/{augmented_train.jsonl,stats.json}` —
    bfcl_bootstrap.py output using sft_v1_5.
  - `runs/factory_bfcl/phase3/sft_v2/<cat>/{adapter,eval_*}` — SFT on
    train ∪ paraphrase ∪ synth ∪ bootstrap-pass.
  - `runs/factory_bfcl/phase3/dpo_pairs/<cat>/{pairs.jsonl,stats.json}` —
    (chosen, rejected) pairs per prompt from sft_v2 sampled N=4 at T=0.7.
  - `runs/factory_bfcl/phase3/dpo/<cat>/{adapter,eval_*}` — DPO-trained adapter.
  - `runs/factory_bfcl/phase3_report.md` — stage × category × ast/syntax matrix
    with absolute deltas vs Phase 2 baselines.
- out-of-scope:
  - BFCL v4 multi-turn (still single-turn only).
  - GRPO / PPO. DPO only — if DPO plateaus, do not extend into RL within this task.
  - Cross-category transfer training (joint multi-category SFT). Each category
    stays independent per the original "two directions" decision.
  - Replacing the Qwen3-0.6B base. Same base; only LoRA weights change.
- on violation:
  - If teacher-synthesized cases drift outside BFCL grader semantics (gt
    accept-lists malformed), branch a separate cleanup task — do not patch
    inline.

## Procedure
trigger: user invocation (one-shot).

**S3a — paraphrase augmentation** (≈45 min wall)
  1. For each (case_id, user_message) in `phase2/sft/<cat>/train.jsonl` whose
     expected_dsl parses cleanly:
     1a. Send a paraphrasing prompt to DashScope qwen3.6-plus asking for K=4
         variant intents preserving the same tool call. Temperature 0.7.
     1b. Pair each variant with the original `ground_truth`. Validate the
         expected_dsl re-parses against the per-case Catalog.
  2. Persist as `paraphrased.jsonl`. Carry a `origin: "paraphrase"` field.
  3. Stats: success count vs filter-out (validation failures).

**S3b — teacher-synthesized cases** (≈55 min wall)
  1. For each category, sample 25 train cases. For each, prompt qwen3.6-plus to
     generate **2 brand-new** (intent, gt) pairs that:
     - Use the SAME `function` schema (per-case tool list).
     - Have DIFFERENT semantic intent (not paraphrase of original).
     - Produce a parseable BFCL ground_truth.
  2. Validate via `Catalog.parse_json_dsl` against the per-case Catalog.
  3. Persist as `synth.jsonl` with `origin: "synth"`.

**S3c — SFT v1.5**
  1. Concatenate `train ∪ paraphrase ∪ synth` (deduplicated by case_id+intent).
  2. Train Qwen3-0.6B + LoRA r=32, lr=2e-4, 3 epoch, BF16. Same recipe as v1.
  3. Evaluate adapter on holdout 20 AND full 100 (Phase 2 parity).

**S3d — self-bootstrap** (`bfcl_bootstrap.py`, already implemented)
  1. With v1.5 adapter, for each train case sample N=4 at T=0.7.
  2. Keep grader-passing samples → `augmented_train.jsonl`.
  3. Concatenate `train ∪ paraphrase ∪ synth ∪ bootstrap-pass`.

**S3e — SFT v2**
  1. Train on the concatenated set. Same recipe.
  2. Evaluate on holdout 20 + full 100.

**S3f — DPO pair construction**
  1. Sample N=4 from sft_v2 at T=0.7 per train prompt.
  2. For each prompt: chosen = sample with best (ast_match=True > action_match
     > parse_strategy=strict). rejected = sample with worst (lowest action_match,
     prefer parse failures).
  3. Skip prompts where 0 or 4 of 4 samples pass — no learnable signal.

**S3g — DPO training**
  1. TRL `DPOTrainer` on the v2 base + LoRA adapter. β=0.1, 1-2 epoch, lr=5e-7.
  2. Evaluate DPO-merged adapter on holdout 20 + full 100.
  3. Re-apply post-correction (S2c rules) on top.

on per-stage failure: log, skip downstream stages for that category, continue.

## Contract
- in:
  - `examples/bfcl/v4/sample/<cat>.jsonl` (SSOT, read-only).
  - `runs/factory_bfcl/phase2/sft/<cat>/` (v1 adapter + train.jsonl/holdout.jsonl from Phase 2).
  - `DASHSCOPE_API_KEY` (qwen3.6-plus teacher for paraphrase + synth).
- out:
  - All artifacts under `runs/factory_bfcl/phase3/` listed in `in-scope`.
  - `docs/factory_bfcl_phase3_report.md`.
- event:  consume — none. emit — none.
- failure:
  - Teacher response unparseable (≥5%) → mark category and continue with smaller K.
  - DPO loss diverges → halt that category at v2, log.
  - GPU OOM in v2 → drop bs to 1 grad-accum 8.
- success:
  - Every category has at least sft_v2 eval_holdout summary written.
  - phase3_report.md table has all rows filled or recorded skip reason.

## Observation
- `phase3_data_yield[cat]` = paraphrase_valid + synth_valid + bootstrap_pass — total added rows.
- `phase3_uplift_v1_5[cat]` = sft_v1_5.holdout − sft_v1.holdout.
- `phase3_uplift_v2[cat]` = sft_v2.holdout − sft_v1_5.holdout.
- `phase3_uplift_dpo[cat]` = dpo.holdout − sft_v2.holdout.
- `holdout_close_full[cat]` = sft_v2.holdout / sft_v2.full — generalization
  gap close (target ≥0.85, Phase 2 was 0.50~0.65 for most cats).

## Estimated cost
- DashScope teacher: ~2000 paraphrase + 250 synth = ~2250 calls × ~$0.0003 ≈ **$0.7**.
- Local GPU wall time: ~3.5 hours total (paraphrase fetch concurrent; SFT v1.5
  + v2 ~15 min each; bootstrap 50 min; DPO ~30 min; eval ~15 min/stage × 3).
- Disk: ≤500 MB more under `runs/factory_bfcl/phase3/`.

Related: [[factory_bfcl]], [[factory_phase2_plan]], [[external_benchmark_bfcl]].
