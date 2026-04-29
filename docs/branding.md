# Ganglion — Branding Kit

> *spinal tool calling for LLMs*

This file collects the project's name, tagline, banners, and abstract
headlines in one place so they can be lifted into READMEs, slides, paper
drafts, and announcements without rewriting.

---

## 1. Name and Tagline

| Field             | Value                              |
| ----------------- | ---------------------------------- |
| Project name      | **Ganglion**                       |
| Tagline (English) | *spinal tool calling for LLMs*     |
| Tagline (Korean)  | *언어 모델을 위한 척수 반사 도구 호출* |
| Pronunciation     | /ˈɡæŋɡliən/ — "GANG-glee-on"       |
| Etymology         | An insect "brain" is a chain of ganglia — small, distributed neural clusters that handle reflexive behavior without a cortex. |

The metaphor: native tool schemas in the prompt are like routing every
request through the cortex. A compact JSON DSL is the spinal reflex —
fewer tokens, lower latency, no full reasoning required for the call.

---

## 2. Text Banners

### Compact (one line, terminal/header use)

```
Ganglion ── spinal tool calling for LLMs
```

### Wordmark (README hero, slide title)

```
   ____                    _ _
  / ___| __ _ _ __   __ _ | (_) ___  _ __
 | |  _ / _` | '_ \ / _` || | |/ _ \| '_ \
 | |_| | (_| | | | | (_| || | | (_) | | | |
  \____|\__,_|_| |_|\__, ||_|_|\___/|_| |_|
                    |___/   spinal tool calling for LLMs
```

### Reflex-arc diagram (illustrates the metaphor)

```
        prompt
          │
          ▼
   ┌──────────────┐         ┌─────────────────┐
   │  LLM (DSL)   │         │  LLM (native)   │
   │  ▽ short IR  │         │  ▽ full schema  │
   └──────┬───────┘         └────────┬────────┘
          │                          │
   ┌──────▼───────┐         ┌────────▼────────┐
   │ Validator    │         │  cortex round-  │
   │ (spinal arc) │         │  trip every     │
   └──────┬───────┘         │  call           │
          │                 └─────────────────┘
          ▼
        tool call
```

The left path is Ganglion: a short reflex circuit. The right path is the
baseline.

---

## 3. Abstract Headlines

Three lengths for different surfaces. All convey the same hypothesis.

### One-liner (announcement, tweet, paper subtitle)

> Ganglion replaces verbose tool schemas in LLM prompts with a compact JSON
> DSL, validated by a deterministic parser — preserving accuracy while
> cutting input tokens and latency.

### Paragraph (paper abstract opener, README intro)

> **Ganglion: Spinal Tool Calling for Language Models.**
> Modern LLM agents handle tool use by routing every request through the
> "cortex" — the model attends to a verbose, fully-typed function schema
> on every call. Ganglion proposes a spinal reflex instead: the model
> emits a compact JSON DSL describing the intended action, and a
> deterministic catalog-driven validator converts it into the executable
> tool call. The DSL surface is much shorter than the equivalent OpenAI
> tool schema, and the validator absorbs canonicalization (locale aliases,
> value normalization, structural checks) that would otherwise burn
> output tokens. We verify the approach on a 500-case IoT control dataset
> across three tool tiers (5, 20, 50 tools) using `qwen3.6-plus`. The DSL
> path matches native tool calling at 100% exact match while reducing
> input tokens by 46–69% (scaling with tool count) and mean latency by
> ~19%. A repair loop on validation failure further raises robustness for
> weaker models.

### Korean abstract (Korean paper / 한국어 발표)

> **Ganglion: 언어 모델을 위한 척수 반사 도구 호출.**
> 일반적인 LLM 에이전트는 도구 호출 시마다 전체 함수 스키마를 prompt에 포함시켜
> "대뇌"를 거치게 한다. Ganglion은 이를 척수 반사로 대체한다 — 모델은 짧은
> JSON DSL로 의도를 표현하고, deterministic catalog 기반 validator가 이를
> 실제 tool call로 변환한다. DSL 표면적은 동등한 OpenAI tool schema보다 훨씬
> 짧으며, validator가 locale alias·값 정규화·구조 검증을 흡수해 출력 토큰
> 소모를 줄인다. 500건 IoT 제어 데이터셋과 3개 도구 tier(5/20/50)로
> `qwen3.6-plus`에서 검증한 결과, DSL 경로는 native tool calling과 동일하게
> exact match 100%를 유지하면서 입력 토큰을 46–69% (도구 수에 따라 증가)
> 줄였고 평균 지연을 약 19% 개선했다. 검증 실패 시 동작하는 repair loop는
> 더 약한 모델에서의 견고성을 추가로 끌어올린다.

---

## 4. Visual Identity (suggested)

- **Mark**: a small node with three to five short branches — the
  schematic shape of an insect ganglion. Monochrome or single-accent
  works on dark/light terminals alike.
- **Accent color**: a low-saturation neural blue/teal. Avoid bright
  primaries; the project's vibe is "low-overhead reflex," not
  high-energy.
- **Typography**: monospace wordmark for technical surfaces; clean sans
  (Inter, IBM Plex Sans) for slides and paper.

A logo image is not committed yet — when one exists, place it at
`docs/assets/ganglion-mark.svg` and link it from the README hero.

---

## 5. Usage Notes

- Prefer **Ganglion** in user-facing artefacts. The Python package is
  `ganglion`; the repo directory is still `reflex-language-model` and
  may be renamed later.
- The legacy term "RLM POC" appears in older reports
  (`docs/m4_repair_loop_report.md`, etc.) where it documents the
  experiment record at the time. Don't rewrite history; use **Ganglion**
  in anything new.
- Environment variables: prefer `GANGLION_MODEL` /
  `GANGLION_ENABLE_THINKING`. The legacy `RLM_MODEL` /
  `RLM_ENABLE_THINKING` are still read as fallbacks so older shell
  scripts and historical run instructions keep working.
