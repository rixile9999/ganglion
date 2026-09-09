# Ganglion 연구실 발표 초안 (구체화 v1, 2026-09-09)

> 원 초안의 네 꼭지(벤치마크 구성안 / ml-agent ops benchmark / 실증 / 테스트)를
> 슬라이드 단위로 풀어 쓴 것. 각 슬라이드는 **제목 → 한 줄 메시지 → 본문 →
> 시각 요소 → 발표 노트** 순. 숫자는 `runs/`와 `docs/tasks/`에서 그대로 가져왔고,
> 외부 사실은 하단 링크에 출처를 달았다. 이전 지도교수 발표 덱
> (`docs/rlm_poc_m1_m3_share_final.pptx`)의 톤(문제 제기 → 메커니즘 → 실험 →
> 결과)을 이어받는다.

---

## 0. 발표 흐름 (표지 다음 1장)

**메시지** — "모델 하나를 잘 만드는 게 아니라, *스펙만 주면 툴콜 모델을 찍어내는
공장*을 만든다. 오늘은 그 공장을 **어떻게 측정하고**, **어디에 꽂아 보고**,
**무엇을 테스트할지**를 말한다."

```
Part A  벤치마크 구성안      — 무엇으로 성능을 주장할 것인가
Part B  실증                 — 실제 장치에 꽂아서 보여줄 두 데모
Part C  테스트               — 로컬 모델별 오류 분포 조사 계획
부록    용어·링크
```

지금까지의 결과 한 줄 복습 (이전 발표 연결):

