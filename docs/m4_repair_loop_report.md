# M4 Repair Loop 실험 보고서

**작성일:** 2026-04-28  
**마일스톤:** M4 - 실패 패턴 기반 repair loop 구현  
**상태:** ✅ 구현 완료, ⚠️ 검증 데이터셋 부족

---

## 1. 요약

M4 에서는 LLM 출력 검증 실패 시 자동으로 재시도를 수행하는 **repair loop**를 구현했다. 50 건 sample 로 repair on/off 를 비교한 결과, **happy-path 에서는 overhead 가 측정 불가능 수준 (무비용 안전망)**임을 확인했다. 다만 현재 데이터셋이 너무 단순해 repair 가 트리거되지 않아, 실제 복구율은 측정하지 못했다.

**핵심 결과:**

| 항목 | 결과 |
|------|------|
| **정확도** | 100% (repair off/on 동일) |
| **Token 사용량** | 변화 없음 |
| **Latency 영향** | -2% (노이즈 범위) |
| **Repair 시도** | 0 회 (트리거 없음) |
| **단위 테스트** | 4/4 통과 |

---

## 2. 구현 내용

### 2.1. 아키텍처

```text
User Prompt
  -> Qwen JSON output
  -> JSON DSL validator
  -> [실패 시] repair loop (최대 N 회)
       -> "Your JSON failed validation: <error>"
       -> 모델 재응답
  -> [성공 시] normalized ActionPlan
  -> tool call
```

### 2.2. 주요 파일

| 파일 | 내용 |
|------|------|
| `rlm_poc/runtime/qwen.py` | `run_dsl_with_repair()`, `RepairConfig` |
| `rlm_poc/eval/runner.py` | `--repair`, `--repair-max-attempts` CLI flags |
| `rlm_poc/eval/metrics.py` | `repair_attempts_total`, `repair_successes_total` 집계 |
| `tests/test_repair_loop.py` | 4 개 단위 테스트 |

### 2.3. RepairConfig API

```python
@dataclass(frozen=True)
class RepairConfig:
    enabled: bool = False
    max_attempts: int = 1
```

**사용 예:**

```bash
# Repair loop 활성화 (최대 1 회 재시도)
python -m rlm_poc.eval.runner --llm qwen --repair --repair-max-attempts 1
```

---

## 3. 실험 설계

### 3.1. 조건

| 항목 | 값 |
|------|-----|
| **Tier** | `iot_light_5` |
| **케이스** | 50 건 sample |
| **LLM** | `qwen3.6-plus` (DashScope) |
| **비교** | `repair off` vs `repair on (max_attempts=1)` |
| **반복** | 1 회/case |

### 3.2. 측정 지표

- `syntax_valid_rate`: JSON parse 성공률
- `exact_match_rate`: 정규화 후 정답 일치율
- `latency_ms_mean/p50/p95/stddev`: 응답 시간 통계
- `input_tokens_total`, `output_tokens_total`: 토큰 사용량
- `repair_attempts_total`: 총 재시도 횟수
- `repair_successes_total`: 복구 성공 횟수

---

## 4. 실험 결과

### 4.1. 정량적 비교

| 지표 | repair off | repair on | 차이 |
|------|-----------|-----------|------|
| **총 케이스** | 50 | 50 | - |
| **syntax_valid_rate** | 100% | 100% | 0 |
| **exact_match_rate** | 100% | 100% | 0 |
| **action_match_rate** | 100% | 100% | 0 |
| **latency mean** | 1,591.18 ms | 1,557.35 ms | -33.83 ms (-2.1%) |
| **latency p50** | 1,581.84 ms | 1,541.32 ms | -40.52 ms (-2.6%) |
| **latency p95** | 2,262.33 ms | 2,224.33 ms | -38.00 ms (-1.7%) |
| **latency stddev** | 388.51 ms | 350.20 ms | -38.31 ms (-9.9%) |
| **input_tokens** | 22,757 | 22,757 | 0 |
| **output_tokens** | 1,209 | 1,209 | 0 |
| **repair_attempts_total** | null | null | 0 |

