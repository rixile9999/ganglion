# Ganglion — Branding and Research Positioning

> *compiler-guided MLOps for token-efficient tool calling*

This file collects the project's name, tagline, research thesis, abstract
language, and usage notes in one place so they can be lifted into READMEs,
slides, paper drafts, and announcements without rewriting.

---

## 1. Core Positioning

**Research thesis**

> Ganglion is a compiler-guided MLOps framework for token-efficient small
> tool-calling language models.

**Korean thesis**

> Ganglion은 토큰 효율적인 소형 tool-calling 언어 모델을 만들고 운영하기 위한
> 컴파일러 기반 MLOps 프레임워크다.

The important claim is not "we made a DSL." The claim is:

> Tool schemas can be compiled into a compact Action IR, and that IR can
> become the stable unit for training, validating, evaluating, repairing,
> and deploying tool-calling language models.

In other words, Ganglion treats tool calling as a systems problem:

- **Compile** verbose tool schemas into a compact Action IR.
- **Train** or prompt a model to emit the IR instead of full tool calls.
- **Validate** the IR with deterministic catalog rules.
- **Emit** executable tool calls through adapters.
- **Measure** accuracy, token cost, latency, repair rate, and schema scaling.
- **Deploy** the same IR contract across hosted LLMs, small local models,
  MCP-style tool catalogs, and eventually physical AI action interfaces.

The current implementation uses a compact JSON DSL as the first Action IR.
Future versions may support other IR surfaces, grammar-constrained decoding,
fine-tuned small models, and automatic schema-to-catalog compilation.

---

## 2. Name and Tagline

| Field                     | Value |
| ------------------------- | ----- |
| Project name              | **Ganglion** |
| Primary tagline           | *compiler-guided MLOps for token-efficient tool calling* |
| Research subtitle         | *compact Action IRs for small tool-calling language models* |
| Metaphor tagline          | *spinal tool calling for LLMs* |
| Korean tagline            | *소형 tool-calling 모델을 위한 컴파일러 기반 MLOps* |
| Pronunciation             | /ˈɡæŋɡliən/ — "GANG-glee-on" |
| Etymology                 | A ganglion is a small neural cluster that can mediate fast reflexive behavior without routing every signal through a central cortex. |

The metaphor: native tool schemas in the prompt are like routing every
request through the cortex. Ganglion compiles the tool surface into a
compact Action IR, lets the model produce that IR, then uses deterministic
validation and emission as the reflex arc.

Use the compiler/MLOps positioning for papers and research talks. Use the
spinal-reflex metaphor for README openings, demos, and memorable slide
transitions.

---

## 3. Message Architecture

### Problem

Modern tool-calling agents repeatedly expose large tool schemas to the model.
As the number of tools grows, this increases prompt tokens, prefill cost,
latency, and the cognitive burden placed on smaller models.

### Insight

Tool invocation does not have to be generated as a verbose provider-specific
schema call. It can be represented as a compact Action IR that is easier for
models to emit and easier for systems to validate.

### System

Ganglion introduces a catalog-driven compiler path:

```
tool schemas / MCP catalog / action specs
  -> Ganglion catalog
  -> compact Action IR
  -> LLM or fine-tuned small model
  -> validator + optional repair loop
  -> deterministic emitter
  -> executable tool call
```

### Evidence So Far

The current POC validates the approach on synthetic IoT control tasks across
5, 20, and 50 tool tiers using `qwen3.6-plus`:

- The DSL/Action-IR path reaches 100% exact match in the measured tier runs.
- Native tool calling ranges from 96-98% exact match in the same M2 tier runs,
  mainly due to non-canonical scene labels.
- Input token reduction grows with tool count: about 45% at 5 tools, 63% at
  20 tools, and 69% at 50 tools.
- Repeated measurement on the 5-tool tier shows about 19% lower mean latency
  for the DSL/Action-IR path.
- The repair loop is implemented and tested as a validation-time safety hook,
  but its recovery value still needs adversarial and weaker-model experiments.

### Long-Term Claim

Ganglion should become an open-source MLOps stack for token-efficient
tool-calling models: catalog generation, Action IR design, small-model
fine-tuning, regression evaluation, repair policies, deployment adapters, and
eventually physical AI action safety interfaces.

---

## 4. Text Banners

### Compact

```
Ganglion ── compiler-guided MLOps for token-efficient tool calling
```

### Short Technical

```
Ganglion: compact Action IRs for small tool-calling language models
```

### Metaphor

```
Ganglion ── spinal tool calling for LLMs
```

### Wordmark

