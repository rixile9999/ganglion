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

---

## 10. Stage 1 measurement results (2026-05-08)

> First quantitative read on what Phase 1's headline number actually buys, and where Phase 2's biggest leverage lies. All runs on `examples/iot_light/dataset.jsonl` (n=500), DashScope-intl, single seed.

### 10.1 Numbers

| Setting | iot_light_5 (5 tools) | smart_home_50 (50 tools) |
|---|---:|---:|
| Untuned qwen3-1.7B (DashScope) | exact 87.4% / syn 93.0% | exact 80.0% / syn 86.8% |
| Phase 1 tuned 1.7B + LoRA | exact **93.8%** / syn 99.4% | exact **87.4%** / syn 93.0% |
| Untuned qwen3-0.6B | exact 38.6% / syn 65.8% | exact 38.2% / syn 65.6% |
| qwen3-0.6B + M4 repair (1 retry) | exact 41.8% / syn 72.2% | exact 40.6% / syn 71.6% |

Cumulative API spend across the 6 baseline runs: **~$0.31**. Artifacts in `runs/factory_phase2/baseline/`.

### 10.2 What this changes about the Phase 1 story

- **Phase 1 LoRA's true delta is +6.4pp (iot_light_5) / +7.4pp (smart_home_50).** The 93.8% headline is *real*, but the untuned 1.7B was already at 87.4% on the same dataset — so the LoRA contribution is roughly an order of magnitude smaller than the 93.8% number suggests in isolation. The acceptance gates remain met; the *value attribution* needs to be honest.
- **The 1.7B → 0.6B drop is a capacity cliff, not a schema-size effect.** Both catalogs land 0.6B at ~38% exact, despite a 10× tool-count gap. The 50-tool catalog at 4,670 chars is *not* what's breaking the small model; raw base capability is. This kills the implicit hope that "0.6B + tighter prompt" was a viable edge tier without LoRA.
- **0.6B failure decomposition**: ~34% syntax-broken (unparseable), and a ~27pp gap between `action_match` and `exact_match` (right tool, wrong args). It picks tools tolerably; it cannot *format*.
- **M4 repair loop helps but is bounded.** Repair only fires on `DSLValidationError` (parsable JSON, schema-violating). On 0.6B it lifts exact ~3pp and syntax ~6pp — useful, but it cannot rescue completely unparseable output, which is the bulk of the 0.6B failure mass.

### 10.3 Implications for Phase 2 priorities

- **Promote A3 (inference-time XGrammar masking) ahead of B1 (GRPO).** Constrained decoding forces `syntax_valid → 100%` by construction, which is the single largest failure category on the small model and a non-trivial slice (6.2%) on the 1.7B + LoRA path too. GRPO pursues a few more pp on the *correct-syntax* tail; XGrammar fixes an entire failure axis. Sequence A3 → B1, not B1 → A3.
- **B6 (Qwen3-0.5B / 4B sweep) needs A3 first.** Without grammar masking the 0.5B branch is dead at 38% and any B6 result is dominated by syntax breakage rather than the recipe-transfer question we actually want to answer.
- The +6.4-7.4pp Phase 1 delta is large enough to keep the LoRA-per-customer thesis alive, but small enough that **Phase 2's burden of proof rises**: GRPO must clear measurable headroom over the *untuned* baseline, not just over Phase 1.

### 10.4 Caveats (not yet measured)

- **Chat-template parity**: all numbers are DashScope-served. We have not yet validated that a HF-local serve of the same weights matches; tokenizer/template drift is a known class of silent regression.
- **Single-seed CIs**: dataset.jsonl at n=500 gives ±2pp binomial CI per run, but we have one seed each. The 6.4pp / 7.4pp deltas are well outside CI; the 0.6B → repair lift (~3pp) is borderline and should be re-run with N≥3 before being load-bearing.
- **No GPU-local validation of the LoRA against these baselines** — Phase 1 numbers were also DashScope-served via the same path, so the comparison is internally consistent, but the absolute numbers are not yet pinned to a reproducible local serve.

### 10.5 Decision for Phase 2 direction (2026-05-08)

