# factory_bfcl Phase 3 — 데이터 증강 + DPO 결과

**실행**: 2026-05-19 21:53 → 2026-05-20 02:57 (총 wall 약 5시간) · **베이스**: `Qwen/Qwen3-0.6B` · **스펙**: [`docs/tasks/factory_bfcl_phase3.md`](tasks/factory_bfcl_phase3.md)

## 1. 헤드라인

Phase 2 의 카테고리당 80건 학습 한계를, 교사(qwen3.6-plus) paraphrase + synth 로
약 5× 증강 후 SFT v1.5 → bootstrap → SFT v2 → DPO 시도. **최종 best 구성은
SFT v1.5 + Phase 2 의 11규칙 post-correction**.

| 구성 | macro AST holdout (20×5) | macro AST full (100×5) |
|---|---:|---:|
| Phase 2 SFT v1 (단순 SFT) | 0.600 | 0.820 |
| Phase 2 v1 + post-corr | — | 0.872 |
| **Phase 3 SFT v1.5** (orig+paraphrase+synth) | **0.690** | 0.880 |
| **Phase 3 v1.5 + post-corr** | **0.810** | **0.912** |
| Phase 3 SFT v2 (+bootstrap) | 0.650 | 0.880 |
| Phase 3 v2 + post-corr | 0.780 | 0.910 |
| Phase 3 DPO (S3g) | n/a (pair 부족 + API 호환성) | n/a |

**holdout +21pp / full +9pp** vs Phase 2 SFT 단순.

## 2. 단계별 결과

### 2.1 S3a paraphrase + S3b synth (qwen3.6-plus 교사)

데이터 증강 yield:

| cat | original | paraphrase (K=4) | synth (N=50) | 합계 |
|---|---:|---:|---:|---:|
| simple_python      | 72  | 288 (90%) | 43 (86%) | **403** |
| multiple           | 73  | 292 (91%) | 48 (96%) | **413** |
| parallel           | 78  | 300 (94%) | 49 (98%) | **427** |
| parallel_multiple  | 70  | 244 (76%) | 47 (94%) | **361** |
| irrelevance        | 80  | 320 (100%) | 47 (94%) | **447** |

`parallel_multiple` 의 paraphrase yield 가 낮은 건 multi-call 표현이 교사
모델도 종종 1-call 로 단순화하면서 BFCL gt accept-list 와 mismatch 가 발생.

### 2.2 S3c SFT v1.5 (=orig + paraphrase + synth)

| cat | v1 holdout | **v1.5 holdout** | Δ | v1 full | **v1.5 full** | Δ |
|---|---:|---:|---:|---:|---:|---:|
| simple_python      | 0.650 | **0.700** | +5pp  | 0.800 | **0.860** | +6pp |
| multiple           | 0.400 | **0.450** | +5pp  | 0.790 | **0.830** | +4pp |
| parallel           | 0.450 | **0.700** | **+25pp** | 0.760 | **0.900** | +14pp |
| parallel_multiple  | 0.500 | **0.600** | +10pp | 0.750 | **0.810** | +6pp |
| irrelevance        | 1.000 | 1.000     | 0     | 1.000 | 1.000     | 0 |
| **macro**          | 0.600 | **0.690** | **+9pp** | 0.820 | **0.880** | +6pp |

특히 `parallel` 카테고리에서 holdout +25pp — multi-call 케이스에 대한 새 학습
데이터가 가장 큰 lever. paraphrase 만으로 자연어 분포를 넓혀도 model 이 multi-call
구조를 generalize 함.

### 2.3 S3d self-bootstrap (sample N=4 @ T=0.7 from v1.5)

| cat | original kept | bootstrap pass | pass rate |
|---|---:|---:|---:|
| simple_python      | 72  | 284 | 88.8% |
| multiple           | 73  | 296 | 92.5% |
| parallel           | 78  | 304 | 95.0% |
| parallel_multiple  | 70  | 274 | 85.6% |
| irrelevance        | 80  | 320 | 100.0% |

