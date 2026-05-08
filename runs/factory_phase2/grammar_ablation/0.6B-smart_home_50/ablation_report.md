# Grammar masking ablation — smart_home_50

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `none (untuned base)`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 63.8% | 100.0% | +36.2pp |
| action_match_rate | 63.2% | 99.4% | +36.2pp |
| exact_match_rate  | 37.8% | 52.6% | +14.8pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 966.82 | 1049.06 |
| latency P95 (ms) | 1476.28 | 1567.57 |
| wall seconds     | 480.4 | 525.7 |

## Verdict

- syntax_valid_rate hit 100% with masking on — A3 contract met.
- exact_match Δ = +14.8pp
- 0.6B + masking 50–60% → marginal Arc A; weigh against Arc B effort.
