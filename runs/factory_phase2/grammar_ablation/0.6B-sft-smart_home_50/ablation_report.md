# Grammar masking ablation — smart_home_50

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `runs/factory_phase2/sft_0.6B/smart_home_50/adapter`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 88.2% | 99.8% | +11.6pp |
| action_match_rate | 80.8% | 90.6% | +9.8pp |
| exact_match_rate  | 64.0% | 70.8% | +6.8pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 2056.2 | 1928.63 |
| latency P95 (ms) | 3245.05 | 3121.06 |
| wall seconds     | 1079.1 | 1026.8 |

## Verdict

- syntax_valid_rate 88.2% → 99.8%: masking did not reach 100%. Investigate: tokenizer vocab_size mismatch? grammar covering all paths?
- exact_match Δ = +6.8pp
- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.
