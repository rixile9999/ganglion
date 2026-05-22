# dtype-pin matrix — M1 ↔ CUDA × bf16 ↔ fp32

> Goal: decompose the §15.1 9.8pp eval gap (M1 76.6% vs CUDA 86.4%) into
> contributions from dtype precision, attention backend, and seed-dependent
> init. Holds everything else constant: same data, same hyperparams, same
> base model, same LoRA config.

## Key controls (relative to docs §15.1 v2 recipe)

- `--attn-impl eager` — forced (transformers default would dispatch to MPS-SDPA
  vs CUDA-SDPA, which is itself a HW-asymmetric variable).
- `--seed {42,1337}` — two seeds to bound seed-axis noise.
- `--dtype {bf16,fp32}` — the variable under test.
- `Qwen3-0.6B`, 341 augmented examples, lr=2e-4, rank=32, bs=4×2, 3 epochs.

## File layout

```
results/<hw>_<dtype>_seed<N>/
├── env.json            platform + package versions + data SHA + git HEAD
├── diag.json           param (device,dtype) histogram, attn_impl, lora_A SHAs
├── train_metrics.json  TRL final loss + runtime
└── adapter/            LoRA adapter (gitignored, ~144 MB)
```

## Live results

### M1 Ultra (this box) — `feature/factory-phase1`

| seed | dtype | loss | runtime | step time |
|---:|---|---:|---:|---:|
| 42 | bf16 | 0.0614 | 505.4s | 3.95s |
| 1337 | bf16 | 0.0616 | 504.0s | 3.91s |
| 42 | fp32 | 0.0367 | 366.6s | 2.84s |
| 1337 | fp32 | 0.0379 | 373.8s | 2.90s |

Seed-axis stddev: bf16 = 0.0001, fp32 = 0.0009 — both negligible.
Cross-seed dtype Δ (bf16 → fp32): -0.0248 (seed 42), -0.0236 (seed 1337) — consistent.

Reference (from docs §15.1, sdpa default):
- M1 v2 sdpa+bf16: loss **0.071** / exact 76.6%
- CUDA v2 sdpa+bf16: loss **0.039** / exact 86.4%

### RTX 4090 — to be filled by CUDA box runs (next section)

| seed | dtype | loss | runtime | step time |
|---:|---|---:|---:|---:|
| 42 | bf16 | _pending_ | | |
| 42 | fp32 | _pending_ | | |
| 1337 | bf16 | _pending_ | | |
| 1337 | fp32 | _pending_ | | |

## CUDA-box handoff — copy-paste runbook

Run on the RTX 4090 box. Assumes the same git checkout (`feature/factory-phase1`)
with HEAD at the commit that introduced this directory.

### 0. Get this code + the M1 adapters onto the CUDA box

Two options:

**Option A — push branch + commit M1 results, pull on CUDA box.** Adapters
are gitignored; you'd need a separate transfer for them anyway, so this is
only useful if you don't need the M1 adapters on CUDA (i.e., you'll only
eval CUDA-trained adapters):

```bash
# on M1
git push origin feature/factory-phase1
# on CUDA box
git fetch && git checkout feature/factory-phase1 && git pull
```

**Option B — rsync the whole dtype_pin/ tree** (recommended; brings
adapters too so eval_all.py covers all 8 cells):

```bash
# from M1, push code via git as above, THEN rsync M1 results:
rsync -av --progress \
  runs/factory_phase2/dtype_pin/results/mps_*_seed*/ \
  cuda-host:reflex-language-model/runs/factory_phase2/dtype_pin/results/
```

### 1. Verify environment (one-time)

```bash
cd <your-clone>/reflex-language-model
git fetch && git checkout feature/factory-phase1 && git pull

# Same package versions as M1 box (compare against env.json from M1 cells)
python -c "import torch, transformers, peft, trl, accelerate, datasets; \
  print('torch', torch.__version__); \
  print('transformers', transformers.__version__); \
  print('peft', peft.__version__); \
  print('trl', trl.__version__); \
  print('accelerate', accelerate.__version__); \
  print('datasets', datasets.__version__); \
  print('cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# Synth file SHA must match (the runner will assert too)
sha256sum runs/factory_phase2/sft_0.6B_v2/iot_light_5/augmented_train.jsonl
# Expected: 45209d7316bd4e334c0fb100f07d0b10a72b3792f02c69e95711aacc3b5e667e
```

