# Ganglion Factory — Phase 2 Plan & Resume Guide

> Self-contained handoff document. Read this if you (or a future agent) are
> picking up the factory work on a fresh machine. All commands assume the
> repo at `reflex-language-model/` with branch `feature/factory-phase1`
> already checked out.

---

## 0. Where Phase 1 left off

Read `docs/factory_phase1_report.md` first for the full context. Short version:

- **Working pipeline** at `ganglion/factory/customer/{ingest,verifier,synth,train_lora,eval}.py` plus `ganglion/factory/grammar/catalog_to_xgrammar.py` and `ganglion/factory/prompts/synth_templates.py`.
- **Two catalogs validated**: `iot_light_5` (93.8% on dataset.jsonl 500 queries) and `smart_home_50` (87.4%).
- **Smoke scripts** at `runs/factory_phase1/{smoke_synth,smoke_train,smoke_train_eval,eval_dataset_jsonl,smoke_inference_only}.py`.
- **Tests**: 144 pass (45 factory + 99 pre-existing).
- **Trained adapters are gitignored** (~144 MB each); they regenerate from synth.jsonl using smoke scripts in 1–2 minutes.

**Branch**: `feature/factory-phase1`. Not yet merged into main.

---

## 1. Environment reproducibility

### Hardware
- GPU with ≥24 GB VRAM (RTX 4090 / A10G / A6000 verified)
- ~10 GB free disk for HF cache (Qwen3-1.7B is 3.4 GB, plus tokenizer + LoRA artifacts)

### Software (versions verified on Phase 1 run)
- Python 3.11+ (Phase 1 ran on 3.13)
- CUDA 12+ driver
- Key Python deps from `pyproject.toml`: transformers 5.8.0, peft 0.19.1, trl 1.3.0, torch 2.11.0, accelerate 1.13.0, datasets 4.8.5, sentence-transformers 5.4.1, openai 1.x

### Required env vars
- `DASHSCOPE_API_KEY` — for synth via qwen3.6-plus
- (optional) `HF_TOKEN` — silences HuggingFace anonymous-rate-limit warnings
- (optional) `GANGLION_MODEL`, `DASHSCOPE_BASE_URL` — override defaults

### Setup commands

```bash
git clone <repo> reflex-language-model
cd reflex-language-model
git checkout feature/factory-phase1

python -m venv .venv && source .venv/bin/activate    # or use conda/uv
pip install -e ".[dev,factory]"                       # core + training stack
pip install -e ".[factory-gpu]"                       # bitsandbytes (optional)

# Verify
python -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
python -c "import os; assert os.environ['DASHSCOPE_API_KEY']; print('key set')"
python -m pytest tests/factory/ -q                    # expect 45 passed
```

### Regenerating Phase 1 artifacts (if you don't trust the JSONLs in the repo)

```bash
# 1. Synth (~7 min, $0.08)
python runs/factory_phase1/smoke_synth.py \
    --catalog iot_light_5 --n 200 --max-cost 1.00 --temperature 0.92

# 2. Train+holdout-eval (~2 min)
python runs/factory_phase1/smoke_train_eval.py \
    --catalog iot_light_5 \
    --synth runs/factory_phase1/iot_light_5/synth.jsonl

# 3. Dataset.jsonl eval (~13 min)
python runs/factory_phase1/eval_dataset_jsonl.py \
    --catalog iot_light_5 \
    --adapter runs/factory_phase1/iot_light_5/holdout_eval/adapter \
    --dataset examples/iot_light/dataset.jsonl \
    --out runs/factory_phase1/iot_light_5/dataset_eval

# Repeat for smart_home_50 with --max-seq-length 2048 on step 2
```

---

## 2. Phase 2 scope

Phase 1 validated the *thesis*. Phase 2 hardens the *product*. Two independent tracks:

### Track A — Pipeline cosmetics + reliability (1 week)

Goal: `ganglion-factory train --schema X --out Y` is a single command, output is a customer-deployable bundle, syntax errors at inference are eliminated.

