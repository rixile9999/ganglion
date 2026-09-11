# Ganglion: 스펙 기반 도구 호출 최적화 모델 팩토리 — 컴팩트 Action IR로 네이티브 함수 호출 스키마를 대체하기

> 학회 제출용 2페이지 확장 초록 초안 (2026-09-11). 모든 수치는 `docs/poc_verification_report.md`,
> `runs/bfcl/`, `runs/factory_phase2/`에서 그대로 가져왔다. 구조도는
> `docs/diagrams/fig1_factory_loop_col.{pdf,svg,png}` (그림 1, 1단 폭 세로형; 2단 통합 폭용은 `fig1_factory_loop.*`) 사용. 저자·소속·키워드는
> 학회 양식에 맞춰 채울 것. 참고문헌은 본문 등장 순서로 번호를 매겼다.

---

## 요약

대규모 언어 모델(LLM)의 도구 호출(tool calling)은 호출 가능한 함수의 전체 JSON 스키마를 매 요청마다 프롬프트에 주입하는 방식으로 동작한다[1]. 이 방식은 도구 수가 늘어날수록 입력 토큰과 응답 지연이 선형 이상으로 증가하며, 온디바이스·소형 모델 환경에서는 비용과 정확도 모두에서 병목이 된다. 본 논문은 네이티브 스키마 대신 도구 카탈로그를 압축한 텍스트 규약과 컴팩트한 중간 표현(Action IR)을 모델에 제시하고, 결정적 파서·검증기가 이를 실제 함수 호출로 낮추는(lowering) 구조를 제안한다. 나아가 이 규약(contract)을 중심으로 언어 모델 생산(lm), 통계 분석 및 규칙 보정(analyzer)의 세 모듈을 결합하여, 임의의 도구 스펙이 주어지면 소형 특화 모델을 자동으로 생산·검증·갱신하는 **모델 팩토리** 아키텍처를 제시한다. 자체 IoT 벤치마크와 BFCL v4 단일 턴 벤치마크에서 Action IR 경로는 네이티브 경로와 동등한 정확도를 유지하면서 입력 토큰을 46~68 % 절감하였고, 0.6B 규모 소형 모델은 팩토리 루프 1회로 정확 일치율 38.6 %에서 97.1 %까지 상승하였다.

## 1. 서론

에이전트형 LLM 응용이 확산되면서 Model Context Protocol(MCP)[2] 등 표준화된 도구 서버가 등장하였고, 하나의 에이전트가 접근하는 도구 수는 수십에서 수백 개로 늘고 있다. 그러나 OpenAI 함수 호출 규격[1]을 비롯한 현행 방식은 매 요청마다 모든 도구의 이름·설명·매개변수 JSON Schema를 컨텍스트에 포함시킨다. 도구 카탈로그가 커질수록 (i) 입력 토큰 비용이 증가하고, (ii) 프리필 지연이 늘어나며, (iii) 소형 모델은 긴 스키마 속에서 올바른 도구와 인자를 고르지 못한다. 특히 스마트홈·로봇처럼 도구 집합이 고정되어 있고 로컬 추론이 요구되는 도메인에서는 "매번 스키마를 다시 읽히는" 설계 자체가 낭비다.

본 연구의 출발점은 다음 가설이다. **도구 스키마의 의미는 결정적 코드(파서·검증기·정규화기)가 담당하고, 모델은 짧은 중간 표현만 생성하도록 역할을 나누면, 정확도를 잃지 않으면서 입력 비용을 크게 줄일 수 있다.** 이 분리는 부수적으로 두 가지 이점을 만든다. 첫째, 모델 출력이 형식 규약에 의해 검증되므로 실패를 구조적으로 분류할 수 있다. 둘째, 분류된 실패 분포는 규약 수정(별칭·기본값·범위 규칙)이나 학습 데이터 증강으로 환류될 수 있어, 특정 스펙에 특화된 소형 모델을 반복적으로 개선하는 파이프라인이 성립한다. 본 논문은 이 가설을 검증한 POC 결과와, 이를 일반화한 팩토리 아키텍처를 보고한다.

## 2. 본론

### 2.1 규약(Contract): 카탈로그와 Action IR

