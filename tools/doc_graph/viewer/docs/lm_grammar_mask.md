[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# lm_grammar_mask

Compile a published [[contract_catalog]] into a JSON Schema → XGrammar-compiled grammar → HF `LogitsProcessor`, and expose a mask-on / mask-off ablation contract so [[analyzer_metrics]] can measure constrained vs unconstrained decoding deltas under fixed model + prompts.

## Role

Translate a `Catalog` into a single-use, per-`generate()` HF `LogitsProcessor` whose admissible-token mask is the DSL envelope grammar, amortising the expensive XGrammar compile across many generations.

## Scope

- **in-scope**:
  - `catalog_to_json_schema(catalog: Catalog) -> dict` — DSL envelope `{type:object, properties:{calls:{...}}, required:[calls], additionalProperties:false}` where `properties.calls = {type:array, items: <branch> | {anyOf:[<branch>_1, …]}}`. Each per-tool branch is `{type:object, properties:{action:{const:<name>}, args:<tool.parameters>}, required:[action,args], additionalProperties:false}`. Single-tool catalogs inline the branch (no `anyOf`).
  - `minItems` clause: `minItems = 0` (omitted) iff `catalog.allow_empty_calls=True`, else `minItems = 1` — the null-action gate from [[contract_catalog]] / [[null_action_contract]] flows through unchanged.
  - `compile_catalog_grammar(catalog, tokenizer, *, vocab_size, stop_token_ids) -> CompiledGrammar` — amortised once per `(catalog, tokenizer)` pair via `xgr.GrammarCompiler(TokenizerInfo.from_huggingface(...)).compile_json_schema(schema)`.
  - `make_logits_processor(compiled_grammar) -> LogitsProcessor` — instantiated per `model.generate()` call because the underlying `GrammarMatcher` carries per-generation state and cannot be reused.
  - Vocab / stop-token contract: `vocab_size` is the **model config**'s `vocab_size`, not `tokenizer.vocab_size` (they diverge for Qwen3 due to padded embedding tables); `stop_token_ids` must include `<|im_end|>` and `<|endoftext|>` for Qwen3 to avoid premature termination.
  - Apple Silicon int-coercion workaround: a `_IntCoercedLogitsProcessor` subclass casting the 0-dim sampled-token tensor to a Python `int` via `.item()` before `GrammarMatcher.accept_token`. Upstream `xgrammar 0.2.0 contrib.hf.LogitsProcessor` passes the matcher a tensor; TVM-FFI rejects this on MPS. Mirrors upstream body otherwise; delete once upstream lands the fix.
  - Mask-on / mask-off ablation contract: a single boolean (`compiled_grammar=None` or the compiled object) in the evaluation config; identical catalog, identical model, identical prompts. Used by [[analyzer_metrics]] to compute mask-uplift on `syntax_valid_rate` and post-SFT regression on `exact_match_rate`.
  - Target path: `ganglion/lm/grammar.py` (current implementation lives at `ganglion/factory/grammar/{catalog_to_xgrammar.py,xgrammar_processor.py}` and will be re-homed under `ganglion/lm/` as part of the three-module redesign).
- **out-of-scope**:
  - Model training, SFT, LoRA adapters — see [[lm_finetune]].
  - Benchmark integration and per-case dataset loops (`iot_light_5`, BFCL v4) — see [[benchmark_iot]] and [[benchmark_bfcl]]. This task ships the building block; benchmarks decide when to apply it.
  - Alternative constrained-decoding backends (Outlines, JSONFormer, llama.cpp grammar, vLLM guided decoding). XGrammar is the chosen backend; alternatives are deferred until concretely needed.
  - Mid-generation or between-turn grammar mutation. Single-turn only; the grammar is fixed for the duration of a generate call.
  - Grammar **synthesis from failure traces**. Failure-driven grammar refinement is analyzer territory (see [[analyzer_metrics]]); this task only consumes the Catalog published by [[contract_catalog]].
  - Non-Qwen tokenizer special-token handling beyond what `xgr.TokenizerInfo.from_huggingface()` already discovers. Exotic tokenizers may need adapters elsewhere.
  - HF `generate()` invocation, sampling-config tuning, batch decoding orchestration — caller's responsibility ([[lm_client]] local_hf path).
- **on violation**: if a model needs grammar-masked tokens that the per-tool branch JSON Schema cannot express (e.g. recursive `$ref`, conditional `if/then`, unsupported `oneOf` discriminators), **fail at compile time loudly** — raise `DSLValidationError` from the upstream [[contract_catalog]] compiler. Do not silently fall through to an unconstrained branch and do not insert a permissive `additionalProperties:true` escape hatch.

## Procedure

```
input: Catalog (from contract.catalog.published), HF tokenizer, model.config

# Build pipeline (once per (catalog, tokenizer)):
openai_tools   ← catalog.render_openai_tools()             # SSOT for args schemas
schema         ← catalog_to_json_schema(catalog)           # DSL envelope + per-tool anyOf
tokenizer_info ← xgr.TokenizerInfo.from_huggingface(
                    tokenizer,
                    vocab_size=model.config.vocab_size,    # NOT tokenizer.vocab_size
                    stop_token_ids=[im_end_id, endoftext_id],
                 )
compiler       ← xgr.GrammarCompiler(tokenizer_info)
compiled       ← compiler.compile_json_schema(schema)      # cached: amortise across generates

# Per generate() invocation (called by lm_client local_hf):
processor      ← make_logits_processor(compiled)           # fresh: matcher state is per-call
outputs        ← model.generate(..., logits_processor=[processor])

on catalog_to_json_schema raising "no tools":
    surface as DSLValidationError to caller — empty catalog is a programmer error.
on xgr.from_json_schema raising (unsupported JSON Schema feature):
    propagate verbatim; do not retry without the offending branch
    (silently dropping the branch would re-open the unconstrained-fallthrough hole
     enumerated in `on violation`).
on tokenizer_info missing stop tokens:
    warn loudly (logger.warning) and continue — generation may overrun max_new_tokens,
    but the mask itself is still well-formed.
```

## Contract

- **in**: a `Catalog` (Module 3, published via `contract.catalog.published`); an HF tokenizer; model config exposing `vocab_size` and stop-token IDs.
- **out**:
  - `CompiledGrammar` — cacheable across many `model.generate()` calls for a fixed `(catalog, tokenizer)` pair.
  - `LogitsProcessor` — single-use, fresh per `model.generate()` invocation.
- **event**: none — passive building block. Consumed directly by [[lm_client]] (`local_hf` path) when it chooses to constrain decoding. This task emits no event.
- **failure**:
  - Catalog references a tool whose `parameters` is not representable as JSON Schema → `DSLValidationError` at compile time.
  - Tokenizer / model `vocab_size` mismatch (caller forgot to pass `vocab_size=model.config.vocab_size`) → compile-time error from XGrammar (silent mask-shape bug if not caught here — `vocab_size` is therefore a required keyword in the runtime call path).
  - Missing or empty `stop_token_ids` for Qwen3 → warn loudly; do not silently substitute defaults.
  - Empty catalog (`tools=()`) → `ValueError("catalog '<name>' has no tools")` from `catalog_to_json_schema`.
- **success**:
  - `tests/factory/test_grammar.py` and `tests/factory/test_xgrammar_processor.py` pass (envelope shape, per-tool branch `const`, `allow_empty_calls` `minItems` toggle, single-tool inlining, large-catalog `smart_home_50` compile, `vocab_size` override threads through, processor is per-call distinct).
  - Mask-on smoke run on `iot_light_5` against any Qwen3 checkpoint yields `syntax_valid_rate ≥ 0.99` — recorded by [[analyzer_metrics]] from the ablation-report artifact in `runs/factory_phase2/grammar_ablation/<run>/ablation_summary.json`.

## Observation

- `grammar_compile_ms` — wall time of `compile_catalog_grammar()`; amortised cost. Recorded once per `(catalog, tokenizer)` pair, not per generation.
- `mask_uplift[catalog]` = `syntax_valid_rate(mask_on) − syntax_valid_rate(mask_off)`. Target ≥ 0 by construction; values ≤ 0 indicate either a grammar bug or a tokenizer / vocab mismatch.
- `mask_post_sft_regression[catalog]` = `exact_match_rate(mask_on) − exact_match_rate(mask_off)`. Phase 2 measurement: this is often **negative** after SFT — the SFT model has already learnt the envelope, and masking over-constrains argument generation. Report it; do **not** treat negative values as a bug (they are the published finding the ablation contract exists to surface).
- `grammar_branch_count[catalog]` = `len(catalog.tools)` — the per-tool `anyOf` arm count. Sanity check that the compiled grammar covered every published tool.
- `processor_instantiation_count[run]` — number of `make_logits_processor()` calls per evaluation run. Should equal the number of `model.generate()` invocations; a mismatch means the caller is reusing a matcher across generations (state-corruption hazard).

## Status

Pre-redesign implementation lives at `ganglion/factory/grammar/{catalog_to_xgrammar.py,xgrammar_processor.py}` with the public re-exports under `ganglion.factory.grammar`. The three-module redesign (lm / analyzer / contract) will re-home this under `ganglion/lm/grammar.py`; the spec above is authored against the redesigned path. Tests at `tests/factory/test_grammar.py` and `tests/factory/test_xgrammar_processor.py` will move alongside. Headline ablation artifact: `runs/factory_phase2/grammar_ablation/`.
