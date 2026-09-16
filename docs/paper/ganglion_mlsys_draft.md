---
title: "Ganglion: Compiling Tool Schemas into Compact Action IRs and Closing the Loop from Compile-Time Failures to Training Data"
date: "2026-09-15"
lang: en
---

> **Draft status.** Paper draft targeted at MLSys (10-page main text, two-column). All numbers are taken from committed artifacts under `runs/` and `docs/` at commit `78c8eb9`; every table names its source. Blocks marked **TODO** are experiments or checks that must land before submission. Author list, affiliations, and the final figure set are placeholders. Korean prompts in examples are shown with English glosses.

**Authors:** [TODO] · **Affiliation:** [TODO] · **Code:** `ganglion` repository (Apache-2.0 vendored BFCL data; license of the code [TODO])

## Abstract

Large language model (LLM) tool calling re-sends the full JSON schema of every callable function with every request. The schema dominates the prompt as tool catalogs grow, and small models often fail to produce a valid call at all. We present **Ganglion**, a system that moves schema semantics out of the prompt and into a deterministic compiler. A single *tool contract* is rendered into (i) a compact text convention and a small JSON *Action IR* that the model emits, (ii) the native tool schema used by the baseline, and (iii) a decoding grammar. A compiler parses, corrects, normalizes, and validates the IR into an executable plan, or reports a *typed* failure. Because every failure is caught at a well-defined stage, Ganglion turns failures into feedback: inference traces are classified into 14 failure types, and the resulting histogram drives (a) proposals of contract patches (aliases, defaults, argument stripping, prompt-aware corrections) and (b) recycling of compile- and validation-time failures into training data (compiler-gated synthesis, failure-targeted augmentation, validator-gated self-bootstrap, and rejected samples for preference pairs).

On an IoT benchmark with 5/20/50-tool catalogs and on 500 BFCL v4 single-turn cases, the IR path matches native tool calling in accuracy (BFCL: 86.2% vs. 85.6% AST match) while cutting input tokens by 45–68% and p50 latency by up to 25%. The token savings grow with catalog size and are invariant across models; the latency savings are model-dependent. Running the factory on Qwen3-0.6B raises exact match on 500 human-written queries from 38.6% to 99.6% (5 tools) and from 38.2% to 93.2% (50 tools), and BFCL macro AST match from 0.44 to 0.91; supervised fine-tuning on compiler-gated data and failure-driven corrections contribute most of the gain. We also report negative results that we believe are broadly useful: grammar-constrained decoding helps untuned small models (+17pp) but hurts after fine-tuning (−5pp); the repair loop is bounded by the invalid-output rate; and preference optimization starves for pairs once fine-tuning saturates.

## 1 Introduction

Agentic applications expose tens to hundreds of tools to a language model, increasingly through standardized servers such as the Model Context Protocol (MCP) [2]. The dominant interface, native function calling [1], places the name, description, and JSON Schema of every callable tool in the request. Three costs follow. Input tokens scale with the catalog, so every request pays for tools it will not use. Prefill latency and KV-cache memory scale with the same prefix, which matters most for on-device serving. And small models, the natural choice for fixed-domain deployments such as smart homes or robots, are unreliable at producing a well-formed call under a long schema: an untuned 0.6B model reaches only 38.6% exact match on our 5-tool benchmark, with a third of its outputs unparseable (§2.2).

Our starting observation is that the schema is doing two jobs at once. It tells the model *what can be called*, and it tells the runtime *what a valid call looks like*. Only the first job needs the model. The second can be done by code, deterministically and for free. Ganglion therefore splits the two roles. A tool catalog is written once as a *contract*. From the contract we render a compact prompt convention, and the model emits a small JSON *Action IR* instead of a provider-specific call structure. A compiler lowers the IR into executable calls, or fails with a typed error. The contract is the single source of truth: the same object renders the native schema for the baseline path, so the comparison is between two representations of the same catalog, and it renders a JSON-Schema grammar for constrained decoding. Externally defined schemas (OpenAI, MCP, BFCL) are compiled into contracts at run time, so nothing is benchmark-specific.

The second observation is that a compiler changes what a failure *is*. In native tool calling, a bad output is a provider error or a silent wrong call. In Ganglion, every output is either an executable plan or a failure at a named stage: syntax, unknown tool, missing or unknown argument, type, enumeration, range, abstention. Typed failures are data. Ganglion records every inference as a trace, classifies it into one of 14 failure types, and feeds the histogram back in two ways. The **contract edge** proposes patches to the contract itself: an alias table, a default that is uniquely implied by sibling arguments, dropping arguments the tool does not declare, a prompt-aware correction. The **data edge** recycles failures into training signal: the compiler gates teacher-synthesized data, failure buckets steer targeted augmentation, validator-gated self-bootstrap turns the model's own corrected outputs into canonical targets, and compile-time failures become the rejected side of preference pairs. Together with LoRA fine-tuning this forms a *factory* that takes a contract and produces a specialized small model plus a contract that has absorbed the model's systematic mistakes.

We evaluate Ganglion on two surfaces. An IoT lighting benchmark with 500 human-written Korean queries is evaluated against catalogs of 5, 20, and 50 tools that share the same queries, which isolates the effect of catalog size on cost. BFCL v4 single-turn [3] supplies 500 externally authored cases across five categories, including a category where the correct answer is to call nothing. The paper makes the following contributions.

- **A contract compiler for tool calling** (§3). One `ToolSpec` source renders the IR convention, the native schema, and a decoding grammar; a four-stage compiler (parse, correct, normalize, validate) with deterministic emission gives validity by construction, including an explicit *null action* for abstention.
- **Cost and accuracy evidence** (§5.2–5.3). The IR path preserves accuracy against native tool calling on both benchmarks while reducing input tokens by 45–68%. Savings grow with the number of tools and are the same for a strong and a weak API model; latency savings are 4–25% and depend on the model.
- **A failure-feedback factory** (§4, §5.4–5.5). Traces, a 14-type failure taxonomy, contract-patch proposals, and four mechanisms that recycle compile-time failures into training data. On Qwen3-0.6B the factory reaches 99.6% and 93.2% exact match on the 5- and 50-tool catalogs and 0.912 macro AST match on BFCL, from 38.6%, 38.2%, and 0.440 respectively. Rescue and regression of every correction are accounted for separately.
- **Systems findings and negative results** (§5.5–5.6, §6). Grammar masking, repair, and conservative corrections all act on the same quantity, the invalid-output mass, and therefore stop helping once fine-tuning removes it; grammar masking can regress a fine-tuned model; bootstrapping saturates; preference pairs become scarce; training numerics on Apple silicon versus CUDA moved exact match by 9.8pp under an identical recipe.
- **An open implementation** with 247 tests, deterministic BFCL subsampling pinned to an upstream commit, and scripts that reproduce every table.

## 2 Background and Motivation

### 2.1 The cost anatomy of native tool calling