| 실험 | 결과 |
|---|---|
| IoT 500건, qwen3.6-plus | DSL / native 모두 exact match 100 %, 입력 토큰 46 % 절감 |
| BFCL v4 single-turn 500건 (M1') | AST match DSL 0.83 vs native 0.856, 입력 토큰 62 % 절감 |
| 카탈로그 스케일링 (M2) | native/DSL 크기 비율 5툴 1.58× → 50툴 3.40× |
| 소형 모델 팩토리 (Qwen3-0.6B) | baseline 38.6 % → repair 41.8 % → LoRA SFT 97.1 % (holdout 70건) |

---

# Part A. 벤치마크 구성안

## A-1. 단일 벤치마크로는 주장할 수 없다

**메시지** — BFCL v4는 여전히 표준이지만, 우리가 쓰는 구간은 "포화된 10 %"이고
평가기 자체의 신뢰도가 2026년 7월에 정량적으로 반박됐다.

- BFCL v4 (2026-04 개편): Agentic 40 / Multi-turn 30 / Live 10 / **Non-Live 10** /
  Hallucination 10. 우리 `--bfcl` 러너의 다섯 카테고리는 전부 Non-Live.
- 타당성 감사 논문 *Benchmarking the Benchmarks* (arXiv 2607.02577, 2026-07):
  BFCL v4·τ²-Bench·LiveMCPBench·MCP-Atlas를 전문가 496개 과제로 재검토.
  - 평가기 ↔ 사람 판정 **18.5 % 불일치**
  - LiveMCPBench: 같은 설정 23회 반복에 **57.9 %~76.8 %** (18.9 pt 폭)
- 결론: "BFCL 점수 하나"는 리뷰어를 설득하지 못한다. **서로 다른 채점 원리를
  가진 벤치마크를 겹쳐서** 같은 방향의 결과를 보여야 한다.

**시각** — 왼쪽에 BFCL v4 가중치 파이차트(우리 구간 10 % 강조), 오른쪽에
LiveMCPBench 23회 분포 박스플롯 한 줄.

**발표 노트** — "BFCL이 틀렸다"가 아니라 "BFCL만으로는 부족하다"로 말할 것.
우리 M1'~M5' 결과는 그대로 유효하고, 통제 실험으로 계속 쓴다.

## A-2. 후보 검토

**메시지** — 후보 8개를 네 기준으로 걸렀다. 결정 기록은
`docs/tasks/benchmark_selection.md`에 있다.

기준: **C1** 결정론적 채점 · **C2** 오프라인 vLLM 실행 가능 · **C3** 카탈로그
크기/툴 탐색 축을 자극 · **C4** IoT 도메인 근접

| 후보 | 채점 | C1 | C2 | C3 | C4 | 판정 |
|---|---|---|---|---|---|---|
| BFCL v4 single-turn | AST | ○ | ○ | △ | × | **채택 (통제 기준선)** |
| MCPMark (127 tasks, 5 MCP 서비스) | task별 `verify.py` | ○ | ○ | ○ | × | **채택** |
| home-assistant-datasets (assist / assist-mini / intents) | 기대 엔티티 상태 diff | ○ | ○ | △ | **○** | **채택** |
| MCP-Atlas (1,000 tasks, 36 서버, 220 tools) | claim rubric, LLM judge | × | △ | **○** | × | 보류 |
| MCP-Universe | 혼합 | △ | ○ | ○ | × | 보류 |
| τ²-Bench v1.0.1 | 정책 + 상태, 사용자 시뮬 | △ | ○ | × | × | 보류 |
| Tool-Veritas / Harness Lab | 결정론 + 정성 | ○ | ? | ? | × | 보류 |
| LiveMCPBench | LLM judge | × | ○ | ○ | × | 제외 |

**발표 노트** — MCP-Atlas는 "220개 툴 중 고르기"라 우리 논지(카탈로그 입력
비용)에 가장 강한 근거인데, LLM judge + 서버별 API 키가 부담이라 MCPMark 결과가
나온 뒤 재검토.

## A-3. 통합 벤치마크: 세 겹 구조

**메시지** — 각 층은 서로 다른 질문에 답하고, 세 층에서 DSL이 native와 같은
순위를 보여야 "IR 효과"라고 부른다.

```
┌──────────────────────────────────────────────────────────┐
│ 3층  도메인      home-assistant-datasets + 자체 IoT 티어    │
│      질문: 실제 장치 어휘(area/brightness/kelvin)에서도      │
│            IR이 native와 같은 정확도를 내는가               │
├──────────────────────────────────────────────────────────┤
│ 2층  MCP 시대   MCPMark                                     │
│      질문: 실제 MCP 서버 툴 목록(길고 중첩된 스키마)에서      │
│            입력 토큰 절감이 유지되고 verify.py를 통과하는가   │
├──────────────────────────────────────────────────────────┤
│ 1층  통제       BFCL v4 single-turn (기존 M1'~M5')          │
│      질문: 같은 조건에서 DSL ≈ native 인가 (연속성 확보)      │
└──────────────────────────────────────────────────────────┘
```

공통 지표 (analyzer_metrics가 이미 내는 것):

- 정확도: `exact_match_rate` / `ast_match_rate` / MCPMark pass
- 비용: `input_tokens_mean`, `dsl_chars` vs `native_chars`
- 안정성: `syntax_valid_rate`, 반복 실행 표준편차 (`--repeat`)
- 실패 분포: 14종 `FailureType` 히스토그램 (Part C에서 재사용)

**시각** — 위 3층 블록 다이어그램. 각 층 오른쪽에 "채점 원리" 아이콘
(AST / 프로그램 검증 / 장치 상태).

## A-4. ml-agent ops benchmark: 모델이 아니라 *공장*을 측정한다

**메시지** — 이 프로젝트의 산출물은 모델이 아니라 "스펙 → 모델" 파이프라인이다.
따라서 파이프라인 자체의 성능 지표가 필요하다.

파이프라인 (goal.md 3모듈 + `factory.run_pipeline`):

```
spec(Catalog) → synth(교사 LLM) → SFT/DPO(학생) → benchmark → analyzer
      ▲                                                        │
      └──────── rule synthesis (ToolSpec 패치 제안) ◄──────────┘
```

제안 지표 (모두 `runs/`에 파일로 떨어지는 것만):

| 지표 | 정의 | 현재 근거 |
|---|---|---|
| **iterations-to-target** | exact match ≥ T(예 95 %)까지 루프 횟수 | 0.6B: SFT 1회로 97.1 % (iot_light_5) |
| **cost-per-point** | 정확도 1 pt 올리는 데 든 교사 토큰 + GPU 시간 | synth 통계 `*_stats.json`에 토큰 기록 있음 |
| **rule precision** | analyzer가 제안한 ToolSpec 패치 중 사람이 채택한 비율 | R1–R11 패턴 (`analyzer/rules.py`) |
| **failure-mass reduction** | 루프 전후 FailureType 히스토그램 질량 감소 | `#N` echo 19/500, missing-state ~6 % 해소 사례 |
| **reproducibility** | seed 간 loss 표준편차 vs dtype 간 차이 | dtype_pin: seed σ 0.0001, bf16→fp32 Δ 0.024 |
| **spec portability** | 새 스펙(티어) 투입 후 코드 변경 없이 도는 비율 | home_assistant_4: 번역 커버리지 320/500 |

프로토콜 초안:

1. 카탈로그·시드·교사 모델 고정, 학생 모델만 교체.
2. 루프 3회 반복, 매 회 `factory.pipeline.iterated` 이벤트와 summary JSON.
3. 위 여섯 지표를 `runs/factory_ops/<model>/<iter>/` 로 저장, `aggregate.py` 표.

**발표 노트** — "MLOps 벤치마크"라는 말은 넓으니, 슬라이드에는 *agent-model
production benchmark* 로 좁혀 쓰고, 여섯 지표 중 지금 당장 계산 가능한 것
(iterations-to-target, reproducibility, spec portability)에 체크 표시.

---

# Part B. 실증

## B-1. IoT: Open Home 장치 + 스마트 조명

**메시지** — 우리 `iot_light_5` 카탈로그를 실제 스마트홈 표준 툴 모양으로 투영해
두었다. 장치를 사면 **실제 전구 상태**가 ground truth가 된다.

> **잘 모르는 분을 위한 설명** — *Open Home Foundation*은 오픈소스 스마트홈
> 플랫폼 **Home Assistant**와 펌웨어 프레임워크 ESPHome을 소유한 비영리 재단
> (2024-04 설립). Home Assistant는 조명·온도계·잠금장치 등을 클라우드 없이
> 집 안 허브에서 제어하며, 2025년부터 LLM이 집을 제어할 수 있도록 **Assist
> API**라는 툴 세트를 노출한다. 이 툴 세트는 **MCP 서버**(`/api/mcp/assist`)로도
> 그대로 나온다.

하드웨어 (2026-07 가격):

| 품목 | 가격 | 역할 |
|---|---|---|
| Home Assistant Green | $199 / €179 | 허브 |
| Connect ZBT-2 | $49 / €45 | Zigbee 3.0 + Thread 동글 |
| Zigbee 전구 2~3개 (IKEA TRÅDFRI / Hue) | 개당 1~3만원 | 밝기·색온도 지원 확인 |
| (대안) ESPHome Starter Kit | 2026-05 출시 | 직접 만든 장치 |

LLM이 실제로 보는 툴 (Home Assistant `dev` 브랜치 기준, 2026-09-09 확인):

```json
{"name":"HassLightSet",
 "description":"Sets the brightness percentage or color of a light",
 "parameters":{"properties":{
   "name":"string","area":"string","floor":"string",
   "domain":"string","device_class":"string",
   "color":"string","temperature":"integer","brightness":"integer"},
   "required":[]}}
```

우리 쪽 준비 상태 (이미 구현, 테스트 통과):

- `home_assistant_4` 티어: `HassTurnOn` / `HassTurnOff` / `HassLightSet` /
  `GetLiveContext` — HA 슬롯 스키마 그대로.
- `iot_light_5 → Assist` 번역기: 500건 중 **320건** 투영
  (HassLightSet 145, GetLiveContext 140, TurnOff 20, TurnOn 15).
  예약(`schedule_light` 140)·씬(`create_scene` 40)은 HA Assist에 대응 intent가
  없어 **제외** — 근사하지 않음.
- DSL 1,491자 vs native 1,733자 (1.16×). 슬롯이 대부분 optional string이라
  압축 이득이 작다는 점을 솔직히 표시.

실험 설계:

```
자연어("거실 불 70%로 켜줘")
  → 로컬 모델 (DSL 경로 / native 경로)
  → validator → ActionPlan
  → Home Assistant MCP 서버 호출
  → 전구 실제 상태 읽기 (GetLiveContext)
  → expected 상태와 diff  ← ground truth
```

**시각** — 왼쪽 사진(Green + 전구), 오른쪽 위 파이프라인, 아래 320/500 스택바.

**발표 노트** — 장치 구매 전에 `home-assistant-datasets`(합성 홈)로 먼저 돌려
같은 하네스임을 보이고, 장치는 "그 하네스의 물리 버전"으로 소개.

## B-2. 로봇: Microduck

**메시지** — 조명이 "인자 몇 개짜리 툴"이라면, 로봇은 "속도·지속시간·자세가
얽힌 툴"이다. 같은 팩토리가 두 스펙을 모두 소화하는지 본다.

> **잘 모르는 분을 위한 설명** — *Microduck*은 Hugging Face와 프랑스 Pollen
> Robotics가 2026-08-27 예약판매를 시작한 **$399 오리 모양 2족 보행 로봇**.
> 키 25 cm, 800 g, 모터 15개, 카메라·LiDAR·IMU 2개, 온보드 RK3566에서 50 Hz
> 제어 루프. 걷기·앉기·차기·물기(부리)·롤러스케이트·넘어지면 일어나기 등
> 학습된 동작 7종이 기본 탑재. 소프트웨어(SDK, MuJoCo 시뮬, PPO 학습 스택)는
> Apache-2.0 공개, **하드웨어 설계는 비공개**. 배송은 2026년 연말 예정.

왜 이 로봇인가:

- 제어 스택이 **Rust 데몬 + 단일 JSON-RPC 계약**(Unix 소켓)이다. 즉 툴 스키마가
  이미 "계약" 형태로 존재 → `contract/schema_compiler`가 MCP/bare 스키마를
  컴파일하듯 그대로 Catalog로 만들 수 있다.
- 동작이 이산적 스킬(walk / sit / stand / grab / kick / get_up / quack)이라
  툴콜 단위와 자연스럽게 맞는다.
- MuJoCo `duck-sim`이 있어 **실물 도착 전에 시뮬에서 같은 카탈로그**로 실험 가능.

카탈로그 초안 (JSON-RPC 파라미터는 아직 문서화 전이므로 *가정*, 실물 SDK 확인 후 확정):

```
- walk    args {"vx": number -0.3..0.3, "vy": number -0.2..0.2,
                "yaw": number -1..1, "duration_s": number 0..10}
- sit     args {}
- stand   args {}
- grab    args {}
- kick    args {}
- get_up  args {}
- quack   args {"times": optional integer 1..5}
- get_state args {}                       # 배터리·자세·넘어짐 여부
```

실험 설계:

```
"앞으로 두 걸음 가서 앉아"
  → 로컬 모델 → ActionPlan [walk(vx=0.2, duration_s=2), sit()]
  → validator (범위·순서 검증)
  → duck-sim 또는 실물 JSON-RPC
  → get_state로 자세 확인  ← ground truth
```

리스크와 대응:

| 리스크 | 대응 |
|---|---|
| 배송 연말, 파라미터 미공개 | 시뮬 우선, 카탈로그는 `RawArg`로 느슨하게 시작 |
| 연속값 인자 → exact match 부적합 | `graded_score`(analyzer_verifier) 로 허용오차 채점 |
| 하드웨어 비공개 | 소프트웨어 재현성만 주장 |

**시각** — Microduck 사진(공식 페이지), 옆에 카탈로그 코드 블록, 아래 리스크 표.

## B-3. 두 실증의 공통 프레임

**메시지** — 장치가 달라도 파이프라인은 같다. 바뀌는 건 Catalog 한 파일과
ground truth를 읽는 방법뿐이다.

| | 스마트 조명 (Home Assistant) | Microduck |
|---|---|---|
| 툴 수 | 4 | 8 (초안) |
| 인자 성격 | enum + 정수 (0–100, 2000–6500 K) | 실수 범위 + 지속시간 |
| 카탈로그 출처 | HA Assist API (LLM 문서) | JSON-RPC 계약 (schema_compiler) |
| ground truth | 엔티티 상태 diff | 로봇 자세/상태 diff (허용오차) |
| 사전 검증 | home-assistant-datasets 합성 홈 | MuJoCo duck-sim |
| 비용 | 약 $250 + 전구 | $399 |
| 준비 상태 | 티어·데이터셋·테스트 완료 | 조사 단계 |

**발표 노트** — "왜 둘 다?"에 대한 답: 조명은 *스키마 크기* 축(A-3의 3층),
로봇은 *인자 타입 다양성* 축을 각각 자극한다. 하나만 하면 팩토리의
일반성을 주장하기 어렵다.

---

# Part C. 테스트

## C-1. 로컬 모델 후보 (단일 H100 80GB)

**메시지** — DashScope API를 로컬 vLLM으로 바꾼다. H100 한 장에 올라가는
2026-09 기준 최신 오픈 모델만 추렸다.

| 모델 | 구조 | 가중치 VRAM | 역할 |
|---|---|---|---|
| **Qwen3.8-27B** (2026-08-14) | 27B dense, thinking 기본 | BF16 54 GB / FP8 27 GB | 주 모델 |
| **gpt-oss-120b** | 117B MoE, 5.1B active, MXFP4 | ≈61 GB | 비Qwen 대조군 |
| **Qwen3.6-27B** | 27B dense | 54 GB | 기존 `qwen3.6-plus` 결과와 연속성 |
| Gemma 4 31B / 26B-A4B | dense / MoE | 62 / 52 GB | 두 번째 대조군 |
| Nemotron 3 Nano 30B-A3B | MoE | ≈60 GB | 툴콜 특화 소형 |
| Qwen3.5 소형 (2B–9B급), Gemma 4 E2B/E4B | | | SFT 학생 모델 |

제외: Qwen3.8-Max(2.4T), Qwen3.8-Flash-Next(FP8 173 GiB), GLM-5.3(-Flash),
Nemotron 3 Super(FP8 2×H100), Mistral Small 4(FP8 120 GB).

전환 방식: `vllm serve` OpenAI 호환 서버 → `DASHSCOPE_BASE_URL`만 localhost로.
세 클라이언트(`qwen` / `qwen-text` / `qwen-native`) 코드 수정 없음.

## C-2. 오류 유형 분류 체계

**메시지** — "틀렸다"를 14가지로 쪼갠다. 이 분포가 rule synthesis의 입력이다.

`analyzer/taxonomy.py`의 `FailureType` (우선순위 순):

```
syntax_invalid            JSON 자체가 안 열림
unknown_tool              카탈로그에 없는 툴
wrong_action              툴은 있으나 다른 툴
missing_required_arg      필수 인자 누락          ← 0.6B: state 누락 ~6 %
unknown_arg               선언 안 된 인자          ← "#8" echo 19/500
type_mismatch             타입 불일치              ← BFCL: [1,3] vs [1.0,3.0]
value_out_of_enum         enum 밖 값
value_out_of_range        범위 밖 값
alias_unrecognised        "거실" 같은 별칭 미해석
abstention_miss_should_call     불러야 하는데 안 부름
abstention_miss_should_abstain  안 불러야 하는데 부름  ← BFCL irrelevance 0.74
parallel_order_mismatch   병렬 호출 순서/개수
partial_arg_value_mismatch 일부 인자 값만 틀림
no_failure
```

지금까지 관측된 분포 예 (발표에서는 막대그래프로):

- Qwen3-0.6B, iot_light_5: syntax 65.8 % / exact 38.6 % → 실패의 대부분이
  `missing_required_arg`·`unknown_arg`·`alias_unrecognised`. 셋 다 규칙으로
  흡수 가능 → repair + defaults로 41.8 %, SFT로 97.1 %.
- qwen3.6-plus, BFCL m3': `type_error:nested`, `parallel wrong_count`가 주류 →
  규칙보다 스키마 컴파일러 쪽 문제.

## C-3. 실험 매트릭스

**메시지** — 모델·크기·카탈로그·경로·복구를 격자로 돌리고, 각 셀에서 14종
분포를 뽑는다.

```
모델      × Qwen3.8-27B, gpt-oss-120b, Qwen3.6-27B, Gemma4-31B, (학생) 0.6B~9B
카탈로그  × iot_light_5, smart_home_50, home_assistant_4, BFCL per-case, MCPMark
경로      × DSL(json_object) / DSL(freeform) / native tools
복구      × repair off / on (max 1)
반복      × 3 (latency·분산)
```

산출물:

- 셀마다 `summary.json` + `cases.jsonl` (`runs/local/<model>/<tier>/<path>/`)
- 셀마다 FailureType 히스토그램 → `analyzer.rules`에 투입 → ToolSpec 패치 제안
- 모델 간 비교표: 정확도 / 입력 토큰 / p50 latency / 실패 상위 3종

읽는 법: **모델이 커질수록 어떤 실패가 사라지고 어떤 실패가 남는가**. 남는
실패가 규칙으로 흡수되면 팩토리의 몫, 안 되면 모델의 몫.

## C-4. 일정

| 주차 | 할 일 | 산출물 |
|---|---|---|
| 1 | vLLM 서빙 env, Qwen3.8-27B·gpt-oss-120b로 IoT 3티어 + home_assistant_4 재현 | `runs/local/*` |
| 2 | BFCL single-turn 로컬 재현, C-3 매트릭스 1차 (repair off) | 모델 비교표 |
| 3 | MCPMark 어댑터 (`benchmark_mcpmark` 스펙 → 구현) | MCPMark pass 표 |
| 4 | home-assistant-datasets `assist-mini` 실행, HA 장치 구매 결정 | 합성 홈 결과 |
| 5–6 | 실패 분포 → rule synthesis 루프 1회, ops 지표 3종 계산 | A-4 표 채움 |
| 이후 | Microduck 시뮬 카탈로그, 실물 도착 시 B-2 | |

---

# 부록

## 용어 (한 줄씩)

- **Action IR** — 모델이 내는 짧은 중간 표현. `{"calls":[{"action":…,"args":…}]}`.
- **Catalog** — 툴 스펙 묶음. DSL 텍스트와 OpenAI tools 스키마를 같은 소스에서 렌더.
- **BFCL** — Berkeley Function Calling Leaderboard. 함수 호출 정확도 표준 벤치마크.
- **MCP** — Model Context Protocol. LLM이 외부 툴 서버에 접속하는 표준.
- **Home Assistant / Assist API** — 오픈소스 스마트홈 허브와 그 LLM용 툴 세트.
- **Microduck** — HF·Pollen의 $399 오픈소스(SW) 2족 로봇.
- **FailureType** — 실패를 14종으로 나눈 분류. rule synthesis의 입력.

## 링크

벤치마크

- BFCL v4 — <https://gorilla.cs.berkeley.edu/leaderboard.html>
- Benchmarking the Benchmarks (2607.02577) — <https://arxiv.org/abs/2607.02577>
- MCPMark — <https://github.com/eval-sys/mcpmark>
- MCP-Atlas — <https://arxiv.org/abs/2602.00933> · <https://labs.scale.com/leaderboard/mcp_atlas>
- MCP-Universe — <https://github.com/SalesforceAIResearch/MCP-Universe>
- τ²-Bench — <https://github.com/sierra-research/tau2-bench>
- home-assistant-datasets — <https://github.com/allenporter/home-assistant-datasets>

Open Home / Home Assistant

- Open Home Foundation — <https://www.openhomefoundation.org/>
- Home Assistant Green — <https://www.home-assistant.io/green/>
- Connect ZBT-2 — <https://www.home-assistant.io/connect/zbt-2/>
- LLM API 개발 문서 — <https://developers.home-assistant.io/docs/core/llm/>
- MCP Server 통합 — <https://www.home-assistant.io/integrations/mcp_server/>
- light 서비스 스키마 — <https://github.com/home-assistant/core/blob/dev/homeassistant/components/light/services.yaml>
- HassLightSet intent — <https://github.com/home-assistant/core/blob/dev/homeassistant/components/light/intent.py>
- 2026.9 릴리스 (툴 이름 접두사) — <https://www.home-assistant.io/blog/2026/09/02/release-20269/>

Microduck

- 공식 페이지 — <https://pollen-robotics.com/microduck/>
- 소개 블로그 — <https://pollen-robotics.com/microduck/blog/introducing-microduck/>
- 로봇 소프트웨어 — <https://github.com/pollen-robotics/microduck>
- RL 학습 스택 — <https://github.com/pollen-robotics/microduck_rl>
- TechCrunch 기사 (2026-08-27) — <https://techcrunch.com/2026/08/27/hugging-face-is-selling-a-cute-399-open-source-duck-robot-microduck/>

로컬 모델

- Qwen3.8-27B — <https://huggingface.co/Qwen/Qwen3.8-27B>
- gpt-oss-120b — <https://huggingface.co/openai/gpt-oss-120b>
- Qwen3.6-27B — <https://huggingface.co/Qwen/Qwen3.6-27B>
- Gemma 4 31B — <https://huggingface.co/google/gemma-4-31B-it>
- vLLM Qwen3.8 day-0 — <https://vllm.ai/blog/2026-08-12-qwen3.8>

저장소 내부

- 벤치마크 결정 — `docs/tasks/benchmark_selection.md`
- HA 티어 스펙 — `docs/tasks/contract_tier_home_assistant.md`
- 실패 분류 — `ganglion/analyzer/taxonomy.py`
- 이전 결과 — `docs/poc_verification_report.md`, `runs/bfcl/aggregated.json`,
  `runs/factory_phase2/`
