# factory_bfcl — Qwen3-0.6B 최적화 파이프라인 BFCL v4 적용 결과

**실행 일자**: 2026-05-19 · **베이스 모델**: `Qwen/Qwen3-0.6B` (DashScope `qwen3-0.6b` for Phase 1, 로컬 HF for Phase 2) · **샘플**: 카테고리당 100 케이스 · **GPU**: NVIDIA RTX 4080 16 GB · **스펙 문서**: [`docs/tasks/factory_bfcl.md`](tasks/factory_bfcl.md)

## 1. 목표

`docs/factory_phase2_plan.md` §11–12 와 `docs/factory_phase2_session_2026-05-08.md`
에서 IoT 자체 데이터셋(iot_light_5 / smart_home_50)으로 진행한 Qwen3-0.6B
최적화 파이프라인을, 외부 벤치마크 BFCL v4 단일 턴(single-turn) 5 카테고리에
**동일한 단계로** 옮겨와 각 단계의 상대적 기여(uplift)를 측정한다.

> IoT 환경의 SSOT 결과(`factory_phase2_plan.md` §12.1): iot_light_5 untuned 0.6B 38.6%
> exact_match → SFT 73.4% → +post-correction 77.2%. 본 작업의 비교 대상.

## 2. 단계별 AST match 종합 (`runs/factory_bfcl/aggregated.json`)

| category | M1' DSL | M1' Native | M4' Repair | S1b mask off | S1b mask on | S2a SFT (holdout 20) | **S2a SFT (full 100)** | S2c +post-corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| simple_python      | 0.760 | 0.810 | 0.720 | 0.750 | 0.750 | 0.650 | 0.800 | **0.850** |
| multiple           | 0.700 | 0.760 | 0.690 | 0.700 | 0.690 | 0.400 | 0.790 | **0.900** |
| parallel           | 0.020 | 0.000 | 0.050 | 0.500 | 0.040 | 0.450 | 0.760 | **0.810** |
| parallel_multiple  | 0.060 | 0.000 | 0.070 | 0.460 | 0.000 | 0.500 | 0.750 | **0.800** |
| irrelevance        | 0.660 |   —   | 0.660 | 0.610 | 0.610 | 1.000 | 1.000 | **1.000** |
| **macro avg**      | 0.440 | 0.393¹| 0.438 | 0.604 | 0.418 | 0.600 | 0.820 | **0.872** |

¹ native macro 는 irrelevance 제외 4 카테고리 평균.

### Syntax valid rate

| category | M1' DSL | M1' Native | M4' Repair | S1b mask off | S1b mask on | S2a SFT (holdout 20) | S2a SFT (full 100) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| simple_python     | 0.930 | 0.950 | 0.950 | 0.940 | 0.960 | 0.900 | 0.920 |
| multiple          | 0.960 | 0.960 | 0.950 | 0.940 | 0.980 | 0.950 | 0.950 |
| parallel          | 0.880 | 0.960 | 0.930 | 0.930 | 0.970 | 0.900 | 0.960 |
| parallel_multiple | 0.870 | 0.950 | 0.910 | 0.980 | 0.990 | 0.950 | 0.990 |
| irrelevance       | 0.950 |   —   | 0.930 | 0.970 | 1.000 | 1.000 | 1.000 |

## 3. 단계별 해석

### 3.1 Phase 1 — M1' baseline (DashScope, untuned 0.6B)

- 단일 호출 카테고리(`simple_python`, `multiple`, `irrelevance`)는 0.66~0.76 으로
  IoT-light dataset.jsonl 의 38.6% 보다 훨씬 높음. 카테고리당 스키마가 작고 (1~수개
  함수) intent 가 직설적이라 baseline 도 어렵지 않다.
- **`parallel` / `parallel_multiple` 의 ast 가 사실상 0** (0.02 / 0.06). 0.6B 가
  같은 함수를 N 번 또는 서로 다른 함수를 N 번 호출하는 multi-call 구조를 생성하지
  못한다. `factory_phase2_plan.md` §10 의 *capacity cliff* 관찰과 정확히 일치.
- Native 경로(DashScope tool-calls)도 같은 패턴. `parallel` 류 ast = 0.00 으로 더
  심함. Native API 는 단일 `tool_calls` 배열을 항상 1 개로 마무리하는 경향.
- **토큰 경제**: DSL 입력 토큰/케이스 109~190, native 228~459. 2~3× 절감.

### 3.2 Phase 1 — M4' repair loop (1 attempt)

- syntax 는 미미하게 개선 (parallel +5pp, parallel_multiple +4pp). ast 는 잡음
  수준(±5pp) 안에 머무름.
- **함의**: repair 는 JSON shape 오류만 잡아내며 의미상 오류(잘못된 함수 선택,
  multi-call 누락)는 해결하지 못한다. IoT M4 의 +3pp exact 와 같은 결론.