**Going for 0.6B max-out (Arc A) as the primary research arc.** A3 (XGrammar inference) is the joint dependency that produces the first decision-relevant data point on both 0.6B and 1.7B; A3 land first, then commit to 0.6B SFT + grammar-masked training + self-bootstrap + DPO/GRPO. 1.7B polish (Arc B) is the fallback if 0.6B plateaus below ~60% post-A3.

A3 implementation status (2026-05-08): module scaffold, generate-time wiring, EvalConfig threading, and unit tests landed in `ganglion/factory/grammar/xgrammar_processor.py` + `tests/factory/test_xgrammar_processor.py` (7 new tests, 52/52 factory tests pass). Awaiting GPU-box validation against actual Qwen3 checkpoints.

---

## 11. Arc A roadmap — 0.6B max-out

> Detailed stage plan committed 2026-05-08. The single thesis under test:
> *can compiler + RL replace ~3× capacity?* Each stage has an explicit
> abort gate; if 0.6B fails to clear the gate, abort Arc A and pivot to
> Arc B (1.7B polish).

### 11.1 Stages

| # | Name | Deliverable | Wall time | Cost | Abort gate (0.6B exact_match) |
|---|---|---|---|---|---|
| **S1b** | A3 inference grammar masking on GPU box | `runs/factory_phase2/grammar_ablation/{0.6B,1.7B-lora}-{iot,smart}/ablation_report.md` | 1 GPU session, ~50 min | GPU only | ≥ 50% to continue; <50% → Arc B |
| **S2a** | 0.6B SFT (Phase 1 recipe, reuse synth.jsonl) | LoRA adapter at `runs/factory_phase2/sft_0.6B/{iot,smart}/adapter` + holdout report | 1-2 hr | GPU only | ≥ 65% post-mask to continue |
| **S2b** | Training-time grammar masking | New: `train_lora.py` accepts `compiled_grammar`; logits masked during SFT generation steps; xgrammar-aware loss path | 5-7 days code | GPU + dev time | ≥ 70% post-mask to continue |
| **S2c** | Self-bootstrap synth | Extend `customer/synth.py` with `--teacher=self` mode using current adapter; one bootstrap iteration, validator-gated | 2-3 days code + 1 GPU session | GPU only | ≥ 73% post-mask to continue |
| **S3** | DPO with verifier-graded preference pairs | New: `customer/dpo.py` building (winner, loser) pairs from sampled outputs, scored by verifier (0/0.5/1) | 4-6 days code + 1 GPU session | GPU only | ≥ 76% post-mask to continue |
| **S3+** | GRPO graded (optional) | Existing TRL GRPOTrainer + verifier reward wiring; group=8, KL=0.04 | 4-7 days code + 1-2 GPU sessions | GPU only | thesis acceptance: ≥ 80% |

### 11.2 What "thesis acceptance" means

A run that clears **80% exact_match on dataset.jsonl with 0.6B + LoRA** would be the headline result: a sub-1B model matching untuned 1.7B (87.4% / 80.0%) tier, with ~4× smaller deployment. Phrased honestly as "compiler + RL closes most of a 3× capacity gap on tool calling".

Below 80%, Arc A still produces useful artifacts:
- **75–80%**: strong "compiler narrows the gap" result, paper-publishable as a partial story.
- **65–75%**: capacity floor partially overcome; "0.6B is workable for narrow domains" product positioning.
- **<65%**: capacity floor confirmed; deliverable is the negative result + recommendation that 1.7B is the practical floor.

### 11.3 Tooling to be built (in priority order)

1. **GPU-box pull + Phase 1 reproducibility check** (S1b prerequisite). Re-run Phase 1 smoke once on the GPU box to confirm adapters reproduce; otherwise S1b's 1.7B+LoRA branch has no comparison baseline.
2. **Multi-seed wrapper** (cuts across all stages). `runs/factory_phase2/sweep.py` — wraps any single-config eval and runs N seeds, emits mean ± stddev. Without this every result has ±2pp noise we can't separate from genuine effect.
3. **Training-time grammar masking** (S2b). Largest new code item. Open question: do we mask only assistant-side generation during validation steps, or also during the generation that produces gradient targets? Decision pending — see §11.5.
4. **Self-bootstrap pipeline** (S2c). Reuses `customer/synth.py` infra; the new piece is "use the trained adapter as teacher" plus deduplication against original synth.jsonl.
5. **DPO loop** (S3). New module. Verifier-graded reward (0/0.5/1) feeds DPO loss with implicit margin via reward gap, not just binary winner/loser.

