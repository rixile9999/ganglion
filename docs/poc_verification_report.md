# Ganglion POC 검증 보고서

> *compiler-guided optimization for LLM tool calling*

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

- `ganglion/runtime/qwen.py`: Qwen JSON DSL client와 native tool calling client
- `ganglion/schema/iot_light.py`: compact JSON DSL catalog와 native tool schema
- `ganglion/dsl/validator.py`: JSON DSL 검증, room/state/time/scene name normalization
- `ganglion/eval/runner.py`: 평가 실행기
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
python -m ganglion.eval.runner --llm rules
```

Qwen structured JSON DSL 평가:

```bash
python -m ganglion.eval.runner --llm qwen
```

Qwen non-thinking, no `response_format` 평가:

```bash
python -m ganglion.eval.runner --llm qwen-text
```

Qwen thinking mode, no `response_format` 평가:

```bash
python -m ganglion.eval.runner --llm qwen-thinking
```

Native tool calling baseline 평가:

```bash
python -m ganglion.eval.runner --llm qwen-native
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
M2. 5/20/50 tool scaling experiment - 완료 (Qwen API 300 calls, §14.4)
M3. qwen JSON DSL vs qwen native tools 반복 측정 리포트 - 완료 (Qwen API 500 calls, §15.3)
M4. 실패 패턴 기반 repair loop 구현 - 완료, native 실패 escalation은 후속 (§16.4)
M5. MCP tool schema -> DSL catalog 자동 생성 prototype
```

## 14. M2 도구 확장 실험

본 절은 도구 수가 5에서 50으로 늘어나도 JSON DSL 방식이 native tool calling 대비 토큰 효율을 유지하는지를 검증하기 위한 실험 결과다. M2의 핵심 가설은 "tool 수가 증가할수록 native tool calling 대비 JSON DSL의 상대적 우위가 더 커진다"이다.

### 14.1 실험 설계

도메인을 단순 복제하지 않고 현실적인 toolset 확장을 위해 세 단계의 tier를 정의했다.

```text
iot_light_5 (M1 baseline)
  list_devices, get_light_state, set_light, schedule_light, create_scene

home_iot_20 (M1 + 15개 추가)
  set_curtain, set_thermostat, get_thermostat, play_music, stop_music,
  set_music_volume, lock_door, unlock_door, arm_security, set_alarm,
  cancel_alarm, start_robot_vacuum, stop_robot_vacuum, open_garage,
  send_notification

smart_home_50 (home_iot_20 + 30개 추가)
  set_water_heater, start_dishwasher, stop_dishwasher, start_washer,
  stop_washer, start_dryer, stop_dryer, start_oven, stop_oven,
  set_fridge_temp, start_microwave, order_groceries, start_sprinkler,
  stop_sprinkler, start_pool_pump, stop_pool_pump, set_pool_temp,
  open_window, close_window, set_fan, set_air_purifier,
  start_humidifier, start_camera_recording, stop_camera_recording,
  send_email, send_sms, set_reminder, start_timer,
  get_weather_forecast, play_tv