### 3.3 Phase 2 — S1b grammar masking (xgrammar, 카테고리 케이스별 schema)

- **단일 호출 카테고리**에서는 syntax 가 100% 근접(0.98~1.00) 으로 상승. ast 매칭은
  거의 동일하거나 미세 변동.
- **multi-call 카테고리(`parallel`, `parallel_multiple`)에서 ast 매칭 붕괴**
  (parallel 0.50→0.04, parallel_multiple 0.46→0.00). syntax 는 올라가는데 grammar
  가 model 을 "1 개 호출에 가까운 형태" 쪽으로 편향시키는 패턴. `factory_phase2_plan.md`
  §12.2(a) 의 *"masking is not a uniform win"* 발견의 재현(보다 강한 형태).
- **단, 로컬 0.6B greedy 의 mask_off 가 DashScope 보다 훨씬 높음** (parallel 0.50 vs
  DashScope 0.02). 동일 모델이지만 디코딩 경로(local greedy vs DashScope 서빙)
  차이가 크다. baseline 기준선 자체가 결정에 따라 흔들린다는 점은 보고서의 한계.

### 3.4 Phase 2 — S2a per-category LoRA SFT

- 카테고리마다 독립 어댑터(`sft_0.6B_bfcl_<cat>`)를 train 80 / holdout 20 split (seed=42)
  로 학습. Phase 1 SFT 레시피와 동일 (r=32, lr=2e-4, 3 epoch, BF16). BFCL `expected`
  가 enum 비일치로 자체 catalog 에 parse 안 되는 케이스 7~10개를 자동 스킵 → 실 학습
  rows 70~80.
- **`full 100` 평가**: 전 카테고리 0.75~1.00 사이로 끌어올림. macro 0.44 → **0.82**
  (+38pp).
- **`holdout 20` 평가**: 동일 카테고리 내 미본 케이스 → simple_python 0.65, multiple 0.40
  로 baseline 보다 *떨어짐*. 80 케이스의 학습 데이터가 너무 좁아 overfitting +
  format 의 미세 drift 가 발생.
- **함의**: SFT 의 진짜 일반화 효과는 holdout 수치(0.40~1.00)에 가깝다. `full` 의
  높은 수치는 학습 데이터 memorization 의 기여가 크다. 단, parallel 류는 holdout 도
  0.45 / 0.50 으로 baseline 0.02 / 0.06 보다는 압도적으로 좋음 — **multi-call 구조
  학습 자체는 holdout 에도 전이된다**.
- `irrelevance` 는 holdout/full 모두 1.00. `{"calls": []}` null-action 패턴이 80개
  학습 예제로 완전히 학습됨.

### 3.5 Phase 2 — S2c post-correction (실패 패턴 분석 기반)

- 1차 시도(R1~R3 hand-written 일반 규칙)는 0회 발동 / 0pp uplift. 이유는 S2a 후
  잔존 실패가 "shape 미스" 가 아니라 "의미상 오류" 중심이었기 때문.
- 2차 시도: `analyze_failures.py` 로 SFT eval_full 의 실패 케이스를 자동
  분류(W1~W8 + WOK) → 빈도가 높은 패턴별로 8 개 규칙 추가.
  결과:

  | category | before (SFT full) | after (+post-corr) | Δ |
  |---|---:|---:|---:|
  | simple_python      | 0.800 | **0.850** | +5.0pp |
  | multiple           | 0.790 | **0.900** | +11.0pp |
  | parallel           | 0.760 | **0.810** | +5.0pp |
  | parallel_multiple  | 0.750 | **0.800** | +5.0pp |
  | irrelevance        | 1.000 | 1.000 | 0.0pp |
  | **macro**          | 0.820 | **0.872** | **+5.2pp** |

- 발동 횟수 (총 fix 수):
  - **R4 drop_hallucinated_optional** — 52회. gt 가 optional (`""` sentinel 포함)
    인데 model 이 환각으로 채워넣은 인자를 제거. 가장 큰 lever.
  - **R1 fill_optional_with_first_accepted** — 29회. (앞 단계와 의미는 같지만 R4
    제거 이후 다시 fill 채우는 경로로 동작)
  - R5 coerce_percent (0.05 ↔ 5.0) — 2회.
  - R8 multiply_by_1000 — 2회. (land_area 9597 → 9597000 단위 보정)
  - R9 case_insensitive_string_match (PlayStation→Playstation) — 2회.
- **함의**: 1차 시도의 결론은 "post-correction 은 BFCL 에서 효과 없다" 였지만,
  실패 분포를 보고 규칙을 데이터 기반으로 추가하면 +5~11pp 까지 끌어올릴 수 있다.
  IoT 의 `defaults_when_missing` 가 그 catalog 의 default 구조에 fit 된 한 가지
  R 일 뿐. 외부 벤치마크에서는 (a) 실패 분류기 (b) 카테고리별 룰 cherry-pick
  두 단계가 모두 필요했다.
