# dtype-pin matrix report

_4 cells loaded from `runs/factory_phase2/dtype_pin/results`_

> **(loss-only)** — no `eval_report.json` present yet. Run eval phase to populate exact_match.

## Cells

| cell | hw | dtype | seed | loss | runtime | exact_match |
|---|---|---|---:|---:|---:|---:|
| mps_bf16_seed1337 | mps | bf16 | 1337 | 0.0616 | 504s | — |
| mps_bf16_seed42 | mps | bf16 | 42 | 0.0614 | 505s | — |
| mps_fp32_seed1337 | mps | fp32 | 1337 | 0.0379 | 374s | — |
| mps_fp32_seed42 | mps | fp32 | 42 | 0.0367 | 367s | — |

## Axis deltas

**dtype axis** (bf16 → fp32, same hw + seed):
- mps seed=1337: bf16=0.0616 → fp32=0.0379 (Δ=-0.0236)
- mps seed=42: bf16=0.0614 → fp32=0.0367 (Δ=-0.0248)

**seed axis** (same hw + dtype, across seeds — noise floor):
- mps bf16: seeds [42, 1337] → losses [0.0616, 0.0614], stddev=0.0001
- mps fp32: seeds [42, 1337] → losses [0.0379, 0.0367], stddev=0.0009

## LoRA init SHA map

| cell | first lora_A SHA[:16] |
|---|---|
| mps_bf16_seed1337 | `ee85a38a94008374` |
| mps_bf16_seed42 | `06498c784288fafc` |
| mps_fp32_seed1337 | `8b00591176cfaf88` |
| mps_fp32_seed42 | `e5b9df4fab72462c` |

Same SHA → identical init weights → loss differences purely from training-time numerics. Different SHA → init also differs (PEFT's Kaiming init went through a HW/dtype-dependent RNG path).

## Failure-set Jaccard (eval-side)

_(no eval failure files yet — re-run after eval_all.py lands)_

## Verdict

- mean loss A (mps,bf16) = 0.0615
- mean loss B (mps,fp32) = 0.0373
- mean loss C (cuda,bf16) = —
- mean loss D (cuda,fp32) = —

_M1 cells only — partial verdict_: bf16→fp32 on M1 closed loss by +0.0242. Pending CUDA cells to confirm CUDA bf16 is near-saturated (i.e., CUDA bf16 ≈ CUDA fp32) and that the M1 fp32 loss matches the CUDA reference.