### 4.2. 시각화

```
Latency Distribution (ms)
                        
repair off  |████████████████ 1591
repair on   |████████████████ 1557
            +------------------------
            0       1000      2000
```

### 4.3. 관찰 사항

1. **Repair 트리거 없음:** `repair_attempts_total`가 두 조건 모두 `null` → repair loop 가 단 한 번도 실행되지 않음
2. **무비용 안전망:** repair=on 일 때 overhead 측정 불가 (오히려 latency 가 약간 감소)
3. **Latency 변동은 노이즈:** API 네트워크 지연, 서버 부하 등의 영향

---

## 5. 단위 테스트 검증

`tests/test_repair_loop.py` 의 4 개 테스트 모두 통과:

| 테스트 | 검증 항목 | 결과 |
|--------|----------|------|
| `test_repair_recovers_on_second_attempt` | invalid room("moon") → valid room("living") 복구 | ✅ |
| `test_repair_disabled_propagates_error` | repair=False 일 때 예외 전파 | ✅ |
| `test_repair_exhausts_attempts` | max_attempts 초과 시 예외 발생 | ✅ |
| `test_repair_recovers_invalid_json` | JSON parse 실패 → 복구 | ✅ |

**기능적 완성도:** repair loop 자체는 **정상 작동**함. 문제는 **트리거 조건**이 발생하지 않는 것.

---

## 6. 문제 분석: 왜 repair 가 트리거되지 않는가?

### 6.1. 현재 데이터셋의 한계

500 건 데이터셋이 **매우 단순한 명령**만 포함:

```text
✅ 단순 명령 (100% valid):
- "거실 불 70% 로 켜줘"
- "밤 10 시 반에 침실 조명 꺼줘"
- "영화 모드 scene 을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘"

❌ 복잡한 명령 (데이터셋에 없음):
- "영화 보기 좋은 분위기로 만들어줘" (ambiguous scene name)
- "안방 불 좀 적당히 켜줘" (unknown alias + vague brightness)
- "거실과 주방 불 모두 켜줘" (multi-call 필요)
- "7 시에 켜고 10 시에 꺼줘" (2 개 schedule_light 호출)
```

### 6.2. 실패 유형 분류

| 실패 유형 | 설명 | 현재 데이터셋 |
|----------|------|--------------|
| **JSON syntax 실패** | Markdown fence, 설명문 혼입 | 발생 안 함 |
| **Invalid enum** | 허용되지 않은 room/state 값 | 발생 안 함 |
| **Missing field** | 필수 인자 누락 | 발생 안 함 |
| **Out of range** | brightness > 100 등 | 발생 안 함 |
| **Ambiguous intent** | 의도 추론 필요 | 발생 안 함 |
| **Multi-call 필요** | 복수 도구 호출 | 발생 안 함 |

**결론:** `qwen3.6-plus`가 현재 데이터셋에서는 **이미 100% valid JSON 생성**

---

## 7. Native Path 실패와의 괴리

### 7.1. M2/M3 에서 관찰된 native 실패

Native tool calling 에서 일관되게 2-4% 실패 발생:

```text
create_scene.name 실패 사례:
- "movie" (정답) → "Movie Time", "영화 볼 때", "movie scene for bedroom light"
```

### 7.2. 왜 repair 가 잡지 못하는가?

현재 구현에서:

1. **Native path:** validator 가 raw label 을 그대로 통과시킴
2. **Exact match 단계:** `SCENE_ALIASES`가 정규화하여 정답 처리
3. **결과:** validator 는 실패로 인식하지 않음 → repair loop 트리거 안 됨

**해결책 후보:**

```python
# (1) SCENE_ALIASES 를 enum 으로 강제
#     name: enum["movie", "relax", "focus"] 처럼 제한
#     → validator 가 미정규화 라벨을 reject

# (2) Ambiguous prompt 추가
#     "영화 보기 좋은 분위기" 같은 케이스를 데이터셋에 추가
#     → raw failure rate 인위적 상승

# (3) Native path 에도 repair loop 적용
#     현재는 DSL path 에만 구현됨
```

