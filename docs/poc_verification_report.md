# RLM POC 검증 보고서

작성일: 2026-04-28

## 1. 요약

본 POC는 `overview.md`의 핵심 가설인 "LLM에 전체 tool schema를 매번 제공하는 대신, 더 짧은 중간 표현을 생성하게 한 뒤 deterministic parser/validator가 실제 tool call로 변환하면 토큰 비용과 응답 지연을 줄일 수 있다"를 검증하기 위해 구현되었다.

1차 검증 도메인은 IoT 조명 제어이며, LLM은 DashScope의 OpenAI-compatible API를 통해 `qwen3.6-plus`를 사용했다. 모델 출력 형식은 Qwen이 공식 지원하는 structured JSON output을 사용했다.

M1에서 IoT 평가셋을 12건에서 500건으로 확장했다. 500건 전체는 deterministic offline runner로 semantic-normalized exact match 100%를 확인했고, Qwen API는 대표 50건 sample로 structured JSON DSL과 native tool calling baseline을 비교했다. 50건 sample 기준 두 방식 모두 exact match 100%를 달성했으며, structured JSON DSL 방식은 native tool calling 대비 입력 토큰을 약 46.0%, 전체 토큰을 약 45.9% 줄였다.

추가 ablation으로 `response_format`을 끈 non-thinking freeform 출력과 thinking mode 출력도 12건 seed set에서 비교했다. non-thinking freeform은 seed set에서는 12건 모두 strict JSON을 출력했지만, structured output처럼 API 레벨 보장이 있는 것은 아니다. thinking mode는 정확도는 100%였으나, structured JSON DSL 대비 output token이 약 11.7배로 증가했고 p50 latency도 약 3.4배 증가했다.

## 2. 검증 가설

검증한 가설은 다음과 같다.

```text
H1. 전체 tool schema 대신 compact JSON DSL catalog를 제공해도 tool-call 정확도를 유지할 수 있다.
H2. compact JSON DSL 방식은 native tool calling 방식보다 입력 토큰 사용량이 낮다.
H3. structured JSON output과 validator를 결합하면 downstream tool execution에 안전한 IR을 얻을 수 있다.
```

이번 POC는 H1, H2, H3의 초기 가능성을 확인하는 단계다. 500건으로 확장했지만 아직 template 기반 synthetic dataset이고 도메인이 단순하므로, 일반화된 결론을 내려면 tool 수와 실제 사용자 표현 분포를 더 넓혀야 한다.

## 3. 구현 범위

구현된 경로는 5개다.

```text
rules
  deterministic rule-based client
  API 비용 없는 회귀 테스트용

qwen
  user prompt
  -> qwen3.6-plus structured JSON output
  -> JSON DSL validator
  -> normalized ActionPlan
  -> tool call

qwen-native
  user prompt + full OpenAI tool schema
  -> qwen3.6-plus native tool calling
  -> JSON arguments validator
  -> normalized ActionPlan
  -> tool call

qwen-text
  user prompt + compact JSON DSL catalog
  -> qwen3.6-plus non-thinking mode
  -> no response_format
  -> lenient JSON extraction
  -> normalized ActionPlan

qwen-thinking
  user prompt + compact JSON DSL catalog
  -> qwen3.6-plus thinking mode
  -> no response_format
  -> streaming response collection
  -> lenient JSON extraction
  -> normalized ActionPlan
```

주요 파일:

- `rlm_poc/runtime/qwen.py`: Qwen JSON DSL client와 native tool calling client
- `rlm_poc/schema/iot_light.py`: compact JSON DSL catalog와 native tool schema
- `rlm_poc/dsl/validator.py`: JSON DSL 검증, room/state/time/scene name normalization
- `rlm_poc/eval/runner.py`: 평가 실행기
- `examples/iot_light/generate_dataset.py`: 500건 deterministic dataset generator
- `examples/iot_light/dataset.jsonl`: 500건 IoT 조명 평가셋

## 4. 모델/API 설정

실제 LLM 호출은 다음 설정으로 실행했다.

```text
provider: DashScope / Qwen Cloud
model: qwen3.6-plus
base_url: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
api_key: DASHSCOPE_API_KEY 환경변수
structured output: response_format={"type": "json_object"}
thinking mode: disabled
```