### 11.4 Estimated timeline (focused work, single contributor)

| Week | Output |
|---|---|
| 1 (this week) | S1b GPU run — Arc A go/no-go decision |
| 2 | S2a (0.6B SFT, Phase 1 recipe) + multi-seed wrapper |
| 3-4 | S2b training-time masking integration |
| 5 | S2c self-bootstrap iteration |
| 6-7 | S3 DPO loop |
| 8 (optional) | S3+ GRPO if S3 plateaus |

Compress to 4-5 weeks if 2-3 stages run in parallel (e.g., S2c development while S2b is training).

### 11.5 Open decisions to lock before each stage

- **Before S2a**: 0.6B LoRA rank — Phase 1 used r=32 on 1.7B. For 0.6B, options are r=32 (more headroom) or r=16 (faster, more efficient). Default: r=32 unless OOM.
- **Before S2b**: training-time mask scope — assistant-only? full-sequence? validation-step-only? Default: assistant-only (mirrors `assistant_only_loss=True`).
- **Before S2c**: self-bootstrap dedup threshold — Phase 1 used 0.95 cosine. Self-generated examples cluster tighter; 0.92-0.93 likely needed. Default: 0.93, revisit on data inspection.
- **Before S3**: DPO β — start at 0.1, sweep {0.05, 0.1, 0.3} if S3 plateaus. Don't skip the sweep; β is the most sensitive DPO hyperparameter.

### 11.6 What this roadmap is NOT

- Not a publication plan (yet). Acceptance numbers are research milestones, not paper milestones.
- Not a multi-customer plan. Single-catalog focus until thesis lands; then re-run on smart_home_50 + a third real-world catalog (e.g., GitHub MCP) before claiming generalization.
- Not Track A productization. pack.py / cli.py / vLLM serving stay deferred until Arc A clears or aborts.

---

## 12. S2a + post-correction results (2026-05-08, end-of-day)

> Stage 2a (0.6B SFT) and the unplanned `defaults_when_missing` post-correction
> rule both landed today on M1 Ultra. This section records final numbers,
> consequent priority shifts, and the Arc-A standing.

### 12.1 Final 0.6B numbers — best config per catalog

| catalog | config | exact | syntax | action | latency p50 |
|---|---|---:|---:|---:|---:|
| iot_light_5 | SFT only (mask off) | 73.4% | 92.0% | 92.0% | 1845 ms |
| iot_light_5 | SFT + mask on | 68.6% | 94.0% | 94.0% | n/a |
| **iot_light_5** | **SFT + post-correction (mask off)** | **77.2%** | **96.6%** | **96.6%** | **1845 ms** |
| smart_home_50 | SFT only (mask off) | 64.0% | 88.2% | 80.8% | 2056 ms |
| smart_home_50 | SFT + mask on | 70.8% | 99.8% | 90.6% | 1929 ms |
| **smart_home_50** | **SFT + post-correction (mask off)** | **71.4%** | **95.8%** | **88.4%** | **2056 ms** |

Headline: **0.6B + Phase 1 SFT recipe + `defaults_when_missing` rule (no inference masking)** wins on both catalogs and beats every prior configuration. 100-example × 3-epoch SFT (171s on iot, 51 min on smart_home with max_seq=2048) suffices.

### 12.2 Two findings that change the §11 priority order

**(a) Inference-time grammar masking is not a uniform win on SFT'd models.**

- Untuned 0.6B: masking adds **+17pp** (huge value, as predicted by A3 thesis).
- SFT'd 0.6B on small catalog (iot, 5 tools): masking adds **−5pp** (regression).
- SFT'd 0.6B on large catalog (smart_home, 50 tools): masking adds **+7pp** (helpful, but post-correction beats it).

The implicit assumption of A3 ("masking always strictly improves syntax_valid → improves exact") fails on SFT'd small-catalog runs because the trained model has already learned the format better than the grammar specifies; masking sometimes biases away from the model's correct token. Catalog-size dependence: more tools → masking still helps clean up confusion clusters that SFT didn't eliminate.

**(b) Deterministic post-correction (`defaults_when_missing`) strictly dominates inference masking on SFT'd models.**