---

## 8. 권장 다음 단계

### 8.1. 우선순위 높음

**(1) Adversarial 데이터셋 추가**

```python
# examples/iot_light/adversarial_cases.py
ADVERSARIAL_CASES = [
    # Ambiguous scene name
    ("영화 보기 좋은 분위기로 만들어줘", create_scene("movie", ...)),
    
    # Unknown alias
    ("안방 불 켜줘", set_light("bedroom", ...)),
    
    # Vague brightness
    ("불 좀 적당히 켜줘", set_light("living", brightness=50)),
    
    # Multi-call
    ("거실과 주방 불 모두 켜줘", [set_light("living"), set_light("kitchen")]),
    
    # Complex schedule
    ("7 시에 켜고 10 시에 꺼줘", [schedule_light("07:00", on), schedule_light("22:00", off)]),
]
```

**(2) Native 실패 Escalation**

- `SCENE_ALIASES` 를 enum 으로 강제
- validator 가 미정규화 라벨을 `DSLValidationError` 로 reject
- repair loop 가 native 실패를 복구

### 8.2. 우선순위 중간

**(3) Repair 통계 개선**

```python
# 현재: repair_attempts_total = null (0 과 구분 안 됨)
# 개선: 명시적으로 0 으로 기록
"repair_attempts_total": 0,  # "시도했으나 실패 없음"
```

**(4) Max attempts ablation**

| 조건 | 예상 latency | 예상 복구율 |
|------|-------------|-----------|
| max_attempts=0 | 100% | 0% |
| max_attempts=1 | 100-105% | 50-80%? |
| max_attempts=2 | 105-115% | 70-90%? |

---

## 9. 종합 평가

| 항목 | 평가 | 근거 |
|------|------|------|
| **기능 완성도** | ✅ 우수 | 4 개 단위 테스트 모두 통과 |
| **성능 영향** | ✅ 무비용 | happy-path overhead 측정 불가 |
| **실효성 검증** | ⚠️ 불완전 | 트리거 조건이 발생하지 않음 |
| **데이터셋 적합성** | ⚠️ 한계 | 너무 단순한 명령만 포함 |
| **보고서 일관성** | ✅ 일치 | §16.4 분석과 실험 결과 일치 |
| **M5 기여도** | ✅ 높음 | 자동화 기반 인프라 |

---

## 10. 결론

**M4 는 "기술적으로 완성되었으나, 검증 데이터셋이 부족하다"가 정확한 평가입니다.**

- ✅ **구현은 완벽:** repair loop 는 단위 테스트에서 의도대로 작동
- ✅ **무비용 안전망:** happy-path 에서 overhead 0 확인
- ⚠️ **검증 부족:** 실제 실패 케이스가 없어 recovery rate 측정 불가

**추천 진행 방향:**

1. M5(MCP schema 자동 생성) 우선 진행
2. M5 진행 중 자연스러운 실패 패턴 발견 시 repair loop 재검증
3. 또는 adversarial 데이터셋 10-20 개 제작 후 M4b 실험

---

## 11. 변경 사항

### 11.1. 코드

```text
rlm_poc/runtime/qwen.py      (확장) run_dsl_with_repair(), RepairConfig
rlm_poc/eval/runner.py       (확장) --repair, --repair-max-attempts
rlm_poc/eval/metrics.py      (확장) repair 통계 집계
tests/test_repair_loop.py    (신규) 4 개 테스트
```

### 11.2. 실험 명령

```bash
# Repair off (baseline)
python -m rlm_poc.eval.runner --llm qwen --tier iot_light_5 --limit 50

# Repair on (max_attempts=1)
python -m rlm_poc.eval.runner --llm qwen --tier iot_light_5 --limit 50 --repair --repair-max-attempts 1
```

### 11.3. 검증 결과

```text
pytest:                     18/18 통과
M4 Qwen API:                100 calls (50 × 2 conditions)
repair_attempts_total:      0 (트리거 없음)
overhead:                   측정 불가 (무비용)
```
