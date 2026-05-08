# Factory Phase 2 — Session Handoff (2026-05-08)

> Resume guide for an agent picking up cold on another machine. Self-contained;
> read this and you can continue without backreading the conversation.
> Reference docs (don't duplicate): `docs/factory_phase2_plan.md` (the long-term
> roadmap, including §10 Stage 1 measurements and §11 Arc A roadmap), and
> `docs/factory_phase1_report.md` (Phase 1 acceptance numbers).

---

## 1. TL;DR

- **Where we are**: Mid-Stage 2a (Arc A — pushing 0.6B to its ceiling). 0.6B + LoRA SFT (Phase 1 recipe) hits **73.4% exact_match** on `iot_light_5` dataset.jsonl, **88.5%** on synth holdout. Strong Arc A signal.
- **Big surprise**: Inference-time XGrammar masking *hurts* trained-model accuracy by ~5pp (was expected to help). Helps untuned base by +17pp. The lever isn't masking, it's **SFT**.
- **In flight**: smart_home_50 grammar_ablation (500 cases × 2) running on M1 Ultra MPS. Memory-stable thanks to the eval-loop leak fix landed today. Expected wall ~1 hour total, ~30 min elapsed.
- **Next**: when ablation finishes, retroactive re-eval iot_light_5 with new `defaults_when_missing` rule (free, no GPU) → expected +6pp lift on iot. Then S2c (self-bootstrap).

---

## 2. Resume in 5 minutes

```bash
# 1. Checkout
cd <your-clone>
git fetch && git checkout feature/factory-phase1 && git pull

# 2. Verify state
git log --oneline -8                                  # expect head at "feat(dsl): post-correction defaults_when_missing rules"
python -m pytest tests/ -q                            # expect 142 passed (or more if more landed)

# 3. Read the headline numbers
cat docs/factory_phase2_plan.md | grep -A 6 "Stage 1 measurement"
cat runs/factory_phase2/grammar_ablation/0.6B-sft-iot_light_5/ablation_report.md
cat runs/factory_phase2/sft_0.6B/iot_light_5/eval_report.md  # holdout 88.5%

# 4. Continue from one of the next-action options in §7.
```

---

## 3. What got done in this session (commits, in order)

| Commit | What | Why |
|---|---|---|
| `fec7f94` | A3 inference XGrammar wiring + Stage 1 baselines | Connect existing grammar generator to HF LogitsProcessor; commit baseline measurements |
| `5f3aba7` | Grammar-ablation runner + Arc A roadmap | `runs/factory_phase2/grammar_ablation.py` + `docs/factory_phase2_plan.md` §11 |
| `8bc5ea9` | Coerce sampled_token to int (MPS workaround) | xgrammar 0.2.0's `contrib.hf.LogitsProcessor` passes a 0-dim tensor to `GrammarMatcher.accept_token`; fails on Apple Silicon. Subclass overrides `__call__` with `.item()` coercion |
| `0e45b6d` | Release device memory between eval cases | Phase 1 eval loop leaked ~1.5 GB per call on MPS (CUDA hides it via caching allocator); explicit `gc.collect()` + `empty_cache` between cases. ~10× wall speedup, RSS now flat at ~3-4 MB/case growth |
| HEAD | `defaults_when_missing` post-correction rules | New `ToolSpec.defaults_when_missing` field. iot_light's `set_light` declares: if `brightness` or `color_temp` present and `state` missing, default to `state="on"`. Closes ~6% of dataset.jsonl failures. |

All on `feature/factory-phase1` branch. Pushed up through `0e45b6d`; HEAD `defaults_when_missing` commit local-only (push if/when desired).

---

## 4. Key findings (the data that should reshape the plan)

### Headline numbers (dataset.jsonl 500 cases unless noted)

| Setting | iot_light_5 exact | smart_home_50 exact |
|---|---:|---:|
| Untuned 1.7B (DashScope) | 87.4% | 80.0% |
| 1.7B + Phase 1 LoRA | 93.8% | 87.4% |
| Untuned 0.6B (DashScope) | 38.6% | 38.2% |
| 0.6B + repair loop | 41.8% | 40.6% |
| 0.6B + grammar masking (no SFT) | **57.8%** | **52.6%** |
| **0.6B + SFT (no mask)** | **73.4%** | _(in flight)_ |
| 0.6B + SFT + mask | 68.6% | _(in flight)_ |

