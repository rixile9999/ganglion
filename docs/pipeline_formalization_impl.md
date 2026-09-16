---
title: "Ganglion 학습·추론 파이프라인: 구현 대조 노트"
date: "2026-09-15"
lang: ko-KR
---

## 1. 목적과 적용 범위

> 이 노트는 특정 커밋의 구현을 기호로 옮긴 **구현 대조 자료**다. 구현과 독립적인 이론 문서는 [pipeline_formalization.md](pipeline_formalization.md)이며, 세미나·논문용 정식화는 그쪽을 기준으로 한다. 이 노트의 기본값·측정치·경로는 커밋 `78c8eb9` 기준이다.

이 문서는 Ganglion의 모델 학습과 도구 호출 추론을 하나의 기호 체계로 정의한다. 기준은 저장소 커밋 `78c8eb9`의 Python 구현이다. 수식은 구현을 설명하기 위해 새로 작성한 것이며, 이 문서의 추가가 런타임 구현을 바꾸지는 않는다.

다음 상태를 구분한다.

| 표시 | 의미 | 대상 |
|---|---|---|
| **구현** | 현재 패키지에서 확인한 연산 | Catalog와 두 렌더링, 합성, LoRA SFT, 로컬 생성, grammar mask, 검증·보정, 평가, 표현 비용 측정 |
| **실험** | `runs/`에 남은 연구용 절차 | self-bootstrap, DPO, BFCL별 학습 |
| **제안** | v2 설계 또는 본 문서의 권장 계약 | 독립 릴리스 평가, 비용 기반 후보 선택, 자동 업데이트 |

실험 스크립트에는 이전 모듈 경로와 설정이 남아 있으므로 현재 체크아웃에서 곧바로 실행된다는 뜻은 아니다. 특히 `ganglion/factory.py`는 전체 학습 루프를 자동 실행하지 않는다. 이 문서는 개별 구현을 합성한 학습 절차와 실제 orchestrator의 동작을 구별한다.

핵심 관계는 다음과 같다.

$$
\boxed{
\begin{aligned}
\text{Training:}\quad &(C,\mathcal D,W_0)\longmapsto\phi^*,\\
\text{Inference:}\quad &(C,W_0,\phi^*,x,d)\longmapsto y
  \longmapsto \hat a\longmapsto u.
\end{aligned}}
$$

모델은 DSL 문자열 $y$를 생성한다. 계약 계층은 이를 보정·검증한 계획 $\hat a$로 변환하고, emitter는 도구 호출 데이터 $u$를 만든다. 실제 외부 도구의 실행 성공은 이 변환만으로 보장되지 않는다.

### 1.1 파이프라인 지도와 읽는 순서

아래 지도는 각 절의 기호가 파이프라인의 어느 단계에 붙는지 보여 준다. 화살표 위 괄호는 그 변환을 정의하는 절 번호다.

```text
[Training]
  C ---(4.1 teacher q_psi, gate g_C)---> D_0 ---(4.2 dedup, split)---> D_train, D_hold
  (D_train, W_0) ---(5 LoRA SFT, assistant-token NLL)---> phi*
  [experiment] phi* ---(7.3 bootstrap / 7.4 DPO)---> phi'

[Inference]
  x ---(3.3 s_C / 6.1 h_C)---> prefix ---(6.1 D_{phi,d}, 6.2 mask G_C)---> y
  y ---(3.1 F_C(.;x): defaults, strip, correct, validate)---> a_hat ---(3.2 E)---> u
  F_C failure ---(6.4 repair; API JSON path only)---> regenerate

[Evaluation]
  (a_hat, a*) ---(8.1)---> Valid, EM, AM        (7.1, 7.2) R_C, R_grade
  (s_C vs sigma(C), usage) ---(3.3, 8.2)---> input-token saving, p50 / p95
```

세미나에서 자주 나오는 질문과 해당 절은 다음과 같다.

| 질문 | 절 |
|---|---|
| 모델이 무엇을 생성하고, 코드는 무엇까지 보장하는가 | §3, §6.2, §11 |
| DSL이 native tool schema보다 왜, 얼마나 싼가 | §3.3, §8.2 |
| 학습 데이터는 어떻게 만들고 무엇을 검사하지 않는가 | §4 |
| 어떤 목적함수로 무엇을 갱신하는가 | §5, §7 |
| 점수 하나하나가 무엇을 재는가 | §7, §8 |
| 현재 orchestrator와 v2 제안의 거리 | §9.3, §10 |
| 기호가 실제 문자열로 어떻게 보이는가 | 부록 A |

## 2. 기호와 객체

| 기호 | 정의 |
|---|---|
| $x\in\mathcal X$ | 사용자 요청 문자열 |
| $C$ | 고정된 도구 카탈로그와 파싱 정책 |
| $\mathcal T_C$ | 카탈로그의 도구 이름 집합 |
| $y\in\mathcal V^*$ | 토큰열 또는 그 디코딩 결과인 DSL 문자열 |
| $a=((t_j,b_j))_{j=1}^{m}$ | 순서가 있는 ActionPlan; $t_j$는 도구, $b_j$는 인자 매핑 |
| $a^*$ | 평가 또는 학습 시 사용하는 정답 계획 |
| $\epsilon_A$ | 호출이 0개인 유효한 계획 |
| $\bot$ | 파싱·검증·생성 실패; 빈 계획과 다름 |
| $W_0,\phi$ | 고정 base 가중치와 학습 가능한 LoRA 파라미터 |
| $p_\phi$ | $W_0$에 adapter $\phi$를 적용한 자기회귀 모델 |
| $d$ | grammar 사용 여부, temperature, 길이 제한 등 생성 설정 |
| $\xi$ | 모델·계약·정밀도·보정·생성·재시도 정책을 포함하는 시스템 구성 |
| $\mathbf 1[E]$ | 명제 $E$가 참이면 1, 아니면 0 |
| $\rho(C),\sigma(C)$ | 같은 카탈로그의 DSL 텍스트 렌더링과 OpenAI tools 스키마 렌더링 (§3.3) |
| $\operatorname{tok}(\cdot)$ | 문자열의 토큰 수 |
| $\Lambda,\Gamma$ | freeform 출력의 salvage와 native tool call의 DSL 변환 (§6.3) |

$C$는 개념적으로 다음 튜플이다.

$$
C=(\mathcal T_C,\{S_t\},N_C,K_C,e_C,P_C).
$$

$S_t$는 도구별 인자 제약, $N_C$는 타입 변환·별칭 정규화, $K_C$는 기본값 주입·인자 제거·요청 기반 보정, $e_C$는 빈 호출 허용 여부, $P_C$는 프롬프트에 쓰이는 설명·예시·규칙이다. 실제 코드에서는 이 정보가 `Catalog`와 `ToolSpec`에 함께 들어 있다. $N_C$와 $K_C$의 분리는 설명용 분해이며, v2가 제안하는 독립 정책 객체는 아직 아니다.

`ActionPlan`의 동등성은 순서가 있는 호출들의 값 동등성이다. 인자 매핑의 키 순서는 무관하지만 호출 순서는 중요하다. 아래 동등성은 Python 값 비교와 현재 정규화를 따른다. BFCL의 병렬 호출 평가는 별도 관계를 사용한다.

## 3. 계약: DSL에서 도구 호출까지 [구현]

### 3.1 파싱·보정·검증

JSON 매핑의 집합을 $\mathcal J$라 하고, 파싱 함수를 부분함수로 정의한다. 토큰열은 디코딩한 문자열로 파서에 전달한다.

$$
F_C:(\mathcal V^*\cup\mathcal J)\times(\mathcal X\cup\{\varnothing\})
\rightharpoonup\mathcal A_C,
\qquad F_C(y;x)=a.
$$