높은 pass-rate 가 곧 *학습 신호의 부재*. 모델이 이미 맞히는 쉬운 케이스만 다량
재생산 → diversity 강화 효과 없음.

### 2.4 S3e SFT v2 (v1.5 + bootstrap-pass)

| cat | v1.5 holdout | **v2 holdout** | Δ | v1.5 full | **v2 full** | Δ |
|---|---:|---:|---:|---:|---:|---:|
| simple_python      | 0.700 | 0.700 | 0    | 0.860 | 0.880 | +2pp |
| multiple           | 0.450 | 0.400 | -5pp | 0.830 | 0.820 | -1pp |
| parallel           | 0.700 | 0.600 | **-10pp** | 0.900 | 0.890 | -1pp |
| parallel_multiple  | 0.600 | 0.550 | -5pp | 0.810 | 0.810 | 0   |
| irrelevance        | 1.000 | 1.000 | 0    | 1.000 | 1.000 | 0   |
| **macro**          | 0.690 | 0.650 | **-4pp** | 0.880 | 0.880 | 0  |

**holdout 회귀**. bootstrap 이 (a) 추가 학습 신호 없이 (b) 기존 분포만 강화해서
overfit 가중. IoT Phase 2 v2 도 비슷한 회귀 (76.6% vs v1+post-correction 77.2%).

### 2.5 S3c+S2c v1.5 + 11규칙 post-correction (최종 best)

Phase 2 에서 개발한 11규칙 post-correction (R1~R11) 을 v1.5 출력에 그대로 적용:

| cat | v1.5 (h20) | **+pc (h20)** | Δ | v1.5 (f100) | **+pc (f100)** | Δ |
|---|---:|---:|---:|---:|---:|---:|
| simple_python      | 0.700 | **0.750** | +5pp   | 0.860 | **0.890** | +3pp |
| multiple           | 0.450 | **0.850** | **+40pp** | 0.830 | **0.920** | +9pp |
| parallel           | 0.700 | **0.750** | +5pp   | 0.900 | **0.910** | +1pp |
| parallel_multiple  | 0.600 | **0.700** | +10pp  | 0.810 | **0.840** | +3pp |
| irrelevance        | 1.000 | 1.000     | 0      | 1.000 | 1.000     | 0   |
| **macro**          | 0.690 | **0.810** | **+12pp** | 0.880 | **0.912** | +3.2pp |

`multiple` holdout +40pp 는 R4 (drop_hallucinated_optional) + R1 (fill_optional_with_first_accepted)
의 조합 효과. 결과적으로 v1.5+pc 가 **모든 구성 중 최고**.

### 2.6 S3g DPO — 무효 (구조적 한계)

- v2 base 의 pass rate 가 86~100% 라 N=4 샘플링 시 거의 모든 prompt 가 4/4 통과 →
  (chosen, rejected) pair 가 카테고리당 0~2개밖에 안 생성.
- 다섯 카테고리 합쳐 학습 가능한 pair = 4. DPO 학습 신호 양이 절대 부족.
- 추가로 trl 1.x 의 `DPOConfig.max_prompt_length` 인자 제거 — API 호환성 버그도 동시 발견 (수정해뒀음).
- 두 문제 모두 해결 가능하지만, 본질은 **bootstrap 의 pass-rate 자체가 너무 높음**.
  더 도전적인 prompt 풀(예: holdout, 또는 더 높은 T) 에서 샘플링하지 않으면 DPO 가
  학습할 게 없다. 본 보고에서는 v1.5+pc 가 이미 macro 0.91 도달이라 DPO 보강의
  marginal value 가 작다고 판단하고 보류.

## 3. IoT Phase 2 와 직접 비교 (4단계 누적)