Qwen structured output 문서상 JSON mode는 prompt 안에 `JSON` 키워드를 포함해야 하며, thinking mode에서는 structured output을 지원하지 않는다. 따라서 본 POC의 기본 경로는 non-thinking + structured JSON output이다.

참고 문서:

- Qwen Cloud quickstart: https://docs.qwencloud.com/
- Alibaba Cloud Model Studio structured output: https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output

## 5. JSON DSL 설계

JSON DSL은 모델이 직접 최종 tool call을 생성하지 않고, 짧은 action plan을 생성하도록 설계했다.

예시:

```json
{
  "calls": [
    {
      "action": "set_light",
      "args": {
        "room": "living",
        "state": "on",
        "brightness": 70
      }
    }
  ]
}
```

지원 action:

```text
list_devices
get_light_state
set_light
schedule_light
create_scene
```

검증기는 다음을 수행한다.

- JSON parse 가능 여부 확인
- 허용된 action인지 확인
- 필수 인자 존재 여부 확인
- room, state, color_temp, time, brightness 값 범위 검증
- 한국어/영어 alias를 canonical value로 정규화
- `create_scene` 내부 action은 `set_light`만 허용

예를 들어 `주방`은 `kitchen`, `영화 모드`는 `movie`, `70%`는 정수 `70`으로 정규화된다.

## 6. 데이터셋

현재 평가셋은 500건이다. 최초 12건 seed case를 유지하고, generator가 나머지 488건을 deterministic하게 생성한다.

도메인 커버리지:

```text
set_light: 180건
schedule_light: 140건
get_light_state: 100건
list_devices: 40건
create_scene: 40건
```

언어 커버리지:

```text
한국어/혼합 명령: 325건
영어 명령: 175건
```

파라미터 커버리지:

```text
room: living, bedroom, kitchen, hallway, office
state: on, off
brightness: 10, 20, 35, 50, 70, 80, 100
color_temp: warm, neutral, cool
schedule time: 24 unique HH:MM values
```

대표 50건 sample의 action 분포:

```text
set_light: 13건
schedule_light: 11건
get_light_state: 10건
list_devices: 8건
create_scene: 8건
```

데이터셋 재생성:

```bash
python examples/iot_light/generate_dataset.py
```

## 7. 실행 방법

오프라인 회귀 테스트:

```bash
python -m pytest
python -m rlm_poc.eval.runner --llm rules
```

Qwen structured JSON DSL 평가:

```bash
python -m rlm_poc.eval.runner --llm qwen
```

Qwen non-thinking, no `response_format` 평가:

```bash
python -m rlm_poc.eval.runner --llm qwen-text
```

Qwen thinking mode, no `response_format` 평가:

```bash
python -m rlm_poc.eval.runner --llm qwen-thinking
```

Native tool calling baseline 평가:

```bash
python -m rlm_poc.eval.runner --llm qwen-native
```

## 8. 검증 결과

M1 전체 offline 검증:

| 경로 | 총 케이스 | Syntax valid | Exact match | Action match | Input tokens | Output tokens | p50 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | 500 | 100% | 100% | 100% | N/A | N/A | 0.01 ms |

M1 Qwen API 대표 50건 sample:

| 경로 | 총 케이스 | Syntax valid | Exact match | Action match | Input tokens | Output tokens | p50 latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen structured JSON DSL | 50 | 100% | 100% | 100% | 21,757 | 1,208 | 1,354.98 ms | 2,229.57 ms |
| qwen native tools | 50 | 100% | 100% | 100% | 40,307 | 2,134 | 1,607.96 ms | 2,413.57 ms |

M1 50건 sample 토큰 절감:

| 항목 | Native | JSON DSL | 절감량 | 절감률 |
| --- | ---: | ---: | ---: | ---: |
| Input tokens | 40,307 | 21,757 | 18,550 | 46.0% |
| Output tokens | 2,134 | 1,208 | 926 | 43.4% |
| Total tokens | 42,441 | 22,965 | 19,476 | 45.9% |

12건 seed set ablation 결과:

| 경로 | 총 케이스 | Syntax valid | Exact match | Action match | Input tokens | Output tokens | p50 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen structured JSON DSL | 12 | 100% | 100% | 100% | 5,226 | 291 | 1,532.09 ms |
| qwen non-thinking/no-json-mode | 12 | 100% | 100% | 100% | 5,250 | 291 | 1,400.11 ms |
| qwen thinking/no-json-mode | 12 | 100% | 100% | 100% | 5,226 | 3,418 | 5,259.68 ms |

12건 seed set 추가 latency:

| 경로 | p95 latency |
| --- | ---: |
| qwen structured JSON DSL | 1,783.34 ms |
| qwen non-thinking/no-json-mode | 1,919.33 ms |
| qwen thinking/no-json-mode | 9,835.95 ms |

JSON parse strategy:

| 경로 | strict JSON | fenced/embedded extraction | reasoning chars |
| --- | ---: | ---: | ---: |
| qwen structured JSON DSL | API JSON mode | N/A | N/A |
| qwen non-thinking/no-json-mode | 12/12 | 0/12 | N/A |
| qwen thinking/no-json-mode | 12/12 | 0/12 | 9,934 |

Thinking mode 비용 증가:

| 항목 | Structured JSON DSL | Thinking/no-json-mode | 증가량 | 배율 |
| --- | ---: | ---: | ---: | ---: |
| Output tokens | 291 | 3,418 | +3,127 | 11.7x |
| Total tokens | 5,517 | 8,644 | +3,127 | 1.6x |
| p50 latency | 1,532.09 ms | 5,259.68 ms | +3,727.59 ms | 3.4x |
| p95 latency | 1,783.34 ms | 9,835.95 ms | +8,052.61 ms | 5.5x |

## 9. 결과 해석

이번 결과에서 가장 의미 있는 지점은 token reduction이다. 동일한 모델과 동일한 50건 sample에서 native tool calling은 전체 tool schema를 request마다 전달해야 하므로 입력 토큰이 크게 증가했다. 반면 JSON DSL 경로는 compact catalog와 예시만 전달하고, downstream에서 validator가 실제 tool call 형태로 변환한다.

정확도 측면에서는 500건 offline과 50건 Qwen sample 모두 100%를 달성했다. 다만 이는 room/action/scene name에 대한 semantic normalization 이후의 exact match다. 실제 모델 raw output만 놓고 보면 native tool calling은 `movie` 대신 `영화 감상`, `영화 감상 모드`, `movie scene` 같은 scene name을 생성한 적이 있었고, validator가 이를 canonical value로 정규화했다.

Latency는 structured JSON DSL이 native tool calling보다 p50/p95 모두 낮게 나왔지만, 50건 sample의 단일 실행 결과만으로 latency 우위를 강하게 주장하기는 어렵다. API 네트워크 지연, 서버 부하, 모델 내부 실행 상태의 영향을 분리하지 못했기 때문이다. 현재 단계에서는 latency보다 token reduction이 더 강한 신호다.

Non-thinking/no-json-mode는 12건 seed set에서 모두 strict JSON을 출력했다. 이는 `qwen3.6-plus`가 짧고 명확한 JSON DSL prompt를 잘 따랐다는 긍정적 신호다. 그러나 이 경로는 API 레벨의 structured output 보장이 없으므로, production 기본값으로 두기보다는 fallback/ablation 비교군으로 남기는 것이 적절하다.

Thinking/no-json-mode는 정확도는 유지했지만, 본 task에는 비용 대비 이득이 없었다. reasoning stream에서 9,934자의 reasoning content가 발생했고, output token은 structured JSON DSL 대비 약 11.7배 늘었다. IoT 조명 intent-to-tool-call처럼 짧고 폐쇄적인 변환 문제에서는 thinking mode가 오히려 latency와 token cost를 크게 악화시키는 것으로 보인다.

## 10. 확인된 장점

JSON DSL 방식의 장점:

- 전체 tool schema를 매번 제공하지 않아 입력 토큰이 감소한다.
- Qwen structured output을 사용해 JSON syntax failure 가능성을 줄인다.
- validator가 canonical tool call을 만들어 downstream executor는 모델 출력 흔들림에 덜 노출된다.
- native tool calling을 지원하지 않는 모델에도 이식 가능한 구조다.
- 향후 fine-tuning/LoRA용 supervised target으로 쓰기 쉽다.

