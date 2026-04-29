# Ganglion Branding and Research Positioning

> *compiler-guided optimization for LLM tool calling*

This file collects the project's public name, tagline, research thesis,
abstract language, and usage notes in one place so they can be lifted into
READMEs, slides, paper drafts, and announcements without rewriting.

---

## 1. Core Positioning

**Research thesis**

> Ganglion is a compiler-guided MLOps framework for token-efficient LLM tool
> calling, with compact Action IRs as the training, validation, and deployment
> interface.

**Korean thesis**

> Ganglion은 LLM tool calling을 토큰 효율적으로 만들기 위한 컴파일러 기반
> MLOps 프레임워크이며, compact Action IR을 학습·검증·배포의 단위로 삼는다.

The important claim is not "we made a DSL." The claim is:

> Tool schemas can be compiled into a compact Action IR, and that IR can become
> the stable unit for prompting, small-model fine-tuning, validation,
> evaluation, repair, and deployment.

Ganglion treats LLM tool calling as an optimization and systems problem:

- **Compile** verbose tool schemas into a compact Action IR.
- **Prompt or train** a model to emit the IR instead of provider-specific tool
  calls.
- **Validate** the IR with deterministic catalog rules.
- **Emit** executable tool calls through provider adapters.
- **Measure** exact match, token cost, latency, repair rate, and schema scaling.
- **Deploy** the same IR contract across hosted LLMs, small local models,
  MCP-style tool catalogs, and eventually physical AI action interfaces.

The current implementation uses a compact JSON DSL as the first Action IR.
Future versions may support other IR surfaces, grammar-constrained decoding,
fine-tuned small models, and automatic schema-to-catalog compilation.

---

## 2. Name and Tagline

| Field | Value |
| --- | --- |
| Public project name | **Ganglion** |
| Primary tagline | *compiler-guided optimization for LLM tool calling* |
| Research subtitle | *compact Action IRs for token-efficient tool-calling models* |
| Paper subtitle | *A compiler-guided MLOps framework for token-efficient LLM tool calling* |
| Korean tagline | *LLM tool calling 최적화를 위한 컴파일러 기반 MLOps* |

### Why "Ganglion"

A ganglion (神經節) is a cluster of nerve cell bodies that sits between the
central nervous system and the periphery, integrating distributed signals into
compact relays. The metaphor maps directly onto what this project does:

- **Intermediate node.** A ganglion sits between brain and end effector;
  Ganglion (the project) sits between the LLM and tool execution.
- **Compaction.** The Greek root γάγγλιον means "knot" — many inputs are
  bundled into a denser representation. Verbose tool schemas are compiled into
  a compact Action IR.
- **Reflex-like routing.** The reflex arc bypasses higher-level reasoning by
  routing through a ganglion for fast, deterministic action. The Action IR
  path skips the verbose schema-aware reasoning step for the same reason.

The repo is named `reflex-language-model` for the same reason — the design
philosophy is "compact, reflex-like action routing" rather than full
schema-aware deliberation.

---

## 3. Message Architecture

### Problem

Modern LLM agents repeatedly expose large tool schemas to the model. As the
number of tools grows, this increases prompt tokens, prefill cost, latency, and
the burden placed on smaller models.

### Insight

Tool invocation does not have to be generated as a verbose provider-specific
schema call. It can be represented as a compact Action IR that is easier for
models to emit and easier for systems to validate.

### System

Ganglion introduces a catalog-driven compiler path:

```text
tool schemas / MCP catalog / action specs
  -> Ganglion catalog
  -> compact Action IR
  -> LLM or fine-tuned small model
  -> validator + optional repair loop
  -> deterministic emitter
  -> executable tool call
```

### Evidence So Far

The current POC validates the approach on synthetic IoT control tasks across 5,
20, and 50 tool tiers using `qwen3.6-plus`:

- The DSL/Action-IR path reaches 100% exact match in the measured tier runs.
- Native tool calling ranges from 96-98% exact match in the same M2 tier runs,
  mainly due to non-canonical scene labels.
- Input token reduction grows with tool count: about 45% at 5 tools, 63% at 20
  tools, and 69% at 50 tools.
- Repeated measurement on the 5-tool tier shows about 19% lower mean latency
  for the DSL/Action-IR path.
- The repair loop is implemented and tested as a validation-time safety hook,
  but its recovery value still needs adversarial and weaker-model experiments.

### Long-Term Claim

Ganglion should become an open-source MLOps stack for token-efficient
tool-calling models: schema ingestion, catalog generation, Action IR design,
small-model fine-tuning, regression evaluation, repair policies, deployment
adapters, and eventually physical AI action safety interfaces.