Let $C$ be a catalog of tools and let $\sigma(C)$ be its native schema rendering and $\rho(C)$ the compact convention Ganglion uses. For a request $x$, the input token count on either path is the rendering plus the request plus a template constant, $n_{\mathrm{in}} = \operatorname{tok}(r(C)) + \operatorname{tok}(x) + c$. Both renderings grow linearly in the number of tools, but with different slopes: a tool costs one line in $\rho(C)$ and a full JSON Schema object in $\sigma(C)$. In our built-in catalogs the per-tool cost is 77–110 characters for the IR convention against 315–420 characters for the schema, while the IR's fixed part (rules and four examples) is 779 characters. Under this additive model the savings $1 - n^{\rho}_{\mathrm{in}}/n^{\sigma}_{\mathrm{in}}$ increase monotonically with the number of tools and converge to one minus the ratio of per-tool costs (Appendix A). Table 1 shows the measured sizes.

**Table 1. Rendering size of the same catalog on the two paths** (characters; `python -m ganglion.benchmarks.iot.scaling`, current checkout).

| Catalog | Tools | IR convention $\rho(C)$ | Native schema $\sigma(C)$ | Ratio |
|---|---:|---:|---:|---:|
| iot_light_5 | 5 | 1,334 | 2,108 | 1.58× |
| home_iot_20 | 20 | 2,552 | 6,842 | 2.68× |
| smart_home_50 | 50 | 4,670 | 15,841 | 3.39× |
| home_assistant_4 | 4 | 1,491 | 1,733 | 1.16× |

The last row is a deliberate counterexample: the Home Assistant Assist API [8] declares almost every slot as an optional string, so there is little constraint information to compress and the gain is small. The savings are a property of the catalog's constraint density, not a constant.

The token count is only the visible part of the cost. On a local server the same prefix occupies KV-cache memory in proportion to its length for the lifetime of the request, which bounds batch size and throughput; on an API it is billed per request and, with prefix caching, still billed at a reduced rate (§6.1). Output tokens matter too: a native tool-call structure is longer than the IR, and on BFCL the IR path emitted 31% fewer output tokens (§5.3).

### 2.2 Small models fail on form before they fail on meaning

Table 2 shows an untuned Qwen3-0.6B [6] on 500 human-written queries. Two thirds of its outputs parse; a third do not. Of the outputs that parse, most name the right tool. The gap between action match and exact match is argument errors. In other words, the small model *can* select tools in a 50-tool catalog at roughly the same rate as in a 5-tool one; what it cannot do is produce a well-formed, fully specified call. A representation whose validity is checked by code, and a training signal that targets exactly the observed failure classes, is the natural remedy.

**Table 2. Untuned Qwen3-0.6B, IR path, 500 IoT queries** (DashScope-served; `docs/factory_phase2_plan.md` §10.1).

| Catalog | Syntax valid | Action match | Exact match |
|---|---:|---:|---:|
| iot_light_5 | 65.8% | — | 38.6% |
| smart_home_50 | 65.6% | — | 38.2% |
| iot_light_5, local greedy | 66.0% | 65.6% | 40.8% |
| smart_home_50, local greedy | 63.8% | 63.2% | 37.8% |

### 2.3 Design goals

Ganglion is designed around four requirements. **R1, one source of truth.** Everything the model sees, everything the validator enforces, and everything the baseline receives must derive from one object, so that comparisons are fair and patches are applied once. **R2, validity by construction.** Anything that leaves the compiler is executable against the contract; anything else is a typed failure. **R3, failures are data.** Every inference leaves a trace that can be classified without labels; labeled failures can be classified more finely. **R4, feedback is gated.** The analyzer proposes contract patches and training data; a human or an explicit flag applies them. The last requirement is a deliberate systems choice: a contract is the specification of allowed behaviour, and an optimizer must not be able to rewrite the specification to make its own errors disappear.

## 3 Ganglion Design

Figure 1 shows the three modules. `contract` owns the catalog and the compiler. `lm` produces models: API and local clients, teacher-driven synthesis, LoRA fine-tuning, and grammar-constrained decoding. `analyzer` measures: a trace store, the failure taxonomy, metrics, the repair policy, a reward function, and rule synthesis. Benchmarks are consumers. `lm` and `analyzer` do not import each other; both depend on `contract`, which depends on nothing.

> **Figure 1.** Ganglion architecture and factory loop (`docs/diagrams/fig1_factory_loop.pdf`). Solid edges are the production path from contract to executable calls; dashed edges are the two feedback edges from the analyzer back to the contract and to the training data. **TODO:** regenerate with the final module names and add the data edge explicitly.

### 3.1 The contract

A `Catalog` is a tuple of `ToolSpec`s plus catalog-level policy. Each `ToolSpec` has a name, a description (used only by the native rendering), and typed arguments: enumerations, integers and numbers with bounds, strings with optional patterns, booleans, 24-hour times, and a raw JSON-Schema escape hatch for shapes the typed forms cannot express, such as nested calls. Enumerations and strings carry an *alias table* that canonicalizes surface forms, for example the Korean word for living room to `living`, and integers may accept percentage strings. The catalog declares whether the empty plan `{"calls":[]}` is a valid output (the *null-action contract*), which BFCL's `irrelevance` category requires and which a smart-home assistant needs whenever the request matches no tool.

Three optional hooks on a `ToolSpec` are the surface on which the feedback loop acts (§4.4): `defaults_when_missing` fills a required argument when the other arguments determine its value uniquely, `strip_unknown_args` drops arguments the tool does not declare, and `prompt_correction` rewrites an argument from the user request. All three are declarative, versioned with the contract, and exercised by tests.

### 3.2 Three renderings from one source

From one catalog Ganglion renders three artifacts.

- **IR convention.** A short text block appended to the system prompt: the IR shape, one line per tool with its typed signature, catalog rules, and a few examples. This is what the model reads.
- **Native schema.** The OpenAI `tools=[...]` array with a JSON Schema per tool. This is what the baseline path sends, so the baseline is a rendering of the same contract rather than a hand-written competitor.
- **Decoding grammar.** A JSON Schema describing the full IR envelope, with the tool name pinned by `const` per branch, compiled to a token mask with XGrammar [5] for local inference.

The convention is deliberately terse. Tool descriptions are omitted; the examples and the alias rules carry the domain hints instead. This is the source of both the cost advantage and a design tension: the model has less prose to condition on, and any hint that matters must be expressed as a rule, an example, or a fine-tuned weight.

### 3.3 The Action IR

The IR is a single JSON object, `{"calls":[{"action":"set_light","args":{"room":"living","state":"on","brightness":70}}]}`. Multiple calls are an ordered list; nested calls, such as the actions of a scene, are IR objects inside an argument. The empty list is the null action. The IR is provider-independent: the same string is produced by a local HuggingFace model, by an API in JSON-object mode, or by a free-form completion that is salvaged by extracting the first decodable object.

### 3.4 The compiler

`Catalog.parse_json_dsl(text, prompt)` is a partial function from strings to `ActionPlan`s. It runs four stages per call. **Parse** turns the string into a raw structure and resolves the tool name. **Correct** applies the declarative hooks: defaults, stripping, and prompt-aware rewrites; nested calls are corrected recursively. **Normalize** canonicalizes values: aliases, percentage strings, case, 24-hour times, numeric coercion. **Validate** checks required arguments, types, enumerations, ranges, and the raw JSON-Schema fragments. Any stage may raise a typed `DSLValidationError` whose message identifies the stage and the field; §4.3 maps these messages onto the failure taxonomy. Emission is a pure function from plans to `{"name": ..., "arguments": ...}` records and never calls a tool.