계산이 실패하면 편의상 $F_C(y;x)=\bot$로 표기한다. 이는 반환값이 아니라 구현의 예외를 수식에 옮긴 것이다. malformed 입력에서 발생할 수 있는 일반 예외의 처리 범위는 호출자마다 다르다.

개별 호출의 기본 처리 순서는 다음과 같다.

$$
\begin{aligned}
b^{(1)}&=\operatorname{Defaults}_t(b),\\
b^{(2)}&=\operatorname{Strip}_{C,t}(b^{(1)}),\\
b^{(3)}&=\operatorname{Correct}_{t}(b^{(2)},x),\\
\bar b&=\operatorname{ValidateNormalize}_{C,t}(b^{(3)}).
\end{aligned}
$$

요청 $x$가 없으면 요청 기반 보정을 생략한다. custom validator가 있으면 마지막 단계를 대체하며 중첩 호출도 처리할 수 있다. 도구 이름은 앞뒤 공백을 제거한 뒤 카탈로그에서 찾는다.

`parse_json_dsl`은 문자열을 JSON으로 읽고 `calls` 배열을 처리한다. 최상위 단일 `action` 형태도 허용한다. 따라서 parser가 받아들이는 모든 표현과, 뒤에서 정의하는 grammar의 출력 언어는 같다고 가정하지 않는다.

$$
F_C(\texttt{\{"calls":[]\}};x)=
\begin{cases}
\epsilon_A,&e_C=1,\\
\bot,&e_C=0.
\end{cases}
$$

파싱 성공은 현재 validator와 보정 정책의 통과를 뜻한다. 실제 사용자 의도 충족, 실행 전 상태 적합성, 외부 시스템의 실행 성공은 별개의 성질이다.

### 3.2 결정적 emission

$$
E(a)=\bigl[\{\texttt{name}:t_j,\texttt{arguments}:b_j\}\bigr]_{j=1}^{m}.
$$

`emit_tool_calls`는 이 데이터를 만들며 외부 API를 호출하지 않는다. $E(\epsilon_A)=[]$이다. 이미 검증된 ActionPlan을 전달하는 것이 보정 문맥을 유지하는 경로다. emitter에 원문을 직접 전달하면 사용자 요청 없이 다시 파싱하므로 $F_C(y;x)$와 결과가 달라질 수 있다.

### 3.3 두 렌더링과 표현 비용

같은 `ToolSpec` 집합에서 두 표현을 결정적으로 생성한다.

$$
\rho(C)=\operatorname{RenderDSL}(C)\in\mathcal V^*,\qquad
\sigma(C)=\operatorname{RenderTools}(C)\in\mathcal J^{\lvert\mathcal T_C\rvert}.
$$

$\rho(C)$는 §5.1의 system 문자열 $s_C$에 삽입되는 짧은 텍스트이고, $\sigma(C)$는 native 경로에서 API의 `tools` 필드로 전달되는 OpenAI 함수 스키마 목록이다. 두 표현은 같은 인자 제약에서 나오므로 비교는 같은 계약에 대한 비교다. 다만 $\rho(C)$에는 `extra_rules`와 `examples`가 들어가고 $\sigma(C)$에는 도구 `description`이 들어가므로 정보 내용이 완전히 같지는 않다.

크기는 고정 부분과 도구별 부분으로 분해된다.

$$
\lvert\rho(C)\rvert=c_C+\sum_{t\in\mathcal T_C}\ell^{\mathrm{dsl}}_t,\qquad
\lvert\sigma(C)\rvert=\sum_{t\in\mathcal T_C}\ell^{\mathrm{nat}}_t+O(1).
$$

$c_C$는 JSON 형태 안내·규칙·예시로 이루어진 고정 부분이고, $\ell_t$는 도구 한 개의 표현 길이다. 세 IoT tier에서 $c_C=779$자, 도구 한 줄은 평균 77~110자, native 스키마 한 개는 평균 315~420자다. 따라서 도구 수가 늘수록 고정 부분이 희석되고 비율 $\lvert\sigma(C)\rvert/\lvert\rho(C)\rvert$는 $\bar\ell^{\mathrm{nat}}/\bar\ell^{\mathrm{dsl}}$ 쪽으로 커진다. 현재 체크아웃에서 `python -m ganglion.benchmarks.iot.scaling`의 측정값은 다음과 같다.

| tier | 도구 수 | $\lvert\rho(C)\rvert$ (chars) | $\lvert\sigma(C)\rvert$ (chars) | 비율 |
|---|---:|---:|---:|---:|
| iot_light_5 | 5 | 1,334 | 2,108 | 1.58 |
| home_iot_20 | 20 | 2,552 | 6,842 | 2.68 |
| smart_home_50 | 50 | 4,670 | 15,841 | 3.39 |
| home_assistant_4 | 4 | 1,491 | 1,733 | 1.16 |

POC 보고서(2026-04)의 값은 1,307 / 2,062 등으로 수십 자 작다. 이후 도구 설명이 바뀌어 절대값이 움직였고 비율은 그대로다. `home_assistant_4`처럼 slot 대부분이 optional string인 카탈로그에서는 이득이 작다. 즉 절감 비율은 상수가 아니라 카탈로그의 제약 밀도에 따른 값이다.

요청 하나의 입력 토큰은 다음과 같이 나뉜다.

$$
\begin{aligned}
n^{\mathrm{dsl}}_{\mathrm{in}}(x)&=\operatorname{tok}(s_C)+\operatorname{tok}(x)+c_{\mathrm{chat}},\\
n^{\mathrm{nat}}_{\mathrm{in}}(x)&=\operatorname{tok}_{\mathrm{prov}}(\sigma(C))+\operatorname{tok}(x)+c'_{\mathrm{chat}}.
\end{aligned}
$$

$\operatorname{tok}$는 모델 tokenizer의 토큰 수, $c_{\mathrm{chat}}$은 chat template 오버헤드다. API native 경로에서 제공자가 `tools`를 프롬프트로 직렬화하는 방식은 관측할 수 없으므로 $\operatorname{tok}_{\mathrm{prov}}(\sigma(C))$는 응답의 `prompt_tokens` 사용량에서 역산한 값이다. 사례 집합에 대한 절감률은

$$
\widehat S_{\mathrm{in}}
=1-\frac{\sum_i n^{\mathrm{dsl}}_{\mathrm{in}}(x_i)}{\sum_i n^{\mathrm{nat}}_{\mathrm{in}}(x_i)}
$$

로 정의한다. $\operatorname{tok}(x)$가 양변에 공통으로 들어가므로 $\widehat S_{\mathrm{in}}$은 문자 비율로 계산한 $1-\lvert\rho(C)\rvert/\lvert\sigma(C)\rvert$보다 작고, 카탈로그가 프롬프트를 지배할수록 그 값에 가까워진다. `qwen3.6-plus`로 측정한 값은 다음과 같다.

