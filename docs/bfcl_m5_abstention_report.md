# BFCL M5 Abstention / No-Call 실험 보고서

**작성일:** 2026-04-30  
**목표:** BFCL `irrelevance`에서 DSL 경로의 no-call/abstention gap 해소  
**모델:** `qwen3.6-plus`, DashScope OpenAI-compatible API, non-thinking  
**변경:** `Catalog.allow_empty_calls` + `--bfcl-allow-empty-calls`

## 1. 요약

M5는 BFCL 재실험에서 가장 큰 정확도 손실 원인이었던 `irrelevance` 문제를 직접
겨냥한다. M1'에서 DSL 전체 AST가 native보다 -2.6pp 낮았는데, callable 400건만
보면 gap은 -0.25pp였다. 실제 gap의 대부분은 tool을 호출하지 않아야 하는
`irrelevance` category에서 발생했다.

M5 변경 후 핵심 결과:

| 항목 | M1' DSL | M5 DSL | Native M1' | 해석 |
| --- | ---: | ---: | ---: | --- |
| Irrelevance AST | 74.0% | **90.0%** | 86.0% | M5가 native보다 +4pp |
| Irrelevance syntax-valid | 26.0% | **98.0%** | 14.0% | 빈 ActionPlan이 정식 valid 출력이 됨 |
| Unexpected call failures | 26 | **10** | 14 | no-call 오탐 대폭 감소 |

Callable 회귀 체크:

| 항목 | M4 repair-off DSL | M5 callable DSL | 해석 |
| --- | ---: | ---: | --- |
| Callable AST | 86.0% | 84.0% | fresh run 기준 -2pp |
| Callable syntax-valid | 97.0% | 97.0% | 동일 |
| Empty-call false abstention | n/a | **0건** | no-call rule이 callable을 빈 출력으로 망가뜨리지는 않음 |

M5의 결론은 명확하다.

> `{"calls":[]}`를 Action IR contract에 포함하면 BFCL irrelevance gap은 닫히며,
> DSL 전체 정확도는 native와 거의 같은 수준 또는 그 이상으로 올라갈 수 있다.

## 2. 구현 내용

### 2.1 Catalog-level no-call contract

`Catalog`에 `allow_empty_calls: bool = False`를 추가했다.

기본값은 기존 동작을 보존한다. 즉 일반 catalog에서는 여전히 `{"calls":[]}`가
validation error다. M5 실험처럼 명시적으로 opt-in한 catalog에서만 empty call
plan을 허용한다.

```python
Catalog(
    name="bfcl_irrelevance_case",
    tools=...,
    allow_empty_calls=True,
)
```

이 경우 DSL prompt에는 다음 문장이 추가된다.

```text
If no tool call is needed, return exactly {"calls":[]}.
```

그리고 validator는 다음 출력을 정상 ActionPlan으로 변환한다.

```json
{"calls":[]}
```

### 2.2 Schema compiler / BFCL runner 연동

`compile_tool_calling_schema(..., allow_empty_calls=True)`로 외부 tool schema에서
만든 catalog에도 no-call contract를 전달할 수 있게 했다.

BFCL runner에는 CLI flag를 추가했다.

```bash
python -m ganglion.eval.runner \
  --bfcl irrelevance \
  --llm qwen \
  --bfcl-allow-empty-calls \
  --bfcl-output runs/bfcl/phase_m5_irrelevance_dsl_cases.jsonl
```

Callable 회귀 체크도 같은 flag로 수행했다.

```bash
python -m ganglion.eval.runner \
  --bfcl callable \
  --bfcl-per-category 25 \
  --llm qwen \
  --bfcl-allow-empty-calls \
  --bfcl-output runs/bfcl/phase_m5_callable_dsl_cases.jsonl
```

## 3. Irrelevance 결과

M5 irrelevance 100건 결과:

| Metric | M5 DSL |
| --- | ---: |
| Total | 100 |
| AST match | **90.0%** |
| Syntax-valid rate | **98.0%** |
| Input tokens total | 12,666 |
| Output tokens total | 676 |
| Latency mean | 1,357.5 ms |
| Latency p50 | 1,282.1 ms |
| Latency p95 | 1,964.4 ms |
| Unexpected call failures | 10 |

기존 M1' irrelevance와 비교:

| Metric | M1' DSL | M5 DSL | 변화 |
| --- | ---: | ---: | ---: |
| AST match | 74.0% | **90.0%** | +16.0pp |
| Syntax-valid rate | 26.0% | **98.0%** | +72.0pp |
| Unexpected call failures | 26 | **10** | -16 |

