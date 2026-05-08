# Grammar masking ablation — iot_light_5

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `runs/factory_phase2/sft_0.6B_v2/iot_light_5/adapter`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 99.0% | 100.0% | +1.0pp |
| action_match_rate | 99.0% | 100.0% | +1.0pp |
| exact_match_rate  | 76.6% | 71.2% | -5.4pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 1524.34 | 1575.62 |
| latency P95 (ms) | 2390.67 | 2494.29 |
| wall seconds     | 819.1 | 838.2 |

## Verdict

- syntax_valid_rate hit 100% with masking on — A3 contract met.
- exact_match Δ = -5.4pp
- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.