```

평가 prompt는 세 tier 모두 동일한 500건 IoT 조명 데이터셋을 사용한다. 이는 "catalog는 커지지만 정답 행동은 동일한 5종"이라는 통제된 조건에서 catalog 크기가 토큰/정확도에 미치는 영향을 분리하기 위한 설계다.

도구 정의는 `ganglion/dsl/tool_spec.py`의 `ToolSpec` 레지스트리를 통해 선언적으로 관리하며, JSON DSL catalog 텍스트와 OpenAI tool schema는 `Catalog.render_json_dsl()` / `Catalog.render_openai_tools()`로 동일한 source-of-truth에서 생성된다. 따라서 두 표현 간 비교는 apples-to-apples다.

### 14.2 카탈로그 사이즈 측정 (offline, deterministic)

`python -m ganglion.eval.scaling` 결과:

| Tier | Tools | DSL chars | Native chars | Native/DSL |
| --- | ---: | ---: | ---: | ---: |
| iot_light_5 | 5 | 1,307 | 2,062 | 1.58x |
| home_iot_20 | 20 | 2,525 | 6,796 | 2.69x |
| smart_home_50 | 50 | 4,643 | 15,795 | 3.40x |

도구 수가 5에서 50으로 10배 늘어날 때 DSL catalog는 약 3.55배 (1,307→4,643) 증가하지만, native tool schema는 약 7.66배 (2,062→15,795) 증가한다. native/DSL 비율은 1.58x에서 3.40x로 약 2.15배 확대되며, 이는 M2 가설을 강하게 지지한다.

DSL이 더 천천히 커지는 이유는 다음과 같다.

- DSL catalog는 도구당 1줄(평균 ~67자)로 표현되지만, native tool schema는 도구당 JSON Schema 객체(평균 ~270자) 전체를 포함한다.
- 공유되는 enum (room, state 등)이 DSL에서는 한 번만 표현되는 반면, native에서는 각 도구의 parameters에 매번 반복된다.

### 14.3 정확도 (offline, rules client)

500건 IoT 조명 데이터셋으로 deterministic rules client를 세 tier에 모두 실행한 결과 모두 syntax_valid 100%, exact_match 100%였다. 이는 "validator가 tier 확장에 따른 새 도구로 인해 기존 도구 검증 행동이 깨지지 않는다"는 것만 보장하며, 모델이 큰 catalog 안에서 올바른 도구를 고르는 능력은 별도 검증이 필요하다.

```bash
python -m ganglion.eval.runner --llm rules --tier iot_light_5
python -m ganglion.eval.runner --llm rules --tier home_iot_20
python -m ganglion.eval.runner --llm rules --tier smart_home_50
```

세 tier 모두 syntax_valid_rate=1.0, exact_match_rate=1.0.

### 14.4 Qwen API 토큰/정확도 측정 (50건 sample, 2026-04-28)

실제 DashScope `qwen3.6-plus` API로 50건 sample을 세 tier × 두 path에 모두 실행한 결과:

| Tier | Path | Exact match | Input tokens | Output tokens | p50 latency | p95 latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| iot_light_5 | DSL | 100% | 22,757 | 1,210 | 1,279.11 ms | 1,930.75 ms |
| iot_light_5 | Native | 98% | 41,507 | 2,135 | 1,838.93 ms | 2,702.55 ms |
| home_iot_20 | DSL | 100% | 40,907 | 1,210 | 1,208.31 ms | 1,825.28 ms |
| home_iot_20 | Native | 96% | 109,207 | 2,138 | 1,857.88 ms | 2,669.60 ms |
| smart_home_50 | DSL | 100% | 74,057 | 1,210 | 1,334.43 ms | 2,236.64 ms |
| smart_home_50 | Native | 98% | 235,207 | 2,134 | 2,064.54 ms | 2,697.22 ms |

토큰 절감 (Native 대비 DSL):

| Tier | Native input | DSL input | Input 절감률 | Native total | DSL total | Total 절감률 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| iot_light_5 | 41,507 | 22,757 | **45.2%** | 43,642 | 23,967 | 45.1% |
| home_iot_20 | 109,207 | 40,907 | **62.5%** | 111,345 | 42,117 | 62.2% |
| smart_home_50 | 235,207 | 74,057 | **68.5%** | 237,341 | 75,267 | 68.3% |

**핵심 발견.** 도구 수가 5에서 50으로 늘어날 때 DSL의 token reduction은 45%에서 69%로 **확대**된다. offline char 비율 (1.58x → 3.40x = 2.15배)이 실제 token 비율 (1.82x → 3.18x = 1.74배)에서도 같은 방향의 더 강한 신호로 재현된다. M2의 핵심 가설인 "tool 수가 증가할수록 DSL의 우위가 확대된다"가 실제 LLM 호출에서 확인되었다.

**정확도.** DSL path는 세 tier 모두 100% exact match를 유지한 반면, native path는 96-98%로 떨어졌다. 모든 native 실패는 `create_scene` action의 `name` 필드가 canonical "movie" 대신 `Movie Time`, `영화 볼 때`, `movie scene for bedroom light` 등 미정규화 라벨로 생성된 사례였다. 이는 DSL prompt에 `"name":"movie"`를 포함한 example이 모델에 anchoring 효과를 제공한 반면, native path의 JSON Schema는 `name: string`으로만 표현되어 정규화 단서가 없기 때문으로 해석된다.

**Latency.** p50/p95 모두 DSL이 native보다 일관되게 낮다. 단일 50건 측정의 한계는 §15에서 반복 측정으로 보강한다.

### 14.5 한계와 후속 작업

- DSL/native chars 비교는 결정론적 measurement이지만, Qwen tokenizer 기반 token 수는 비례 관계를 따르더라도 절대값은 다르다. tier별 1회 API smoke call로 calibration 권장.
- 500건 데이터셋이 5종 액션만 사용하므로, 도구 수가 50개여도 모델은 5종 중 선택만 하면 된다. "irrelevant tool 수가 증가했을 때 distractor effect가 있는가"는 이 데이터셋으로는 측정할 수 없다. 후속 단계에서는 tier 20/50의 새 도구를 사용하는 prompt를 추가해 catalog-wide selection accuracy를 측정해야 한다.

## 15. M3 반복 측정 인프라

M1 보고서 §9에서 latency 우위 주장이 단일 실행 결과에 의존한다는 한계가 지적되었다. M3는 이를 보완하기 위한 반복 측정 인프라를 추가한다.

### 15.1 구현

`ganglion.eval.runner`에 `--repeat N` flag를 추가했다. 각 case를 N회 호출한 뒤 mean / p50 / p95 / stddev를 모두 합산해 단일 summary로 출력한다.

`CaseResult.runs: tuple[RunResult, ...]` 구조로 회차별 latency, input/output token, raw response, error를 보존한다. exact_match는 모든 회차가 같은 expected와 일치할 때만 true로 평가하므로, "한 번 우연히 맞은" 결과를 걸러낼 수 있다.

### 15.2 offline 검증

```bash
python -m ganglion.eval.runner --llm rules --tier iot_light_5 --limit 50 --repeat 5
```

50 case × 5 repeat = 250 latency sample에서 mean=0.02ms, stddev=0.07ms로 산출된다. rules client는 latency가 ~10µs 수준이라 통계적 의미는 없지만, repeat 인프라가 정상 작동함을 보여준다.

### 15.3 Qwen API 반복 측정 (50건 × 5 repeat = 250 sample/path, 2026-04-28)

iot_light_5 baseline에서 두 path를 각각 5회 반복 실행:

| Path | Exact match | Mean | p50 | p95 | Stddev | Input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DSL | 100% | 1,442.99 ms | 1,388.12 ms | 2,130.21 ms | 376.44 ms | 113,785 | 6,047 |
| Native | 98% | 1,782.29 ms | 1,765.85 ms | 2,753.32 ms | 498.27 ms | 207,535 | 10,674 |

n=250 sample 기준:

- DSL이 native보다 mean latency에서 **339.30 ms (19.0%) 빠르다**. n=250이고 stddev=376/498로 차이가 노이즈를 충분히 상회한다.
- DSL의 stddev가 native보다 **24.5% 낮다**. 동일 prompt에 대한 응답 시간 일관성이 더 높다.
- 토큰 cost는 250 case 기준으로 input 절감 45.2%, output 절감 43.4%. 단일 50건 결과(§14.4)와 일치한다.

M1 보고서 §9에서 "단일 실행의 latency 우위 주장은 약하다"는 한계를 명시했는데, n=250 반복으로 그 우려가 해소되었다. DSL이 native보다 빠른 것은 일관된 효과이며, 이는 input token 양이 작아 (a) prefill 시간 단축과 (b) tool-calling 후처리 절감 두 가지가 합쳐진 결과로 해석된다.

(Native의 exact match가 250개 중 5개 실패한 것은 §14.4와 같은 `create_scene.name` 정규화 누락 사례. 5회 반복에서 1건 case가 일관되게 실패함.)

## 16. M4 Repair Loop

### 16.1 동기

M1 §9에서 native tool calling raw output에 `영화 모드` 대신 `영화 감상 모드` 등 비표준 scene name이 등장한 사례가 보고되었다. 현재는 validator의 `SCENE_ALIASES`가 이를 normalize해서 정확도 100%를 만들었지만, alias 사전 외 패턴이 등장하면 case가 실패한다. M4는 validator 실패를 모델에게 다시 알려서 한 번 더 재시도시키는 repair loop를 도입한다.

### 16.2 구현

`ganglion/runtime/qwen.py`에 `run_dsl_with_repair(catalog, user_prompt, completer, repair_config)` 헬퍼를 추가했다. validator가 `DSLValidationError`를 던지면 다음 메시지를 추가해 동일한 `Completer`를 재호출한다.

```text
assistant: <previous JSON>
user: Your previous JSON failed validation: <error>. Return only valid JSON that matches the DSL.
```

`RepairConfig(enabled=True, max_attempts=N)`로 재시도 횟수를 제한한다.

CLI:

```bash
python -m ganglion.eval.runner --llm qwen --tier iot_light_5 --repair --repair-max-attempts 1
```

`summarize`는 `repair_attempts_total`과 `repair_successes_total`을 집계해서 repair가 얼마나 자주 동원되었고 그중 몇 %가 성공했는지 리포트한다.

### 16.3 offline 검증 (합성 실패 주입)

`tests/test_repair_loop.py`에 `ScriptedCompleter`를 사용한 4개의 단위 테스트를 추가했다.

```text
test_repair_recovers_on_second_attempt
  첫 응답: room="moon" (validator 실패)
  두 번째 응답: room="living" (성공)
  → repair loop가 두 번째 attempt에서 valid plan을 반환