(Phase 2 plan §10 has the full table including syntax/action breakdowns.)

### Findings worth changing your priors over

1. **Phase 1's true LoRA delta is +6.4-7.4pp, not the 93.8% headline.** Untuned 1.7B was already at 87.4%. The Phase 1 single-line summary overstates the LoRA contribution.
2. **0.6B has a capacity cliff, but SFT closes most of it.** Untuned 0.6B → 38%, +SFT → 73.4% (+35pp). Far above the +6-7pp Phase 1 saw on 1.7B because there's more headroom.
3. **Grammar masking inverts in sign with SFT state.**
    - Untuned 0.6B: mask **+17pp** (huge value).
    - SFT'd 0.6B: mask **-5pp** (regression, see below).
4. **Why mask regresses on SFT models** — Agent C diagnosed two patterns:
    - 30/500 are `create_scene` nested `set_light` missing `state`. Grammar's RawArg fallback (`{"type":"object"}`) doesn't constrain inner schema → grammar permits invalid output. Fixable via `catalog_to_xgrammar.py` (deferred — instead we use post-correction).
    - 42/500 are *semantic flips* (model emits `state="off"` when `state="on"` is right; `brightness=100` instead of `70`). Theoretically masking shouldn't change valid-token probabilities. Mechanism unconfirmed; possibly tokenization-path bias or MPS-specific. **Open mystery.**
5. **The Phase 1 eval loop has a memory leak** that was hidden by CUDA. Now fixed (`0e45b6d`). Reproducibility implication: Phase 1 numbers are fine, but they wouldn't have been if anyone had tried to reproduce on MPS.
6. **MPS-specific xgrammar bug** (`8bc5ea9` workaround): xgrammar 0.2.0 hands `GrammarMatcher.accept_token` a 0-dim tensor; CUDA auto-coerces, MPS rejects. Our subclass calls `.item()`. Can be deleted when upstream lands the fix.

### Updated priority for Arc A roadmap

§11 originally ordered: S1b (mask) → S2a (SFT) → S2b (training-time mask) → S2c (bootstrap) → S3 (DPO/GRPO).

After today's data, the right order is:

1. **S2a SFT** — ✅ done on iot_light_5, in flight on smart_home_50. **+35pp delivered** (the big card).
2. **Post-correction layer** — landed today via `defaults_when_missing`. Predicted **+6pp** on iot_light_5 (verify after current ablation finishes).
3. **S2c self-bootstrap** — next major lift candidate. Predicted **+3-5pp**. Implementation pending (skeleton not started).
4. **S3 DPO with verifier-graded preference pairs** — replaces vanilla GRPO in priority; more stable, less HP-sensitive. **+3-5pp** estimate.
5. **S2b training-time grammar masking** — **deprioritized** to almost-ignore. If inference masking hurts SFT'd models, training masking likely also distorts learning. Don't burn a week on it without new evidence.

If 0.6B + SFT + post-correction + bootstrap + DPO clears 80%, **thesis acceptance** met (sub-1B matches untuned 1.7B 80-87% range, with ~4× smaller deployment).

---

## 5. Files changed / added this session

### New
- `ganglion/factory/grammar/xgrammar_processor.py` — XGrammar HF wiring (`compile_catalog_grammar`, `make_logits_processor`)
- `runs/factory_phase2/grammar_ablation.py` — A/B masking ablation runner (mask_off + mask_on, side-by-side report, automated verdict)
- `runs/factory_phase2/sft_0.6B/iot_light_5/adapter/` — trained 0.6B LoRA (gitignored binaries, but `eval_report.md`, `train_metrics.json`, `holdout.jsonl`, `train.jsonl` are committed)
- `runs/factory_phase2/sft_0.6B/smart_home_50/adapter/` — same for smart_home (untracked binaries; in flight)
- `runs/factory_phase2/baseline/*.json` — 6 baseline runs (1.7B and 0.6B, both catalogs, ±repair) via DashScope
- `runs/factory_phase2/grammar_ablation/0.6B-iot_light_5/`, `0.6B-smart_home_50/`, `0.6B-sft-iot_light_5/` — completed ablations
- `tests/factory/test_xgrammar_processor.py` — 7 unit tests using in-memory BPE tokenizer (no HF hub dep)

