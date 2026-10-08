# Ganglion 논문 — 실험설계 · 벤치마크 리포트 구성 제안

작성일 2026-09-22. 대상: `docs/paper/ganglion_mlsys_draft.md` (commit 78c8eb9 기준 수치)의 §5 재구성.
근거: 2024–2026 tool-calling / structured-output / MLSys 평가 관행 문헌 조사 (4개 스트림, 약 350건 fetch). 각 항목에 URL을 달았고, 1차 출처로 확인하지 못한 것은 **(unverified)** 로 표시.

---

## 0. 결론 요약

1. **투고처.** "MLSys 성격의 저널"은 사실상 존재하지 않는다. MLSys는 proceedings-only 학회다. 현실적 조합은 **MLSys 2027 (마감 2026-10-30, 10p+refs, double-blind) → 낙방 시 TMLR (rolling, 무료, ~2개월, J2C 인증으로 ICML/NeurIPS 포스터)**. 진짜 저널이 1순위라면 **ACM TACO journal-first (HiPEAC 발표 연계)** 가 IR/compiler 프레이밍에 가장 잘 맞지만, SFT 루프 절반을 정당화해야 한다. ([MLSys 2027 CFP](https://mlsys.org/Conferences/2027/CallForResearchPapers), [TMLR](https://jmlr.org/tmlr/editorial-policies.html), [J2C](https://icml.cc/public/JournalToConference), [TACO→HiPEAC](https://www.hipeac.net/news/7094/hipeac-2026-call-papers/))
2. **가장 큰 위험은 2026년 5–9월에 쏟아진 인접 preprint 군이다.** TSCG(schema→compact text 컴파일, 52–57% 토큰 절감), Tool-Schema Compression(context budget sweep), Notation Matters(compact notation이 정확도를 -9~-14pp 깎음), Bitter Lesson of Tool Calling(code-as-action이 JSON 이김), Attributing Structured-Output Gains("interface alignment vs skill" 통제). 리뷰어는 이들을 알고 있을 것이고, 우리 논문의 차별점은 **"compact 표기 자체"가 아니라 "compiler가 표기 손실을 흡수 + 실패를 데이터로 되돌리는 루프"** 임을 실험으로 보여야 한다.
3. **통계.** 현재 초안의 "±2pp / ±5pp binomial" 은 CLT 기반이라 n≤수백에서 과소추정이라는 ICML 2025 spotlight 비판이 있다. **Wilson CI + paired McNemar/sign-flip + template-family clustered SE + ≥3 seeds** 로 바꾼다. BFCL 100건/카테고리로는 수 pp 차이를 판별할 수 없음을 명시한다.
4. **평가 섹션은 RQ 단위로 재편**하고 claim ↔ figure 1:1 매핑. 최근 MLSys 수상작 패턴.
5. **우리만 가진 것**: (a) per-hook rescue/regression 전이행렬, (b) hook 흡수(absorption) → rule retirement 곡선, (c) F⁰/Fᴷ 귀속. 문헌에 정확히 같은 측정이 없다. 이걸 §5의 중심 figure로 올린다.

---

## 1. 투고처 결정

| 순위 | venue | 마감 | 판단 |
|---|---|---|---|
| 1 | **MLSys 2027** (Bellevue, 2027-06-21~25) | **2026-10-30** (OpenReview에서 시각 재확인), 통지 2027-02-28 | 주제 정합 최상 ("Autonomous and agentic AI systems", "LLM fine-tuning/inference", "ML compilers and runtimes" 명시). AE는 voluntary, ACM 배지(Available/Functional/Reproducible)는 camera-ready에만 붙음. 5주 남음. |
| 2 | **TMLR** → ICLR/ICML J2C | rolling; ICLR 2027 J2C 마감 2026-12-18 | 빠르고(리뷰 ~4주) 무료. "correctness over significance" 정책이라 negative result가 많은 우리 논문에 유리. MLSys 결과 후 순차 투고 (동시 투고 불가). |
| 3 | **ACM TACO** journal-first | 상시, HiPEAC 발표 cutoff ~6/1 (unverified) | Action IR = compiler boundary 로 재프레이밍 시 최적의 "진짜 저널". 첫 리뷰 <2개월. |
| 4 | **JSys** | rolling, 6주 리뷰 | Diamond OA, **AE 필수** → run bundle/console 재현성 자산이 그대로 점수. 위상은 낮음. |
| 5 | NAACL 2027 (ARR 10/12) 또는 EuroMLSys 2027 (~2월, 6p) | | 분할 투고 옵션: DSL-vs-native 정확도/null-action은 NLP 스토리, 시스템 부분은 워크숍 티저. |

놓친 것: ICLR 2027 main (9/25), EuroSys/ASPLOS/NSDI 가을 사이클, NeurIPS 2026 워크숍 전부. OSDI '27 (12/8)은 12p지만 우리 논문의 ML 비중이 너무 크다.

**권고**: 10/30 MLSys를 목표로 §3의 Tier A 실험만 수행. Tier B는 TMLR/TACO 확장판용으로 예약.

---

## 2. 리뷰어가 물을 것 (문헌에서 도출)

### 2.1 인접 연구와의 위치 (Related Work + 실험 baseline 양쪽에 반영)

| 논문 | 무엇을 하나 | 우리와의 관계 | 대응 |
|---|---|---|---|
| [TSCG](https://arxiv.org/abs/2605.04107) (2026-05) | JSON schema → 구조화 텍스트 결정적 컴파일, 8 operators, 12 models, 20/50-tool | 가장 가까운 선행. 우리 `render_json_dsl()` 과 동일 아이디어, fine-tune 없음 | **baseline으로 재현** (`home_iot_20`/`smart_home_50` 축 동일). "accuracy-retained ratio" 지표 채택 |
| [Notation Matters](https://arxiv.org/abs/2605.29676) (2026-05) | JSON vs TOON vs TRON on BFCL; compact → -9~-14pp, parallel call 붕괴 | 우리가 이겨야 할 negative baseline | "compact 표기만" vs "compact + compiler hook" ablation |
| [Attributing Structured-Output Gains](https://arxiv.org/abs/2607.02595) (2026-07) | BFCL-CANONICAL rescoring + format-only control prompt; 보고된 gain 상당수가 interface alignment | F⁰/Fᴷ 귀속과 직결 | **format-only control** 실행 (native schema에 우리 canonicalizer만 얹은 조건) |
| [Bitter Lesson of Tool Calling](https://arxiv.org/abs/2608.06370) (2026-08) | typed-Python 호출이 JSON 이김 11/14 models; fan-out에선 -38% 토큰, chaining에선 +1.5× | "왜 code가 아니라 JSON IR인가" | Discussion: grammar mask/parse 결정성/on-device. 가능하면 CodeAct 조건 1개 |
| [CD in small LLMs: semantic gap](https://arxiv.org/abs/2609.23742) (2026-09), [Format Tax](https://arxiv.org/abs/2604.03616), [Constraint Tax](https://arxiv.org/abs/2606.25605) | constrained decoding은 syntax만 고치고 tool selection은 못 고침; format tax는 prompt에서 발생 | 우리 Table 12 (mask 결과)와 정합 | structural/semantic 2축 분해를 모든 표에 적용 |
| [LoopTool](https://arxiv.org/abs/2511.09148), [iTool](https://arxiv.org/abs/2501.09766), [ToolRL](https://arxiv.org/abs/2504.13958) | 실패 기반 데이터 루프 (probe → verify → expand), per-round curve | 루프 baseline | per-iteration 표 형식 미러링 |
| [Training with Harnesses](https://arxiv.org/abs/2605.08741) (2026-05) | harness 붙인 teacher에서 self-distill → student는 harness 없이 동등, 재부착 시 오히려 손해 | **hook absorption의 이론적 유사물** | absorption 주장을 "harness self-distillation for validator hooks" 로 인용·프레이밍 |
| [When2Call](https://arxiv.org/abs/2504.18851), [Over-calling bias](https://arxiv.org/abs/2605.18882), [Sleeping Agent](https://arxiv.org/abs/2604.08388) | call/no-call 2×2, no-tool 예시 없는 SFT는 irrelevance 80→21.7% 붕괴 | null action 주장 | irrelevance를 **2×2 (call/no-call × correct)** 로 보고 |
| [MAST](https://arxiv.org/abs/2503.13657) (NeurIPS 2025), [AgentEval](https://arxiv.org/abs/2604.23581) | taxonomy 검증: ≥3 annotator κ 0.88, 자동 분류기 κ 0.77–0.84 | 14-bucket `classify()` 검증 | 150 trace κ study (Tier B) |
| [Tool-Veritas audit](https://arxiv.org/abs/2607.02577) (2026-06) | BFCL v4 등에서 18.5% evaluator–human 불일치, rerun 편차 18.9pp | BFCL 수치 caveat | 재실행 편차 보고, per-category만 |

### 2.2 BFCL 사용 방식

- v4 headline = Agentic 40 + Multi-turn 30 + Live 10 + Non-Live 10 + Hallucination 10. 우리가 쓰는 non-live single-turn + irrelevance는 **headline의 20~30%** 이고 Non-Live는 saturated로 간주됨 ([v4 blog](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html), [Databricks](https://www.databricks.com/blog/unpacking-function-calling-eval)).
  → **live_simple / live_multiple / live_irrelevance / live_relevance 셀 추가** (v2 Live는 contamination 방지 목적으로 만든 user-contributed set). 자체 "overall"은 절대 만들지 말고 per-category만.
- 우리 DSL 경로 = BFCL "prompt-mode", native = "-FC". 이 용어로 명시.
- BFCL 자체에 **format_sensitivity** 카테고리(26 variations, non-scoring)가 있음 → 우리 IR 렌더링 ablation의 자연스러운 근거.
- sub-2B multi-turn 바닥: Qwen3-0.6B 1.38%, 1.7B 16.88% ([ToolRM](https://arxiv.org/pdf/2509.11963)) → multi-turn 제외를 이 수치로 정당화하고, 가능하면 `multi_turn_base` 20건을 boundary negative result로.
- BFCL harness 기본 temperature 0.001, seed 없음 → 우리는 greedy + 3 seeds(local) / `--repeat` (API).

### 2.3 통계·재현성 관행

- **CI**: [Miller 2024](https://arxiv.org/abs/2411.00640) 의 CLT 권고를 [Bowyer et al. ICML 2025](https://arxiv.org/abs/2503.01747) 가 n<수백에서 부적절하다고 비판. → **Wilson** (단일 시스템), **paired McNemar / sign-flip permutation** (같은 item 위 두 시스템, [evalci](https://arxiv.org/html/2607.04429)), **clustered SE** (IoT 500건은 템플릿 패밀리 = cluster; Miller: clustered SE가 naive의 최대 3×).
  - 대략 폭: p≈0.8에서 n=100 → ±8pp, n=500 → ±3.5pp. 10% discordant에서 3pp 차이를 80% power로 잡으려면 ≈860 items. **BFCL 100/카테고리로는 "win" 주장 불가 → "동등 within CI"로 서술.**
- **Seeds**: LoRA SFT on <2B 의 seed 분산을 정량화한 논문 없음 (gap). 3B LoRA 10 seeds 연구([Bui et al.](https://arxiv.org/abs/2503.07329))가 최근접. → ≥3 (5 권장) seeds, mean±SD + min/max, item set 고정. [Bouthillier MLSys 2021](https://arxiv.org/abs/2103.03098) 인용.
- **Numerics**: MPS vs CUDA LoRA 편차를 문서화한 논문 없음 (gap) → 우리 §5.6 finding이 reportable. [2506.09501](https://arxiv.org/abs/2506.09501)(bf16 greedy가 GPU 종류/batch로 최대 9% 편차, Std@Acc 지표), [bf16 LoRA collapse](https://arxiv.org/abs/2510.26788) 인용. dtype×device 매트릭스를 표로.
- **Contamination**: SE-LLM 논문 중 32.8%만 언급. → synthetic SFT 데이터 vs BFCL/IoT test 13-gram overlap 수치, holdout은 **tool family 단위**, in-prompt 4건 제외 재실행, teacher(qwen3.6-plus)가 baseline이자 teacher임을 명시.
- **NeurIPS checklist #7/#8** 형식으로 "어떤 변동성을 error bar가 잡는가" + 총 compute(GPU-h, teacher $) 기재.
- **Artifact**: run bundle(`manifest.json` identity triple)이 그대로 AE appendix. Available/Functional/Reproducible 배지 목표.

### 2.4 MLSys 평가 섹션 골격

최근 MLSys 논문 (XGrammar, FlashInfer, Marconi, SuperInfer) 공통: `setup → end-to-end(claim당 1 fig/table) → microbenchmark → ablation(모든 컴포넌트 toggle) → sensitivity sweep → overhead 한 줄`. SuperInfer(MLSys 26)는 분석 소절마다 질문으로 시작. 수상작은 claim→figure 매핑이 촘촘함. 표준 지표: mask-gen µs/token, TTFT, TPOT, throughput, KV memory/seq, overhead vs unconstrained, 하드웨어 명시.

---

## 3. 제안하는 §5 구조 (RQ 기반)

각 RQ = 하나의 claim = 하나의 main figure/table. 모든 수치에 `(model, accelerator, dtype, seeds, n, CI)` 태그.

### RQ1. IR이 정확도를 보존하는가? (IoT + BFCL)
- **Table**: IoT 3 tier × {native, IR} × {qwen3.6-plus, flash} — **500건 전부** (현재 50건 샘플 → 500으로 확대), Wilson CI, McNemar p.
- **Table**: BFCL per-category: non-live 5 + **live 4 셀 추가**, prompt-mode/FC 표기, Tool-Veritas caveat.
- **Control (신규)**: (a) *format-only*: native schema + canonicalizer만 → IR gain 중 interface alignment 분리. (b) *compact-only*: IR 렌더링 + hook 전부 strip(F⁰) → Notation Matters식 "compact가 깎는 양"을 우리 compiler가 얼마나 회복하는지. 이 두 조건이 논문의 방어선.
- **Figure**: 렌더링 ablation (JSON schema / Python doc / TSCG-style / our DSL) — BFCL format_sensitivity 200건 활용.

### RQ2. IR의 비용은? (tokens → serving)
- **Figure 2 (기존 TODO)**: input tokens vs #tools (5/20/50 + BFCL 산점) + 가산 모델 fit. **100-tool 지점 추가** 권장(TSCG/Tool-Schema Compression이 8K budget에서 JSON 붕괴를 보임 → context-budget 축 하나 넣으면 강함).
- **Table (신규, H100 vLLM)**: 두 prefix에 대해 TTFT / TPOT / throughput @ batch 1–32, **prefix caching on/off**, **KV memory per sequence → max batch**. 이것이 "prefix caching이 토큰 논거를 지운다"는 반박의 정량 답변. 하드웨어: 보유 H100 PCIe 80GB.
- Output tokens -31% 는 caching 무관 → 별도 행.
- Compiler overhead 한 줄 (render / validate / grammar compile µs) — XGrammar/JSONSchemaBench의 GCT 관행.

### RQ3. Factory가 0.6B를 쓸 만하게 만드는가?
- **Figure 3**: stage progression bars (Table 9/10) — **3–5 seeds CUDA, mean±SD**, 각 stage delta에 paired CI.
- **Sweep**: data size (100 / 341 / 5× aug) × model size (0.6B / 1.7B / +4B 1점) — Octopus/Nemotron/LoopTool 관행.
- **Loop cost table**: iteration별 examples, compiler reject rate, teacher call 수/$, GPU-min. (TinyAgent ~$500, Octopus $0.0224/1K 선례)
- **General-capability regression** 한 행 (IFEval 또는 MMLU 소량) — COVERT/ToolFlow 이후 리뷰어 기대.

### RQ4. 피드백 루프는 무엇을 고치고 무엇을 망치나? ← **논문의 차별점**
- **Figure 4 (핵심)**: per-hook **rescue / regression 전이행렬** (`compare_runs`) + paired-bootstrap CI. Reinforced Agent의 Helpfulness–Harmfulness 비율과 대응.
- **Figure 5 (신규)**: **hook absorption curve** — SFT iteration에 따른 "F⁰만으로 통과하는 비율" per hook → `retire_rule` 시점. 문헌에 동일 측정 없음. Training-with-Harnesses의 "재부착 시 손해" 체크 포함 (`strip_hooks` 로 재파싱).
- **Table**: 14-bucket histogram before/after (Table 11), structural vs semantic 2축으로 재집계.
- **Proposer precision (기존 TODO)**: `analyzer/rules.py` 를 기록된 trace에 돌려 operator 선택 rule 대비 precision/recall.
- **Taxonomy validation (Tier B)**: 150 trace, 3 annotator, Cohen's κ; `classify()` vs human κ. (MAST 0.88/0.77, AgentEval 0.84 기준선)

### RQ5. Null action이 abstention을 어떻게 바꾸나?
- **2×2 table**: call/no-call × correct/incorrect, false-call rate vs false-abstention rate, {no null, null} × {plus, flash} × {non-live irrelevance, live_irrelevance, live_relevance}. Hammer/When2Call 형식.
- SFT 데이터에 no-tool 예시 비율 sweep 1점 (Sleeping Agent 붕괴 재현 여부).

### RQ6. Overheads, numerics, negative results
- dtype × device 매트릭스 (MPS bf16/fp32, CUDA bf16/fp32) × 3 seeds, Std@Acc.
- Grammar mask / repair / bootstrap / DPO 의 negative results — "invalid-output mass에 상한" 명제를 Appendix A 식으로.
- Threats: 단일 모델 family, BFCL single-turn 비중, 커스텀 IoT 벤치 정당화(Octopus Android / TinyAgent MacOS / FunctionGemma Mobile Actions / HammerBench 선례 + Home Assistant projection).

---

## 4. 벤치마크 리포트(repo 산출물) 구성

`runs/` 와 콘솔의 리포트를 논문 표와 1:1로 맞춘다. 리포트 하나 = 하나의 RQ.

```
runs/paper/<rq>/<condition>/            # --trace-store 로 생성된 run bundle
  manifest.json                          # identity triple + seed + accelerator + dtype + decoding
  summary.json / report.md
  compare-<baseline>.json                # 전이행렬 + paired bootstrap CI
runs/paper/aggregate.py                  # → docs/paper/tables/*.md (표 자동 생성, 수치 손편집 금지)
docs/paper/eval_card.md                  # Evaluation Card: 데이터 provenance, contamination 검사 결과,
                                         #   seeds, 하드웨어, compute 총량, 어떤 변동성을 CI가 잡는지
```

리포트 템플릿 (report.md 에 추가할 필드):

| 필드 | 내용 |
|---|---|
| identity | `catalog_fingerprint`, `model_fingerprint`, `dataset_sha256`, git sha |
| decoding | greedy / T / seed / repeat |
| hardware | GPU, dtype, framework 버전 |
| n, CI | Wilson 95%, cluster 단위(템플릿 family) |
| structural / semantic | syntax-valid, tool-select, arg-correct, abstention 4분해 |
| vs baseline | fixed / regressed / same_pass / same_fail + McNemar p + ΔEM CI |
| cost | input / output tokens, TTFT, TPOT, $ (API) 또는 GPU-min |

`analyzer/compare.py` 에 McNemar와 Wilson 을 추가하면 위 표가 기계 생성된다 (현재 paired bootstrap만 있음).

---

## 5. 실험 우선순위 (10/30 마감 기준)

### Tier A — MLSys 제출에 필수 (약 4주)
| # | 실험 | 근거 | 규모 |
|---|---|---|---|
| A1 | IoT 3 tier × 2 path × 2 API 모델, **500건 전부**, `--repeat 3` | RQ1 CI 폭 | API 호출 ~18k |
| A2 | 로컬 SFT 헤드라인 (Table 9/10/12) **CUDA 3 seeds** | RQ3, checklist #7 | H100 수 시간 |
| A3 | **vLLM serving**: TTFT/TPOT/throughput/KV-per-seq, batch 1–32, APC on/off, 두 prefix × 3 catalog | RQ2 반박 방어 | H100 반나절 |
| A4 | **format-only control** + **compact-only(F⁰) control** on BFCL 500 + IoT | Attributing/Notation Matters 방어 | 코드 소량 + API |
| A5 | **TSCG-style minified schema baseline** (native schema 텍스트 압축) | 기존 TODO, 최근접 선행 | 렌더러 1개 |
| A6 | BFCL **live_*** 4 셀 (100건씩 seed-42 subsample) | §2.2 | loader 확장 |
| A7 | Wilson + McNemar + clustered SE 를 `compare.py`/aggregate에 추가, 모든 표 재생성 | §2.3 | 코드 |
| A8 | contamination: 13-gram overlap 수치, in-prompt 4건 제외 재실행, tool-family holdout | §2.3 | 스크립트 |
| A9 | proposer precision (기존 TODO #5) | RQ4 | 오프라인 |
| A10 | hook absorption curve: v1→v2 (+v3 1회 더) 에서 per-hook F⁰ pass 비율 | RQ4 핵심 figure | H100 수 시간 |
| A11 | irrelevance 2×2 표 재집계 (기존 데이터로 가능) | RQ5 | 집계 |
| A12 | Figures 1–5 + LaTeX 템플릿 이관 + related work에 2026 preprint 군 추가 | | |

### Tier B — 저널 확장판 (TMLR/TACO)
- 두 번째 모델 family (Gemma 4 / Llama) on BFCL 500 + IoT.
- top-k tool retrieval baseline (ToolRAG), CodeAct/PTC 조건.
- context-budget sweep (8K/16K/32K) with 100-tool catalog.
- taxonomy κ study (150 trace, 3 annotators).
- 다중 iteration 수렴 (3+ rounds), cross-catalog patch transfer.
- multi_turn_base 20건 boundary result, ACEBench Special 카테고리.
- data-size × model-size 전체 grid, general-capability regression.
- 콘솔 human-in-the-loop 측정 (time-to-root-cause; AgentEval 4.2h→22min 선례).

---

## 6. 초안에서 바꿔야 할 문장들

- §5.1 "At n=500 a binomial 95% interval is about ±2pp" → Wilson/clustered로 교체, "BFCL 100/카테고리는 수 pp 차이를 판별할 power가 없다" 명시.
- §5.2 BFCL 표: "IR 86.2% vs native 85.6%" 를 **win이 아니라 within-CI 동등** 으로 서술. irrelevance +4pp 도 n=100 CI 안.
- §5.2 IoT 50건 → 500건으로 교체.
- §6.1 prefix caching 논의 → A3 수치로 대체.
- §7 Related Work: TSCG, Tool-Schema Compression, Notation Matters, Bitter Lesson, Attributing Structured-Output Gains, CD-in-small-LLMs, Format/Constraint Tax, LoopTool, Training with Harnesses, When2Call, MAST 추가. 2026 preprint는 "concurrent work"로.
- 기여 bullet 3 ("factory") 에 **absorption/retirement** 를 명시적 기여로 승격.
- Threats: "IoT 벤치가 custom" 정당화 문단 추가 (선례 인용), "BFCL single-turn = headline의 20–30%" 수치 명시.

---

## 부록: 핵심 출처

- Venue: [MLSys 2027 CFP](https://mlsys.org/Conferences/2027/CallForResearchPapers) · [MLSys 2026 AE](https://mlsys.org/Conferences/2026/CallForAEs) · [TMLR](https://jmlr.org/tmlr/) · [JSys](https://www.jsys.org/) · [OSDI '27](https://www.usenix.org/conference/osdi27/call-for-papers) · [NAACL 2027](https://2027.naacl.org/calls/main_conference_papers/)
- Stats: [Miller 2024](https://arxiv.org/abs/2411.00640) · [Bowyer ICML 2025](https://arxiv.org/abs/2503.01747) · [evalci](https://arxiv.org/html/2607.04429) · [Bui IJCNLP 2025](https://arxiv.org/abs/2503.07329) · [Bouthillier MLSys 2021](https://arxiv.org/abs/2103.03098) · [Numerical nondeterminism](https://arxiv.org/abs/2506.09501) · [NeurIPS checklist](https://neurips.cc/public/guides/PaperChecklist) · [Evaluation Cards](https://arxiv.org/abs/2606.09809)
- BFCL: [ICML 2025 paper](https://proceedings.mlr.press/v267/patil25a.html) · [v4 blog](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html) · [format sensitivity](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html) · [v2 Live](https://gorilla.cs.berkeley.edu/blogs/12_bfcl_v2_live.html) · [Tool-Veritas audit](https://arxiv.org/abs/2607.02577)
- Adjacent 2026: [TSCG](https://arxiv.org/abs/2605.04107) · [Tool-Schema Compression](https://arxiv.org/abs/2605.26165) · [Notation Matters](https://arxiv.org/abs/2605.29676) · [Bitter Lesson](https://arxiv.org/abs/2608.06370) · [Attributing Gains](https://arxiv.org/abs/2607.02595) · [CD semantic gap](https://arxiv.org/abs/2609.23742) · [Format Tax](https://arxiv.org/abs/2604.03616) · [Training with Harnesses](https://arxiv.org/abs/2605.08741) · [LoopTool](https://arxiv.org/abs/2511.09148) · [When2Call](https://arxiv.org/abs/2504.18851) · [MAST](https://arxiv.org/abs/2503.13657)
- Systems eval 관행: [XGrammar MLSys 25](https://arxiv.org/abs/2411.15100) · [SuperInfer MLSys 26](https://arxiv.org/html/2601.20309) · [Marconi](https://arxiv.org/abs/2411.19379) · [JSONSchemaBench](https://arxiv.org/abs/2501.10868) · [Anthropic tool search](https://www.anthropic.com/engineering/advanced-tool-use)
