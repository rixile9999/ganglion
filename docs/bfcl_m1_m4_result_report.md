# BFCL 기반 M1-M4 재실험 결과 보고서

**작성일:** 2026-04-30  
**대상:** BFCL v4 deterministic subsample 500건  
**모델:** `qwen3.6-plus`, DashScope OpenAI-compatible API, non-thinking  
**비교:** ToolCallOpt JSON DSL/Action IR 경로 vs native tool calling 경로

## 1. 결론 요약

BFCL 재실험은 기존 IoT POC보다 훨씬 강한 증거다. IoT 결과는 template 기반
single-domain 데이터에서 100% 정확도를 보인 것이어서 일반화 위험이 컸지만,
BFCL은 실제 function-calling benchmark의 다양한 schema와 호출 패턴을 포함한다.

핵심 결론은 다음과 같다.

- **토큰/latency 최적화 가설은 강하게 지지된다.**
  DSL 경로는 500건 기준 native 대비 input token을 **62.25%** 줄였고,
  p50 latency를 **21.8%** 낮췄다.
- **정확도는 전체로 보면 native보다 낮다.**
  AST match는 DSL **83.0%**, native **85.6%**로 DSL이 **-2.6pp** 뒤진다.
- **하지만 callable case에서는 사실상 parity다.**
  `simple_python`, `multiple`, `parallel`, `parallel_multiple` 400건만 보면
  DSL은 341/400, native는 342/400으로 gap이 **-0.25pp**에 불과하다.
- **전체 정확도 gap의 대부분은 irrelevance/abstention 문제다.**
  `irrelevance`에서 DSL은 74%, native는 86%다. 이는 DSL이 명시적인
  "no tool call" contract를 아직 충분히 갖지 못했기 때문이며, M5에서
  `{"calls":[]}` 또는 `no_call` action을 정식 지원하면 닫을 수 있는 문제다.
- **repair loop는 syntax 안전망으로 의미가 있지만 정확도 개선 장치는 아니다.**
  repair는 syntax-valid rate를 97%에서 99%로 올렸지만 AST match는 86%에서
  84%로 내려갔다. 이는 fresh API run의 비결정성과 semantic/value 오류를
  repair가 고치지 못한다는 점을 보여준다.

한 줄로 말하면:

> BFCL 결과는 ToolCallOpt의 "token-efficient tool calling" 주장을 실제
> benchmark에서 지지한다. 다만 논문 주장은 "정확도 우위"가 아니라
> "거의 같은 callable accuracy에서 큰 token/latency 절감"으로 잡아야 한다.

## 2. 실험 구성

실험은 BFCL v4에서 category당 100건씩, 총 500건을 사용했다.

| Category | 의미 | 건수 |
| --- | --- | --- |
| `simple_python` | 단일 함수 호출 | 100 |
| `multiple` | 여러 후보 도구 중 선택 | 100 |
| `parallel` | 여러 tool call 병렬 호출 | 100 |
| `parallel_multiple` | 복수 도구 + 복수 호출 | 100 |
| `irrelevance` | tool을 호출하지 않아야 하는 요청 | 100 |

두 경로는 같은 BFCL tool schema에서 출발한다.

```text
BFCL tool schema
  -> compile_tool_calling_schema()
  -> ToolCallOpt Catalog
  -> JSON DSL / Action IR prompt
  -> qwen3.6-plus
  -> validator / grader
```

Native baseline은 같은 catalog에서 OpenAI-compatible `tools=[...]` schema를
렌더링해 호출한다. 따라서 DSL과 native 비교는 같은 source-of-truth에서 나온
표현 간 비교다.

평가는 BFCL AST semantics에 맞춘 local grader로 수행되며, 결과 파일은
`runs/bfcl/*.jsonl`, 집계 파일은 `runs/bfcl/aggregated.json`에 있다.

## 3. M1' 결과: 정확도, 토큰, latency

500건 전체 결과:

| Metric | DSL / Action IR | Native tools | 해석 |
| --- | ---: | ---: | --- |
| AST match | 83.0% | 85.6% | DSL -2.6pp |
| Syntax-valid rate | 83.0% | 81.0% | DSL +2.0pp |
| Input tokens total | 70,163 | 185,881 | DSL -62.25% |
| Output tokens total | 29,425 | 42,852 | DSL -31.33% |
| Input tokens mean/case | 140.3 | 371.8 | DSL -62.3% |
| Output tokens mean/case | 58.9 | 85.7 | DSL -31.3% |
| Latency mean | 2,422.2 ms | 3,042.4 ms | DSL -20.4% |
| Latency p50 | 1,907.9 ms | 2,441.3 ms | DSL -21.8% |
| Latency p95 | 4,899.8 ms | 5,298.3 ms | DSL -7.5% |
| Prompt/schema chars mean | 311.2 | 674.3 | DSL -53.9% |

Category별 결과:

| Category | DSL AST | Native AST | Gap | DSL input mean | Native input mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| `simple_python` | 85.0% | 85.0% | 0.0pp | 118.1 | 334.2 |
| `multiple` | 86.0% | 89.0% | -3.0pp | 166.3 | 536.8 |
| `parallel` | 87.0% | 85.0% | +2.0pp | 164.2 | 385.6 |
| `parallel_multiple` | 83.0% | 83.0% | 0.0pp | 222.7 | 554.8 |
| `irrelevance` | 74.0% | 86.0% | -12.0pp | 30.4 | 47.4 |

가장 중요한 해석은 callable category와 irrelevance를 분리해야 한다는 점이다.

Callable 400건만 보면 DSL은 341건 성공, native는 342건 성공이다. 즉 native가
1건 앞설 뿐이다. 반면 irrelevance 100건에서는 native가 12건 앞선다. 따라서
전체 -2.6pp gap은 "도구 호출 능력" 자체의 열세라기보다 "호출하지 않기"를 DSL
contract에 충분히 넣지 않은 설계 문제로 보는 것이 타당하다.

## 4. M2' 결과: Tool-count scaling

BFCL sample의 per-case tool 수는 1개 또는 2-5개에 몰려 있어, 기존 IoT POC의
5/20/50 tool scaling을 그대로 재현하지는 못한다. 그래도 tool 수가 늘어날 때
DSL compactness가 더 커지는 방향은 확인된다.

| Tools/case | n | DSL AST | Native AST | DSL chars mean | Native chars mean | DSL/native chars |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 300 | 82.0% | 85.3% | 247.0 | 406.6 | 0.61 |
| 2-5 | 200 | 84.5% | 86.0% | 407.4 | 1,075.9 | 0.38 |

Input token 기준으로도 같은 경향이다.

| Tools/case | DSL input mean | Native input mean | 절감률 |
| --- | ---: | ---: | ---: |
| 1 | 104.2 | 255.7 | 59.3% |
| 2-5 | 194.5 | 545.8 | 64.4% |

해석:

- tool 수가 1개에서 2-5개로 늘어나면 native schema는 훨씬 빠르게 커진다.
- DSL/native char ratio는 0.61에서 0.38로 개선된다.
- AST gap은 tool 수가 늘어난다고 폭발하지 않는다.
- 다만 6개 이상 tool catalog가 없어, 대규모 toolset scaling은 별도 benchmark가
  필요하다.

## 5. M3' 결과: 반복 측정과 latency 안정성

Callable 50건을 대상으로 5회 반복 측정을 수행했다.

| Metric | DSL | Native | 해석 |
| --- | ---: | ---: | --- |
| AST match | 86.0% | 88.0% | DSL -2.0pp |
| Syntax-valid rate | 94.0% | 96.0% | DSL -2.0pp |
| Latency mean | 2,356.0 ms | 2,886.5 ms | DSL -18.4% |
| Latency p50 | 1,800.7 ms | 2,269.6 ms | DSL -20.7% |
| Latency p95 | 4,773.1 ms | 5,220.7 ms | DSL -8.6% |
| Latency stddev | 1,300.5 ms | 1,376.0 ms | DSL -5.5% |
| Input tokens total | 39,145 | 109,625 | DSL -64.3% |
| Output tokens total | 17,340 | 25,134 | DSL -31.0% |

M1'의 latency advantage가 단일 run noise가 아니었음을 보여준다. 평균과 p50 모두
18-21% 낮고, p95도 8.6% 낮다. stddev 차이는 크지 않지만 DSL 쪽이 약간 작다.
즉 DSL은 outlier 몇 개만 제거한 것이 아니라 전체 latency distribution을 아래로
이동시킨다.

## 6. M4' 결과: Repair loop

Callable 100건에서 repair off/on을 비교했다.

| Metric | Repair off | Repair on | 해석 |
| --- | ---: | ---: | --- |
| AST match | 86.0% | 84.0% | -2.0pp |
| Syntax-valid rate | 97.0% | 99.0% | +2.0pp |
| Input tokens total | 17,924 | 18,877 | +5.3% |
| Output tokens total | 7,994 | 8,091 | +1.2% |
| Latency mean | 2,661.3 ms | 2,759.7 ms | +3.7% |
| Latency p50 | 2,023.3 ms | 2,184.1 ms | +7.9% |

Repair loop의 의미는 명확하다.

- 검증 실패를 줄이는 **syntax-level safety net**으로는 작동한다.
- 비용은 실패한 case에만 추가 turn이 붙기 때문에 전체 input token +5.3%로 제한된다.
- AST match는 오히려 내려갔는데, 이는 repair가 semantic/value error를 고치지
  못하고, repair on/off가 fresh API run이라 model variance가 섞였기 때문이다.

따라서 M4는 논문의 핵심 contribution보다는 runtime robustness 장치로 두는 것이
맞다. 핵심 결과는 M1-M3의 token/latency 절감과 callable accuracy parity다.

## 7. 실패 패턴 분석

대표 실패는 다섯 부류로 나뉜다.