| 평가 집합 | DSL 입력 토큰 | native 입력 토큰 | $\widehat S_{\mathrm{in}}$ | 근거 |
|---|---:|---:|---:|---|
| iot_light_5, 50건 | 22,757 | 41,507 | 45.2% | POC 보고서 §14.4 |
| home_iot_20, 50건 | 40,907 | 109,207 | 62.5% | POC 보고서 §14.4 |
| smart_home_50, 50건 | 74,057 | 235,207 | 68.5% | POC 보고서 §14.4 |
| BFCL v4 single-turn 500건 (M1') | 70,163 | 185,881 | 62.2% | `runs/bfcl/aggregated.json` |
| BFCL 500건, no-call 규칙 포함 (M5 DSL 대 M1' native, 건당 평균) | 171.5 | 371.8 | 53.9% | POC 보고서 §18.2 |

문자 비율 1.58→3.39가 토큰 비율 1.82→3.18로 재현된다는 점이 M2의 근거다. 세 IoT tier는 같은 500건 프롬프트(5종 의도)를 공유하므로 20·50 도구 행은 프롬프트 비용만 분리해 측정한 것이고, 방해 도구 사이에서 올바른 도구를 고르는 능력은 이 측정에 포함되지 않는다. 토큰 절감은 요청당 비용이며, §10의 수명주기 비용에는 학습·엔지니어링 비용이 따로 더해진다.

## 4. 학습 데이터 구성 [구현]

### 4.1 도구별 교사 합성과 gate

교사 모델의 합성 분포를 $q_\psi$라 하면, 대상 도구 $t$마다 요청·DSL 후보를 생성한다.

$$
(x,\tilde y)\sim q_\psi(\cdot\mid C,t).
$$

현재 `tool_anchored` gate는 다음 술어다.

$$
g_C(x,\tilde y;t)=
\mathbf 1\left[
\begin{array}{l}
x\text{ is a nonempty string},\quad \tilde y\text{ is a mapping},\\
a=F_C(\tilde y;x)\ne\bot,\quad |a|=1,\quad a_1.t=t
\end{array}\right].
$$

성공한 계획을 JSON으로 직렬화한 $y=\operatorname{Serialize}(a)$가 SFT target이 된다. `sort_keys=True`를 사용한다. 즉, 학습 target에는 parser의 보정 결과가 반영될 수 있다. `teacher_score=1.0`은 현재 placeholder이며 독립적인 의미 정답 점수가 아니다.

$$
\mathcal D_0=\bigl[(x,\operatorname{Serialize}(F_C(\tilde y;x)),t)
  :g_C(x,\tilde y;t)=1\bigr].
$$

이 gate는 verifier 보상 $R_C$를 호출하지 않는다. 의도와 인자값의 의미적 일치도 검사하지 않는다. 예를 들어 `set_value(value=1)`만 허용된 형식으로 출력하면 “99로 설정”이라는 의도와 모순돼도 gate를 통과할 수 있다.

도구별 목표 개수는 $n_t=\max(1,\lfloor n_{\mathrm{target}}/|\mathcal T_C|\rfloor)$이다. 도구별 요청 횟수 제한과 전역 추정 비용 제한에 도달하면 부분 결과를 반환한다. 비용은 요청 전에 검사하므로 마지막 요청의 비용만큼 상한을 넘을 수 있다. $n_{\mathrm{target}}$은 전체 결과 개수의 엄격한 상한도 아니다.

### 4.2 중복 제거와 분할

정규화 embedding $h(x)$를 사용해 입력 순서대로 greedy dedup을 수행한다. 앞서 유지한 사례와

$$
h(x_i)^\top h(x_j)\ge\tau_{\mathrm{dup}}
$$

이면 뒤의 사례를 제거한다. 기본 임계값은 $0.95$이다. embedding 의존성을 import할 수 없으면 `strip().lower()` 문자열 동등성으로 대체한다. 따라서 결과는 입력 순서와 설치 환경에 의존한다.

각 strategy 그룹 $\mathcal D_s$를 seed 기반으로 섞은 뒤

$$
n_s^{\mathrm{hold}}=
\max(1,\operatorname{round}(|\mathcal D_s|\rho))
$$

개를 holdout에 넣고 나머지를 train에 넣는다. 기본값은 $\rho=0.2$, seed 42다. 사례가 하나인 그룹은 train에 남지 않는다. 이 분할은 strategy별 분할이며, paraphrase family 단위 독립성을 보장하지 않는다.

## 5. LoRA SFT [구현]

### 5.1 프롬프트와 토큰 마스크

$s_C$는 공통 `SYSTEM_PROMPT_TEMPLATE`에 `render_json_dsl()`을 삽입한 system 문자열이다. 메시지는

$$
M_C(x,y)=[(\mathrm{system},s_C),(\mathrm{user},x),(\mathrm{assistant},y)].
$$

토크나이저의 chat template 적용 결과를 $z_i=(z_{i,1},\ldots,z_{i,L_i})$라 하고, assistant 응답에 속하는 예측 대상 토큰의 마스크를 $m_{i,k}\in\{0,1\}$로 둔다. padding, 잘린 토큰, system·user 토큰은 학습 대상에서 제외한다.

**중요한 전제:** `assistant_only_loss=True` 설정은 코드에서 확인되지만 실제 마스크 생성은 설치된 TRL과 tokenizer template에 의존한다. 아래 수식은 유효한 assistant mask가 만들어졌다는 조건의 목적함수다. 잘못된 template에서도 이 성질이 자동 보장된다고 주장하지 않는다.

### 5.2 학습 파라미터와 목적함수

각 대상 선형층 $\ell$의 유효 가중치는 다음과 같다.

$$
W_\ell=W_{0,\ell}+\frac{\alpha}{r}B_\ell A_\ell,
\quad A_\ell\in\mathbb R^{r\times d_{\mathrm{in}}},
\quad B_\ell\in\mathbb R^{d_{\mathrm{out}}\times r}.
$$

$W_0$는 고정하고 $\phi=\{A_\ell,B_\ell\}_\ell$만 학습한다. 학습 중 adapter 입력에는 dropout이 적용되며 위 식은 dropout을 끈 유효 가중치 표현이다. LoRA의 저랭크 파라미터화는 [Hu et al., LoRA](https://arxiv.org/html/2106.09685v2)에 따른다.

assistant 토큰의 음의 로그우도 목적은 다음과 같이 정리된다.

$$
\begin{aligned}
Z&=\sum_i\sum_{k=2}^{L_i}m_{i,k},\qquad Z>0,\\
\mathcal L_{\mathrm{SFT}}(\phi)
&=-\frac{1}{Z}\sum_i\sum_{k=2}^{L_i}
 m_{i,k}\log p_\phi(z_{i,k}\mid z_{i,<k}),\\
\phi^*&\approx\arg\min_\phi\mathcal L_{\mathrm{SFT}}(\phi).
\end{aligned}
$$

이는 전체 유효 토큰으로 정규화한 수학적 요약이다. 실제 minibatch·gradient accumulation·분산 실행의 reduction 및 optimizer는 TRL/Transformers 설정에 따르며, 위 식이 모든 버전의 실행을 비트 단위로 규정하지는 않는다. 유한 epoch 학습은 전역 최적해를 보장하지 않아 $\approx$를 사용한다.

현재 기본 설정은 `all-linear`, $r=32$, $\alpha=64$, dropout 0.05, 3 epochs, learning rate $2\times10^{-4}$, batch 4, accumulation 2, 최대 길이 1024다. base 기본값은 `Qwen/Qwen3-1.7B`이며 과거 실험은 0.6B도 사용한다. grammar mask는 현재 SFT 손실에 적용하지 않는다.

### 5.3 학습과 추론의 일치 조건

현재 학습·추론은 같은 system 문자열을 조립한다. 그러나 강한 일치 조건은 문자열 비교보다 넓다.

$$
\operatorname{Prefix}_{\mathrm{response}}
 (\operatorname{Tokenize}(M_C(x,y)))
=\operatorname{TokenizeForGeneration}(M_C(x)).
$$

왼쪽은 첫 학습 응답 토큰 직전의 문맥이다. tokenizer, special token, assistant prefix, thinking 설정, truncation이 모두 일치해야 한다. 현재 `generate_dsl`은 `enable_thinking=False`를 명시하지만 학습에서는 TRL에 template 처리를 맡긴다. 패키지에는 이 토큰 수준 불변조건이나 명세의 `TrainPromptParityError`를 검사하는 코드가 없다.

## 6. 추론과 grammar 제약 [구현]

### 6.1 조건부 생성

추론 prefix를 $h_C(x)$라 하면 제약 없는 모델 분포는

$$
p_\phi(y\mid h_C(x))=
\prod_{k=1}^{|y|}p_\phi(y_k\mid h_C(x),y_{<k}).
$$

카탈로그에서 생성한 JSON Schema를 tokenizer에 맞춰 컴파일한 grammar를 $G_C$라 한다. prefix $u$ 다음에 허용되는 토큰 집합을

$$
\mathcal M_{G_C}(u)=
\{v\in\mathcal V:uv\text{ is a prefix of a sequence accepted by }G_C\}
$$

로 둔다. 종료 토큰은 grammar matcher의 종료 규칙을 따른다. 마스크를 사용하지 않으면 허용 집합은 전체 vocabulary이다.

모델 logits $\ell_\phi(v\mid h_C(x),u)$에 대해

$$
\ell'_\phi(v)=
\begin{cases}
\ell_\phi(v),&v\in\mathcal M_{G_C}(u),\\
-\infty,&\text{otherwise}.
\end{cases}
$$

$T>0$일 때 temperature만 적용한 분포는

$$
\tilde p_{\phi,G,T}(v\mid h_C(x),u)=
\frac{\mathbf 1[v\in\mathcal M_{G_C}(u)]\exp(\ell_\phi(v)/T)}
{\sum_{w\in\mathcal M_{G_C}(u)}\exp(\ell_\phi(w)/T)}.
$$

위 분포는 정규화 분모가 유한하고 양수일 때 정의된다. 실제 sampling에는 모델의 generation config에서 상속된 top-k, top-p 등 추가 변환이 있을 수 있다. 이를 $\mathcal W_d$로 표기하면 실제 선택 분포는 $\mathcal W_d(\tilde p)$다. 설정을 고정하지 않은 채 위 softmax만을 실제 전체 sampling 규칙이라고 가정해서는 안 된다.

기본 경로 $T=0$은 sampling 없이 HF의 결정적 선택 경로를 사용한다. 일반적인 단일 beam greedy 설정에서

$$
y_k=\arg\max_{v\in\mathcal M_{G_C}(y_{<k})}
\ell_\phi(v\mid h_C(x),y_{<k}).
$$

이는 토큰별 greedy 선택이며 문자열 전체의 최빈값을 구하는 전역 최적화가 아니다. `num_beams` 등 상속 설정도 재현 시 기록해야 한다.

### 6.2 grammar의 보장 범위

완료까지 도달하고 tokenizer·vocabulary·stop token이 호환되며 matcher가 정상 동작하면 출력은 컴파일된 grammar 언어에 속한다. 하지만 길이 제한으로 중단되면 완성되지 않은 prefix만 남을 수 있다. 기본 `max_new_tokens`는 256이다.

또한 grammar는 JSON Schema로 표현한 부분만 검사한다. custom validator, 요청 기반 보정, 사용자 의도, 환경 상태까지 표현한다고 가정하지 않는다. 따라서 $y\in\mathcal L(G_C)$만으로 $F_C(y;x)\ne\bot$ 또는 의미적 성공을 일반적으로 증명할 수 없다.

컴파일 결과는 여러 생성에서 재사용할 수 있지만 matcher는 생성마다 새로 만든다. 모델의 padded vocabulary 크기와 tokenizer vocabulary 크기가 다를 수 있어 컴파일의 `vocab_size`에는 모델 설정값을 전달하는 것이 의도된 사용이다.

### 6.3 추론의 함수 합성

생성을 $D_{\phi,d}$라 쓰면 로컬 DSL 경로는 다음과 같다.

$$
\hat a=F_C(D_{\phi,d}(h_C(x));x),\qquad
\operatorname{ToolCalls}_{\xi}(x)=
\begin{cases}
E(\hat a),&\hat a\ne\bot,\\
\bot,&\text{otherwise}.
\end{cases}
$$

현재 로컬 평가기는 생성·파싱 결과를 채점하며 모든 요청에 실제 외부 도구를 실행하지 않는다. 구현된 추론 경로는 네 가지이며 모두 같은 $F_C$와 $E$로 끝난다.

| 경로 (`--llm`) | 프롬프트 | 생성 제약 | 문자열에서 계획으로 | 재시도 |
|---|---|---|---|---|
| 로컬 HF `generate_dsl` | $s_C$ | 선택적 $G_C$ 토큰 마스크 | $F_C(y;x)$ | 없음 |
| `qwen` | $s_C$ | API `json_object` 모드 | $F_C(y;x)$ | §6.4 |
| `qwen-text`, `qwen-thinking` | $s_C$와 문구가 다른 system 문자열 | 없음 | $F_C^{\Lambda}(y;x)$ | 없음 |
| `qwen-native` | 고정 한 문장 + `tools`$\,=\sigma(C)$ | 제공자의 tool calling | $F_C(\Gamma(\mathbf u^{\mathrm{raw}});x)$ | 없음 |

freeform 경로의 salvage $F_C^{\Lambda}$는 후보 문자열을 순서대로 시도해 처음 성공하는 파싱을 돌려준다.

$$
F_C^{\Lambda}(y;x)=F_C(y_{k^*};x),\qquad
k^*=\min\{k: F_C(y_k;x)\ne\bot\}.
$$

여기서 $y_1=y$이고, 그 다음은 Markdown 코드 울타리 안의 블록들, 마지막은 $y$ 안에서 `{`부터 디코딩 가능한 첫 JSON 객체들이다. 어느 후보로 성공했는지는 `strict | fenced | embedded`로 기록되고, 모두 실패하면 $\bot$이다.

native 경로의 변환 $\Gamma$는 제공자가 돌려준 tool call 목록 $\mathbf u^{\mathrm{raw}}=((\mathrm{name}_j,\mathrm{args}_j))_j$를 같은 DSL 매핑으로 바꾼다.

$$
\Gamma(\mathbf u^{\mathrm{raw}})=
\bigl\{\texttt{calls}:[\{\texttt{action}:\mathrm{name}_j,\ \texttt{args}:\operatorname{json}(\mathrm{args}_j)\}]_{j}\bigr\}.
$$

그 뒤에는 $F_C(\cdot;x)$가 그대로 적용되므로 native 결과에도 정규화 $N_C$와 보정 $K_C$가 들어간다. native 경로는 "보정 없는 baseline"이 아니다. IoT native 클라이언트는 tool call이 하나도 없으면 예외를 던지므로 이 경로에서 $\epsilon_A$는 나오지 않는다. API의 JSON 출력 모드는 로컬 XGrammar 토큰 마스크와 동일한 구현으로 취급하지 않는다.

### 6.4 검증 실패 시 재시도

API JSON 경로의 repair는 첫 응답에 실패한 경우에만 validation error를 문맥에 추가한다.

$$
\begin{aligned}
h^{(0)}&=M_C(x),\\
y^{(j)}&\sim\operatorname{Completer}(h^{(j)}),\\
h^{(j+1)}&=h^{(j)}\mathbin{\Vert}
[(\mathrm{assistant},y^{(j)}),(\mathrm{user},\operatorname{ErrorText}(e_j))].
\end{aligned}
$$

최초 $F_C(y^{(j)};x)\ne\bot$에서 반환한다. `enabled=True`, `max_attempts=r`이면 모델 호출은 최대 $r+1$회다. disabled이면 한 번만 시도한다. parser가 통과시킨 의미적 오답은 이 루프를 트리거하지 않는다. 현재 로컬 `generate_dsl`에는 이 재시도 루프가 연결돼 있지 않다.

## 7. 보상 함수와 선호 학습

### 7.1 Catalog verifier의 실제 점수 [구현]

예측 계획 $a$, 정답 계획 $a^*$의 호출 수를 각각 $m,n$이라 하자. 행동 점수는

$$
q_{\mathrm{act}}(a,a^*)=
\begin{cases}
\mathbf 1[m=0],&n=0,\\
\displaystyle\frac{1}{n}\sum_{j=1}^{\min(m,n)}
\mathbf 1[t_j=t_j^*],&n>0.
\end{cases}
$$

인자 점수는 정답 위치별 $b_j$의 일치율을 평균한다.

$$
q_{\mathrm{arg}}(a,a^*)=
\begin{cases}
1,&n=0,\\
\displaystyle\frac{1}{n}\sum_{j=1}^{n}u_j,&n>0,
\end{cases}
$$

$$
u_j=
\begin{cases}
0,&j>m\text{ or }t_j\ne t_j^*,\\
1,&b_j^*=\varnothing\text{ and }b_j=\varnothing,\\
0.5,&b_j^*=\varnothing\text{ and }b_j\ne\varnothing,\\
\displaystyle\frac{1}{|b_j^*|}\sum_{(k,v)\in b_j^*}
\mathbf 1[\operatorname{get}(b_j,k)=v],&\text{otherwise}.
\end{cases}
$$

`get`는 실제 `dict.get`이며 누락 키는 `None`이다. null을 허용하는 인자에서는 이 비교가 키 존재 여부를 별도로 구분하지 않는다는 점까지 구현에 포함된다.

이때 `make_verifier`의 보상은

$$
R_C(x,y,a^*)=
\begin{cases}
0,&F_C(y;x)=\bot,\\
0.3,&F_C(y;x)\ne\bot\text{ and no gold},\\
1,&F_C(y;x)=a^*,\\
0.3+0.4q_{\mathrm{act}}+0.2q_{\mathrm{arg}},&\text{otherwise}.
\end{cases}
$$

정답 DSL은 요청 문맥 없이 파싱한다. 잘못된 정답은 입력 오류로 예외를 발생시킨다. 예측 파싱 실패는 정답 파싱보다 먼저 처리돼 바로 0이 된다. 이 점수는 결정적 부분 점수이며 문자열과 이산 계획에 대해 미분 가능한 목적함수는 아니다.

**명세와의 차이:** 기존 task 문서는 행동 점수의 분모를 $\max(m,n)$, 인자 점수를 Jaccard로 정의하지만 현재 verifier는 위 수식을 사용한다. 정답 호출을 그대로 두 번 반복하면 $q_{\mathrm{act}}=q_{\mathrm{arg}}=1$이므로 정확 일치는 실패하면서 $R_C=0.9$가 된다. 추가 호출 패널티를 개선한 별도 식으로 몰래 대체하지 않는다.

### 7.2 `graded_score`는 별개의 함수 [구현]

`analyzer.metrics.graded_score`를 $R_{\mathrm{grade}}$로 표기한다. $I(b)$는 인자 매핑의 `(key, value)` 집합이며 중첩 list/dict 값은 sorted-key JSON 문자열로 바꾼다. $J$는 Jaccard 유사도이고 두 집합이 모두 비면 1이다.

$$
R_{\mathrm{grade}}(a,a^*)=
\begin{cases}
0,&a=\bot,\\
1,&a=a^*,\\
0.25,&(t_1,\ldots,t_m)\ne(t_1^*,\ldots,t_n^*),\\
\displaystyle\frac{1}{n}\sum_{j=1}^{n}
\left(0.5+0.5J(I(b_j),I(b_j^*))\right),&\text{otherwise}.
\end{cases}
$$

마지막 분기에서는 $m=n>0$이다. 빈 계획 둘은 앞선 정확 일치 분기에서 처리된다. 정답 호출의 중복은 이 함수에서 0.25다. IoT DPO pair 실험은 이 함수를 사용하므로 $R_C$와 혼용하면 학습 신호를 잘못 설명하게 된다.

### 7.3 Self-bootstrap [실험]

학습 prompt에서 $N$개의 출력을 샘플링하고, 정답에 대한 grader $V$를 통과한 예측을 재학습 데이터에 추가한다.

$$
\begin{aligned}
y_{ij}&\sim D_{\phi,d}(h_C(x_i)),\quad j=1,\ldots,N,\\
a_{ij}&=F_C(y_{ij};x_i),\\
\mathcal D_{\mathrm{boot}}
&=\mathcal D_{\mathrm{train}}\uplus
\bigl[(x_i,\operatorname{Serialize}(a_{ij})):
a_{ij}\ne\bot,\ V(a_{ij},a_i^*)=1\bigr].
\end{aligned}
$$

$\uplus$는 중복 사례가 남을 수 있는 데이터셋 연결이다. BFCL 실험은 lenient parser와 BFCL AST grader를 사용한다. 이때 $F_C$는 그 실험의 parser로 대체한다. 정답은 채점에 쓰이며 학생에게 정답을 입력해 생성하는 것은 아니다. 이 절차를 최종 평가 데이터에 적용하면 그 데이터는 더 이상 독립 holdout이 아니다.

### 7.4 DPO [실험; 수식은 표준 목적]

고정된 학습 prompt에서 출력들을 채점하고 최고·최저 점수의 문자열을 $y^+,y^-$로 선택한다. IoT pair 스크립트의 유지 조건은

$$
R_{\mathrm{grade}}(F_C(y^+;x),a^*)-
R_{\mathrm{grade}}(F_C(y^-;x),a^*)\ge\delta>0.
$$

기본 설정은 $N=8$, $T=0.7$, $\delta=0.5$이다. 이는 task 문서의 $N=4$ 및 혼합 pass-count 조건과 구별된다. 보정된 계획을 채점하더라도 chosen/rejected 학습 target은 원래 생성 문자열이다.

선호 데이터 $\mathcal P=\{(h,y^+,y^-)\}$와 고정 reference 정책 $p_{\mathrm{ref}}$에 대해

$$
\begin{aligned}
\Delta_\phi(h,y^+,y^-)
&=\log\frac{p_\phi(y^+\mid h)}{p_{\mathrm{ref}}(y^+\mid h)}
-\log\frac{p_\phi(y^-\mid h)}{p_{\mathrm{ref}}(y^-\mid h)},\\
\mathcal L_{\mathrm{DPO}}(\phi)
&=-\mathbb E_{(h,y^+,y^-)\sim\mathcal P}
\left[\log\sigma\bigl(\beta\Delta_\phi(h,y^+,y^-)\bigr)\right].
\end{aligned}
$$

$\sigma$는 sigmoid이고 로그 확률은 completion 토큰 로그 확률의 합이다. 이는 [Rafailov et al., DPO, 식 (7)](https://arxiv.org/html/2305.18290v3#S4)의 표준 목적이다. 결정적 점수는 선호 쌍을 만드는 데 사용하며, 점수 함수를 미분해 모델을 갱신하지 않는다.

현재 실험은 `DPOTrainer(ref_model=None)`에 reference 처리를 맡긴다. 따라서 reference가 반드시 SFT snapshot이라고 단정하지 않는다. adapter 비활성화 여부 등 실제 reference 구성은 실행 환경에서 확인·기록해야 한다. 보존된 스크립트의 legacy import와 API 설정을 검증하지 않고 DPO 경로의 재현 가능성을 주장하지 않는다. BFCL Phase 3 보고서는 선호 쌍 부족 등으로 유효한 DPO 개선 결과를 확보하지 못했다고 기록한다.

## 8. 평가: 모델 출력과 최종 시스템의 구분 [구현 및 제안]

### 8.1 정확도 지표 [구현]

한 사례를 한 번 실행하는 로컬 평가에서 $\hat a_i=F_C(y_i;x_i)$이고 생성 또는 파싱 실패 시 $\hat a_i=\bot$라 하자.

$$
\begin{aligned}
\widehat{\mathrm{Valid}}&=\frac1n\sum_{i=1}^{n}\mathbf1[\hat a_i\ne\bot],\\
\widehat{\mathrm{EM}}&=\frac1n\sum_{i=1}^{n}\mathbf1[\hat a_i=a_i^*],\\
\widehat{\mathrm{AM}}&=\frac1n\sum_{i=1}^{n}
\mathbf1[\hat a_i\ne\bot\ \land\ \operatorname{Actions}(\hat a_i)=\operatorname{Actions}(a_i^*)].
\end{aligned}
$$

분모는 실패를 포함한 전체 사례 수다. $n=0$일 때 현재 집계 함수는 rate 0을 반환한다. 여러 run이 있는 `CaseResult`에서는 모든 run이 해당 조건을 만족해야 그 사례가 성공이다. run이 없으면 실패다.

`syntax_valid_rate`라는 현재 이름은 JSON 문법만의 성공률이 아니다. 실제로는 보정·정규화·타입 검증 후 `RunResult.valid`를 집계한다. EM 역시 raw JSON 문자열 비교가 아니라 보정된 ActionPlan 비교다. BFCL에서는 별도 `ast_match`가 허용 정답값과 병렬 호출의 순서 무관 매칭 등을 처리하므로 IoT EM과 동일한 식으로 대체하지 않는다.

### 8.2 비용·지연 지표 [구현]

`summarize`는 run 단위 값을 집계한다. run $r$의 입력·출력 토큰을 $n_{\mathrm{in},r},n_{\mathrm{out},r}$, 지연을 $\lambda_r$(ms)라 하고 지연이 기록된 run 수를 $R$이라 하면

$$
N_{\mathrm{in}}=\sum_r n_{\mathrm{in},r},\qquad
N_{\mathrm{out}}=\sum_r n_{\mathrm{out},r},\qquad
\lambda_{50}=\operatorname{median}(\lambda_1,\ldots,\lambda_R),\qquad
\lambda_{95}=\lambda_{(1+\operatorname{round}(0.95(R-1)))}
$$

이다. $\lambda_{(k)}$는 $k$번째 순서통계량이고 `round`는 Python의 반올림이다. 표준편차는 모집단 표준편차이며 run이 둘 미만이면 비어 있다. 토큰 수를 보고하지 않는 클라이언트(로컬 HF 경로)의 합계는 0이 아니라 `null`이다. 절감률 $\widehat S_{\mathrm{in}}$(§3.3)은 같은 사례 집합에 대한 두 summary를 비교해 계산하며 `runs/aggregate.py`와 `runs/bfcl/aggregate.py`가 이를 수행한다.

지연은 backend 안에서만 비교한다. API 지연에는 네트워크와 서버 부하가 섞이고, 로컬 지연은 하드웨어·정밀도·grammar 마스크 여부에 따라 달라진다. `--repeat`로 같은 사례를 여러 번 실행하면 분산을 볼 수 있지만, 정확도 판정은 모든 run이 성공해야 하므로 run을 추가하면 사례별 판정은 참에서 거짓으로만 바뀔 수 있다. 즉 반복 수가 늘면 EM은 같거나 줄어든다.

### 8.3 보정의 기여 분리 [제안]

모델 단독과 보정 포함 시스템을 분리하려면 도메인 정규화는 유지하고 보정 정책만 끈 $F_C^{(0)}$를 별도 구성한다. 이는 아직 독립된 표준 API가 아니다. $v_i^{(0)},v_i^{(K)}\in\{0,1\}$를 두 구성의 정확 일치 판정이라 하면

$$
\begin{aligned}
\mathrm{Rescue}&=\frac1n\sum_i\mathbf1[v_i^{(0)}=0\land v_i^{(K)}=1],\\
\mathrm{Regression}&=\frac1n\sum_i\mathbf1[v_i^{(0)}=1\land v_i^{(K)}=0],\\
\mathrm{EM}^{(K)}-\mathrm{EM}^{(0)}&=\mathrm{Rescue}-\mathrm{Regression}.
\end{aligned}
$$

마지막 등식은 같은 사례·출력·정답·분모로 비교할 때의 항등식이다. 서로 다른 모델 실행 결과를 섞으면 보정의 기여를 분리한 비교가 아니다. grammar on/off 비교도 모델·prompt·평가 집합·backend·생성 설정을 고정한 실험으로 정의해야 한다.

측정 기록에는 순증만 남아 있다. Qwen3-0.6B SFT v2(CUDA)에 보정 규칙 두 층을 더했을 때 500건 EM은 86.4%에서 99.2%로 올랐고 신규 정답은 64건이었다. 순증 12.8pp도 64건이므로 이 비교에서 regression은 0건이다. 다만 두 구성 모두 `defaults_when_missing`을 포함하므로 이는 $F_C^{(0)}$ 대 $F_C^{(K)}$의 비교가 아니라 보정 층 사이의 비교다.

## 9. 알고리즘으로 연결하기

### 9.1 Algorithm A: 카탈로그별 학습 [구현 함수의 합성]

```text
Input: Catalog C, teacher Q, base W0, synth/train configs
1. D0 <- synthesize tool-anchored pairs with structural gate
2. D  <- canonicalize, deduplicate, shuffle D0
3. (Dtrain, Dhold) <- strategy-stratified split(D)
4. Mtrain <- [system(C), user(x), assistant(y)] for Dtrain
5. phi <- LoRA SFT(W0, Mtrain; assistant token loss)
6. Save adapter, tokenizer, training metrics
7. Generate on Dhold; parse with C and request x; compute metrics
8. Return adapter and evaluation artifacts
Optional experiment: bootstrap / preference pairs -> further training
```

이는 개별 함수의 논리적 합성이다. `train_lora` 자체는 5–6단계를 수행하며, holdout 평가나 DPO를 자동 호출하지 않는다. 합성 함수 내부에서 직렬화·dedup도 수행하므로 구현 호출자는 이를 중복 수행할 필요가 없다.

### 9.2 Algorithm B: 로컬 요청 추론 [구현]

```text
Input: C, model W0+phi, tokenizer, x, decoding config d
1. h <- tokenize system(C) + user(x) + generation prefix
2. If grammar enabled: create a fresh matcher from compiled grammar
3. Generate y with configured decoding and length limit
4. a <- C.parse_json_dsl(y, prompt=x)
5. If parse fails: surface failure to caller
6. Return a; emitter may convert a into tool-call data
```

### 9.3 현재 factory orchestrator [구현]

현재 `run_pipeline`의 실제 경로는

$$
C\longrightarrow\operatorname{Benchmark}(C,\mathrm{client})
\longrightarrow\mathrm{Summary}\longrightarrow\operatorname{Stop}
$$

이다. IoT는 선택적으로 trace를 기록한다. 구현 내에서 synth·SFT·규칙 합성을 호출하지 않으며 제안 patch는 빈 튜플이다.

일치율을 $q_k$라 하면 종료 조건은 다음 순서다.

$$
\begin{cases}
\texttt{threshold\_reached},&q_k\ge\eta,\\
\texttt{max\_iter\_reached},&k\ge K_{\max},\\
\texttt{plateau},&k>K\ \land\ \max_{j=k-K+1}^{k}(q_j-q_{k-K})<\varepsilon,\\
\texttt{no\_patches\_proposed},&\text{no patches available}.
\end{cases}
$$

기본값은 $\eta=0.95$, $K_{\max}=3$, $K=2$, $\varepsilon=0.01$이다. 현재 patch가 항상 비므로 정상 평가 경로에서 첫 반복 후 종료한다. 성공 threshold도 반환 객체의 `aborted=True`와 함께 표현하므로 `reason`을 읽어야 한다. BFCL에서는 종료 판정용 `exact_match_rate`에 AST match rate를 넣는다.

## 10. 지속적 팩토리의 최적화 문제 [제안]

### 10.1 모델 학습과 시스템 선택의 두 수준

학습 가중치 $\phi$와 시스템 구성 $\xi$를 구분한다.

$$
\xi=(W_0,\phi,C,\mathrm{precision},\mathrm{representation},
\mathrm{schema\ delivery},K,d,\mathrm{retry/fallback}).
$$

v2의 수명주기 비용을 다음과 같이 재표기한다.

$$
\begin{aligned}
\min_{\xi\in\Xi}\ J_H(\xi)
&=C_{\mathrm{data}}(\xi)+C_{\mathrm{train}}(\xi)
+C_{\mathrm{engineering}}(\xi)\\
&\quad+\mathbb E[C_{\mathrm{updates}}(H;\xi)]
+N_H\mathbb E[C_{\mathrm{request}}(\xi)].
\end{aligned}
$$

요청 비용은 전체 retry·fallback·컴파일 상각을 포함한다. 합산하는 항은 같은 단위여야 한다. 금액 환산이 정해지지 않은 사람 시간·에너지는 별도 축으로 보고한다. $N_H$는 기간 $H$의 요청 수이며, 기대값의 요청 분포와 환경 변경 가정도 평가 프로파일에 명시한다.

제약은

$$
\begin{aligned}
\operatorname{LCB}(Q(\xi))&\ge Q_{\min},&
\operatorname{UCB}(E_{\mathrm{wrong}}(\xi))&\le E_{\max},\\
A_{\mathrm{unresolved}}(\xi)&\le A_{\max},&
L_{95}(\xi)&\le L_{\max},\\
M_{\mathrm{peak}}(\xi)&\le M_{\max},&
U_{\mathrm{recover}}(\xi)&\le U_{\max}.
\end{aligned}
$$

$Q$는 작업 성공률, $E_{\mathrm{wrong}}$은 잘못된 실행률, $A_{\mathrm{unresolved}}$는 미해결률이다. LCB/UCB는 사전에 정한 신뢰 수준과 추정법의 하한/상한이다. 현재 패키지가 이 신뢰구간을 계산한다는 뜻은 아니다. 분모, 표본 추출법, 지연 측정 구간, 최대 메모리 측정 조건을 선언해야 제약이 검증 가능한 명제가 된다.

SFT/DPO는 $\xi$의 후보를 만드는 내부 연산이다. 현재 SFT loss를 최소화한다고 위 시스템 비용을 직접 최소화하는 것은 아니다.

### 10.2 상태 갱신과 평가 경계

상태 $s_k=(C_k,\mathcal D_k,\xi_k,\mathcal H_k,b_k)$는 계약, 사용 가능한 개발 데이터, 후보, 이력, 남은 예산을 포함한다. 제안된 갱신 관계는

$$
\begin{aligned}
\mathcal P_k&=\operatorname{Analyze}(\mathcal H_k,C_k),\\
\xi'_k&=\operatorname{Build}(\operatorname{Select}(\mathcal P_k,b_k)),\\
r_k^{\mathrm{dev}}&=\operatorname{Evaluate}_{\mathrm{dev}}(\xi'_k),\\
s_{k+1}&=\operatorname{Update}(s_k,\xi'_k,r_k^{\mathrm{dev}}).
\end{aligned}
$$

독립 릴리스 평가는 개발 평가로 고른 후보를 동결한 뒤 수행한다. train, development, release evaluation은 원본·paraphrase family 단위로 분리한다. 평가 정답은 runtime 생성 입력에 들어가지 않는다. 이 선택·갱신 연산의 구체적 구현 및 수렴 보장은 아직 없다. budget, deadline, 개선 정체에 따른 유한 종료는 품질 목표 달성과 다른 조건이다.

## 11. 확인 가능한 성질과 반례

1. **검증 후 emission의 결정성.** 같은 ActionPlan에 대해 $E(a)$는 같다. 상태 의존 custom validator까지 포함한 $F_C$의 순수성은 별도 전제다.
2. **빈 호출과 실패의 구분.** $\epsilon_A\ne\bot$. no-call 허용 여부는 계약에 속한다.
3. **형식과 의미의 분리.** $g_C=1$ 또는 grammar 통과만으로 요청에 대한 정답임을 증명할 수 없다.
4. **문법 마스킹과 정확도의 분리.** 마스크는 선택 가능한 토큰을 제한한다. EM 향상이나 길이 제한 내 완료를 일반적으로 보장하지 않는다.
5. **보상과 정확 일치의 분리.** 중복 호출은 $R_C=0.9$, $R_{\mathrm{grade}}=0.25$, EM=0이 될 수 있다.
6. **보정의 양면성.** 보정은 rescue와 regression을 모두 만들 수 있다. 보정 포함 점수만으로 모델 단독 성능을 추론할 수 없다.
7. **재시도 범위.** parser가 받아들인 의미적 오답은 현재 repair 대상이 아니다.
8. **경로 불변성.** 네 추론 경로(§6.3)는 모두 $F_C$와 $E$로 끝나므로 §8의 지표 정의는 경로에 의존하지 않는다. 경로가 바꾸는 것은 $y$의 분포, 프롬프트 비용 $n_{\mathrm{in}}$, salvage 여부다. native 결과도 $K_C$를 통과한다.
9. **표현 비용의 성장.** $\lvert\sigma(C)\rvert/\lvert\rho(C)\rvert$가 도구 수와 함께 커지는 것은 현재 렌더러와 내장 카탈로그에서 측정된 사실이지 정리가 아니다. optional string slot이 많은 카탈로그에서는 이득이 작다.

측정 기록과의 대응은 다음과 같다. 4번은 grammar ablation과 일치한다. Qwen3-0.6B untuned에서 마스크는 EM을 17pp 올렸지만, SFT 후 5도구 카탈로그에서는 5pp 내렸고 50도구에서는 7pp 올렸다. CUDA로 재학습한 v2에서는 마스크가 syntax를 95.6%에서 99.4%로 올리는 동안 EM은 86.4%에서 86.0%로 내려갔다. 6번은 §8.3의 보정 기록(86.4%→99.2%)에 대응한다.

3번과 5번은 이 문서를 마무리한 2026-09-15에 도구 두 개짜리 인공 카탈로그로 다시 실행해 확인했다. `set_value(value=1)`은 "99로 설정" 의도에도 gate를 통과했고, 정답 호출을 두 번 반복한 계획은 $R_C=0.9$, $R_{\mathrm{grade}}=0.25$, EM $=0$이었다. 학습·추론 GPU 실험과 tokenizer 수준 mask 검증은 이번 문서 작성의 검증 범위에 포함하지 않았다.

## 12. 구현 근거와 기존 문서의 관계

아래 경로는 모두 저장소 루트 기준이다. 상대 링크는 Markdown 문서 위치를 기준으로 해석한다.

| 내용 | 근거 |
|---|---|
| 계약·보정·정규화 | [catalog.py](../ganglion/contract/catalog.py), [tool_spec.py](../ganglion/contract/tool_spec.py) |
| 도구 호출 데이터 출력 | [emitter.py](../ganglion/contract/emitter.py) |
| 교사 합성·gate·dedup | [synth/pipeline.py](../ganglion/lm/synth/pipeline.py) |
| train/holdout 분할·로컬 평가 | [local_hf.py](../ganglion/lm/local_hf.py) |
| 프롬프트·SFT·로컬 생성 | [prompts.py](../ganglion/lm/prompts.py), [finetune/sft.py](../ganglion/lm/finetune/sft.py) |
| grammar compile·mask | [grammar.py](../ganglion/lm/grammar.py) |
| 검증 실패 재시도 | [repair.py](../ganglion/analyzer/repair.py) |
| 두 보상 함수·메트릭 | [verifier.py](../ganglion/analyzer/verifier.py), [metrics.py](../ganglion/analyzer/metrics.py) |
| BFCL grader | [grader.py](../ganglion/benchmarks/bfcl/grader.py) |
| 현재 orchestrator | [factory.py](../ganglion/factory.py) |
| 두 렌더링 크기 측정 | [scaling.py](../ganglion/benchmarks/iot/scaling.py) |
| API 경로·salvage·native 변환 | [dashscope.py](../ganglion/lm/dashscope.py), [parse.py](../ganglion/contract/parse.py) |
| 오프라인 예시 클라이언트 (부록 A) | [rules.py](../ganglion/lm/rules.py) |
| 토큰·지연 측정값 | [poc_verification_report.md](poc_verification_report.md) §14, §18, [aggregated.json](../runs/bfcl/aggregated.json) |
| grammar ablation·보정 기여 측정값 | [factory_phase2_plan.md](factory_phase2_plan.md) §12.2, §15.1, §16.3 |
| DPO 연구용 절차 | [dpo_pairs.py](../runs/factory_phase2/dpo_pairs.py), [dpo_train.py](../runs/factory_phase2/dpo_train.py) |
| Bootstrap 연구용 절차 | [bfcl_bootstrap.py](../runs/factory_bfcl/bfcl_bootstrap.py) |
| 전체 시스템 설계 | [architecture_v2.md](architecture_v2.md) |
| 이전 학습·verifier 명세 | [lm_finetune.md](tasks/lm_finetune.md), [analyzer_verifier.md](tasks/analyzer_verifier.md) |
| DPO 실험 한계 기록 | [factory_bfcl_phase3_report.md](factory_bfcl_phase3_report.md) |

이 문서와 task 명세가 다르면 **현재 동작 설명에는 해당 커밋의 코드**를 우선한다. 목표 설계는 `architecture_v2.md`를 참조한다. LoRA와 DPO의 일반 수학적 정의는 본문의 원 논문 링크를 참조한다.

## 13. 문서 유지

이 노트는 LaTeX로 빌드하지 않는다. `tools/build_formalization.py`는 이론 문서 `pipeline_formalization.md`만 `pipeline_formalization.tex`로 변환한다. 이 노트의 수치를 갱신할 때는 §12의 근거 파일을 다시 실행해 값을 맞춘다.

## 부록 A. 기호와 실제 값의 대응 (iot_light_5)

아래 값은 저장소의 오프라인 구성요소만으로 재현된다. 모델 출력 $y$는 `rules` 클라이언트가 만든 결정적 값이며, LLM 경로에서는 이 문자열만 달라지고 이후 변환은 같다.

**두 렌더링.** 같은 도구 `get_light_state`가 $\rho(C)$에서는 한 줄(81자), $\sigma(C)$에서는 스키마 객체 하나(312자)다.

```text
- get_light_state args {"room": one of living, bedroom, kitchen, hallway, office}
```

```json
{"type": "function", "function": {"name": "get_light_state", "description": "Get the current state of a room light.", "parameters": {"type": "object", "properties": {"room": {"type": "string", "enum": ["living", "bedroom", "kitchen", "hallway", "office"]}}, "additionalProperties": false, "required": ["room"]}}}
```

**system 문자열 $s_C$.** 1,419자이며 도구 5줄, 규칙 3줄, 예시 4쌍을 포함한다. 앞부분과 예시 첫 쌍은 다음과 같다.

```text
You convert user requests into the JSON DSL below. The response must be valid JSON.

Return JSON only.
JSON shape: {"calls":[{"action":"<action>","args":{...}}]}
Allowed actions:
- list_devices args {}
- get_light_state args {"room": one of living, bedroom, kitchen, hallway, office}
- set_light args {"room": one of living, bedroom, kitchen, hallway, office, "state": "on"|"off", "brightness": optional integer 0..100, "color_temp": optional "warm"|"neutral"|"cool"}
- schedule_light args {"room": one of living, bedroom, kitchen, hallway, office, "at": "HH:MM" 24h time, "state": "on"|"off", "brightness": optional integer 0..100}
- create_scene args {"name": one of movie, relax, focus, sleep, "actions": array of set_light calls}
Rules:
- Use canonical English room names.
- Use 24-hour HH:MM for schedules.
- Do not include explanations or Markdown.
Examples:
User: 거실 불 70%로 켜줘
JSON: {"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":70}}]}
(예시 3쌍 생략)
```

예시 4쌍의 프롬프트는 평가 데이터셋 `examples/iot_light/dataset.jsonl`의 `iot-001`, `iot-002`, `iot-003`, `iot-008`과 동일하다. 이 네 건은 프롬프트에 정답이 들어 있는 사례이므로 §10.2의 독립 평가 기준에서는 제외해야 한다.

**요청 하나의 경로.** 사례 `iot-001`은 다음과 같이 흐른다.

- $x$: `거실 불 70%로 켜줘`
- $y$: `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 70}}]}`
- $\hat a=F_C(y;x)$: `((set_light, {room: living, state: on, brightness: 70}))`
- $a^*$: 위와 같으므로 $\mathbf 1[\hat a=a^*]=1$
- $u=E(\hat a)$: `[{"name": "set_light", "arguments": {"room": "living", "state": "on", "brightness": 70}}]`

**정규화 $N_C$와 보정 $K_C$.** 같은 요청에 대해 모델이 별칭과 퍼센트 문자열을 냈다면

`{"room": "거실", "state": "on", "brightness": "70%"}` → `{room: living, state: on, brightness: 70}`

이 되고(`EnumArg.aliases`, `IntArg.allow_percent`), `state`를 빠뜨렸다면

`{"room": "living", "brightness": 70}` → `{room: living, state: on, brightness: 70}`

이 된다. 후자는 `set_light`의 `defaults_when_missing` 규칙(`brightness` 또는 `color_temp`가 있으면 `state="on"`)이 채운 값이며, §3.1의 순서대로 타입 검증보다 먼저 적용된다. 두 결과는 모두 $a^*$와 같으므로 EM에 포함되지만, 전자는 계약의 정규화이고 후자는 §8.3이 분리하려는 보정에 해당한다.

**grammar $G_C$와 parser의 차이.** `catalog_to_json_schema`는 호출마다 `action`과 `args`를 모두 요구하고 `calls`에 `minItems: 1`을 둔다. 반면 parser는 `args`가 없는 호출을 `{}`로 읽고 최상위 `action` 형태도 받아들인다. 따라서 $\mathcal L(G_C)$는 parser가 받는 언어의 진부분집합이며, 이것이 §3.1에서 두 언어를 같다고 가정하지 않은 이유다.

**이 요청의 표현 비용.** $\lvert s_C\rvert=1{,}419$자, $\lvert\sigma(C)\rvert=2{,}108$자다. 토큰 수는 tokenizer에 따라 다르며 §3.3의 표는 API가 보고한 사용량 기준이다.