### Modified
- `ganglion/dsl/tool_spec.py` — added `DefaultRule` type and `ToolSpec.defaults_when_missing` field
- `ganglion/dsl/catalog.py` — `validate_call` applies `defaults_when_missing` before validation
- `ganglion/schema/iot_light.py` — `set_light` declares the state-default rule
- `ganglion/factory/customer/eval.py` — memory cleanup between cases + `GANGLION_EVAL_MEMORY_LOG=1` opt-in RSS tracking
- `ganglion/factory/customer/train_lora.py` — `load_base_for_inference()` helper; `generate_dsl()` accepts `compiled_grammar`
- `ganglion/factory/grammar/__init__.py` — re-exports
- `runs/factory_phase1/eval_dataset_jsonl.py` — `--no-grammar-mask` flag
- `runs/factory_phase1/smoke_train_eval.py` — `--base-model` override
- `pyproject.toml` — `xgrammar>=0.2.0` in `[factory]` extras
- `tests/test_validator.py` — 5 new tests for post-correction
- `docs/factory_phase2_plan.md` — §10 Stage 1 measurement results + §11 Arc A roadmap

---

## 6. In-flight work (as of handoff time)

**`runs/factory_phase2/grammar_ablation/0.6B-sft-smart_home_50/`** — full 500-case ablation, 0.6B + SFT adapter on smart_home_50.

Status when handoff written:
- ~28 min elapsed, mask_off case 450/500 (90% done)
- RSS 2.7 GB, growing linearly at ~4 MB/case (memory fix verified)
- Estimated remaining: ~35 min (mask_off 50 + mask_on 500)
- Background task ID: `bhd03nk39` (this terminal session only)

When it completes, look at:
- `runs/factory_phase2/grammar_ablation/0.6B-sft-smart_home_50/ablation_report.md` — auto-verdict + numbers
- Compare `mask_off exact` to iot_light_5's 73.4% — robustness check across catalog sizes

If the smart_home_50 result is similar (60%+ mask_off, mask hurting), Arc A robustness is confirmed.

---

## 7. Next concrete actions (three ranked options)

### Option A — Retroactive post-correction re-eval (5 min, free, lossless)
Re-parse the saved `eval_report.json` from prior 0.6B+SFT runs with the new `defaults_when_missing` rule. Doesn't need GPU, doesn't need to re-run inference.

```python
# Sketch — write as runs/factory_phase2/recompute_with_defaults.py
import json
from pathlib import Path
from ganglion.factory.customer.ingest import ingest_schema
catalog = ingest_schema("iot_light_5")
src = Path("runs/factory_phase2/grammar_ablation/0.6B-sft-iot_light_5/mask_off/eval_report.json")
data = json.loads(src.read_text())
rescued = 0
for fail in data["failures"]:
    raw_output = fail.get("raw", {}).get("raw_output")
    if raw_output is None: continue
    try:
        plan = catalog.parse_json_dsl(raw_output)
        # Compare to fail['expected']
        ...
        rescued += 1
    except Exception:
        pass
print(f"rescued {rescued}/{len(data['failures'])}")
```

Expected: ~30 of the failures were the create_scene + missing-state pattern → ~30/(500*0.266) ≈ 22% of failures rescued → ~+6pp lift on the 73.4% number → **~79.4%**.

### Option B — Self-bootstrap (S2c) implementation (~1 day)
Extend `ganglion/factory/customer/synth.py` with a `teacher="self"` mode:
- Load currently-trained adapter
- For each intent in dataset.jsonl (or fresh teacher-generated paraphrases), sample N=4 outputs at temperature 0.7
- Validator-gate each sample (parse + match expected for labeled, parse-only for unlabeled)
- Dedupe vs original synth.jsonl with cosine 0.93 (loosened from 0.95 since self-samples cluster tighter)
- Continue-SFT on augmented set
- Re-eval

Predicted lift: **+3-5pp** on top of post-correction. Brings 0.6B to ~82-85%. Approaches the 80% Arc A acceptance line.