M1'에서 syntax-valid가 낮았던 이유는 모델이 실제로는 no-call에 해당하는
`{"calls":[]}`를 출력해도 기존 validator가 이를 invalid로 보았기 때문이다.
M5에서는 이 출력이 정식 Action IR이 되었고, 따라서 syntax-valid가 98%까지 오른다.

## 4. Callable 회귀 체크

M5 callable 100건 결과:

| Metric | M5 DSL |
| --- | ---: |
| Total | 100 |
| AST match | 84.0% |
| Syntax-valid rate | 97.0% |
| Input tokens total | 19,379 |
| Output tokens total | 7,587 |
| Latency mean | 2,660.1 ms |
| Latency p50 | 2,131.4 ms |
| Empty-call false abstention | **0건** |

Category별:

| Category | AST | Syntax-valid |
| --- | ---: | ---: |
| `simple_python` | 84.0% | 100.0% |
| `multiple` | 88.0% | 96.0% |
| `parallel` | 84.0% | 100.0% |
| `parallel_multiple` | 80.0% | 92.0% |

M4 repair-off 100건 baseline은 86.0%였고 M5 callable은 84.0%다. 다만 M5에서는
empty-call false abstention이 0건이므로, 하락은 no-call rule이 callable 요청을
잘못 거부해서 생긴 문제가 아니다. 실패 유형은 기존과 같은 nested type,
value canonicalization, parallel matching/count 오류다.

## 5. 전체 BFCL 점수에 대한 기대효과

M1' 전체 500건에서 DSL은 415/500 = 83.0%였다.

M5 irrelevance 결과만 M1'에 대입하면:

```text
M1' callable success: 341/400
M5 irrelevance success: 90/100
Projected total: 431/500 = 86.2%
```

즉 native M1'의 85.6%를 근소하게 넘어선다.

보수적으로 M5 callable 100건의 fresh-run 결과 84%와 M5 irrelevance 90%를 같은
비율로 조합하면:

```text
Projected total from M5 sample rates:
0.8 * 84.0% + 0.2 * 90.0% = 85.2%
```

이 경우에도 M1' DSL 83.0%보다 +2.2pp 개선되고, native 85.6%와 거의 같은 수준이다.

## 6. 해석

M5는 BFCL 결과의 해석을 크게 바꾼다.

M1'-M4'까지만 보면:

> DSL은 native보다 토큰은 크게 줄이지만 전체 AST가 -2.6pp 낮다.

M5까지 포함하면:

> DSL은 callable task에서 native와 거의 같은 정확도를 유지하고, no-call contract를
> 추가하면 BFCL 전체에서도 native와 거의 같거나 더 높은 AST를 기대할 수 있다.

이는 ToolCallOpt의 핵심 thesis를 훨씬 강하게 만든다. Action IR은 단순히 tool을
부르는 형식만이 아니라, **부르지 않는 결정**까지 포함하는 runtime contract가
되어야 한다.

## 7. 남은 한계

- M5 전체 500건 fresh run은 아직 수행하지 않았다. 현재는 M1' callable 결과와 M5
  irrelevance 결과를 조합한 projected total이다.
- Callable 100건 fresh run에서 AST가 86%에서 84%로 낮아졌다. false abstention은
  0건이지만, full rerun으로 variance를 줄여야 한다.
- Irrelevance 90%의 남은 10건은 여전히 semantic temptation이 큰 케이스다. 예를
  들어 스포츠/여행/계산 질문이 tool schema와 비슷하게 보여 모델이 tool을 호출한다.

## 8. 다음 단계

1. **M5 full run:** `--bfcl all --bfcl-allow-empty-calls`로 500건 fresh run을 수행해
   projected 86.2%를 실제 전체 점수로 확인한다.
2. **No-call prompt tuning:** "listed tools are unavailable unless they exactly
   satisfy the user request" 같은 stronger instruction을 비교한다.
3. **Semantic abstention classifier:** tool schema와 user request의 semantic match가
   약하면 empty plan을 선호하도록 별도 gate를 둔다.
4. **M6 normalization:** 남은 callable 오류의 큰 축인 value/unit/string
   canonicalization을 compiler/validator layer에서 흡수한다.

## 9. 결론

M5는 기대효과가 실제로 확인된 milestone이다. Irrelevance AST가 **74%에서 90%**로
올라갔고, native baseline 86%를 넘었다. 또한 callable 회귀 체크에서 false
abstention은 0건이었다.

따라서 논문 관점의 메시지는 다음처럼 강화된다.

> With explicit no-call support, ToolCallOpt closes the major BFCL gap caused by
> irrelevance cases, while preserving the token and latency advantages of compact
> Action IR generation.