test_repair_disabled_propagates_error
  repair=False일 때 첫 실패에서 DSLValidationError를 그대로 raise

test_repair_exhausts_attempts
  세 번 모두 실패 (max_attempts=2 = 3회 시도 후 raise)

test_repair_recovers_invalid_json
  첫 응답: 자유형 텍스트 (JSON parse 실패)
  두 번째 응답: 정상 JSON
  → JSON parse 단계 실패도 repair가 복구함
```

모든 테스트가 통과하므로 repair loop의 (a) 성공 복구, (b) 정책에 따른 propagation, (c) 시도 횟수 enforcement, (d) JSON parse 실패와 schema 실패 모두 커버 검증되었다.

### 16.4 Qwen API ablation (50건 sample, 2026-04-28)

iot_light_5 + DSL path에서 repair off / on을 50건씩 측정:

| Variant | Exact match | Syntax valid | Input tokens | Output tokens | Repair attempts |
| --- | ---: | ---: | ---: | ---: | ---: |
| repair off | 100% | 100% | 22,757 | 1,209 | 0 |
| repair on (max=1) | 100% | 100% | 22,757 | 1,209 | 0 |

happy-path 측정에서 DSL path는 `qwen3.6-plus`가 100% syntax valid한 JSON을 생성했고, 따라서 repair loop가 자연스럽게 트리거되지 않았다. 이 결과는 다음 두 가지 의미를 가진다.

1. 현재 데이터셋에서는 DSL path의 raw failure rate가 충분히 낮아서 repair loop의 추가 호출 비용은 정확히 0이다. repair=on은 무비용 안전망이다.
2. 그러나 native path에서는 §14.4와 §15.3에서 본 것처럼 `create_scene.name` 라벨링 실패가 일관되게 4-5%대로 발생한다. 이 실패는 `DSLValidationError`가 아니라 *exact_match* 단계에서 잡히므로 (즉, validator는 raw 라벨을 그대로 통과시킴), 현재 repair loop는 native 실패를 잡지 못한다.

후속 작업 후보:

- `SCENE_ALIASES`를 더 빡센 enum으로 강제 (`name: enum["movie", ...]`)하여 native 라벨 오류를 `DSLValidationError`로 escalate하면 repair loop가 잡을 수 있다.
- 또는 ambiguous한 prompt (e.g. "영화 보기 좋은 분위기로") 셋을 추가해 raw failure rate를 인위적으로 끌어올리고 repair recovery rate를 측정한다.
- native path에도 동일한 repair 패턴을 도입 (현재는 DSL path에만 구현됨).

## 17. 종합 변경 사항

### 17.1 코드 구조 변경

```text
ganglion/dsl/tool_spec.py     (신규) ToolSpec, EnumArg, IntArg, StringArg, TimeArg, RawArg
ganglion/dsl/catalog.py       (신규) Catalog: render_json_dsl, render_openai_tools, validate
ganglion/dsl/validator.py     (리팩터) iot_light catalog로 위임하는 thin shim
ganglion/schema/iot_light.py  (리팩터) ToolSpec list로 재정의, JSON_DSL_CATALOG/OPENAI_TOOLS는 derive
ganglion/schema/home_iot.py   (신규) tier 20 catalog
ganglion/schema/smart_home.py (신규) tier 50 catalog
ganglion/schema/__init__.py   (확장) get_catalog(tier), TIERS registry
ganglion/runtime/qwen.py      (확장) Catalog 인자, run_dsl_with_repair, RepairConfig
ganglion/eval/metrics.py      (확장) RunResult, repeat-aware CaseResult, mean/stddev/repair stats
ganglion/eval/runner.py       (확장) --tier, --repeat, --repair, --repair-max-attempts
ganglion/eval/scaling.py      (신규) catalog 사이즈 측정 CLI
tests/test_catalog_tiers.py  (신규) 6개 테스트
tests/test_repair_loop.py    (신규) 4개 테스트
```

### 17.2 검증 결과 요약

```text
pytest:                     18/18 통과
rules @ iot_light_5:        500/500 exact match
rules @ home_iot_20:        500/500 exact match (catalog 4x, prompt 동일)
rules @ smart_home_50:      500/500 exact match (catalog 12x, prompt 동일)