### Option C — Push and prepare GPU-box scale-up (30 min + scheduling)
- `git push` if not yet (HEAD `defaults_when_missing` commit may be local-only)
- Prepare run instructions for a real CUDA box: same `grammar_ablation.py`, but on a larger model (Qwen3-1.7B or Qwen3-4B). The pipeline is now device-agnostic enough to just work.
- Useful if M1 wall time is the bottleneck for further iteration.

**Recommended**: **A first** (free verification of the +6pp post-correction claim), then **B** (next stage of the roadmap). C is optional and scheduling-dependent.

---

## 8. Open decisions / unresolved

1. **The 42 mask-on semantic regressions on `set_light`** — mechanism unknown. Worth one more diagnostic pass once Arc A is settled. May be irrelevant in practice (we're not using inference masking on trained models anymore).
2. **`catalog_to_xgrammar.py` RawArg expansion** — minor schema gap, ~30 cases worth. Low priority if we're not using inference masking; medium priority if S2b ever gets revisited.
3. **smart_home_50 SFT + post-correction** — the post-correction rule is on `set_light` only; smart_home_50 has 50 different tools, some with their own missing-arg patterns. May need per-tool default rules. Discoverable from failure analysis after ablation finishes.
4. **HF_TOKEN** — not set in this env; HF hub warns about anonymous rate limit. Suggest `export HF_TOKEN=...` before long runs.
5. **MPS vs CUDA reproducibility** — final headline numbers should ideally be on a CUDA box for canonical reproducibility. M1 results are within ±2-3pp of DashScope-served numbers (see §10.4 in plan), but a final pin on CUDA is good hygiene before any external claim.

---

## 9. Environment / reproducibility

### What this Mac has

- **Hardware**: Mac Studio M1 Ultra, 128 GB unified memory
- **Python**: `/opt/homebrew/bin/python3` (Python 3.14.3, Homebrew). Use `--break-system-packages` for pip (PEP 668 externally-managed env).
- **Installed**: torch 2.11, transformers 5.8, peft 0.19.1, accelerate 1.13, trl 1.3, datasets 4.8.5, xgrammar 0.2.0, openai 2.33, psutil 7.2.

### Required env vars
- `DASHSCOPE_API_KEY` — for the API baselines (already set in this session's env)
- `PYTORCH_ENABLE_MPS_FALLBACK=1` — needed for some PyTorch ops that haven't been MPS-ported yet
- `GANGLION_EVAL_MEMORY_LOG=1` — opt-in RSS tracking (every 10 cases)
- (optional) `HF_TOKEN` — silences anonymous-rate-limit warnings

### Phase 1's `--max-seq-length` trap
`smart_home_50` requires `--max-seq-length 2048` due to its 1300-token catalog DSL. Default 1024 overflows.

### MPS gotchas (learned today, encoded in code)
- `xgrammar.contrib.hf.LogitsProcessor` requires `.item()` coercion of `input_ids[i][-1]` on MPS. Workaround in `xgrammar_processor.py`.
- HF generate's KV cache + LoRA + MPS leaks ~1-1.5 GB per call without explicit cleanup. Workaround in `eval.py:_release_device_memory()`.
- Tokenizer vocab vs model vocab: pass `config.vocab_size` (not `tokenizer.vocab_size`) to `xgr.TokenizerInfo.from_huggingface`. Qwen3 has padded embeddings.

---

## 10. Cumulative cost / time so far this session

- DashScope API: ~$0.31 (6 baseline runs)
- M1 Ultra GPU time: ~3 hours (SFT + ablations + smoke)
- Net research output: 0.6B path validated for Arc A (73.4%), post-correction adds +6pp expected, smart_home_50 ablation ~30 min from completing.

---

## 11. Single-line summary

> **0.6B + SFT (Phase 1 recipe, 30 sec → 3 min on M1 Ultra) hits 73.4% on real dataset.jsonl — within striking distance of untuned 1.7B (87.4%). Post-correction predicted +6pp; self-bootstrap +3-5pp; DPO +3-5pp. Arc A is alive; thesis acceptance (≥80%) feasible with the next 1-2 stages.**