### 2. Run the 4 CUDA cells (≈15 min total)

```bash
python runs/factory_phase2/dtype_pin/run_one.py --hw cuda --dtype bf16 --seed 42
python runs/factory_phase2/dtype_pin/run_one.py --hw cuda --dtype fp32 --seed 42
python runs/factory_phase2/dtype_pin/run_one.py --hw cuda --dtype bf16 --seed 1337
python runs/factory_phase2/dtype_pin/run_one.py --hw cuda --dtype fp32 --seed 1337
```

Each cell outputs `results/cuda_<dtype>_seed<N>/{env.json,diag.json,train_metrics.json,adapter/}`.

If a cell aborts for HW mismatch reasons, the script exits 2 — re-check that
`torch.cuda.is_available()` returns True before running.

### 3. Eval all 8 adapters under canonical CUDA bf16 (≈100 min total)

After both M1 and CUDA cells exist (rsync M1 results dir to the CUDA box first
so all 8 adapter dirs are local):

```bash
# rsync from M1 (on the M1 box):
rsync -av runs/factory_phase2/dtype_pin/results/mps_*_seed*/ \
  cuda-box:reflex-language-model/runs/factory_phase2/dtype_pin/results/

# eval (on the CUDA box):
python runs/factory_phase2/dtype_pin/eval_all.py
# Iterates results/*/adapter/, runs eval on dataset.jsonl, writes
# eval_report.json + failures.json into each cell dir. ~12-15 min/cell × 8.
# --skip-existing to resume after a crash.
# --limit 100 for a fast smoke (still ~3 min/cell × 8 ≈ 25 min).
```

### 4. Run analyze.py

Produces `dtype_pin_report.md` with:
- 8-cell loss + exact_match table
- per-tool exact_match breakdown
- failure-set Jaccard (same-config different-seed = noise floor;
  cross-HW same-seed = signal)
- decision verdict per docs §15.5 #3

## Decision criteria (preview, from plan §5)

| Observation | Conclusion |
|---|---|
| C ≈ D, A ≪ B, B ≈ D (≤ 2pp) | **H1 confirmed** — bf16 is the culprit, M1 needs fp32 to match CUDA. Operational rule: M1-fp32 OK, M1-bf16 forbidden. |
| C ≈ D, A < B, B < D (≥ 3pp) | **H2** — bf16 plus other MPS precision issues. Need deeper layer-level dtype audit. |
| A ≈ B, C ≈ D, A < C still | **H3** — dtype is not the cause; kernel/allocator differences. CUDA-only training. |
| seed-axis var > HW-axis var | **H4** — single-seed noise inflated the documented gap. Re-baseline. |

## Current preliminary read (M1 cells only — n=4)

- **H1 strongly favored**: M1 fp32 mean loss (0.0373) ≈ documented CUDA bf16
  (0.039), Δ = 0.0017 — within seed-axis noise.
- Decomposition of original 0.032 loss gap:
  - attn_impl sdpa → eager: ~0.010 (~30%)
  - dtype bf16 → fp32: ~0.024 (~78%)
  - Combined > 100% — M1 fp32+eager *beats* CUDA bf16+sdpa on this metric.
- **H4 (seed × HW noise)** essentially ruled out. Bf16 reproduces to 0.0001
  across seeds; fp32 to 0.0009. Neither approaches the 0.024 dtype Δ.
- Subject to confirmation by exact_match on dataset.jsonl.

## Open questions for CUDA cells to answer

1. Does CUDA also gain from fp32 (cuda,bf16 ≪ cuda,fp32)? If yes → CUDA
   bf16 was *also* under-converged; the doc's 86.4% is below the achievable
   ceiling. If no (CUDA bf16 ≈ CUDA fp32) → CUDA bf16 is near-saturated
   and dtype is a *MPS-specific* problem only, not a general bf16 problem.
2. M1 fp32 vs CUDA fp32: the cleanest comparison. If they match, the
   factory pipeline is *truly* hardware-portable when dtype-pinned. If
   CUDA fp32 still beats M1 fp32 by >0.005, residual MPS-specific issues
   remain (kernel ordering, allocator, etc).
