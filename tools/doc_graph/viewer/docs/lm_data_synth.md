[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# lm_data_synth

Teacher-driven synthesis of `(intent, expected_dsl)` training examples anchored to a [[contract_catalog]]. Phase 1 ships **tool_anchored** (one tool per pair). Phase 2 adds **multi_tool**, **adversarial**, and **abstain** strategies — the last one exercises [[contract_null_action]]. Every kept row is validator-gated through `Catalog.parse_json_dsl`, so the JSONL output is by construction a legal Action IR corpus against its source catalog.

The module is the synthesis half of `lm/`. Downstream consumers ([[lm_finetune]], [[analyzer_verifier]]) treat the JSONL output as immutable; this task does not own training, serving, or repair semantics.

## Role

Generate, validate, dedup, and persist a catalog-anchored `(intent, expected_dsl)` corpus from a teacher LM, with per-strategy budget and quality accounting.

## Scope

- **in-scope**:
  - Teacher protocol: `Teacher.generate(prompt) -> (content, input_tokens, output_tokens)`. Default `DashScopeTeacher` against `qwen3.6-plus` (OpenAI SDK against DashScope, same pattern as `ganglion/runtime/qwen.py`); pluggable `FakeTeacher` for offline tests.
  - Strategy base class with two methods:
    - `render_prompt(catalog, **kwargs) -> list[dict]` — system + user messages.
    - `gate(catalog, candidate, **kwargs) -> tuple[bool, str]` — validator + strategy-specific structural check, returns `(kept, reason)`.
  - Concrete strategies under `ganglion/lm/synth/strategies.py`:
    1. `tool_anchored` — for a target tool, generate K intent/DSL pairs whose `expected_dsl` calls **exactly that tool**. Gate: parse OK + `len(plan.calls) == 1` + `plan.calls[0].action == tool.name`.
    2. `multi_tool` — intents whose DSL emits **2–4 calls** across the catalog (parallel-call test). Gate: parse OK + `2 <= len(plan.calls) <= 4` + every call's action ∈ catalog.
    3. `adversarial` — near-miss phrasings (slang, ellipsis, foreign-language mix, partial argument hints) that should still resolve to a specified anchor tool. Gate: same as `tool_anchored` plus a `phrasing_distance` lower-bound (Levenshtein over normalised intent vs. canonical phrasing ≥ configured threshold) — catches the trivial-paraphrase failure mode of over-narrow training sets.
    4. `abstain` — intents with **no matching tool**; expected_dsl is `{"calls": []}`. Requires `catalog.allow_empty_calls=True` (see [[contract_null_action]]); strategy refuses to instantiate against a catalog without it.
  - Synth pipeline `ganglion/lm/synth/pipeline.py`:
    - Budget caps: `max_cost_usd` (global), `max_attempts_per_tool` (per anchor), `max_attempts_per_strategy` (per non-anchored strategy).
    - Dedup: embedding cosine ≥ 0.92 → drop (sentence-transformers; falls back to exact-string equality when sentence-transformers is unavailable). Dedup runs **within strategy** and **across all kept rows** at the end.
    - Per-strategy stats accounting: `n_attempted`, `n_kept`, `n_dropped_{parse, structural, dedup}`, cost, tokens, duration.
  - Validator gate: every candidate must `catalog.parse_json_dsl(expected_dsl)` cleanly; strategy-specific extra checks layered on top.
  - Persistence: JSONL output with one row per kept example carrying `{intent, expected_dsl, strategy, origin, teacher_score, case_id}` — `origin ∈ {tool_anchored, multi_tool, adversarial, abstain}`, `case_id` is a content hash over `(intent, expected_dsl, catalog_id)` for stable dedup across runs.
  - Target paths: `ganglion/lm/synth/{teacher.py, strategies.py, pipeline.py}`, public entry `ganglion.lm.synth.run(catalog, config, teacher) -> SynthStats`.
- **out-of-scope**:
  - Training that consumes the JSONL output — see [[lm_finetune]].
  - Inference / serving — see [[lm_client]].
  - Grader choice / benchmark integration — see [[benchmark_iot]] and [[benchmark_bfcl]].
  - Inference-time grammar masking — see [[lm_grammar_mask]].
  - Reward modelling — [[analyzer_verifier]] supplies the deterministic Catalog-bound reward; synth uses it as a *gate*, not a training target.
  - Hand-curated catalog authoring — `Catalog` is **input** to synth, not output. Catalog construction lives in [[contract_catalog]] / `tool_schema_compiler`.
  - Cross-catalog transfer / synthetic-data style transfer between catalogs — separate task.
  - In-loop self-correction of failed candidates — see [[analyzer_repair_policy]] (that is an *inference-time* concern, not a synth-time one).
- **on violation**:
  - If a generated pair fails the validator, **drop it** — never try to repair it inside synth. Repair belongs to [[analyzer_repair_policy]] at inference time.
  - If a strategy's `gate` would mutate the candidate (rewrite args, inject defaults) to make it pass, that is a violation — gates are pure predicates.
  - If the `abstain` strategy is instantiated against a catalog with `allow_empty_calls=False`, refuse at construction time with `ValueError`; do not silently downgrade.

## Procedure

```
trigger: caller invokes ganglion.lm.synth.run(catalog, config, teacher).

for each strategy_name in config.strategies:
    strategy = build_strategy(strategy_name, catalog, config)
    if strategy_name == "abstain" and not catalog.allow_empty_calls:
        raise ValueError("abstain requires catalog.allow_empty_calls=True")

    for anchor in strategy.iter_anchors(catalog):   # tools for anchored strategies, [None] for multi_tool/abstain
        attempts = 0
        kept_for_anchor = 0
        while kept_for_anchor < config.n_target_per_tool and attempts < config.max_attempts_per_tool:
            if stats.estimated_cost_usd >= config.max_cost_usd:
                stats.cost_capped = True
                break_out_of_all_strategies()
            messages = strategy.render_prompt(catalog, anchor=anchor, n=config.samples_per_request)
            content, in_toks, out_toks = retry_with_backoff(teacher.generate, messages, retries=3)
            stats.input_tokens  += in_toks
            stats.output_tokens += out_toks
            stats.estimated_cost_usd += price(config.teacher_model, in_toks, out_toks)
            for candidate in parse_candidates(content):
                attempts += 1
                stats.n_attempted += 1
                kept, reason = strategy.gate(catalog, candidate, anchor=anchor)
                if not kept:
                    stats.n_dropped[reason] += 1
                    continue
                example = SynthExample(
                    intent=candidate["intent"],
                    expected_dsl=candidate["expected_dsl"],
                    strategy=f"{strategy_name}:{anchor or '*'}",
                    teacher_score=1.0,
                )
                kept_buffer.append(example)
                kept_for_anchor += 1

    if all attempts for strategy failed (no rows kept): mark strategy failed in stats; continue.

final = embedding_dedupe(kept_buffer, threshold=config.dedupe_threshold)
persist_jsonl(final, config.output_path)
emit_event("lm.synth.completed", catalog_id=catalog.id(), n_kept=len(final),
           n_attempted=stats.n_attempted, cost_usd=stats.estimated_cost_usd)
return stats
```

## Contract

- **in**:
  - `Catalog` (consumes [[contract_catalog]] / `contract.catalog.published`).
  - `SynthConfig`: `strategies: list[str]`, `n_target_per_tool: int`, `samples_per_request: int`, `max_cost_usd: float`, `max_attempts_per_tool: int`, `dedupe_threshold: float`, `output_path: Path`, `teacher_model: str`, `teacher_temperature: float`, `seed: int`.
  - `Teacher` instance implementing the `Teacher` protocol.
- **out**:
  - JSONL file at `config.output_path`, one row per kept `SynthExample` with fields `{intent, expected_dsl, strategy, origin, teacher_score, case_id}`.
  - `SynthStats` JSON sibling at `config.output_path.with_suffix(".stats.json")`.
  - One `lm.synth.completed(catalog_id, n_kept, n_attempted, cost_usd)` event per run.
- **event**: emit `lm.synth.completed`; consume `contract.catalog.published`.
- **failure**:
  - Teacher API error → exponential backoff retries 3× (1s, 2s, 4s + jitter).
  - Persistent teacher failure → mark strategy `failed` in `SynthStats.strategy_status`, continue with remaining strategies; do not abort the run.
  - Budget cap hit → stop, write partial stats with `cost_capped=true`, still emit `lm.synth.completed` with `n_kept` reflecting what survived.
  - Validator parse failure on a candidate → drop the candidate (`n_dropped_parse += 1`); never recurse into repair.
- **success**:
  - Every row in the output JSONL parses cleanly via `catalog.parse_json_dsl(row["expected_dsl"])` — invariant verified by `tests/factory/test_synth_offline.py` using a `FakeTeacher`.
  - `SynthStats` JSON is written and contains non-zero `n_attempted` for each requested strategy (or an explicit `failed` marker).
  - PR's CI runs `pytest tests/factory/test_synth_offline.py` (offline, no network) green.

## Observation

- `synth_pass_rate[strategy]` = `n_kept / n_attempted` per strategy. Phase-1 baseline on `iot_light_5` tool_anchored is ~0.6; multi_tool expected ~0.3 (harder), adversarial ~0.4, abstain ~0.7 (less structural surface area).
- `cost_per_kept_usd` = `estimated_cost_usd / n_kept` — primary teacher-cost regression signal.
- `dedup_drop_rate` = `n_deduped / (n_deduped + n_kept_pre_dedup)`. Spikes here mean the teacher is collapsing onto repeated phrasings — surfaces low-diversity regimes.
- `pass_rate_by_tool` (tool_anchored only) — per-tool gate-pass rate; flags catalog entries the teacher cannot consistently produce DSL for (often a `RawArg` / nested-shape signal).
- `strategy_failed_set` — set of strategy names that aborted due to teacher failures; non-empty means the run is partial.

Related: [[contract_catalog]], [[contract_null_action]], [[lm_finetune]], [[analyzer_verifier]], [[analyzer_failure_taxonomy]], [[analyzer_repair_policy]], [[lm_grammar_mask]], [[lm_client]], [[benchmark_iot]], [[benchmark_bfcl]].