| catalog | best with masking | best with post-correction | Δ in favor of post-correction |
|---|---:|---:|---:|
| iot_light_5 | 73.4% (mask off) | 77.2% | +3.8pp |
| smart_home_50 | 70.8% (mask on) | 71.4% | +0.6pp |

Plus zero inference latency cost. Plus zero risk of grammar-induced semantic regressions. Plus generalizes to *any* tool with declarative defaults.

→ **Inference-time masking demoted from a Phase 2 deliverable to a *diagnostic tool* used only on untuned baselines.** Post-correction layer is now the standard inference-time component.

### 12.3 Updated stage status

| § | Stage | Status as of 2026-05-08 EOD | Note |
|---|---|---|---|
| 11.1 | **S1b** mask ablation | ✅ done (M1 Ultra, no GPU box needed) | A3 thesis partially refuted; post-correction is the right default. |
| 11.1 | **S2a** 0.6B SFT | ✅ done both catalogs | 73.4% / 64.0% mask-off |
| (new) | **S2a+** post-correction layer | ✅ done | 77.2% / 71.4% — current best |
| 11.1 | **S2b** training-time masking | ⛔ **deprioritized** | Inference masking didn't help SFT'd models; training masking likely also distorts. Don't burn a week on it. |
| 11.1 | **S2c** self-bootstrap | 🔜 next | Predicted +3-5pp. iot at 77.2% needs only +2.8 to clear 80% acceptance line. |
| 11.1 | **S3** DPO graded | future | Predicted +3-5pp on top of S2c. Brings smart_home toward 80%. |
| 11.1 | **S3+** GRPO graded | optional | Only if DPO plateaus. |

### 12.4 Distance to thesis acceptance (≥80% exact)

| catalog | current | target | gap | next-step coverage |
|---|---:|---:|---:|---|
| iot_light_5 | 77.2% | 80% | **+2.8pp** | S2c alone likely closes |
| smart_home_50 | 71.4% | 80% | +8.6pp | S2c + S3 likely required |

iot_light_5 is one stage away from the headline result (sub-1B matches untuned 1.7B ≈ 80%). smart_home_50 needs two more stages but the trend line is clean.

### 12.5 Engineering bugs landed today (won't show up again)

- **MPS ffi-tensor bug** in xgrammar 0.2.0's HF LogitsProcessor → workaround in `xgrammar_processor.py` (`.item()` coercion).
- **Phase 1 eval-loop memory leak** hidden by CUDA's caching allocator → `eval.py` now does `gc.collect()` + `empty_cache()` per case. ~10× wall speedup on long-context eval. Optional `GANGLION_EVAL_MEMORY_LOG=1` for RSS tracking.
- **Phase 1 `smoke_train_eval.py` lacked `--base-model`** → added. Now usable for any HF base.
- **`runs/factory_phase2/recompute_with_defaults.py`** added: replays a saved eval_report.json against current catalog rules. Free verification of post-correction patches without GPU.

### 12.6 Open issues moving into next session

1. **Why mask_on regresses on iot_light_5 SFT'd model in 42 cases (semantic flips on `set_light` args).** Agent diagnosis was inconclusive; mechanism unknown. Low priority since we've moved off inference masking, but worth one diagnostic pass before publication.
2. **catalog_to_xgrammar.py RawArg gap** — nested set_light schema is `{"type":"object"}` (loose). Only matters if S2b (training-time masking) gets revisited.
3. **smart_home_50 needs more `defaults_when_missing` rules.** The single set_light rule rescued 37 cases on smart_home but other tools likely have their own missing-arg patterns. Discoverable from failure analysis if/when smart_home becomes a bottleneck.
4. **Multi-seed CIs** — all numbers are still single-seed. Before any external claim, run N≥3 seeds. The eval-loop memory fix makes this cheap now.

---

## 13. S2c self-bootstrap full cycle (2026-05-08, late session)

> Stage 2c (self-bootstrap with teacher-paraphrased intent pool) executed
> end-to-end on iot_light_5. ~60 min wall on M1 Ultra, $0.027 API. The
> result is small but instructive: structural failures essentially
> eliminated, remaining errors are now purely semantic (args values).

### 13.1 Pipeline executed