도구 집합은 `Catalog`로 표현하며, 각 도구는 이름·설명·타입 있는 인자(열거형, 정수, 실수, 문자열, 불리언, 시각)로 구성된 `ToolSpec`이다. 열거형·문자열 인자는 별칭 테이블을 가져 "거실"→`living`과 같은 정규화를 규약 층에서 수행한다. 하나의 카탈로그에서 두 가지 표면을 렌더링한다. (a) 네이티브 경로용 OpenAI `tools=[...]` 스키마, (b) IR 경로용 압축 텍스트 규약. 모델은 IR 경로에서 `{"calls":[{"action":"set_light","args":{"room":"living","brightness":70}}]}` 형태의 Action IR만 출력하고, 파서는 이를 불변 `ActionPlan`으로 변환한다. `ActionPlan`의 값 동등성이 정확 일치(exact match) 지표가 되며, 빈 호출 목록 `{"calls":[]}`을 허용하는 **null-action 규약**으로 "도구를 부르지 않아야 하는" 경우까지 동일한 검증기로 처리한다. 외부 스키마(OpenAI / MCP / BFCL)는 스키마 컴파일러가 실행 시점에 `Catalog`로 변환하므로 벤치마크마다 별도 코드가 필요 없다.

### 2.2 팩토리 아키텍처

그림 1은 규약을 중심으로 한 전체 구조다. 도메인 스펙과 배포 목표(품질 하한, 지연 상한, 장치 메모리)를 입력받아 규약 컴파일러가 IR과 검증기를 생성하고, 제어기가 규칙 보정·SFT·압축 중 어떤 후보를 만들지 선택한다. 후보는 독립 평가기를 거쳐 모델·규약·보정 규칙·실행 설정을 하나로 묶은 릴리스 번들로 배포되며, 운영 트레이스는 분석기로 돌아가 다음 개선 후보가 된다. 구현은 세 모듈로 나뉜다.

- **contract** (모듈 3): 카탈로그, IR 파서·검증기, 스키마 컴파일러. 다른 두 모듈은 이 규약을 통해서만 통신한다.
- **lm** (모듈 1): API/vLLM[7] 클라이언트, 교사 모델 기반 데이터 합성, LoRA[4] SFT 학습기, 카탈로그를 JSON Schema로 컴파일해 디코딩을 제약하는 문법 마스크(XGrammar)[5].
- **analyzer** (모듈 2): 추가 전용 트레이스 저장소, 14종 실패 분류 체계(문법 오류, 미지 도구, 필수 인자 누락, 별칭 미해석, 부적절한 기권 등), 수리(repair) 루프 정책, 실패 히스토그램에서 `ToolSpec` 패치를 제안하는 규칙 합성(R1–R11).

> **그림 1.** Ganglion 팩토리 루프 (`docs/diagrams/fig1_factory_loop_col.pdf`). 실선은 생산·배포·운영 흐름, 점선은 분석기가 제어기로 되돌리는 개선 피드백.

### 2.3 실험 설정

세 가지 벤치마크를 사용하였다. (1) **IoT 조명 벤치마크**: 자체 제작 500건 한국어 데이터셋. 도구 수를 5 / 20 / 50으로 늘린 세 티어가 같은 프롬프트를 공유하여 카탈로그 크기의 효과만 분리한다. (2) **BFCL v4 단일 턴**[3]: `simple`, `multiple`, `parallel`, `parallel_multiple`, `irrelevance` 다섯 범주에서 seed=42로 각 100건을 추출한 500건. 채점은 BFCL의 AST 일치 기준을 따른다. (3) **소형 모델 팩토리**: Qwen3-0.6B[6]를 학생 모델로, 교사 모델이 합성한 IoT 데이터(학습 271건, 홀드아웃 70건)로 LoRA SFT를 1회 수행한다. 대형 모델 실험은 qwen3.6-plus와 qwen3.6-flash를 사용하였고, IR 경로와 네이티브 경로는 동일한 검증기와 동등성 기준으로 채점된다.

### 2.4 결과

**표 1. IoT 벤치마크 카탈로그 스케일링 (qwen3.6-plus, 50건 표본)**

| 티어 (도구 수) | 스키마 길이 비 (native/IR) | 정확 일치 IR / native | 입력 토큰 절감 |
|---|---:|---:|---:|
| iot_light_5 (5) | 1.58× | 100 % / 98 % | 45.2 % |
| home_iot_20 (20) | 2.69× | 100 % / 96 % | 62.5 % |
| smart_home_50 (50) | 3.40× | 100 % / 98 % | 68.5 % |

도구 수가 5에서 50으로 늘어날 때 네이티브 스키마는 2,062자에서 15,795자로 증가한 반면 IR 규약은 1,307자에서 4,643자에 그쳤고, 절감률은 카탈로그 크기와 함께 커졌다. 50건 × 5회 반복 측정에서 IR 경로의 p50 지연은 1,388 ms로 네이티브 1,766 ms보다 짧았고 표준편차도 작았다(376 ms vs 498 ms).

**표 2. BFCL v4 단일 턴 500건 (AST 일치)**