```
   ____                    _ _
  / ___| __ _ _ __   __ _ | (_) ___  _ __
 | |  _ / _` | '_ \ / _` || | |/ _ \| '_ \
 | |_| | (_| | | | | (_| || | | (_) | | | |
  \____|\__,_|_| |_|\__, ||_|_|\___/|_| |_|
                    |___/   compact Action IRs for tool calling
```

### Reflex-arc Diagram

```
        user intent
            │
            ▼
   ┌────────────────┐
   │  compact IR    │  <- generated by LLM / small LM
   └───────┬────────┘
           │
   ┌───────▼────────┐
   │ validator      │  <- catalog constraints, aliases, types
   └───────┬────────┘
           │
   ┌───────▼────────┐
   │ emitter        │  <- provider/tool adapter
   └───────┬────────┘
           │
           ▼
     executable tool call
```

The compact IR is the reflex signal. The catalog, validator, and emitter are
the reflex circuit. The model no longer needs to carry the full tool schema
burden on every call.

---

## 5. Abstract Headlines

Three lengths for different surfaces. All convey the same hypothesis.

### One-Liner

> Ganglion compiles verbose tool schemas into compact Action IRs that small
> language models can emit, validate, and deploy with lower token cost.

### Paper Subtitle

> A compiler-guided MLOps framework for token-efficient small tool-calling
> language models.

### Paragraph

> **Ganglion: Compiler-Guided MLOps for Token-Efficient Tool Calling.**
> Modern LLM agents commonly expose verbose tool schemas to the model on every
> request, increasing token cost, latency, and the burden placed on smaller
> models. Ganglion reframes tool calling as compact Action IR generation. A
> catalog compiler derives a short IR surface from tool specifications; the
> model emits that IR; and deterministic validation and emission convert it
> into executable tool calls. In a 500-case IoT control POC across 5, 20, and
> 50 tool tiers, the current JSON-based IR path achieves 100% exact match while
> reducing input tokens by about 45-69% versus native tool schemas, with a
> repeated 5-tool measurement showing about 19% lower mean latency. These
> results motivate Ganglion as an open-source MLOps stack for training,
> evaluating, repairing, and deploying token-efficient small tool-calling
> models.

### Korean Abstract

> **Ganglion: 토큰 효율적인 도구 호출을 위한 컴파일러 기반 MLOps.**
> 일반적인 LLM 에이전트는 도구 호출 시마다 큰 tool schema를 모델에 제공한다.
> 이는 도구 수가 늘어날수록 입력 토큰, latency, 소형 모델의 학습 난이도를 함께
> 증가시킨다. Ganglion은 도구 호출을 compact Action IR 생성 문제로 재정의한다.
> catalog compiler가 tool specification에서 짧은 IR 표면을 만들고, 모델은 이
> IR을 생성하며, deterministic validator와 emitter가 이를 실행 가능한 tool
> call로 변환한다. 현재 POC는 500건 IoT 제어 데이터셋과 5/20/50 tool tier에서
> JSON 기반 IR 경로가 exact match 100%를 달성하면서 native tool schema 대비
> 입력 토큰을 약 45-69% 줄이고, 5-tool 반복 측정에서 평균 latency를 약 19%
> 낮출 수 있음을 보였다. 이 결과는 Ganglion을 소형 tool-calling 모델의
> 학습·검증·복구·배포를 포괄하는 오픈소스 MLOps 스택으로 확장할 근거를 준다.

---

## 6. Contribution Language

Use this framing in papers:

1. **Problem formulation**: schema bloat and provider-specific tool calling
   make small-model deployment inefficient as tool catalogs grow.
2. **Action IR abstraction**: tool calling can be represented as compact,
   model-facing Action IR generation plus deterministic system-side emission.
3. **Compiler-guided MLOps pipeline**: a catalog becomes the source of truth
   for IR rendering, validation, native schema generation, evaluation, repair,
   and deployment adapters.
4. **Scaling evidence**: token savings increase as the tool catalog grows,
   while exact-match accuracy remains stable in the current POC.
5. **Small-model path**: the IR provides a clean fine-tuning and distillation
   target for local or edge tool-calling models.

Avoid overclaiming:

- Do not claim Ganglion is merely "a shorter JSON prompt."
- Do not claim the current POC proves small-model superiority yet.
- Do not present the repair loop as empirically proven robustness until
  adversarial and weaker-model runs are added.
- Do not claim novelty from DSLs alone; claim novelty from the compiler-guided
  MLOps framing around Action IRs for tool-calling models.

---

## 7. Visual Identity

- **Mark**: a small node with three to five short branches, suggesting a
  compact reflex circuit. Monochrome or single-accent works on dark/light
  terminals alike.
- **Accent color**: low-saturation neural blue/teal with a restrained systems
  feel. Avoid high-energy primary colors; the project should feel efficient,
  inspectable, and deployable.
- **Typography**: monospace wordmark for technical surfaces; clean sans
  (Inter, IBM Plex Sans) for slides and papers.
- **Diagrams**: prefer compiler/dataflow diagrams over decorative AI imagery.
  The visual story should be "schema -> IR -> validator -> tool call."

A logo image is not committed yet. When one exists, place it at
`docs/assets/ganglion-mark.svg` and link it from the README hero.

---

## 8. Usage Notes

- Prefer **Ganglion** in user-facing artifacts. The Python package is
  `ganglion`; the repo directory is still `reflex-language-model` and may be
  renamed later.
- Use **Action IR** for the research abstraction. Use **JSON DSL** when
  referring to the current concrete implementation.
- Use **compiler** for catalog-to-IR, IR-to-schema, and IR-to-tool emission.
  Use **MLOps** for evaluation, regression, repair, fine-tuning, and
  deployment lifecycle.
- The legacy term "RLM POC" appears in older reports
  (`docs/m4_repair_loop_report.md`, etc.) where it documents the experiment
  record at the time. Don't rewrite history; use **Ganglion** in anything new.
- Environment variables: prefer `GANGLION_MODEL` /
  `GANGLION_ENABLE_THINKING`. The legacy `RLM_MODEL` /
  `RLM_ENABLE_THINKING` are still read as fallbacks so older shell scripts and
  historical run instructions keep working.