The compiler distinguishes two classes of correction. *Conservative* corrections change only structures that would otherwise fail validation, so they can never turn a correct plan into a wrong one. *Rewriting* corrections can change a structure the validator would accept, so they can both rescue and regress and must be evaluated as such (§4.4). This distinction is the basis for the accounting in §5.5.

The compiler costs 9.5 µs per case including emission on a workstation CPU, and rendering all three surfaces of the 50-tool catalog costs 0.12 ms, so its overhead is invisible next to a model call. For external schemas, `compile_tool_calling_schema` builds a fresh catalog per BFCL case from the case's `function` list, normalizing BFCL's type aliases (`dict`, `float`, `tuple`) at every nesting level and propagating the null-action flag.

### 3.5 Inference paths and path invariance

Ganglion runs four inference paths, summarized in Table 3. All four end in the same compiler and emitter, so accuracy metrics are defined identically across paths; the paths differ in prompt cost, output format, and salvage. One consequence deserves emphasis: the native path's tool calls are converted into the IR structure and pass through the same corrections, so the native baseline in this paper is *not* a correction-free baseline. Model-only numbers are reported separately by disabling corrections (§5.5).

**Table 3. Inference paths.**

| Path (`--llm`) | Prompt | Generation constraint | String to plan | Retry |
|---|---|---|---|---|
| Local HF `generate_dsl` | IR convention | optional grammar mask | compiler | none |
| API JSON (`qwen`) | IR convention | provider JSON-object mode | compiler | repair loop |
| API free-form (`qwen-text`, `-thinking`) | IR convention (variant wording) | none | salvage, then compiler | none |
| API native (`qwen-native`) | one-sentence system prompt + native schema | provider tool calling | convert, then compiler | none |

### 3.6 The repair loop

When the compiler rejects an output on the API JSON path, the runtime appends the error message to the conversation and asks once more, up to a configured number of attempts. The trigger is validity, not correctness: a well-formed but wrong call never triggers repair. This bounds what the loop can achieve by the invalid-output rate of the first attempt, a bound that the experiments confirm (§5.5).

## 4 The Factory: From Compile-Time Failures to Contract Patches and Training Data

The factory is the loop in Figure 1: synthesize against a contract, fine-tune, evaluate, record traces, classify failures, and feed the histogram back into the contract and into the next training set. This section describes each stage and, in particular, the two feedback edges. Everything here runs against a contract; nothing here is specific to a benchmark.

### 4.1 Compiler-gated synthesis

A teacher model (qwen3.6-plus through DashScope) is prompted per tool to produce (request, IR) pairs. The compiler is the gate: a pair is kept only if its IR compiles against the contract, contains exactly one call, and calls the anchored tool. Kept IRs are re-serialized from the compiled plan, so the training target is the *canonical* form, including any correction the compiler applied. Near-duplicate requests are removed with a sentence-embedding threshold, and the corpus is split by strategy into train and holdout. The gate is structural: it rejects malformed teacher output but cannot detect a well-formed pair whose intent and call disagree (Appendix A); semantic noise is bounded only by teacher quality.

The gate's first observed value was during the initial run on the 5-tool catalog: the teacher emitted flat dictionaries for the nested `create_scene` argument and every such pair was rejected until an example of the nested shape was added to the synthesis prompt. Table 4 gives the synthesis budget.

**Table 4. Synthesis with the compiler as gate** (qwen3.6-plus teacher; `docs/factory_phase1_report.md` §3).

| Catalog | Attempted | Kept by gate | After dedup | Gate pass | API cost | Wall time |
|---|---:|---:|---:|---:|---:|---:|
| iot_light_5 | 210 | 200 | 126 | 95.2% | $0.076 | 7 min |
| smart_home_50 | 514 | 500 | 441 | 97.3% | $0.155 | 15 min |

### 4.2 Fine-tuning with prompt parity

Ganglion fine-tunes a LoRA adapter [4] (rank 32, alpha 64, all linear layers, three epochs, learning rate $2\times10^{-4}$) with TRL's assistant-only loss, so the constant contract text in the system prompt is conditioned on but not learned. The training messages are assembled by the same function as the inference prompt, and the system text is byte-identical on both sides. Training is cheap at this scale: 100 examples fine-tune Qwen3-1.7B in 30 s on an RTX 4090, and 341 examples fine-tune Qwen3-0.6B in 81 s. The adapter for the 1.7B model is 144 MB on disk.

### 4.3 Traces and the failure taxonomy

Every inference, on every path, produces a `Trace`: prompt, raw output, the full repair chain, parse strategy, latency, token counts, model and catalog identifiers, the compiled plan or null, and, when available, the expected plan. Traces are content-addressed, so replaying a benchmark is idempotent, and the store is append-only.

A deterministic classifier assigns each trace one of 14 types in a fixed priority order: `syntax_invalid`, `unknown_tool`, `wrong_action`, `abstention_miss_should_call`, `abstention_miss_should_abstain`, `missing_required_arg`, `unknown_arg`, `type_mismatch`, `value_out_of_enum`, `alias_unrecognised`, `value_out_of_range`, `parallel_order_mismatch`, `partial_arg_value_mismatch`, `no_failure`. The order goes from shape to address to argument to match, so a trace with several problems reports the coarsest, which is what a patch needs to know first. The first ten types can be assigned from the compiler's error alone, without a gold plan; the label-free subset is exactly the set of failures a deployed system observes in production. Each classification carries an evidence dictionary (the field, the observed value, the nearest alias, the declared arguments) that the patch proposer consumes. The taxonomy is the fine-grained version of an identity that holds for any evaluator: exact match implies action match implies validity, so the three gaps partition failures into contract violations, tool-selection errors, and argument errors (Appendix A).

### 4.4 Feedback edge A: contract patches

Rule synthesis groups classifications by (catalog, failure type, tool) and runs one matcher per bucket. Table 5 lists the matchers. Each proposal carries a confidence $\text{frequency}\times\text{consistency}\times\text{narrowness}$: how many traces support it, how often the same transform recovers the gold value, and how specific the pattern is to one tool. Proposals are written to a sidecar file and emitted as events; they are never applied automatically. Patches that would change the shape of `ToolSpec` itself are escalated rather than proposed.

**Table 5. Failure bucket to proposed patch.**

| Failure type | Evidence pattern | Proposed patch | Correction class |
|---|---|---|---|
| `missing_required_arg` | same default closes the gap in $\ge N$ traces, predicate from sibling arguments | `defaults_when_missing` | conservative |
| `unknown_arg` | same undeclared name in $\ge N$ traces, dropping it is safe | `strip_unknown_args` | conservative |
| `value_out_of_enum`, `alias_unrecognised` | functional mapping observed value to accepted value | `aliases` extension | normalization |
| `abstention_miss_should_call` | empty plan for prompts that match a tool | system-level `prompt_correction` | rewriting |
| `type_mismatch` | one transform recovers the value (string to int, percent, unit strip) | `ArgSpec` relaxation | conservative |

The two correction classes matter for accounting. For the same outputs and gold plans, let $\mathrm{EM}^{(0)}$ be exact match with corrections disabled and $\mathrm{EM}^{(K)}$ with them enabled. Then $\mathrm{EM}^{(K)}-\mathrm{EM}^{(0)}=\mathrm{Rescue}-\mathrm{Regression}$, and a conservative correction has zero regression by construction because it only touches structures that would otherwise fail (Appendix A). We therefore report rescue and regression for every correction rather than the net alone.