```
runs/factory_phase2/sft_0.6B/iot_light_5/train.jsonl   (100 entries)
                  │
                  │  paraphrase_intents.py        (DashScope qwen3.6-plus, $0.027)
                  ▼
           paraphrased_iot_light_5.jsonl          (300 entries, 3.6 min)
                  │
                  │  self_bootstrap.py            (sample×4, T=0.7, validator-gate)
                  ▼
           bootstrap_iot_light_5.jsonl            (241 kept after dedup, 14 min M1)
                  │
                  │  cat train + bootstrap → augmented_train.jsonl  (341 entries)
                  │  smoke_train_eval.py            (re-SFT, 9 min M1)
                  ▼
        sft_0.6B_v2/iot_light_5/adapter           (LoRA v2)
                  │
                  │  grammar_ablation.py            (28 min M1, mask off+on)
                  ▼
           grammar_ablation/0.6B-sft-v2-iot_light_5/  (final numbers)
```

### 13.2 Headline numbers

iot_light_5 dataset.jsonl 500 cases, 0.6B + LoRA on M1 Ultra:

| config | syntax | action | exact |
|---|---:|---:|---:|
| **S2a v1** (orig SFT 100ex)        | 92.0% | 92.0% | 73.4% |
| **S2a+ v1**: v1 + post-correction  | 96.6% | 96.6% | **77.2%** |
| **S2c v2** (SFT 341ex augmented)   | 99.0% | 99.0% | **76.6%** |
| **S2c+ v2**: v2 + post-correction  | 99.0% | 99.0% | 76.6% (no rescue, model never omits state) |
| (compare) v2 mask_on               | 100.0% | 100.0% | 71.2% (mask still hurts) |

### 13.3 What S2c actually did — failure-mode shift

The exact_match number didn't move much (77.2 → 76.6, within noise) but the **internal failure decomposition changed dramatically**:

| failure type | v1 | **v2** |
|---|---:|---:|
| structural (parse-fail or wrong action) | ~15% | **~1%** |
| semantic (right action, wrong args) | ~12% | **~22%** |

S2c moved the failure mass *off* the structural axis (which post-correction handles) *onto* the semantic axis (which post-correction can NOT handle). This is the right preparation for S3.

### 13.4 Match-rate signal — the model already understands paraphrases

When the v1 adapter ran on 300 unfamiliar paraphrased intents (T=0.7, N=4):
- 84% of paraphrases produced ≥1 sample matching gold
- 41% of all attempts produced parsable-but-wrong outputs
- 4% produced unparsable garbage

→ The intent generalization of even the v1 model is much stronger than the dataset.jsonl exact_match suggests. The 73.4% on dataset.jsonl is bounded by dataset-specific phrasings the model hasn't seen — *not* by the model lacking the underlying mapping.

### 13.5 Implications for S3

After S2c, the iot_light_5 v2 model has:
- 99% action_match (tool selection essentially solved)
- 23.4% args errors (room values, brightness, scene names)

S3 (DPO with verifier-graded preference pairs) is the right next stage: the model produces multiple samples per intent, the verifier scores each by exact_match (with partial credit for action-only match), and DPO pushes the policy toward higher-scoring outputs. This is precisely the lever for fine-grained semantic correction.

Predicted S3 lift: +3-5pp → 80-82% on iot. Acceptance threshold reachable.

### 13.6 Wall time and cost

| stage | budget | actual | over/under |
|---|---:|---:|---|
| 1: paraphrase script | 35m | 5m | -30 |
| 2: paraphrase pool 100→300 | 15m | 4m | -11 |
| 3: self_bootstrap on 300 paraphrases | 50m | 14m | -36 |
| 4: re-SFT augmented | 18m | 9m | -9 |
| 5: re-ablation (mask off+on) | 25m | 28m | +3 |
| 6: docs + commit + push | 20m | _in flight_ | — |
| **total** | **163m** | **~60m + docs** | **64% under budget** |

API cost incremental: $0.027 (paraphrase generation). All other work was local M1 GPU time, no API.

### 13.7 What's NOT done (deferred)

- smart_home_50 S2c cycle — same recipe should run, ~2x wall (50 tools, 1300-token context). Predicted v2 lift smaller because smart_home's failure mass is more diverse than iot's nested-state pattern.
- Multi-seed CIs on the new v2 numbers.
- Robust fallback extractor (proposed mid-session) — left as a parallel improvement; small CPU-only patch, not blocking.