| 단계 | IoT iot_light_5 (dataset 500건) | BFCL macro (5×100) | 일치 |
|---|---:|---:|---|
| Untuned 0.6B          | exact 38.6% / syn 65.8% | ast 0.440 / syn 0.918 | BFCL baseline 가 더 쉬움 |
| + SFT v1              | exact 73.4% / syn 91% | ast 0.820 / syn 0.964 | 동일 패턴 |
| + post-correction     | exact 77.2% (+3.8pp) | ast 0.872 (+5.2pp) | 동일 패턴 |
| + 데이터증강(v1.5)    | (해당 없음 — IoT 는 augmented 학습 v2) | ast 0.880 (+0.8pp full) | BFCL 만의 추가 단계 |
| + 데이터증강 + post-corr | ≈ 81% (IoT v2+pc 추정) | **ast 0.912 (+4pp)** | BFCL 가 holdout 점수가 명시적 |

**핵심**: 데이터 증강 stage 가 SFT 단독 대비 holdout 에 가장 큰 영향(+9pp).
Post-correction 은 두 데이터셋 모두에서 안정적인 +3~5pp 보조 layer.

## 4. 한계와 다음 단계

- **DPO 실행 실패**: pair 생성에 holdout / OOD 프롬프트 필요. 후속 작업으로 분리.
- **paraphrase 의 천장**: 자연어 분포만 넓혀서는 holdout `multiple` 0.45 → 0.85
  의 점프가 post-correction 없이는 불가능. 의미상(W7 wrong_choice) 실패는 학습
  데이터 다양성으로 완전히 못 풀고 결정적 규칙으로 메꿈.
- **카테고리별 어댑터의 운영 부담**: 5 개 어댑터를 단일 deploy 로 묶는 LoRA-router
  또는 어댑터 merge 는 별도 작업.
- **multi-turn 미포함**: BFCL v4 multi-turn 은 여전히 out-of-scope.

## 5. 산출물

- `runs/factory_bfcl/phase3/paraphrase/<cat>/{paraphrased.jsonl,stats.json}`
- `runs/factory_bfcl/phase3/synth/<cat>/{synth.jsonl,stats.json}`
- `runs/factory_bfcl/phase3/sft_v1_5/<cat>/{adapter,train_metrics.json,eval_{holdout,full}/}`
- `runs/factory_bfcl/phase3/bootstrap/<cat>/{augmented_train.jsonl,augmented_train.stats.json}`
- `runs/factory_bfcl/phase3/sft_v2/<cat>/{adapter,train_metrics.json,eval_{holdout,full}/}`
- `runs/factory_bfcl/phase3/dpo_pairs/<cat>/{pairs.jsonl,pairs.stats.json}` — 1~2 pair/cat
- `runs/factory_bfcl/phase3/dpo/<cat>/<train log only — train failed>`
- `runs/factory_bfcl/phase3/post_corr/{v1_5,v2}/<cat>/{summary.json,cases.jsonl}` — full eval
- `runs/factory_bfcl/phase3/post_corr_holdout/{v1_5,v2}/<cat>/{summary.json,cases.jsonl}` — holdout
- `runs/factory_bfcl/aggregated.json` · `runs/factory_bfcl/table.md` (Phase 1+2+3 통합 표)

드라이버: `runs/factory_bfcl/{teacher_augment.py, bfcl_sft_v2.py, bfcl_bootstrap.py, bfcl_dpo.py, run_phase3.sh, apply_post_corr_to_phase3.py, apply_post_corr_holdout.py}`

## 6. 재현

```bash
bash runs/factory_bfcl/run_phase3.sh                        # paraphrase → synth → v1.5 → bootstrap → v2 → DPO
python runs/factory_bfcl/apply_post_corr_to_phase3.py       # post-correction on full
python runs/factory_bfcl/apply_post_corr_holdout.py         # post-correction on holdout
python runs/factory_bfcl/aggregate.py                       # 통합 표
```