추가 실험에서 확인한 점:

- non-thinking freeform 출력도 짧은 DSL에서는 strict JSON을 안정적으로 생성했다.
- thinking mode는 복잡한 reasoning task에는 의미가 있을 수 있지만, 현재 DSL 변환 task에는 과도하다.
- structured output은 가장 보수적인 기본값으로 적합하다. prompt-following만 믿는 freeform 방식보다 실패 표면이 작다.

## 11. 확인된 리스크

현재 POC의 한계:

- 데이터셋은 500건이지만 template 기반 synthetic data다.
- IoT 조명 도메인은 tool 수와 argument 복잡도가 낮다.
- multi-turn interaction이 없다.
- tool 간 dependency, permission, irreversible action 같은 agent safety 이슈를 다루지 않았다.
- JSON Schema strict mode 수준의 schema enforcement가 아니라 `json_object` 모드와 자체 validator 조합이다.
- latency 측정은 반복 실행/분산 통계가 부족하다.
- thinking mode는 structured output과 동시에 사용할 수 없어서 별도 freeform 경로로만 평가했다.
- no-json-mode 결과가 이번에는 strict JSON이었지만, 더 긴 prompt나 복잡한 toolset에서도 유지된다는 보장은 없다.

제품화 관점의 리스크:

- tool 수가 많아지면 DSL catalog도 다시 커질 수 있다.
- 서로 유사한 action이 많아지면 action selection 오류가 늘어날 수 있다.
- validator가 너무 많은 alias/보정 규칙을 갖게 되면 규칙 관리 비용이 증가한다.
- model-specific structured output 기능에 의존하면 provider portability가 낮아질 수 있다.

## 12. 다음 실험 계획

우선순위 높은 다음 작업:

1. tool 수를 5개에서 20-50개로 확장해 token scaling 확인
2. 각 경로를 최소 5회 반복 실행해 latency 평균, p50, p95, 표준편차 측정
3. invalid JSON, missing field, wrong enum, unsupported action에 대한 repair loop 추가
4. JSON DSL catalog 크기와 native tool schema 크기를 케이스별로 측정
5. no-json-mode에서 fenced JSON, 설명문 혼입, trailing text가 발생하는 실패율 측정
6. thinking mode는 복잡한 multi-step tool planning task에서만 재평가
7. 500건 전체 Qwen API 평가는 비용/시간 예산을 잡고 별도 실행

2차 연구 과제:

1. 텍스트 DSL + EBNF parser 비교군 추가
2. BFCL 일부 케이스로 외부 벤치마크 연결
3. tool schema 입력에서 JSON DSL catalog와 validator stub을 자동 생성
4. 축적된 dataset으로 small model SFT/LoRA 가능성 검증
5. MCP server tool schema를 입력으로 받아 RLM adapter를 자동 구성

## 13. 중간 결론

현재 POC는 연구개발을 계속 진행할 만한 신호를 보여준다. 특히 native tool calling 대비 약 46%의 token reduction은 tool 수가 증가하는 환경에서 더 큰 차이로 확대될 가능성이 있다.

확장 실험 후 권장 기본 경로는 다음과 같다.

```text
기본값: qwen3.6-plus non-thinking + structured JSON output
보조 비교군: qwen3.6-plus non-thinking + no response_format
비권장 기본값: qwen3.6-plus thinking mode for simple DSL conversion
```

다만 정확도 100%라는 결과는 아직 controlled synthetic dataset에서의 결과다. 다음 단계에서는 tool 수를 늘려 "schema 규모가 커질수록 JSON DSL 방식의 효율성이 얼마나 유지되는가"를 검증해야 한다.

추천하는 다음 milestone은 다음과 같다.

```text
M1. IoT dataset 500건 확장 - 완료
M2. 5/20/50 tool scaling experiment
M3. qwen JSON DSL vs qwen native tools 반복 측정 리포트
M4. 실패 패턴 기반 repair loop 구현
M5. MCP tool schema -> DSL catalog 자동 생성 prototype
```