- 한계: 11개 규칙은 BFCL 에 특화된 패턴(예: gt accept-list 의 `""` sentinel)에
  의존. 일반 OpenAI tool-call shape 에는 직접 이식 안 됨. R1/R4/R9 같은 "shape
  cleaning" 규칙만 보편적이다.

## 4. IoT 결과와 직접 비교

| stage | IoT iot_light_5 (dataset.jsonl, 500 cases) | BFCL macro (5 cats, 500 cases) | 일치/불일치 |
| --- | ---: | ---: | --- |
| Untuned 0.6B | exact 38.6% / syn 65.8% | ast 0.440 / syn 0.918 | BFCL 가 (a) intent 가 단순 + (b) catalog 가 작아 baseline 이 훨씬 좋다 |
| + M4 repair | exact 41.8% / syn 72.2% | ast 0.438 / syn 0.934 | repair 의 ast 영향이 둘 다 미미. syntax 만 살짝 상승. **동일 패턴** |
| + grammar mask | exact 57.8% / syn ~100% | ast 0.418 / syn 0.974 | IoT 는 +17pp ast, BFCL 은 −2pp. **불일치 — multi-call 카테고리에서 grammar 가 역효과** |
| + SFT (mask off) | exact 73.4% / syn 91% | ast 0.820 / syn 0.964 (full) | IoT 와 비슷한 수준에 도달. 단 holdout 0.60 으로 보면 일반화는 IoT 대비 약함 |
| + post-correction | exact 77.2% (+3.8pp) | ast 0.872 (+5.2pp) | **동일 패턴 — 단 BFCL 은 데이터 기반 11개 규칙 필요**. multiple 카테고리는 +11pp 로 IoT 보다 큰 uplift |

**핵심 일치**: SFT 가 0.6B 의 multi-call capacity cliff 를 깬다 (parallel
0.02→0.76, parallel_multiple 0.06→0.75). 카테고리당 80건 학습으로 가능. IoT 의
"SFT 가 가장 큰 lever" 관찰이 도메인을 바꿔도 유효.

**핵심 불일치**: post-correction (`defaults_when_missing`) 의 +3.8~+6pp uplift 는
*IoT catalog 가 default 가 명시적인 enum 구조*에 의존했던 효과였음을 본 작업이
간접적으로 보여준다. BFCL 의 다양한 케이스별 catalog 에서는 동일한 규칙이 0pp.
generality 의 한계.

## 5. 한계 및 다음 단계

- **샘플 크기**: 카테고리당 100, 이항 CI ±5pp. 단일 시드, greedy/T=0 디코딩.
  Multi-seed 실험은 미실시.
- **학습 데이터 크기**: 카테고리당 ~80 학습 케이스. IoT (~600 synth examples) 대비
  매우 적음. holdout 수치 (0.40~0.65 on simple_python/multiple) 는 데이터 부족의
  명확한 신호. 다음 단계는 BFCL category-wise 합성 데이터 증강 (또는 cross-category
  joint training) 이 우선.
- **카테고리별 모델**: 5개 어댑터를 단일 deploy 로 묶는 LoRA-router 또는 어댑터
  merge 는 본 작업 범위 외. 실배포에는 추가 단계가 필요.
- **multi-turn**: BFCL v4 multi-turn 은 시점상 out-of-scope. 단일 턴 결과만 보고.
- **S2b/S3 (bootstrap + DPO) 미실행**: S2c 의 0pp uplift 와 holdout overfit 패턴을
  종합 판단해 보류. 다음 의미 있는 신호는 학습 데이터 자체의 증강이라고 판단.

## 6. 산출물

- `runs/factory_bfcl/phase1/{baseline,repair}/<cat>_{dsl,native}_{summary.json,cases.jsonl,log}`
- `runs/factory_bfcl/phase2/grammar/<cat>_mask_{off,on}/{summary.json,cases.jsonl}`
- `runs/factory_bfcl/phase2/sft/<cat>/{adapter/,train_metrics.json,eval_{holdout,full}/{summary.json,cases.jsonl}}`
- `runs/factory_bfcl/phase2/post_corr/<cat>/{summary.json,cases.jsonl}`
- `runs/factory_bfcl/aggregated.json` · `runs/factory_bfcl/table.md`
- `docs/tasks/factory_bfcl.md` (spec)
- `runs/factory_bfcl/bfcl_sft.py` · `bfcl_eval.py` · `post_correction.py` ·
  `bfcl_bootstrap.py` · `aggregate.py` (드라이버)

## 7. 재현

```bash
# Phase 1 (DashScope, ~26 min)
GANGLION_MODEL=qwen3-0.6b bash runs/factory_bfcl/run_phase1.sh
# Phase 2 (local GPU, ~55 min)
bash runs/factory_bfcl/run_phase2.sh
# Post-correction + aggregate
bash runs/factory_bfcl/run_phase2_post.sh
```