In the experiments reported in §5 the loop was operated semi-automatically: the classifier bucketed failures, an operator selected the rule, and the rule was implemented as a declarative hook and tested. The automated proposer reproduces the three IoT patch types on fixtures. **TODO:** run the proposer over the recorded IoT and BFCL traces and report proposal precision against the operator-chosen rules.

### 4.5 Feedback edge B: recycling failures into training data

Compile-time failures are also a training signal. Ganglion uses them in four ways.

1. **Gating.** Teacher data that fails to compile is dropped (§4.1). The gate removed 10 of 210 and 14 of 514 teacher pairs in the two IoT runs, and up to 24% of teacher paraphrases in the hardest BFCL category (§5.5).
2. **Failure-targeted augmentation.** The histogram tells the synthesizer where to spend. When multi-call structure was the dominant failure on BFCL `parallel`, paraphrases and new synthetic cases were generated per category with the same per-case tool schema, and the compiler validated each one against that schema. When 12-hour Korean time expressions dominated IoT failures, out-of-distribution paraphrases emphasizing unusual time and number wordings were generated.
3. **Validator-gated self-bootstrap.** The fine-tuned model samples several outputs per paraphrased request; samples whose compiled plan equals the gold plan *after* correction are kept, re-serialized in canonical form, and added to the training set. Training on corrected canonical targets moves a correction rule from the compiler into the weights, so the rule can later be retired. We call this *rule absorption* and measure it in §5.5.
4. **Failures as negatives.** For preference optimization, several samples are drawn per request and scored with a deterministic, contract-derived grade in which compile failures score zero; the highest- and lowest-scoring samples form a pair when their margin exceeds a threshold. A compile failure is thus the preferred rejected example. The DPO objective [10] is standard; the scorer only selects pairs and never enters the loss.

Only mechanisms 1 and 3 require gold plans for the training prompts; mechanism 2 requires labels only through the teacher; mechanism 4 requires a grade. In production, the label-free subset of the taxonomy (§4.3) still fuels mechanism 2 through the choice of what to augment.

### 4.6 Orchestration, stopping, and gating

`run_pipeline` drives one contract through evaluate, trace, classify, propose. It stops on a target exact-match threshold, an iteration cap, a plateau over $K$ iterations, or the absence of proposals, and it applies proposals only when `auto_apply` is set. In the current implementation the automated loop covers one evaluation iteration; synthesis, fine-tuning, and patch application were run by scripts under operator control. Table 6 gives the end-to-end budget of the runs in §5.4.

**Table 6. Cost of one factory pass on Qwen3-0.6B.**

| Stage | iot_light_5 | smart_home_50 | Source |
|---|---|---|---|
| Teacher synthesis | $0.076, 7 min | $0.155, 15 min | phase 1 report |
| Paraphrase pool for bootstrap (300 / OOD 813) | $0.027 + $0.137 | — | phase 2 plan §13, §15 |
| Self-bootstrap sampling (300 × 4 samples) | 14 min (M1 Ultra) | — | phase 2 plan §13 |
| SFT v1 (100 / 349 examples) | 171 s (M1) | 51 min (M1, seq 2048) | train metrics |
| SFT v2 (341 examples) | 81 s (RTX 4090) | 241 s (RTX 4090) | train metrics |
| Evaluation, 500 queries, local HF | ~12 min | ~13 min | ablation summaries |

## 5 Evaluation

### 5.1 Setup

**Models.** API models through DashScope: qwen3.6-plus (strong), qwen3.6-flash (weaker), and qwen-turbo (repair experiments). Local models: Qwen3-1.7B and Qwen3-0.6B [6] with LoRA adapters, served by HuggingFace `generate` at batch size 1 with greedy decoding unless stated. Teacher: qwen3.6-plus.

**IoT benchmark.** 500 human-written Korean queries over five lighting intents (set 180, schedule 140, query state 100, list devices 40, create scene 40), each with a gold plan. Three catalogs of 5, 20, and 50 tools share the same 500 queries; the larger catalogs add distractor tools, so the tiers isolate prompt cost and do not test selection among distractors. A 28-case adversarial set with ambiguous phrasings is used for the repair experiments. Four of the 500 queries also appear as examples inside the IR convention; we report this contamination in §6.

**BFCL v4 single-turn.** A deterministic seed-42 subsample of 100 cases from each of `simple_python`, `multiple`, `parallel`, `parallel_multiple`, and `irrelevance` (upstream sizes 399, 199, 199, 199, 239), pinned to an upstream commit and vendored. Grading re-implements BFCL's AST checker for Python categories, including accepted-value lists and order-insensitive matching for parallel calls. Each case ships its own tool list, so a catalog is compiled per case.

**Metrics.** Validity (compiles), action match (same tool sequence), exact match (same plan after normalization), AST match (BFCL), input and output tokens as reported by the API, and latency mean, p50, and p95. Rates are over all cases including failures. At $n=500$ a binomial 95% interval is about ±2pp; at $n=100$, ±5pp. All local numbers are single-seed. **TODO:** repeat the headline local runs with three seeds.

**Hardware.** RTX 4090 (CUDA) for the final IoT small-model runs; RTX 4080 16 GB for the BFCL small-model runs; Mac Studio M1 Ultra (MPS) for early phase-2 runs, which we keep because they expose a numerics effect (§5.6).

### 5.2 Does the IR preserve accuracy?

**IoT.** On 50-case samples per tier, qwen3.6-plus reaches 100% exact match on the IR path in all three tiers and 98%, 96%, and 98% on the native path (Table 7). Every native failure was the scene name field, where the model produced an uncanonicalized label such as "Movie Time" instead of `movie`; the IR path's in-prompt example anchored the canonical value. With the rules client, all 500 queries compile and match in all three tiers, which verifies that adding tools does not perturb validation of existing ones.

**BFCL.** Table 8 summarizes the 500-case runs. Without the null action, the IR path trailed native by 2.6pp overall, but 341 versus 342 correct cases on the 400 callable cases, so the gap was almost entirely the `irrelevance` category, where the model had no way to say "no call". With the null-action contract, the IR path reaches 86.2% against 85.6% for native, with 0 false abstentions on the 400 callable cases, and `irrelevance` rises from 74% to 90%. On the weaker qwen3.6-flash the IR path leads native on every callable category (+2.5pp on average) and, with the null action, overall (80.8% vs. 80.4%). Syntax validity is consistently higher on the IR path (97.6% vs. 81.0% for plus), which is the first sign of a pattern that recurs throughout: the IR is a more stable output format for weaker models.

**Table 7. IoT tiers, qwen3.6-plus, 50 cases per tier** (`runs/aggregate.py`, M2).

| Catalog | Exact match IR / native | Input tokens IR / native | Saving | p50 latency IR / native (ms) |
|---|---:|---:|---:|---:|
| iot_light_5 | 100% / 98% | 22,757 / 41,507 | 45.2% | 1,279 / 1,839 |
| home_iot_20 | 100% / 96% | 40,907 / 109,207 | 62.5% | 1,208 / 1,858 |
| smart_home_50 | 100% / 98% | 74,057 / 235,207 | 68.5% | 1,334 / 2,065 |

