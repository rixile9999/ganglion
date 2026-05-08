# Grammar masking ablation — iot_light_5

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `runs/factory_phase2/sft_0.6B/iot_light_5/adapter`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 92.0% | 94.0% | +2.0pp |
| action_match_rate | 92.0% | 94.0% | +2.0pp |
| exact_match_rate  | 73.4% | 68.6% | -4.8pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 1600.58 | 1610.32 |
| latency P95 (ms) | 2462.55 | 2614.69 |
| wall seconds     | 774.9 | 781.8 |

## Verdict

- syntax_valid_rate 92.0% → 94.0%: masking did not reach 100%. Investigate: tokenizer vocab_size mismatch? grammar covering all paths?
- exact_match Δ = -4.8pp
- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.