M2 (Qwen API, 50건 × 3 tier × 2 path = 300 calls):
  iot_light_5  DSL/Native 토큰 절감 45.2% (input)
  home_iot_20  DSL/Native 토큰 절감 62.5% (input)
  smart_home_50 DSL/Native 토큰 절감 68.5% (input)
  DSL exact match: 100% (모든 tier)
  Native exact match: 96-98% (create_scene.name 정규화 실패)

M3 (Qwen API, 50건 × 5 repeat × 2 path = 500 calls):
  DSL    mean=1,442.99 ms  stddev=376.44 ms  exact=100%
  Native mean=1,782.29 ms  stddev=498.27 ms  exact=98%
  DSL이 19.0% 빠르고 stddev 24.5% 낮음 (n=250)

M4 (Qwen API, 50건 × repair on/off = 100 calls):
  DSL path는 happy-path에서 100% valid → repair 트리거 0회
  repair=on은 무비용 안전망 (overhead 없음)
  native path 실패는 validator를 통과하므로 현재 loop가 잡지 못함

총 Qwen API 호출: 900건 (M2 300 + M3 500 + M4 100)
```

Qwen API 토큰/latency 측정은 비용 동반이므로 별도 승인 후 §14.4, §15.3, §16.4의 명령으로 실행한다.