**Table 8. BFCL v4 single-turn, 500 cases** (`runs/bfcl/aggregated.json`; `docs/bfcl_m5_abstention_report.md`; `docs/bfcl_flash_replay_report.md`).

| Model | Path | AST match | Callable (400) | Irrelevance | Syntax valid | Input tokens / case | p50 latency (ms) |
|---|---|---:|---:|---:|---:|---:|---:|
| qwen3.6-plus | native | 85.6% | 85.5% | 86% | 81.0% | 371.8 | 2,441 |
| qwen3.6-plus | IR, no null action | 83.0% | 85.25% | 74% | 83.0% | 140.3 | 1,908 |
| qwen3.6-plus | IR, null action | **86.2%** | 85.2% | **90%** | **97.6%** | 171.5 | **1,819** |
| qwen3.6-flash | native | 80.4% | 79.75% | 83% | 81.0% | 371.3 | 1,243 |
| qwen3.6-flash | IR, no null action | 77.6% | 82.25% | 59% | 85.4% | 143.0 | 1,097 |
| qwen3.6-flash | IR, null action | **80.8%** | 81.5% | 78% | 95.6% | 168.3 | 1,058 |

Two limits are visible. The weak model's abstention ceiling with the null action, 78%, stays below its native 83%; the contract makes abstention expressible but cannot make a model that is tempted by a plausible tool decline to call it. And the remaining callable failures on both paths are the same: nested numeric types, value canonicalization such as `0.05` versus `5.0`, and parallel-call counts.

### 5.3 What does the IR cost?

**Input tokens.** The saving grows with the catalog, from 45.2% at 5 tools to 68.5% at 50 (Table 7), reproducing the character ratios of Table 1 in tokens (1.82× to 3.18×). On BFCL, where cases carry one to five tools, the saving is 59.3% for single-tool cases and 64.4% for cases with two to five tools, and 62.25% overall without the null action; with the null action's extra instruction line it is 53.9%. The saving is the same on the strong and the weak model (61.5% vs. 62.3% on the same 500 cases) because it depends only on the prompt.

**Output tokens.** The IR path emitted 31% fewer output tokens than native tool calling on BFCL (58.9 vs. 85.7 per case). Enabling the provider's thinking mode on the IR path kept accuracy at 100% on the 12-case seed set but multiplied output tokens by 11.7 and p50 latency by 3.4; for a closed transformation task the reasoning tokens buy nothing.

**Latency.** p50 latency fell by 21.8% (BFCL, no null action), 25.5% (BFCL, null action), and 21.4% on the IoT benchmark with 50 cases repeated five times (1,388 vs. 1,766 ms, standard deviation 376 vs. 498 ms). On the fast qwen3.6-flash the saving shrank to 3.7% in the repeated measurement and 14.9% on the full run: the absolute saving is similar, tens of milliseconds, but the baseline is faster. Token savings are model-invariant; latency savings are not. API latency also includes network and server load that we cannot separate, so we treat the token numbers as the primary evidence.

> **Figure 2 (TODO).** Input tokens per request versus number of tools for both paths, with the fitted additive model of §2.1 and the BFCL per-case points overlaid.

### 5.4 Can the factory make a small model usable?

Table 9 traces Qwen3-0.6B through the factory on the 500 IoT queries, and Table 10 on BFCL. Three stages carry the gain. Fine-tuning on compiler-gated synthetic data is by far the largest lever: +35pp on the 5-tool catalog from 100 examples. The failure-driven corrections are the second: after fine-tuning, two rules mined from 68 remaining failures rescued 64 of them, and a six-rule stack took the 50-tool catalog from 82.4% to 93.2%. The third is the training numerics discussed in §5.6: the same recipe on CUDA rather than MPS added 9.8pp. Repair and grammar masking contribute little once the model is fine-tuned.

**Table 9. Qwen3-0.6B through the factory, exact match on 500 IoT queries** (`docs/factory_phase2_plan.md` §10–17, `docs/factory_phase2_session_2026-05-08.md`).

| Stage | iot_light_5 | smart_home_50 | Notes |
|---|---:|---:|---|
| Untuned (API-served) | 38.6% | 38.2% | syntax 65.8% / 65.6% |
| + repair loop, one retry | 41.8% | 40.6% | syntax 72.2% / 71.6% |
| Untuned + grammar mask (local) | 57.8% | 52.6% | +17pp / +15pp |
| SFT v1, 100 / 349 examples (M1 Ultra) | 73.4% | 64.0% | mask off |
| SFT v1 + `defaults_when_missing` | 77.2% | 71.4% | 19 / 37 cases rescued |
| SFT v2, bootstrap-augmented 341 ex. (M1 Ultra) | 76.6% | — | structural failures 15% to 1% |
| SFT v2, same recipe on RTX 4090 | 86.4% | 82.4% | loss 0.039 vs. 0.071 |
| + failure-mined rules (2 / 6 rules) | **99.2% / 99.6%** | **93.2%** | rescue 64 of 68 / 54 of 88 |
| Reference: Qwen3-1.7B untuned / + LoRA | 87.4% / 93.8% | 80.0% / 87.4% | phase 1 |

On the synthetic holdout the v2 adapter reaches 97.1% (n=70), but 47 of those 70 cases are paraphrases of training intents, so we treat the 500 human-written queries as the external test and the holdout as a training diagnostic.

**Table 10. Qwen3-0.6B on BFCL v4, macro AST match over five categories, 100 cases each** (`runs/factory_bfcl/table.md`; `docs/factory_bfcl_report.md`; `docs/factory_bfcl_phase3_report.md`).

| Stage | Full 100 | Holdout 20 | Notes |
|---|---:|---:|---|
| Untuned, IR path (API-served) | 0.440 | — | `parallel*` near 0; native 0.393 on callable |
| + repair loop | 0.438 | — | syntax +1–5pp only |
| Untuned + grammar mask (local) | 0.418 | — | `parallel` 0.50 to 0.04: mask biases toward one call |
| SFT v1, ~80 train cases per category | 0.820 | 0.600 | `parallel` 0.02 to 0.76 |
| + 11 rules mined from SFT failures (R1–R11) | 0.872 | — | R4 fired 52×, R1 29× |
| SFT v1.5, failure-targeted augmentation (~5×) | 0.880 | 0.690 | `parallel` holdout +25pp |
| SFT v1.5 + rules | **0.912** | **0.810** | `multiple` holdout +40pp |
| SFT v2, + self-bootstrap | 0.880 | 0.650 | holdout regression −4pp |
| DPO on v2 | — | — | 4 usable pairs across five categories |

The BFCL numbers carry two caveats. The full-100 evaluation includes the ~80 training cases per category, so it measures memorization plus generalization; the holdout-20 column is the honest generalization number, and it is noisy (±11pp). And the untuned baseline differs between API-served and local greedy decoding (`parallel` 0.02 vs. 0.50), so stage deltas are computed within one serving path.

### 5.5 Does the feedback loop work, and what does it cost?

**Failure histograms drive the rules.** Table 11 shows the failure decomposition of the CUDA v2 model on the 5-tool catalog and what each rule did. The two dominant buckets were a 12-hour to 24-hour time confusion and an artifact of the dataset: 28% of prompts carry a `#N` deduplication suffix, and the small model echoed the number into an argument. A rewriting correction (read the time from the prompt) and a conservative one (drop undeclared arguments) rescued 64 of the 68 failures with zero regressions. On the 50-tool catalog, six rules rescued 54 of 88 failures; the 34 that remain are dominated by genuine tool-selection errors between similar tools (`set_thermostat` vs. `set_pool_temp`), which no contract patch can fix and which are the input to the next round of targeted synthesis.