---

## 4. Text Banners

### Compact

```text
Ganglion: compiler-guided optimization for LLM tool calling
```

### Research

```text
Ganglion: compact Action IRs for token-efficient tool-calling models
```

### Paper

```text
Ganglion: A Compiler-Guided MLOps Framework for Token-Efficient LLM Tool Calling
```

### Wordmark

```text
  ____                   _ _
 / ___| __ _ _ __   __ _| (_) ___  _ __
| |  _ / _` | '_ \ / _` | | |/ _ \| '_ \
| |_| | (_| | | | | (_| | | | (_) | | | |
 \____|\__,_|_| |_|\__, |_|_|\___/|_| |_|
                   |___/
compiler-guided optimization for LLM tool calling
```

### Compiler Path Diagram

```text
        tool schema
            │
            ▼
   ┌────────────────┐
   │ catalog compiler│
   └───────┬────────┘
           │
           ▼
   ┌────────────────┐
   │ compact IR     │  <- generated by LLM / small LM
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

The compact IR is the model-facing contract. The catalog, validator, and
emitter are the compiler/runtime path that turns that contract into executable
tool calls.

---

## 5. Abstract Headlines

### One-Liner

> Ganglion compiles verbose tool schemas into compact Action IRs that LLMs
> and small language models can emit with lower token cost.

### Paper Subtitle

> A compiler-guided MLOps framework for token-efficient LLM tool calling.

### Paragraph

> **Ganglion: Compiler-Guided Optimization for LLM Tool Calling.** Modern
> LLM agents commonly expose verbose tool schemas to the model on every request,
> increasing token cost, latency, and the burden placed on smaller models.
> Ganglion reframes tool calling as compact Action IR generation. A catalog
> compiler derives a short IR surface from tool specifications; the model emits
> that IR; and deterministic validation and emission convert it into executable
> tool calls. In a 500-case IoT control POC across 5, 20, and 50 tool tiers, the
> current JSON-based IR path achieves 100% exact match while reducing input
> tokens by about 45-69% versus native tool schemas, with a repeated 5-tool
> measurement showing about 19% lower mean latency. These results motivate
> Ganglion as an open-source MLOps stack for training, evaluating, repairing,
> and deploying token-efficient tool-calling models.

### Korean Abstract

> **Ganglion: LLM tool calling 최적화를 위한 컴파일러 기반 MLOps.**
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
3. **Compiler-guided MLOps pipeline**: a catalog becomes the source of truth for
   IR rendering, validation, native schema generation, evaluation, repair, and
   deployment adapters.
4. **Scaling evidence**: token savings increase as the tool catalog grows,
   while exact-match accuracy remains stable in the current POC.
5. **Small-model optimization path**: the IR provides a clean fine-tuning and
   distillation target for local or edge tool-calling models.

Avoid overclaiming:

- Do not claim Ganglion is merely "a shorter JSON prompt."
- Do not claim the current POC proves small-model superiority yet.
- Do not present the repair loop as empirically proven robustness until
  adversarial and weaker-model runs are added.
- Do not claim novelty from DSLs alone; claim novelty from compiler-guided
  MLOps for optimized LLM tool calling.

---

## 7. Visual Identity

- **Mark**: a compact node — a ganglion-style cluster relaying signal from a
  schema source into a deterministic adapter. A simple neural-knot or
  intermediate-relay glyph fits the metaphor; avoid medical/anatomical
  illustration.
- **Accent color**: restrained blue/teal or graphite with one sharp accent. The
  project should feel efficient, inspectable, and deployable.
- **Typography**: monospace wordmark for technical surfaces; clean sans
  (Inter, IBM Plex Sans) for slides and papers.
- **Diagrams**: prefer compiler/dataflow diagrams over decorative AI imagery.
  The visual story should be "schema -> Action IR -> validator -> tool call."

A logo image is not committed yet. When one exists, place it at
`docs/assets/ganglion-mark.svg` and link it from the README hero.

---

## 8. Usage Notes

- Use **Ganglion** in user-facing artifacts, papers, READMEs, slide decks,
  and announcements.
- Use **Action IR** for the research abstraction. Use **JSON DSL** when
  referring to the current concrete implementation.
- Use **compiler** for catalog-to-IR, IR-to-schema, and IR-to-tool emission.
  Use **MLOps** for evaluation, regression, repair, fine-tuning, and deployment
  lifecycle.
- The Python package namespace is `ganglion`. Environment variables are
  `GANGLION_MODEL` / `GANGLION_ENABLE_THINKING`. Legacy `RLM_*` variables
  remain readable as a fallback for older scripts and historical reports.
