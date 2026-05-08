# Grammar masking ablation — iot_light_5

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `none (untuned base)`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 66.0% | 100.0% | +34.0pp |
| action_match_rate | 65.6% | 99.6% | +34.0pp |
| exact_match_rate  | 40.8% | 57.8% | +17.0pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 681.93 | 774.93 |
| latency P95 (ms) | 1193.65 | 1274.48 |
| wall seconds     | 348.0 | 389.6 |

## Verdict

- syntax_valid_rate hit 100% with masking on — A3 contract met.
- exact_match Δ = +17.0pp
- 0.6B + masking 50–60% → marginal Arc A; weigh against Arc B effort.