**Table 11. Failure buckets and rescue on iot_light_5, Qwen3-0.6B v2 (CUDA), 68 failures of 500** (`docs/factory_phase2_plan.md` §16).

| Bucket | Count | Rule | Class | Rescued | Regressed |
|---|---:|---|---|---:|---:|
| `schedule_light.at` 12h/24h confusion | 43 | prompt-aware time correction | rewriting | 43 | 0 |
| `#N` suffix echoed into an argument | 22 | `strip_unknown_args` | conservative | 21 | 0 |
| alias miss (study to `office`) and others | 3 | none | — | 0 | 0 |

On BFCL the same procedure needed a second attempt. Three hand-written generic rules fired zero times after fine-tuning because the residual failures were semantic; bucketing the residual failures (eight categories) produced eleven data-driven rules, of which two (drop a hallucinated optional argument, 52 fires; fill an optional argument with the accepted default, 29 fires) carried most of the +5.2pp macro gain, and +40pp on the `multiple` holdout. Several of these rules depend on BFCL's accepted-value lists and do not transfer to generic schemas; the shape-cleaning rules do.

**Rule absorption.** The `defaults_when_missing` rule rescued 19 cases for the v1 adapter. The v2 adapter was trained on bootstrap samples re-serialized *after* that rule had completed them, and on the same 500 queries the rule then rescued 0 cases: the model had stopped omitting the argument. Structural failures fell from about 15% to about 1% of cases while semantic argument errors rose from 12% to 22%, that is, the failure mass moved from the bucket the compiler can fix to the bucket only data can fix. This is the intended effect of the data edge, and it is also why the rule can be retired from the contract once the absorbed model ships.

**Failure-targeted augmentation.** On BFCL, per-category paraphrase (K=4) and synthesis (N=50) validated against each case's schema multiplied the training set by about five; the compiler rejected 6–24% of teacher paraphrases, most in `parallel_multiple`, where the teacher simplified multi-call requests into one call. Fine-tuning on the augmented set raised the holdout macro from 0.600 to 0.690, with +25pp on `parallel`, the category whose failures had motivated it.

**Where recycling stops helping.** Self-bootstrap on the BFCL v1.5 model passed 86–100% of its own samples, so it mostly reproduced cases the model already solved and the retrained v2 regressed 4pp on holdout. Preference pairs had the same problem: with in-distribution paraphrases only 6% of prompts yielded a pair with the required margin; out-of-distribution paraphrases raised the yield to 24%, but on BFCL only four pairs survived across five categories and no DPO run produced a reportable improvement in either domain. The yield bound is structural: a pair needs two samples with different scores, and a fine-tuned model at temperature 0.7 rarely produces them (Appendix A). Recycling needs prompts the model does not already solve, which conflicts with keeping the evaluation set independent; a dedicated hard-prompt pool is required.

**Repair.** On qwen3.6-plus the repair loop never triggered on the 50 IoT cases (100% valid on the first attempt) and cost nothing on the happy path. On qwen-turbo with the 28 adversarial cases it triggered three times and recovered all three. On BFCL it raised syntax validity from 97% to 99% on the strong model at +5.3% input tokens with AST match within variance, and on the weak model it added +2pp AST at +2.8% tokens. The loop is a cheap safety net whose value is proportional to the invalid-output rate, and after fine-tuning that rate is small.

**Grammar masking.** Table 12 collects the ablations. On untuned models masking is the single most effective inference-time device (+17pp). On fine-tuned models it is neutral to harmful on small catalogs and mildly helpful on the 50-tool catalog, and on BFCL `parallel` it collapsed accuracy from 0.50 to 0.04 while raising syntax validity. Two mechanisms are visible. The grammar is stricter than the compiler in places, for example it requires an `args` object for every call and gives nested raw-schema arguments only a loose `object` type, so it can forbid a token the compiler would have accepted on a correct path; and the mask changes the greedy path only when the unconstrained path leaves the grammar, so its possible gain is bounded by the invalid-output rate, which fine-tuning drives to a few percent (Appendix A). We now use masking as a diagnostic for untuned models and rely on conservative corrections for fine-tuned ones.

**Table 12. Grammar masking ablation, exact match, mask off to mask on** (`runs/factory_phase2/grammar_ablation/*`, `runs/factory_bfcl/table.md`).

| Model state | Catalog | Syntax valid | Exact / AST match |
|---|---|---|---|
| Untuned 0.6B | iot_light_5 | 66% to 100% | 40.8% to 57.8% |
| Untuned 0.6B | smart_home_50 | 64% to 100% | 37.8% to 52.6% |
| SFT v1 0.6B | iot_light_5 | 92% to 94% | 73.4% to 68.6% |
| SFT v1 0.6B | smart_home_50 | 88% to 99.8% | 64.0% to 70.8% |
| SFT v2 0.6B (CUDA) | iot_light_5 | 95.6% to 99.4% | 86.4% to 86.0% |
| Untuned 0.6B | BFCL `parallel` | 93% to 97% | 0.50 to 0.04 |

### 5.6 System overheads and reproducibility

The compiler and renderings are negligible (§3.4). Synthesis for a catalog costs cents and minutes (Table 4). Fine-tuning a sub-2B model on a few hundred examples costs seconds to minutes on a single consumer GPU (Table 6). Evaluation dominates the loop's wall time: 500 queries through unoptimized HuggingFace generation take about 12 minutes at 1.4 s p50 per query for the 0.6B model, which a batched server would cut by an order of magnitude. **TODO:** serve the adapters with vLLM [7], report throughput and p50 at batch sizes 1–32, and repeat the token and latency comparison with prefix caching on and off.

Two reproducibility findings are systems results in their own right. First, the identical recipe (341 examples, three epochs, rank 32, bf16) trained on an M1 Ultra reached a final loss of 0.071 and 76.6% exact match, and on an RTX 4090 a loss of 0.039 and 86.4%. A dtype pin on the Mac showed that bf16 to fp32 alone closed 0.024 of the loss gap while the seed-to-seed floor was 0.0001, and that PEFT's initialization went through a hardware- and dtype-dependent random path. Small-batch LoRA on small models is sensitive to accelerator numerics at a level that moves headline metrics by ten points, so every number in this paper is tagged with its accelerator. **TODO:** complete the CUDA cells of the dtype matrix and report exact match per cell. Second, HuggingFace generation on MPS leaked roughly 1–1.5 GB per call on long-context catalogs until the evaluation loop released device memory explicitly; the CUDA caching allocator had hidden the same leak.

## 6 Discussion

### 6.1 Prompt caching and when the IR pays