| Task | Deliverable | Risk |
|---|---|---|
| **A1**. Implement `factory/customer/pack.py` | `lora/` + `catalog.yaml` + `grammar.json` + `eval_report.{md,json}` + `serving.yaml` in one bundle dir, with a stable manifest hash | low |
| **A2**. Implement `factory/cli.py` + console script | `ganglion-factory train --schema iot_light_5 --out ./bundle` runs ingest→synth→train→eval→pack end-to-end | low |
| **A3**. Inference-time XGrammar | Wire `factory/grammar/catalog_to_xgrammar.py` into the inference path so syntax_valid → 100% | medium — XGrammar + transformers integration may need adapter; dependency missing from pyproject |
| **A4**. Multi-seed acceptance runs | 3-seed runs on iot_light_5 + smart_home_50 with averaged + stddev metrics; tighten CIs on the headline numbers | low |
| **A5**. Regression test of Day-5b results | CI-style script that reproduces 92.3% / 85.9% within ±3pp on a single GPU | low |

### Track B — Training quality (2–4 weeks)

Goal: close the visible gaps from Phase 1 (create_scene 60%, Korean alias misses, smart_home_50 confusion clusters).

| Task | Deliverable | Risk |
|---|---|---|
| **B1**. GRPO loop on top of SFT | Verifier-driven RL run; A/B against SFT-only on dataset.jsonl | medium — TRL 1.3 GRPOTrainer API is recent; reward shaping matters |
| **B2**. Multi-tool synth strategy | `prompts/synth_templates.py` + `factory/customer/synth.py` extended for compound intents (calls 2-3 tools) | low |
| **B3**. Adversarial + abstain synth | Add intent classes that should clarify/abstain rather than call | medium — needs `Catalog.allow_empty_calls` story |
| **B4**. Customer-examples-as-bias | If customer provides real queries, bias synth distribution toward them | low |
| **B5**. Larger n_target sweeps | Test 5k / 20k synth pairs vs 500. Find the data-saturation point | low — just costs API time |
| **B6**. Try Qwen3-0.5B and Qwen3-4B | Same recipe, different bases; map size–quality tradeoff | medium — 4B may need lower LoRA rank or grad-accum bump |

### Track C — Production readiness (4-6 weeks)

| Task | Deliverable |
|---|---|
| **C1**. INT4 quantization (AWQ) | Ship-ready ~150 MB INT4 adapter |
| **C2**. vLLM + multi-LoRA serving | Single base + N customer LoRAs hot-swappable on one endpoint |
| **C3**. Real public MCP server | Pick a real third-party MCP (e.g., GitHub MCP) and run the factory end-to-end; first "external customer" demo |
| **C4**. Verifier hardening / co-pilot | Detect reward-hackable verifiers; auto-suggest fixes |

---

## 3. Phase 2 acceptance gates

A successful Phase 2 demonstrates:

- [ ] `ganglion-factory train --schema X --out Y` works in one command (Track A)
- [ ] Syntax_valid_rate = 100% under constrained decoding (Track A)
- [ ] dataset.jsonl exact_match ≥ 95% on iot_light_5 with 3-seed mean (Track B, post-GRPO)
- [ ] dataset.jsonl exact_match ≥ 90% on smart_home_50 with 3-seed mean (Track B)
- [ ] Per-tool exact ≥ 80% for *every* tool (no <50% holes) (Track B)
- [ ] Real third-party MCP run produces a deployable bundle (Track C)

---

## 4. Open decisions Phase 2 must close

These are unresolved from Phase 1; pick a position before coding:

1. **Constrained decoding mode**: train with masking, or train without and apply only at inference? Phase 1 chose the latter; Phase 2 should benchmark both and decide.
2. **GRPO group size and KL coefficient**: untested. Start with group=8, KL=0.04 (DeepSeek-R1 defaults) and iterate.
3. **Default base for the factory**: Qwen3-1.7B was the Phase 1 default. If 0.5B can hit ≥85% with the same recipe, that becomes the entry-tier offering. Run B6 early.
4. **What counts as a "customer schema"**: do we accept raw OpenAPI specs directly, or require pre-conversion to a Catalog? Phase 1's `ingest.py` accepts both; Phase 2 should pick a primary supported shape and document.
5. **Pricing model for production**: charge per training run, per LoRA hosted, per inference call? Architectural choices (multi-LoRA serving cost) follow from this.

---

## 5. Known traps / things that bit us in Phase 1

Before re-running anything, be aware:

- **`apply_chat_template` returns BatchEncoding, not Tensor**, on transformers 5.x with `return_dict=True`. Pass `input_ids` explicitly to `model.generate`. Already fixed in `train_lora.py:generate_dsl`.
- **`max_seq_length=1024` overflows for catalogs >2k chars** (e.g., smart_home_50 at 4670 chars ≈ 1300 tok). Pass `--max-seq-length 2048` or higher.
- **`assistant_only_loss=True` in TRL 1.3 SFTConfig** is essential — without it the model wastes capacity learning the catalog system prompt.
- **Diversity gate at 70% was too tight** for small catalogs with this teacher. 60% is the empirical baseline; values above 0.9 cosine similarity collapse identical paraphrases.
- **`create_scene` (RawArg, nested calls)** required injecting a concrete in-prompt example to teach the teacher the nested shape. See `synth_templates._raw_arg_example`.
- **HuggingFace anonymous downloads** silently rate-limit. Set `HF_TOKEN` for stable downloads.
- **`runs/factory_phase1/.gitignore`** excludes adapter/ and trainer/ dirs. If you commit new run outputs, make sure binary checkpoints stay out of git.
- **`DASHSCOPE_API_KEY` in env**: the synth pipeline raises immediately on missing key; verify before launching long jobs.

---

## 6. Resume checklist (if picking up cold)

If you walk into this fresh:

1. `git checkout feature/factory-phase1`
2. Run §1 setup commands. Confirm `pytest tests/factory/ -q` passes (45/45).
3. Read `docs/factory_phase1_report.md`. Skim §5 *Findings* and §7 *Open issues*.
4. Decide which Phase 2 track to start. **Recommended**: Track A first (cosmetics, low risk, gives you a clean CLI), then Track B (real quality work).
5. Pick a single Track A task — A2 (cli.py) is the highest-leverage start because every other task benefits from one-command operation.
6. Before any new training run, regenerate Phase 1 results once on the new machine to confirm reproducibility.

---

## 7. Useful one-liners

```bash
# Quick sanity: does the existing iot_light_5 LoRA still parse?
python runs/factory_phase1/smoke_inference_only.py

# Full Phase 1 reproduction (takes ~25 min, costs ~$0.16)
python runs/factory_phase1/smoke_synth.py --catalog smart_home_50 --n 500 --max-cost 1.00 \
  && python runs/factory_phase1/smoke_train_eval.py \
       --catalog smart_home_50 \
       --synth runs/factory_phase1/smart_home_50/synth.jsonl \
       --max-seq-length 2048 \
  && python runs/factory_phase1/eval_dataset_jsonl.py \
       --catalog smart_home_50 \
       --adapter runs/factory_phase1/smart_home_50/holdout_eval/adapter \
       --dataset examples/iot_light/dataset.jsonl \
       --out runs/factory_phase1/smart_home_50/dataset_eval

# Inspect a sample of synthesized data
head -3 runs/factory_phase1/iot_light_5/synth.jsonl | python -m json.tool

# Watch a long-running smoke
tail -f /tmp/smoke_train_eval.log

# Diagnose a specific failure
grep -A3 "Failures" runs/factory_phase1/smart_home_50/dataset_eval/eval_report.md
```

---

## 8. Files of interest, in dependency order

```
ganglion/dsl/catalog.py                  # Catalog IR (Phase 0 — DO NOT MODIFY without coordination)
ganglion/dsl/compiler.py                 # external schema → ToolSpec
ganglion/factory/grammar/catalog_to_xgrammar.py
ganglion/factory/customer/ingest.py
ganglion/factory/customer/verifier.py
ganglion/factory/prompts/synth_templates.py
ganglion/factory/customer/synth.py
ganglion/factory/customer/train_lora.py
ganglion/factory/customer/eval.py
runs/factory_phase1/smoke_*.py           # smoke runners
runs/factory_phase1/eval_dataset_jsonl.py

docs/factory_phase1_report.md            # final acceptance numbers
docs/factory_phase2_plan.md              # this file
docs/research_vision_for_review.md       # full project context for external reviewers
```

---

## 9. Single-line summary

> **Phase 2 = "make the Phase 1 pipeline shippable" (Track A, 1 week) + "close the quality gaps with GRPO and broader synth" (Track B, 2–4 weeks). Track C makes it a real product. Each track is independently scoped — pick whichever risk is highest priority for your context.**
