# Ganglion — Research Vision & POC Plan (for external review)

> This document is a self-contained brief for an external reviewer (e.g., another LLM or a human collaborator) who has no prior context on this project. The author is the primary researcher; they want critical evaluation of both **the vision** and **the POC implementation plan**.

---

## What we want from you (the reviewer)

1. **Critique the vision.** Is the "verifier-driven model factory" thesis defensible in 2026? Where is it weakest? What would kill it?
2. **Evaluate the POC plan.** Is the 10-week scope realistic? Are the success criteria sharp enough? What's missing? What's over-engineered?
3. **Identify blind spots.** Especially: prior art we haven't cited, recent papers (DeepSeek-R1, GRPO variants, verifier-driven RL), competing platforms, failure modes of the abstraction.
4. **Suggest concrete improvements** to the TaskSpec abstraction and the pipeline shape.

Push back hard. The author has already discarded the original POC's headline claim under criticism (see §2) and prefers honest objection over agreement.

---

## 1. Existing codebase — Ganglion (POC, ~6 months in)

**Repo**: directory `reflex-language-model`, Python package `ganglion`. Public name: **Ganglion**.

**Original POC question**: *Can a compact JSON DSL replace native tool/function-call schemas in the prompt while preserving accuracy?* Hypothesis was that DSL reduces input token cost vs. handing the model the full OpenAI tool schema.

**Architecture (current)**:

```
ganglion/
├── dsl/
│   ├── tool_spec.py        # ToolSpec, EnumArg/IntArg/StringArg/TimeArg/RawArg/BoolArg/NumberArg
│   ├── catalog.py          # Catalog: render_json_dsl / render_openai_tools / parse_json_dsl
│   ├── compiler.py         # external schema (OpenAI/MCP/BFCL) → ToolSpec
│   ├── validator.py        # _validate_flat_args, DSLValidationError
│   ├── emitter.py
│   ├── json_extract.py     # parse_json_dsl_lenient (strict / fenced / embedded)
│   └── types.py            # ToolCall, ActionPlan (frozen dataclasses, value equality)
├── schema/                 # Hand-written catalogs at three sizes (5/20/50 tools)
│   ├── iot_light.py        # CATALOG (5 tools)
│   ├── home_iot.py         # CATALOG (20 tools)
│   └── smart_home.py       # CATALOG (50 tools)
├── runtime/
│   ├── qwen.py             # QwenJSONDSLClient / QwenFreeformJSONDSLClient / QwenNativeToolClient + run_dsl_with_repair
│   ├── rules.py            # RuleBasedJSONDSLClient (offline stub)
│   ├── executor.py         # mock executor for tests
│   └── types.py            # ModelClient/ModelResult interface
├── eval/
│   ├── runner.py           # `python -m ganglion.eval.runner`
│   ├── bfcl_runner.py      # BFCL v4 benchmark runner
│   ├── scaling.py          # DSL vs native catalog token-size measurement (M2)
│   ├── dataset.py          # JSONL loader (parses expected DSL at load)
│   └── metrics.py          # syntax/exact/action match, latency, tokens, repair stats
└── bfcl/
    ├── loader.py           # BFCL case → ganglion format
    └── grader.py
```

**Key design**: `Catalog` is the compiler boundary — it bundles `ToolSpec`s and renders **two artifacts from the same source of truth**: a compact DSL prompt (`render_json_dsl()`) and the full OpenAI tools schema (`render_openai_tools()`). The validator (`parse_json_dsl()`) accepts either string or mapping, normalizes via `_validate_flat_args`, and returns an immutable `ActionPlan`.

**Compression that the DSL achieves over native** (for prompt tokens):
- JSON Schema scaffolding (`{"type":"function","function":{...}}`, `"type":"object"`, `"additionalProperties":false`) removed
- Type keywords inline (`integer` not `{"type":"integer"}`)
- `required` array → inline `optional` prefix
- Enum → `"a"|"b"|"c"` for ≤3 values, `one of …` for more
- Bounds → `integer 0..100`
- Time pattern → `"HH:MM" 24h time`
- All `description` fields stripped
- Common header rendered once for N tools

**Repair loop (M4)** lives in `run_dsl_with_repair()` — on `DSLValidationError` it appends the failing assistant message + corrective user message and retries up to `RepairConfig.max_attempts`.