| 실패 유형 | 예 | 해석 |
| --- | --- | --- |
| abstention 실패 | irrelevance에서 불필요한 call 생성 | DSL에 no-call contract가 부족 |
| value canonicalization | `annual_rate=0.05` vs expected `5.0` | 단위/표현 정규화 필요 |
| locale/string normalization | `Chicago` vs `Chicago, IL` | alias/normalizer 필요 |
| nested type mismatch | `interval=[1,3]` vs `[[1.0,3.0]]` | RawArg schema prompt/validator 개선 필요 |
| parallel matching/count | 병렬 호출 개수/값 일부 누락 | few-shot, planning hint, ordering policy 필요 |

이 실패들은 "Action IR 접근 자체가 불가능하다"는 신호가 아니다. 대부분은
IR contract와 validator/compiler layer를 더 풍부하게 만들면 줄일 수 있는
시스템 엔지니어링 문제다.

## 8. 기존 IoT POC 대비 의미

| 항목 | IoT POC | BFCL 재실험 |
| --- | ---: | ---: |
| 데이터 | 500건 synthetic single-domain | 500건 BFCL v4 multi-category |
| Best accuracy | 100% | Native 85.6%, DSL 83.0% |
| Callable-only gap | 0pp | -0.25pp |
| 전체 gap | 0pp | -2.6pp |
| Input token 절감 | tier별 약 45-69% | 62.25% |
| Latency p50 절감 | 반복 측정에서 약 19% | 21.8% |
| Repair trigger | 거의 없음 | syntax 97% -> 99% |
| 주요 실패 | 거의 없음 | abstention, value normalization, parallel calls |

BFCL은 IoT POC의 낙관적인 100% 정확도를 현실화시켰다. 그 대신 중요한 점은
token/latency 이점이 유지되었고, callable tool-calling accuracy가 native와 거의
같다는 것이다. 이것은 논문 contribution으로 훨씬 건강한 그림이다.

## 9. 논문 관점 평가

현재 BFCL 결과는 논문으로 쓸 가치가 충분하다. 다만 주장 문장은 조심해야 한다.

강하게 주장 가능한 것:

- verbose native tool schema를 compact Action IR로 바꾸면 input token을 크게 줄일
  수 있다.
- 이 절감은 real-world function-calling benchmark에서도 유지된다.
- callable task에서는 native tool calling과 거의 같은 AST accuracy를 보인다.
- latency 절감은 반복 측정에서도 재현된다.
- schema compiler + validator + emitter 구조는 benchmark schema를 자동으로
  Action IR contract로 바꾸는 MLOps pipeline의 핵심이 될 수 있다.

아직 주장하면 안 되는 것:

- DSL이 native보다 정확도가 높다.
- repair loop가 end-to-end accuracy를 개선한다.
- BFCL 전체/모든 언어/모든 provider에서 일반화된다.
- 소형 모델 최적화가 검증되었다.

논문 제목/초록에는 다음 정도가 적절하다.

> ToolCallOpt compiles verbose tool schemas into compact Action IRs, reducing
> input tokens by 62% and p50 latency by 22% on a BFCL v4 replay while preserving
> near-parity AST accuracy on callable tool-calling tasks.

## 10. 다음 단계

우선순위는 다음 순서가 좋다.

1. **M5: Abstention/no-call support**
   `irrelevance` gap을 닫기 위해 `allow_empty_calls`, `{"calls":[]}`, 또는
   `no_call` action을 명시적으로 도입한다.
2. **M6: Value/unit canonicalization**
   `0.05` vs `5.0`, `Chicago` vs `Chicago, IL`, `en` vs `English` 같은 오류를
   compiler-generated normalizer 또는 alias discovery로 흡수한다.
3. **M7: Larger toolset scaling**
   BFCL sample은 6개 이상 tool catalog가 없으므로, 20/50/100 tool setting을
   별도로 구성한다.
4. **M8: Small model experiment**
   Qwen 3.6 Plus가 아니라 1.5B/3B/7B급 모델에서 DSL이 native schema prompting보다
   유리한지 확인한다.
5. **M9: Provider adapter**
   DSL output을 OpenAI, DashScope, MCP, local executor로 변환하는 adapter를 명확히
   분리한다.

## 11. 최종 판단

BFCL 재실험 결과는 연구 방향을 강화한다. IoT POC가 "가능성"이었다면, BFCL은
"외부 benchmark에서도 비용 이점이 유지된다"는 증거다. 특히 callable-only gap이
0.25pp라는 점은 매우 좋은 신호다.

현재 가장 설득력 있는 contribution은 다음이다.

> ToolCallOpt is a compiler-guided MLOps framework that optimizes LLM tool
> calling by compiling verbose tool schemas into compact Action IRs, preserving
> near-parity callable accuracy while substantially reducing token cost and
> latency.

이제 연구의 병목은 core concept이 아니라 productization/research hardening이다.
즉 no-call contract, value normalization, large-tool scaling, small-model
fine-tuning을 추가하면 workshop 수준을 넘어 systems/MLSys 계열 논문으로 발전시킬
수 있다.
