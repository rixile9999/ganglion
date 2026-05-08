# Grammar masking ablation — smart_home_50

- base model: `Qwen/Qwen3-0.6B`
- adapter:    `runs/factory_phase2/sft_0.6B_v2/smart_home_50/adapter`
- dataset:    n=500

## Headline

| metric | mask off | mask on | Δ |
|---|---:|---:|---:|
| syntax_valid_rate | 93.4% | 92.8% | -0.6pp |
| action_match_rate | 90.4% | 87.2% | -3.2pp |
| exact_match_rate  | 82.4% | 81.0% | -1.4pp |

## Latency

| metric | mask off | mask on |
|---|---:|---:|
| latency P50 (ms) | 1541.01 | 1555.34 |
| latency P95 (ms) | 2490.94 | 11382.9 |
| wall seconds     | 875.8 | 1204.7 |

## Verdict

- syntax_valid_rate 93.4% → 92.8%: masking did not reach 100%. Investigate: tokenizer vocab_size mismatch? grammar covering all paths?
- exact_match Δ = -1.4pp
- 0.6B + masking ≥60% → strong Arc A signal, commit to 0.6B SFT.