Provider-side prefix caching amortizes the schema across requests that share it, and reviewers should ask whether that erases the token argument. It does not, for three reasons. Cached input is still billed, at a discount, and the IR prefix is equally cacheable, so the relative saving persists at the discounted rate. On a local server the prefix occupies KV-cache memory for the lifetime of every request whether or not it was cached, and a 1,300-token schema prefix against a 380-token IR prefix for the 50-tool catalog is a direct difference in memory per sequence and therefore in batch size. And output tokens, which caching does not touch, are 31% fewer on the IR path. The IR's advantage is smallest when the schema is short, when the model is fast, and when slots are unconstrained strings; it is largest for large catalogs, weak or small models, and constraint-dense contracts. **TODO:** quantify KV-cache memory per sequence and maximum batch size for both prefixes on the vLLM deployment.

### 6.2 Form versus meaning

The negative results have one explanation. Grammar masking, repair, and conservative corrections all reduce the same quantity, the mass of outputs that fail to compile. They differ in where they act (inside decoding, outside it with extra calls, inside the compiler) and in what they cost, but none of them acts on a well-formed wrong call. Fine-tuning on compiler-gated data removes most of the invalid mass, after which these devices have little left to do and can only cause harm by disagreeing with the compiler. What remains after fine-tuning is semantic: wrong tool among similar tools, wrong argument value. Those failures need data, and the data edge is the only mechanism in the loop that reaches them, with the constraint that it must be pointed at prompts the model does not already solve.

### 6.3 Human in the loop by design

Ganglion proposes patches; it does not apply them. The runs in this paper were operated by a person who read the histogram, chose the rule, and tested it, with the proposer reproducing the chosen rules on fixtures. We think this is the right boundary for a system whose contract is a specification: an optimizer that can edit its own specification can also redefine correctness. The cost is that one turn of the loop takes human minutes rather than machine seconds. Measuring proposal precision against operator choices (§4.4) is the experiment that would justify raising the automation level.

### 6.4 Threats to validity and limitations

- **Single model family for the API and local experiments.** All API runs use Qwen 3.6 models and all local runs use Qwen3; the direction of the flash-versus-plus comparison suggests weaker models benefit more, but we have not tested other families. **TODO:** Llama or Gemma class open-weight models on the same 500 BFCL cases and on the IoT benchmark.
- **Synthetic and small evaluation sets.** The IoT queries are human-written but template-influenced, four of them appear inside the prompt (an upper bound of 0.8pp on the affected metrics), and the API tier comparison uses 50-case samples. BFCL categories are 100-case subsamples. Confidence intervals are ±2–5pp and all local runs are single-seed.
- **The native baseline shares the compiler's corrections** (§3.5). This is fair for the accuracy comparison but means that neither path is a raw model number; §5.5 reports raw numbers for the local models.
- **Latency measurements** on an API include network and load; on local hardware they use an unoptimized generation loop.
- **No comparison against schema minification or tool retrieval.** Both reduce the schema prefix on the native path and are complementary to the IR; retrieval changes the number of tools on both paths (§2.1). **TODO:** add minified-schema and top-k retrieval baselines.
- **The automated loop is one iteration.** Multi-iteration convergence, rule retirement, and cross-catalog transfer of patches are not measured.
- **BFCL single-turn is a saturated slice** of the benchmark, and the multi-turn, Java, and JavaScript categories are out of scope.

## 7 Related Work

**Tool use and benchmarks.** Toolformer [11] and ToolLLM [12] established tool use as a training target; Gorilla and the Berkeley Function Calling Leaderboard [3] fixed AST-based grading, which we re-implement for Python categories. Native function calling [1] and MCP [2] define the schema-in-prompt interface whose cost this paper addresses. APIGen [13] generates verified function-calling data with format, execution, and semantic checks; Ganglion's compiler gate plays the format-and-schema role and adds the loop back from deployed failures.

**Structured and constrained generation.** Grammar-constrained decoding [14, 15] and engines such as XGrammar [5], Outlines [16], and SGLang [17] guarantee well-formed output. Our contribution is not a new engine but a measurement: on fine-tuned small models masking can reduce task accuracy, consistent with reports that format restrictions can hurt performance [18], and we give the mechanism (grammar stricter than the compiler; gain bounded by the invalid rate).

**Small models for function calling.** TinyAgent [19] and Octopus v2 [20] specialize sub-2B models for on-device function calling, the latter with functional tokens that compress the call. Ganglion keeps the output in plain JSON, so the same IR works for API models without retraining, and moves the specialization into a contract that can be patched.

**Prompt cost.** Prompt compression [21] and tool retrieval [12] shorten the prefix on the native path; both are orthogonal to changing the representation and compose with it. Provider prefix caching amortizes but does not remove the cost (§6.1).

**Self-training and preference optimization.** STaR [22] and ReST [23] bootstrap from a model's own verified outputs; DPO [10] learns from preference pairs. Our results reproduce the known saturation behaviour of both when the verifier is the compiler and the policy is already accurate, and quantify the pair yield.

**Failure analysis and data flywheels.** Structured error taxonomies and feedback loops from production traces are common practice in MLOps, but to our knowledge the combination of a contract that both renders the prompt and classifies the failures, and that is itself the object of feedback, is new for tool calling.

> **TODO:** verify every citation against the venue's bibliography requirements and add missing entries (SGLang, Outlines, LLMLingua, TinyAgent, Octopus, APIGen, STaR, ReST, the format-restriction study).

## 8 Conclusion

Ganglion treats a tool catalog as a contract to be compiled rather than a schema to be read. The compiler makes the model's job smaller and cheaper, makes every failure typed, and makes the two feedback edges, patches to the contract and recycling of failures into training data, mechanical. The measurements support the design at both ends of the model spectrum: for large API models the IR preserves accuracy while cutting input tokens by half to two thirds, and for a 0.6B model one pass of the factory turns an unusable baseline into a deployable one. The negative results are as useful as the positive ones: the inference-time devices are all bounded by the invalid-output mass, and once fine-tuning removes that mass only data reaches what is left.

## References