**Recent results** (in repo's `docs/` folder): on BFCL v4 (500 cases), the DSL approach reached 86.2% AST accuracy, *slightly above* native tool-calling baseline at 85.6% — so under measurement, DSL is not just smaller but marginally more accurate. Whether the ~0.6pp gap is signal or noise has not been rigorously tested.

---

## 2. Why the original POC headline doesn't justify the project anymore

The original justification ("DSL is shorter → cheaper input tokens") was strongest in 2023. By 2026:

- **Prompt caching** (Anthropic, OpenAI, DashScope) reduces cached-prefix cost by ~80–90%. For *fixed-catalog* deployments, the schema becomes a static prefix that's almost free after first call.
- **Output token cost** is unaffected by DSL vs. native — both produce JSON of comparable length.
- **TTFT** still benefits from shorter prompts even with caching, but margin is small.
- **Per-call variable catalogs** (BFCL eval, multi-tenant SaaS, MCP-style dynamic tool discovery) defeat caching → token argument still applies, but this is a niche.

**Honest conclusion**: token economy alone is no longer a strong project justification. What survives:

1. **Validator + repair loop** — alias normalization, range clamping, custom validators, M4 repair. Native tool calling does *none* of this; you'd write it yourself anyway.
2. **Provider portability** — same `Catalog` IR drives DSL prompt, OpenAI tools, and (planned) decoding grammars and LoRA training data.
3. **Empirical accuracy edge** (M5: +0.6pp on BFCL) — needs rigorous statistical confirmation.
4. **Path to small specialized models** — once you've got a clean IR + validator, training a domain SLM is the natural next step.

Item (4) is what this document is about.

---

## 3. Vision (after multiple revisions)

### 3.1 The dead-end direction we explored and rejected

**"Build a 200M-parameter tool-calling SLM that beats GPT-4o."** Rejected because:

- Existing tool-specialized 7B models (xLAM, Hammer, Octopus) plateau at 75–82% on BFCL.
- Going from 7B → 200M *and* improving accuracy 5pp+ has no precedent.
- "We beat GPT-4o" requires apples-to-apples comparison the small model will lose at the headline level.
- The *real* defensible claim for an SLM is **deployment-conditional**: "edge, 50ms latency, INT4 120MB, fixed-catalog 90%+." Not absolute accuracy.

We re-calibrated: an honest target SLM is 200M-1B with 70–80% BFCL, beating size-matched open baselines (Qwen3-0.5B at ~60%, etc.) by 10–15pp through specialization.

### 3.2 The actual vision — Verifier-driven Model Factory

The strongest insight from the project so far:

> **Ganglion's `parse_json_dsl()` validator is a deterministic, dense, automatic reward function. RL/DPO without human labels.**

Generalize this beyond tool calling:

> **For any narrow task with a deterministic verifier, you can train a specialized small model with verifier-driven RL — no human reward labels needed. Build a platform that takes (grammar, verifier) as input and produces a deployable specialized SLM as output.**

In this framing, **tool calling is just one task among many**. The product is the *factory*, not the model. Each customer's specialized model is a byproduct.

### 3.3 Why now (2026)

Five enabling shifts have all happened in the last 12–18 months:

| Shift | Why it matters |
|---|---|
| DeepSeek-R1 (Jan 2025) | Verifier-driven RL produces SOTA on math/code with deterministic rewards |
| GRPO popularized | Cheaper, more stable than PPO for verifier-reward setups |
| Open base models matured | Qwen3, Llama-3, Gemma — viable customer-side fine-tune targets |
| Constrained decoding production-ready | XGrammar (vLLM/SGLang), Outlines — sub-5% latency overhead for grammars |
| MCP / tool ecosystem explosion | Demand for verifiable structured outputs is surging |

The category — "verifier-driven training as a service" — does not yet have a polished platform. Predibase, Together, OpenAI fine-tuning all assume customer-provided labeled data, not customer-provided verifiers.

### 3.4 Core abstraction — TaskSpec as IR

Same single-source-of-truth pattern that worked for `Catalog` in vertical-1, raised one level:

```
TaskSpec (IR)
├── synthesize_data(teacher, verifier)    # auto Stage 1-2 data generation
├── render_decoding_grammar()              # XGrammar for inference + training
├── render_reward_fn()                     # RL reward function
├── render_eval_suite()                    # held-out eval harness
└── render_serving_artifact()              # quantized weights + grammar bundled
```

`Catalog` becomes one TaskSpec instance (`ToolCallingTaskSpec(catalog)`), not a special case in the pipeline.

### 3.5 What gets generalized — TAM check

Tasks where this approach is plausible:

| Domain | Verifier example |
|---|---|
| Tool/function calling | schema parse + dry-run execute |
| SQL generation | parse + sandbox execute + result match |
| Code generation (constrained) | compile + unit tests |
| JSON/YAML/config generation | schema + lint + smoke test |
| OpenAPI/HTTP request synthesis | OpenAPI validator + sandbox call |
| Structured data extraction | schema validation (+ optional LLM judge) |
| Math problem solving | numeric/symbolic answer match |
| Regex generation | positive/negative example match |
| Browser automation plans | DOM state assertions |
| Test generation | execution + coverage delta |

Each is its own vertical SLM market. The thesis: factory makes them all from `(grammar, verifier)` pairs.

---

## 4. POC plan — what we want to build first

### 4.1 The hypothesis the POC must prove

> **The same pipeline code, parameterized only by `TaskSpec`, produces useful specialized SLMs across at least two structurally distinct task verticals.**

Not "tool calling works" (already shown). Not "we built a platform" (premature). The narrowest sharp claim: **abstraction generalizes**.

If we can only do tool calling, we have a specialized model. If we can do tool calling *and* SQL with the same pipeline code, we have evidence for a factory.

### 4.2 Vertical choices

- **Vertical 1: Tool calling.** Reuses existing Ganglion `Catalog` infrastructure. Eval = BFCL v4.
- **Vertical 2: SQL generation.** Chosen because (a) input shape differs significantly (NL question + DB schema → SQL string), (b) verifier is real execution (parse + sandbox-run + result-equality), (c) Spider/BIRD benchmarks exist for reference comparison, (d) commercially attractive (BI/analytics).

We rejected JSON extraction (too structurally similar to tool calling — would not stress the abstraction), math (small models too weak for honest results), and code generation (sandbox infra too heavy for a 10-week POC).

### 4.3 TaskSpec design (Python sketch)

```python
@dataclass(frozen=True)
class Example:
    input: dict       # all task info: NL query, schema, etc.
    output: str       # gold output if known (None otherwise)

@dataclass(frozen=True)
class TaskSpec:
    name: str

    # input dict → prompt text
    render_prompt: Callable[[dict], str]

    # XGrammar JSON Schema or CFG (constrained decoding)
    grammar: dict | str

    # (input, raw_output) → reward in [0, 1]
    # MUST be deterministic, cheap, and continuous (not binary)
    verify: Callable[[dict, str], float]

    # input distribution sampler (for synth + RL prompts)
    sample_inputs: Callable[[int], list[dict]]

    # human-curated holdout reference set (NOT teacher-generated)
    eval_examples: tuple[Example, ...]

    # base model + size hint
    base_model: str = "Qwen/Qwen3-0.5B"
    target_size: Literal["small", "medium"] = "small"
```

Tool calling adapter (uses existing `Catalog`):

```python
def make_tool_calling_taskspec(catalog: Catalog, eval_jsonl: Path) -> TaskSpec:
    return TaskSpec(
        name=f"toolcall_{catalog.name}",
        render_prompt=lambda inp: f"{catalog.render_json_dsl()}\nUser: {inp['query']}",
        grammar=catalog_to_xgrammar(catalog),
        verify=make_toolcall_verifier(catalog),
        sample_inputs=make_intent_sampler(catalog),
        eval_examples=load_bfcl_split(eval_jsonl),
    )

def make_toolcall_verifier(catalog: Catalog):
    def verify(inp: dict, raw: str) -> float:
        try:
            plan = catalog.parse_json_dsl(raw)
        except DSLValidationError:
            return 0.0
        gold = inp.get("expected")
        if gold is None:
            return 0.5
        if plan == catalog.parse_json_dsl(gold):
            return 1.0
        return 0.3 * action_match_ratio(plan, gold)  # partial credit
    return verify
```

SQL adapter:

```python
def make_sql_taskspec(spider_path: Path) -> TaskSpec:
    return TaskSpec(
        name="sql_spider",
        render_prompt=lambda inp: SQL_PROMPT.format(
            schema=inp["db_schema"], question=inp["question"]),
        grammar=SQL_LARK_GRAMMAR,
        verify=make_sql_verifier(spider_path),
        sample_inputs=make_spider_sampler(spider_path),
        eval_examples=load_spider_dev(spider_path),
        base_model="Qwen/Qwen3-1.7B",
        target_size="medium",
    )

def make_sql_verifier(spider_path: Path):
    def verify(inp: dict, raw: str) -> float:
        sql = extract_sql(raw)
        if sql is None: return 0.0
        try: sqlglot.parse(sql, dialect="sqlite")
        except: return 0.1
        try:
            db = load_sqlite_sandbox(inp["db_id"], spider_path)
            result = run_with_timeout(db, sql, timeout_s=2)
        except: return 0.2
        gold = run_with_timeout(db, inp["gold_sql"], timeout_s=2)
        return 1.0 if results_equal(result, gold) else 0.4
    return verify
```

**Critical design rules**:
- `verify` returns continuous reward in [0, 1] with partial credit at meaningful intermediate stages (parse / execute / result-match for SQL). Binary 0/1 makes GRPO unstable.
- `verify` must be deterministic and cheap (called many times per RL step).
- `eval_examples` come from human-curated benchmarks, *not* from the synthesis teacher. Otherwise contamination.

### 4.4 Pipeline (5 stages, all TaskSpec-generic)

```python
def train_factory(spec: TaskSpec, cfg: FactoryConfig) -> ModelArtifact:
    # 1. synthesize data with teacher, gate by verifier
    raw = synthesize_with_teacher(
        spec=spec, teacher=cfg.teacher,
        n_target=cfg.synth_n, use_constrained=True,
    )
    examples = [
        ex for ex in raw
        if spec.verify(ex.input, ex.output) >= cfg.synth_keep_threshold
    ]

    # 2. SFT
    base = AutoModelForCausalLM.from_pretrained(spec.base_model)
    sft_model = trl_sft(base, to_hf_dataset(examples, spec),
                        peft_config=LoraConfig(r=32), args=cfg.sft)

    # 3. GRPO with verifier as reward
    reward_fn = make_grpo_reward(spec)
    rl_model = trl_grpo(
        sft_model,
        prompts=[spec.render_prompt(x) for x in spec.sample_inputs(cfg.rl_n)],
        reward_funcs=[reward_fn], args=cfg.grpo,
    )

    # 4. INT4 quantize (AWQ)
    quantized = awq_quantize(rl_model, bits=4)

    # 5. eval & pack
    return pack_artifact(
        weights=quantized,
        grammar=spec.grammar,
        eval=run_eval(quantized, spec),
    )
```

**Self-test rule**: `grep -E '\b(tool|sql)\b' factory/` must return zero matches. All task-specific logic lives behind `spec.*` calls. If pipeline knows what task it's running, abstraction has leaked.

### 4.5 Tech stack — what we will NOT build

| Component | Use | Reason |
|---|---|---|
| SFT trainer | TRL `SFTTrainer` | reinventing wheel |
| GRPO trainer | TRL `GRPOTrainer` | post-DeepSeek-R1, production-ready |
| LoRA | PEFT | standard |
| Constrained decoding | XGrammar (vLLM-integrated) | best-in-class for grammars |
| Quantization | AutoAWQ | standard for INT4 |
| Serving | vLLM | grammar + multi-LoRA support |
| Synth teacher | Anthropic Claude Sonnet 4.6 API | grammar-constrained generation + prompt cache |
| SQL parsing | sqlglot | dialect-aware |
| SQL sandbox | SQLite in-memory | safe, fast |
| Eval orchestration | reuse Ganglion `eval/` | already works |

What we DO write (~1000 LOC total):
- `factory/taskspec.py` (~50 LOC)
- `factory/pipeline.py` (~200 LOC, mostly TRL glue)
- `factory/synth.py` (~150 LOC, teacher API + verifier gate + diversity sampling)
- `factory/eval.py` (~100 LOC)
- `factory/serve.py` (~100 LOC, vLLM + grammar)
- `tasks/tool_calling.py` (~150 LOC, Catalog adapter)
- `tasks/sql.py` (~200 LOC, Spider loader + verifier)
- `cli.py` (~50 LOC)

### 4.6 Schedule (10 weeks, 1 person full-time)

| Week | Goal | Gate |
|---|---|---|
| 1 | TaskSpec abstraction; wrap Ganglion Catalog as TaskSpec; refactor eval/runner.py to be TaskSpec-generic | Existing BFCL eval reproduces bit-identically through new abstraction |
| 2 | Synthesis pipeline (teacher + verifier gate + diversity sampling) | Tool calling: 50k synth → 30k+ verifier-passed |
| 3 | SFT stage via TRL | Tool calling SFT reproduces existing Ganglion baseline metrics |
| 4 | GRPO + verifier reward; reward shaping; KL tuning | SFT → GRPO improves metric by 1–2pp |
| 5 | Quantize + serve + tool calling end-to-end demo | One-command run produces artifact; BFCL ≥ 70% |
| 6–7 | SQL vertical: tasks/sql.py, Spider loader, sandbox verifier | `factory/` diff = 0 lines |
| 8 | SQL training run | Spider EX ≥ 50% (Qwen3-1.7B baseline) |
| 9 | Cross-vertical comparison; ablation on which stages matter | Stage-attribution table produced |
| 10 | Report + decision gate | Headline table delivered |

### 4.7 Headline result we want at week 10

| Vertical | Same pipeline? | Base | Final size (INT4) | Eval | Reference baseline | POC target |
|---|---|---|---|---|---|---|
| Tool calling | yes | Qwen3-0.5B | ~200M | BFCL v4 | xLAM-1B 73% | **70%+** |
| SQL | yes | Qwen3-1.7B | ~600M | Spider EX | DAIL-SQL-7B 69%, SQLCoder-1.3B 50% | **50%+** |

Plus a code diff:

```
$ git diff --stat tool_calling..sql -- factory/
0 files changed
$ git diff --stat tool_calling..sql -- tasks/
3 files changed, ~412 insertions
```

If both numbers AND zero-diff in `factory/`: thesis validated. Either fails: abstraction needs redesign.

### 4.8 Compute budget

Per vertical, single A100/H100:
- Synth: ~$500 in teacher API calls (50k examples)
- SFT: ~30 min, ~$5
- GRPO: 6–12h, ~$50–100
- Quantize: minutes
- Eval: hours of inference

Total POC: ~$1500–3000 compute. Engineering time is the bottleneck.

---

## 5. Explicitly out of scope for POC

To prevent scope creep:

| Item | Why deferred |
|---|---|
| Verifier co-pilot (LLM helps customer write verifiers) | Whole separate R&D |
| Recipe meta-learning (auto-pick base/hyperparams from TaskSpec) | Need ≥10 verticals worth of data |
| Multi-tenant serving | Single-user (the author) suffices for POC |
| Quality SLA estimator | Best-effort eval report only |
| Web UI / customer onboarding | CLI suffices |
| 3+ verticals | Two is sufficient evidence |
| Encoder-decoder / T5 architecture experiments | Compression thesis is separate from factory thesis |
| Sub-200M parameter compression | Same |
| Production verifier hardening (against reward hacking) | Critical post-POC, not POC-blocking |

---

## 6. Risks we know about

### 6.1 GRPO unstable on sparse verifier rewards
**Symptom**: GRPO on top of SFT changes metric < 1pp.
**Causes**: reward too sparse, KL penalty too strong, group size too small.
**Mitigation**: continuous partial-credit reward (parse / execute / result-match for SQL); KL coef sweep; group size 4 → 8.

### 6.2 Synthesis diversity collapse
**Symptom**: teacher produces uniform examples; model brittle on OOD.
**Mitigation**: temperature sweep, paraphrase prompts, schema/intent perturbation, dedup by embedding.

### 6.3 Abstraction leak
**Symptom**: SQL vertical needs changes to `factory/`.
**Mitigation**: when leak detected, *extend TaskSpec interface, don't touch pipeline*.

### 6.4 Eval contamination
**Symptom**: teacher generated eval set → inflated numbers.
**Mitigation**: eval splits come from human-curated benchmarks (BFCL v4, Spider dev) only.

### 6.5 SQL sandbox safety
**Mitigation**: SQLite in-memory, read-only, per-query timeout, subprocess isolation.

---

## 7. Open questions we want feedback on

1. **Is "two verticals" sufficient to claim abstraction generality?** Or should we plan for three (e.g., add JSON extraction as cheap third)?

2. **Should SFT be done with or without constrained decoding masking?**
   - With: model learns to put mass on legal tokens by construction; no self-correction learning.
   - Without (with grammar applied only at inference): better self-correction, slight train-inference mismatch.
   - Current plan: without during training, with at inference. Open to argument.

3. **Is GRPO the right RL algorithm here, or is DPO sufficient given how cheap our verifier is?**
   - DPO: needs preference pairs; we'd auto-mine from teacher rollouts.
   - GRPO: native verifier reward, more sample-efficient with deterministic reward; popularized by DeepSeek-R1.
   - Default: GRPO. Reviewer pushback welcome.

4. **For the SQL vertical, is Qwen3-1.7B too small to reach a meaningful Spider EX?** Published 1B-class SQL models hover at 50%; we'd be content with that, but is it possible the SLM size choice will dominate the result and obscure the abstraction validation? Should we just use Qwen3-3B/4B to remove that variable?

5. **What's the right granularity for the verifier's continuous reward shape?** Three buckets (parse/execute/result-match) for SQL feels right, but for tool calling we have many possible signals (action match, arg-name match, arg-value match, full equality). Too many shaping levels can hurt RL. Where to draw the line?

6. **We have not modeled multi-turn / tool-execution-loop tasks.** TaskSpec as defined is single-turn-input → structured-output. Agentic tool use (call, observe, call again) is harder. Should the POC explicitly defer multi-turn, or design TaskSpec to leave a hook for it?

7. **Defensibility post-POC.** If recipe + abstraction is open-source-able, what stops customers from running this themselves with TRL + their own verifier? We tentatively believe the moats are: (a) operational excellence at scale, (b) meta-learned recipe selection (gets better with multi-customer usage), (c) shared synthesis infrastructure. Are these convincing? Are there others we're missing?

8. **Comparison to prior art we should engage with**:
   - xLAM, Hammer, Octopus, NexusRaven, Gorilla — tool-calling specialized SLMs (we know about these)
   - DeepSeek-R1, Self-Rewarding LMs, RLEF — verifier-driven RL (we know)
   - Predibase, Together fine-tune, OpenAI fine-tune — fine-tune platforms (we know)
   - **What else should we know?** Especially recent 2025–2026 papers on (verifier × RL × small model).

---

## 8. Long-term framing (post-POC)

If POC succeeds, the natural next moves are:

- **Verticals 3–5**: add JSON extraction, code generation, regex generation. Test abstraction at higher N.
- **Verifier co-pilot**: LLM-aided verifier writing + adversarial verifier mining (auto-detect reward hacking surfaces).
- **Recipe meta-learning**: learn `TaskSpec features → optimal recipe` from cross-customer data.
- **Multi-LoRA serving**: S-LoRA-style hot-swap for low-cost multi-tenant serving.
- **Quality SLA**: pre-flight estimate + tiered pricing.
- **MCP bridge**: auto-import any MCP tool server as TaskSpec.

The product framing evolves from:
- Y0: "DSL-based tool calling library"
- Y1: "Verifier-driven SLM trainer (CLI)"
- Y2: "Verifier-driven model factory (platform)"

Each stage's artifacts become the next stage's foundation. POC is the Y0 → Y1 transition.

---

## 9. What success looks like at end of POC

- Two trained, deployable SLMs in INT4
- One pipeline codebase that produced both
- A reproducible CLI (`factory train spec.yaml`)
- An eval report comparing both to published baselines
- A list of identified abstraction-leak points (for v2)
- A go/no-go decision on continuing to Y1 platform R&D

---

## Reviewer prompt

Given all the above, please:

1. Identify the **single weakest part of the vision** and argue that it kills the project, or that it doesn't.
2. Identify the **single weakest part of the POC plan** and propose a fix.
3. Recommend **at most three concrete changes** to either the TaskSpec abstraction or the pipeline shape that would materially improve POC outcomes.
4. List **prior art** (papers, products, open-source projects) we should engage with that we haven't named.
5. Flag any **factual errors** in the document (about TRL, GRPO, XGrammar, BFCL, prompt caching numbers, anything).

Be skeptical. Disagreement is more useful than agreement.