| 모델 | IR 경로 | 네이티브 | 문법 유효율 IR / native | 입력 토큰 절감 | p50 지연 절감 |
|---|---:|---:|---:|---:|---:|
| qwen3.6-plus | **86.2 %** | 85.6 % | 97.6 % / 81.0 % | 53.9 % | 25.5 % |
| qwen3.6-flash | **80.8 %** | 80.4 % | — | 54.7 % | 14.9 % |

외부 벤치마크에서도 IR 경로는 네이티브와 동등하거나 소폭 앞섰다. 특히 `irrelevance` 범주는 null-action 규약 도입 후 90.0 %로 네이티브(86.0 %)를 앞섰으며, 문법 유효율의 16.6 %p 차이는 IR이 소형·약한 모델에서 더 안정적인 출력 형식임을 시사한다.

**표 3. 소형 모델 팩토리 루프 (Qwen3-0.6B, iot_light_5)**

| 단계 | 문법 유효율 | 정확 일치 |
|---|---:|---:|
| 미조정 기준선 | 65.8 % | 38.6 % |
| + 수리 루프 (재시도 1회) | 72.2 % | 41.8 % |
| + LoRA SFT (합성 데이터, 홀드아웃 70건) | 98.6 % | **97.1 %** |

미조정 0.6B 모델의 실패는 필수 인자 누락, 미선언 인자, 별칭 미해석 세 유형에 집중되었다. 수리 루프는 문법 오류만 부분적으로 흡수하였고, 실패 분포를 근거로 합성한 학습 데이터로 SFT를 1회 수행하자 대형 모델 수준의 정확도에 도달하였다. 이는 실패 분류 → 데이터 합성 → 학습의 환류가 실제로 닫힌 루프로 작동함을 보여준다.

## 3. 결론

본 논문은 도구 호출에서 스키마의 의미 해석을 모델이 아닌 규약 층으로 옮기는 설계를 제안하고, 세 벤치마크에서 검증하였다. Action IR 경로는 네이티브 함수 호출과 동등한 정확도를 유지하면서 입력 토큰을 46~68 % 절감하였고, 절감 폭은 카탈로그가 커질수록 확대되었다. 규약 중심 구조는 실패를 14종으로 분류하고 규칙·데이터로 환류시키는 팩토리 루프를 가능하게 하였으며, 0.6B 소형 모델을 한 번의 루프로 실용 수준까지 끌어올렸다. 한계로는 (i) 슬롯이 대부분 선택적 문자열인 스키마(예: Home Assistant Assist API[8])에서는 압축 이득이 1.16×로 작다는 점, (ii) BFCL 단일 턴은 전체 v4 가중치의 10 %에 해당하는 포화 구간이라는 점, (iii) 현재 결과가 API 모델 중심이라는 점이 있다. 후속 연구로 로컬 vLLM 서빙 기반의 다중 모델 실패 분포 조사, MCP 실서버 벤치마크와 Home Assistant 합성 홈을 포함한 다층 벤치마크, 실제 스마트홈 장치와 소형 로봇에서의 실증을 진행 중이다.

## 참고문헌

[1] OpenAI, "Function calling," OpenAI API Documentation. https://platform.openai.com/docs/guides/function-calling (접속 2026-09-11).

[2] Anthropic, "Model Context Protocol (MCP) Specification," 2024. https://modelcontextprotocol.io (접속 2026-09-11).

[3] S. G. Patil, H. Mao, S. Yan, C. C.-J. Ji, V. Suresh, I. Stoica, and J. E. Gonzalez, "The Berkeley Function Calling Leaderboard (BFCL): From Tool Use to Agentic Evaluation of Large Language Models," in *Proc. International Conference on Machine Learning (ICML)*, 2025. https://gorilla.cs.berkeley.edu/leaderboard.html

[4] E. J. Hu, Y. Shen, P. Wallis, Z. Allen-Zhu, Y. Li, S. Wang, L. Wang, and W. Chen, "LoRA: Low-Rank Adaptation of Large Language Models," in *Proc. International Conference on Learning Representations (ICLR)*, 2022.

[5] Y. Dong, C. F. Ruan, Y. Cai, R. Lai, Z. Xu, Y. Zhao, and T. Chen, "XGrammar: Flexible and Efficient Structured Generation Engine for Large Language Models," in *Proc. Machine Learning and Systems (MLSys)*, 2025.

[6] A. Yang et al. (Qwen Team), "Qwen3 Technical Report," arXiv:2505.09388, 2025.

[7] W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica, "Efficient Memory Management for Large Language Model Serving with PagedAttention," in *Proc. 29th ACM Symposium on Operating Systems Principles (SOSP)*, 2023.

[8] Home Assistant, "LLM API," Home Assistant Developer Documentation. https://developers.home-assistant.io/docs/core/llm/ (접속 2026-09-11).