[1] OpenAI. Function calling. OpenAI API documentation. https://platform.openai.com/docs/guides/function-calling
[2] Anthropic. Model Context Protocol specification. 2024. https://modelcontextprotocol.io
[3] S. G. Patil, H. Mao, S. Yan, C. C.-J. Ji, V. Suresh, I. Stoica, and J. E. Gonzalez. The Berkeley Function Calling Leaderboard (BFCL): From tool use to agentic evaluation of large language models. ICML, 2025.
[4] E. J. Hu, Y. Shen, P. Wallis, Z. Allen-Zhu, Y. Li, S. Wang, L. Wang, and W. Chen. LoRA: Low-rank adaptation of large language models. ICLR, 2022.
[5] Y. Dong, C. F. Ruan, Y. Cai, R. Lai, Z. Xu, Y. Zhao, and T. Chen. XGrammar: Flexible and efficient structured generation engine for large language models. MLSys, 2025.
[6] Qwen Team. Qwen3 technical report. arXiv:2505.09388, 2025.
[7] W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica. Efficient memory management for large language model serving with PagedAttention. SOSP, 2023.
[8] Home Assistant. LLM API. Developer documentation. https://developers.home-assistant.io/docs/core/llm/
[9] Y. Zhou et al. [TODO placeholder, remove if unused]
[10] R. Rafailov, A. Sharma, E. Mitchell, S. Ermon, C. D. Manning, and C. Finn. Direct preference optimization: Your language model is secretly a reward model. NeurIPS, 2023.
[11] T. Schick, J. Dwivedi-Yu, R. Dessì, R. Raileanu, M. Lomeli, L. Zettlemoyer, N. Cancedda, and T. Scialom. Toolformer: Language models can teach themselves to use tools. NeurIPS, 2023.
[12] Y. Qin et al. ToolLLM: Facilitating large language models to master 16000+ real-world APIs. ICLR, 2024.
[13] Z. Liu et al. APIGen: Automated pipeline for generating verifiable and diverse function-calling datasets. NeurIPS, 2024. [TODO verify]
[14] S. Geng, M. Josifoski, M. Peyrard, and R. West. Grammar-constrained decoding for structured NLP tasks without finetuning. EMNLP, 2023.
[15] L. Beurer-Kellner, M. Fischer, and M. Vechev. Guiding LLMs the right way: Fast, non-invasive constrained generation. ICML, 2024. [TODO verify]
[16] B. T. Willard and R. Louf. Efficient guided generation for large language models. arXiv:2307.09702, 2023.
[17] L. Zheng et al. SGLang: Efficient execution of structured language model programs. NeurIPS, 2024. [TODO verify]
[18] Z. R. Tam et al. Let me speak freely? A study on the impact of format restrictions on performance of large language models. EMNLP Findings, 2024. [TODO verify]
[19] L. E. Erdogan et al. TinyAgent: Function calling at the edge. EMNLP Demo, 2024. [TODO verify]
[20] W. Chen and Z. Li. Octopus v2: On-device language model for super agent. arXiv:2404.01744, 2024. [TODO verify]
[21] H. Jiang, Q. Wu, C.-Y. Lin, Y. Yang, and L. Qiu. LLMLingua: Compressing prompts for accelerated inference of large language models. EMNLP, 2023.
[22] E. Zelikman, Y. Wu, J. Mu, and N. D. Goodman. STaR: Bootstrapping reasoning with reasoning. NeurIPS, 2022.
[23] C. Gulcehre et al. Reinforced self-training (ReST) for language modeling. arXiv:2308.08998, 2023.

## Appendix A. Formal statements used in the text

The full framework is in `docs/pipeline_formalization.md`; we restate the four results the paper relies on.

**A.1 Cost scaling.** With additive rendering sizes $\operatorname{tok}(r(C)) = c_r + \sum_{t}\ell_r(t)$, per-tool means $\bar\ell_\rho<\bar\ell_\sigma$, and fixed parts $c_\rho\ge c_\sigma$, the input-token saving $S(N)$ is increasing in the number of tools $N$ and $\lim_{N\to\infty}S(N)=1-\bar\ell_\rho/\bar\ell_\sigma$. Proof: $S=1-A/B$ with $A=c_\rho+N\bar\ell_\rho+X$, $B=c_\sigma+N\bar\ell_\sigma+X$, and $\frac{d}{dN}(A/B)$ has the sign of $\bar\ell_\rho(c_\sigma+X)-\bar\ell_\sigma(c_\rho+X)<0$.

**A.2 Masking under greedy decoding.** Let $y^\circ$ and $y^G$ be the unconstrained and constrained greedy outputs. If every prefix of $y^\circ$ is allowed by the grammar then $y^G=y^\circ$; hence $\mathrm{Acc}^G-\mathrm{Acc}^\circ=\Pr[\text{rescue}]-\Pr[\text{break}]$ with $\{\text{break}\}\subseteq\{y^\circ\notin\mathcal L(G)\wedge F_C(y^\circ)=\pi^*(x)\}$. Breaks are possible only where the grammar rejects a string the compiler accepts, and the gain is bounded by the unconstrained invalid rate.

**A.3 Correction accounting.** For fixed outputs and gold plans, $\mathrm{EM}^{(K)}-\mathrm{EM}^{(0)}=\mathrm{Rescue}-\mathrm{Regression}$, and a correction that only modifies structures with $F^{(0)}_C=\bot$ has $\mathrm{Regression}=0$.

**A.4 Pair yield.** If $\pi_{\mathrm{agree}}(x)$ is the probability that all $N$ samples for prompt $x$ receive the same score, the fraction of prompts that yield a preference pair is at most $1-\mathbb E_x[\pi_{\mathrm{agree}}(x)]$; for independent binary scores with accuracy $p$, $\pi_{\mathrm{agree}}=p^N+(1-p)^N$.

**A.5 Metric chain.** $\mathrm{EM}\le\mathrm{AM}\le\mathrm{Valid}$; the gaps are contract violations, tool-selection errors, and argument errors, which is the top-level split of the taxonomy in §4.3. A structural synthesis gate cannot bound semantic label noise, since any well-formed single call passes it regardless of the request.

## Appendix B. Reproducibility

All experiments are driven by committed scripts. Environment: Python 3.11+, `pip install -e ".[dev,factory]"`, `DASHSCOPE_API_KEY` for API paths; local runs need a CUDA GPU with 16–24 GB.

```bash
pytest                                                    # 247 tests
python -m ganglion.benchmarks.iot.scaling                 # Table 1
bash runs/m2_run.sh && python runs/aggregate.py           # Table 7, latency repeats
python -m ganglion.cli --llm qwen --bfcl all --bfcl-allow-empty-calls \
    --bfcl-output runs/bfcl/m5_full_cases.jsonl            # Table 8 (IR, null action)
python -m ganglion.cli --llm qwen-native --bfcl all       # Table 8 (native)
python runs/bfcl/aggregate.py
python runs/factory_phase2/grammar_ablation.py --catalog iot_light_5 \
    --base-model Qwen/Qwen3-0.6B --adapter runs/factory_phase2/sft_0.6B_v2/iot_light_5/adapter \
    --out runs/factory_phase2/grammar_ablation/0.6B-sft-v2-iot_light_5-cuda   # Tables 9, 12
python runs/factory_phase2/recompute_with_corrections.py  # Table 11 replay, CPU only
bash runs/factory_bfcl/run_phase1.sh; bash runs/factory_bfcl/run_phase2.sh
bash runs/factory_bfcl/run_phase2_post.sh; bash runs/factory_bfcl/run_phase3.sh   # Table 10
```

The BFCL subsample is a seed-42 draw pinned to upstream commit `6ea57973` and vendored under `examples/bfcl/v4/sample/`. LoRA adapters are not committed; training metrics, evaluation reports, and per-case outputs are.

## Appendix C. Submission checklist (editorial, delete before submission)

1. **Multi-seed local runs** for Tables 9, 10, 12 (three seeds, CUDA-pinned); report mean ± sd.
2. **vLLM serving numbers**: throughput, p50/p95 at batch 1–32, KV memory per sequence for both prefixes, prefix caching on/off (§5.6, §6.1).
3. **Second model family** on BFCL 500 and IoT (§6.4).
4. **Baselines**: minified native schema; top-k tool retrieval on both paths (§6.4).
5. **Proposer precision**: run `analyzer/rules.py` over recorded traces, compare with operator-chosen rules (§4.4).
6. **Figures**: Fig. 1 regenerated with the data edge; Fig. 2 token scaling; Fig. 3 small-model progression bars (Tables 9–10); Fig. 4 failure histogram before/after rules (Table 11).
7. **Contamination**: rerun the IoT headline numbers excluding the four in-prompt example queries.
8. **Citations**: resolve every `[TODO verify]`, remove placeholder [9].
9. **Style**: convert to the MLSys LaTeX template; move Appendix A to supplementary material if space is short.
