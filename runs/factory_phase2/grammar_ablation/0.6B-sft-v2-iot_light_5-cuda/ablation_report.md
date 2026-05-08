# Grammar masking ablation — iot_light_5

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `runs/factory_phase2/sft_0.6B_v2/iot_light_5/adapter`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 95.6% | 99.4% | +3.8pp |
| action_match_rate | 95.4% | 99.2% | +3.8pp |
| exact_match_rate  | 86.4% | 86.0% | -0.4pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 1403.74 | 1438.51 |
| latency P95 (ms) | 2513.61 | 2581.28 |
| wall seconds     | 749.5 | 792.1 |

## Verdict

- syntax_valid_rate 95.6% → 99.4%: masking did not reach 100%. Investigate: tokenizer vocab_size mismatch? grammar covering all paths?
- exact_match Δ = -0.4pp
- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.
