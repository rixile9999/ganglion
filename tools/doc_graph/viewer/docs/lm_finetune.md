[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# lm_finetune

LoRA SFT + DPO graded-reward training of a base LLM against a [[contract_catalog]] `Catalog`. Produces a PEFT adapter that the [[lm_client]] loads at inference time. **Load-bearing invariant:** the training prompt template MUST be byte-identical to the inference prompt template emitted by [[lm_prompts]]. A whitespace-level divergence breaks adapter transfer; the build aborts on detected diff.

## Role

Train a LoRA adapter (and optionally DPO-refine it) against a fixed `Catalog`, consuming the synth output from [[lm_data_synth]] and emitting a PEFT-loadable adapter directory plus a `lm.finetune.completed` event.

## Scope

- **in-scope**:
  - `ganglion/lm/finetune/config.py` — `TrainConfig` dataclass:
    `base_model`, `lora_rank` (default 32), `lora_alpha` (64), `lora_dropout` (0.05),
    `lora_target` ("all-linear"), `epochs` (3), `per_device_batch_size` (4),
    `gradient_accumulation_steps` (2), `learning_rate` (2e-4), `warmup_ratio` (0.05),
    `lr_scheduler_type` ("cosine"), `bf16` (True), `gradient_checkpointing` (True),
    `max_seq_length` (1024), `seed` (42), `logging_steps` (5).
  - `ganglion/lm/finetune/data.py` — `build_messages(catalog, example) -> list[dict]`
    returning the `[system, user, assistant]` triplet. The system content is
    `catalog.render_json_dsl()` wrapped by the SAME template as [[lm_prompts]].
    `_dataset_from_examples` constructs an HF `Dataset` with a single `messages` column.
  - `ganglion/lm/finetune/sft.py` — TRL `SFTTrainer` driver:
    `assistant_only_loss=True` (system + user are masked out of the loss),
    `packing=False` (preserve per-row prompt boundaries), `save_only_model=True`,
    `save_strategy="epoch"`, `save_total_limit=1`. Adapter persisted to
    `<output_dir>/adapter/` with `adapter_config.json` + safetensors (PEFT-compatible,
    loadable via `peft.PeftModel.from_pretrained`).
  - `ganglion/lm/finetune/dpo.py` — TRL `DPOTrainer` driver. `β=0.1`, `lr=5e-7`,
    `1–2 epochs`. Pair construction: per train prompt, sample N=4 at T=0.7 from the
    SFT'd adapter; `chosen` = best by `(ast_match=True > action_match > parse_strategy=strict)`;
    `rejected` = worst (parse failures preferred). Skip prompts with 0/4 or 4/4 passes
    — no learnable signal. Refined adapter persisted to `<output_dir>/adapter_dpo/`.
  - **Per-category training** (default; matches `factory_bfcl_phase3` Phase 3 layout).
    One adapter per `Catalog`; the orchestrator iterates externally.
  - **Train/inference parity check.** `sft.py` renders both templates at startup
    and asserts byte-equality of the system message produced by
    `ganglion.lm.finetune.data.build_messages` vs `ganglion.lm.prompts.render_dsl_messages`.
    On mismatch, raise `TrainPromptParityError` BEFORE any tokenizer/model load.
- **out-of-scope**:
  - GRPO / PPO / online RL — defer; DPO is the only post-SFT optimiser in this task.
  - Base-model selection (0.5B vs 1.7B vs 4B tradeoff) — separate decision; this
    task accepts `cfg.base_model` as input and does not justify it.
  - Inference-time decoding (greedy / temperature / grammar masking) — see
    [[lm_client]] and [[lm_grammar_mask]].
  - Data synthesis — see [[lm_data_synth]]; this task consumes a finalised train JSONL.
  - Cross-category transfer / joint multi-category SFT — explicitly deferred. One
    adapter per `Catalog`.
  - RLHF reward modelling — the reward signal is the deterministic Catalog-bound
    grade from [[analyzer_verifier]], not a learned reward model.
  - Quantisation-aware training (QLoRA-int4, GPTQ, AWQ) — defer.
  - Hyperparameter search / Bayesian sweeps — `TrainConfig` defaults are pinned.
- **on violation**:
  - If `build_messages` system content drifts from `render_dsl_messages` by even
    one byte, raise `TrainPromptParityError` and abort BEFORE the trainer
    instantiates the model. Do NOT silently retemplate.
  - If a request to train jointly across catalogs arrives, stop and branch to a
    follow-up `lm_joint_finetune` task — do not handle inline.

## Procedure

```
trigger: lm.synth.completed(synth_path, catalog_id) OR explicit invocation
input:   catalog, TrainConfig cfg, synth_jsonl, optional eval_jsonl

1. load synth → list[SynthExample]
2. assert byte-equal(build_messages(catalog, _probe).system,
                     render_dsl_messages(catalog, _probe).system)
   on mismatch → raise TrainPromptParityError
3. train_ds ← HF Dataset.from_list([{messages: build_messages(c, ex)} for ex in synth])
4. load base model + tokenizer (cfg.base_model, BF16, device_map=auto)
   apply gradient_checkpointing if cfg.gradient_checkpointing
5. lora_config ← LoraConfig(r=cfg.lora_rank, α=cfg.lora_alpha,
                            dropout=cfg.lora_dropout,
                            target_modules=cfg.lora_target, bias="none",
                            task_type="CAUSAL_LM")
6. trainer ← SFTTrainer(model, SFTConfig(..., assistant_only_loss=True,
                                          packing=False), train_ds,
                         processing_class=tokenizer, peft_config=lora_config)
7. trainer.train() → save_pretrained(output_dir/adapter)
                   → write train_metrics.json
8. (optional) DPO refinement:
   8a. for prompt in train_prompts:
         samples ← sample_n=4 from sft_adapter at T=0.7
         if 0 < pass_count < 4:
            pairs.append((prompt, best(samples), worst(samples)))
   8b. DPOTrainer(sft_adapter, β=0.1, lr=5e-7, 1–2 ep) on pairs
   8c. save_pretrained(output_dir/adapter_dpo)
9. evaluate adapter (and adapter_dpo if present) on eval_jsonl via
   [[analyzer_verifier]] → eval_summary
10. emit lm.finetune.completed(adapter_id, base_model, catalog_id,
                               train_size, eval_summary)

on GPU OOM:
    drop per_device_batch_size to 1, gradient_accumulation_steps to 8, retry once;
    record the downgrade in train_metrics.json.
on HF model download failure:
    fail loud — no fallback model swap.
on DPO loss diverges (loss_step > 5 × loss_init for ≥10 steps):
    halt DPO, keep SFT adapter as final, log divergence_step.
```

## Contract

- **in**:
  - `Catalog` from [[contract_catalog]] (Module 3) — defines the system prompt and the validator.
  - `TrainConfig` — frozen dataclass; defaults pinned, callers may override.
  - Train JSONL — output of [[lm_data_synth]] at the `synth_path` carried by
    `lm.synth.completed`.
  - Optional held-out eval JSONL (same row shape).
- **out**:
  - `<output_dir>/adapter/` — PEFT adapter directory (`adapter_config.json` +
    safetensors), loadable via `peft.PeftModel.from_pretrained()`.
  - `<output_dir>/adapter_dpo/` — present only if DPO ran to completion.
  - `<output_dir>/train_metrics.json` — TRL metrics dump (`train_loss`,
    `train_runtime`, hyperparameters, OOM downgrade if any).
  - `<output_dir>/eval_summary.json` — `exact_match_rate`, `action_match_rate`,
    `parse_strategy_counts` for `base | sft | dpo` columns when applicable.
- **event**:
  - consume: `lm.synth.completed(synth_path, catalog_id)` (input data source).
  - emit: `lm.finetune.completed(adapter_id, base_model, catalog_id, train_size, eval_summary)`
    on success; one event per training run.
- **failure**:
  - GPU OOM at default batch → drop `bs=1` / `grad_accum=8`, retry once; record.
    Second OOM → fail loud with `cuda_oom_persistent`.
  - HF model download / auth failure → fail loud, no silent fallback model.
  - DPO loss divergence → halt at SFT v2 adapter; log `dpo_divergence_step`.
  - Empty train set → `ValueError("no examples to train on")` before model load.
  - Parity check mismatch → `TrainPromptParityError` with a unified diff of the
    two rendered system messages.
- **success**:
  - `<output_dir>/adapter/` exists and `peft.PeftModel.from_pretrained` returns
    a model without error.
  - `eval_summary.json` exists, `exact_match_rate ∈ [0, 1]`, and
    `sft_uplift = sft.exact_match_rate − base.exact_match_rate > 0` on the
    held-out eval (recorded margin; gate enforced by [[analyzer_metrics]]).
  - Exactly one `lm.finetune.completed` event emitted per invocation.

## Observation

- `train_loss_final` = last logged `train_loss` from TRL metrics.
- `eval_loss_final` = last logged `eval_loss` (if eval JSONL provided).
- `adapter_size_mb` = on-disk size of `<output_dir>/adapter/` in MB.
- `train_wall_minutes` = `train_runtime / 60` from TRL metrics.
- `sft_uplift[catalog]` = `sft.exact_match_rate − base.exact_match_rate` on eval.
- `dpo_uplift[catalog]` = `dpo.exact_match_rate − sft.exact_match_rate` on eval
  (omitted if DPO did not run).
- `oom_downgrade_count` = number of times the OOM fallback was triggered (0 in
  the steady state).
- `prompt_parity_diff_bytes` = byte distance between train and inference system
  prompts (always 0 on success; nonzero values surface in the abort log only).

## Status

Spec only. Target module path `ganglion/lm/finetune/{config,data,sft,dpo}.py` does
not yet exist; the live ancestor lives at `ganglion/factory/customer/train_lora.py`
and `runs/factory_phase2/dpo_train.py`, which this task abstracts into Module 1 of
the factory. The migration lands together with [[lm_client]] and [[lm_prompts]] so
that the parity invariant has both sides of the equation under the new layout.

Related: [[contract_catalog]], [[lm_client]], [[lm_data_synth]], [[lm_prompts]], [[lm_grammar_mask]], [[analyzer_verifier]], [[analyzer_metrics]].
