[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_correction_attribution

The *contract* side of the rule lifecycle. [[analyzer_rule_synthesis]] expands a catalog's correction hooks; nothing so far measures whether an existing hook still earns its place after the model has been retrained. This primitive re-parses each trace's **first model output** twice — once through the catalog with every correction hook stripped (F⁰, "the model alone") and once through the full catalog (Fᴷ) — and attributes every pass/fail flip to the hook kind that caused it. Hooks that rescue nothing over a large enough sample become `retire_rule` proposals, fed back into the same `proposed_patches.jsonl` that [[analyzer_patch_decision]] gates. ΔEM = Rescue − Regression becomes a column with a bound instead of an anecdote.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/corrections.py`, tests `tests/test_analyzer_corrections.py`.

## Role

For one run, compute per-trace F⁰ / Fᴷ outcomes against the resolved gold, attribute rescues and regressions to `(tool, hook_kind)` by single-kind ablation, summarise per gold origin and per hook, and issue `retire_rule` patches for hooks that meet the retire predicate.

## Scope

- **in-scope**:
  - Hook kinds (exactly three, `ALL_HOOK_KINDS` from [[contract_patch_apply]]): `defaults_when_missing`, `strip_unknown_args` (including `Catalog.default_strip_unknown_args`), `prompt_correction`. `custom_validator` is never stripped — it carries validation and normalisation that F⁰ keeps by definition.
  - Hook class: `strip_unknown_args` → `conservative`; `defaults_when_missing` → `conservative` iff the defaulted arg is in the tool's required args, else `rewriting`; `prompt_correction` → `rewriting`.
  - `Attribution` frozen dataclass: `trace_id`, `case_id`, `gold_origin`, `f0_plan: dict | None`, `fk_plan: dict | None`, `f0_ok: bool | None`, `fk_ok: bool | None` (`None` = ungraded), `rescued: bool`, `regressed: bool`, `rescued_by: tuple[str, ...]`, `regressed_by: tuple[str, ...]`, `necessary_hooks: tuple[str, ...]`, `unattributable: bool`.
  - `attribute(catalog, trace, gold: ActionPlan | None, *, gold_origin: str) -> Attribution`:
    - input is **`attempts[0]["content"]` only** — the model's first answer; later attempts are products of the repair loop's fed-back error messages and belong to neither F⁰ nor Fᴷ (repair is read from `summary.json`'s `repair_*` instead). Native traces contribute their first `{"calls": [...]}`.
    - `f0 = strip_hooks(catalog, ALL_HOOK_KINDS).parse_json_dsl(raw, prompt=trace.prompt)`; `fk = catalog.parse_json_dsl(raw, prompt=trace.prompt)`; `DSLValidationError` → that side's plan is `None`.
    - `rescued = (not f0_ok) and fk_ok`; `regressed = f0_ok and (not fk_ok)`.
    - ablation: for each kind `k`, parse through `strip_hooks(catalog, (k,))`; a hook `"<tool>:<kind>"` is in `rescued_by` when removing `k` alone loses the pass, in `regressed_by` when removing `k` alone recovers it; `necessary_hooks` = kinds whose single removal changes the plan at all. When ≥ 2 kinds are individually necessary for one rescue the trace is counted once under `shared`.
    - `unattributable = True` when `attempts` is empty or `gold is None`; such traces contribute to `n` and `unattributable` only.
  - `summarize_corrections(catalog, traces, golds: Mapping[str, tuple[ActionPlan, str]]) -> dict`:
    `{"n", "unattributable", "by_gold_origin": {origin: {"n", "em_f0", "em_fk", "rescue", "regression"}}, "by_hook": {"tool:kind": {"class", "n_active", "necessary", "rescues", "regressions", "verdict", "cp95_upper"}}, "shared"}`.
    - `n_active` = traces where that hook changed the plan (necessary or not); `cp95_upper = 1 − 0.025 ** (1 / n_active)` when `rescues == 0` (Clopper–Pearson one-sided 95 % upper bound of the rescue rate at 0 successes), else `None`.
    - `verdict`: `retire_candidate` iff `n_active ≥ 30 and rescues == 0 and cp95_upper < 0.10`; `insufficient_evidence` iff `n_active < 30 or (rescues == 0 and cp95_upper ≥ 0.10)`; else `keep`.
    - Stratified by `gold_origin` so that golds produced with the compiler in the loop (a `correct` verdict on Fᴷ) do not inflate rescue.
  - `write_corrections(base_dir, catalog_id, run_id, attributions, summary) -> tuple[Path, Path]` — `<run dir>/corrections.jsonl` (one `Attribution` per line) and `<run dir>/corrections.summary.json`.
  - `retire_candidates_as_patches(catalog_id, summary) -> list[RulePatch]` — one `RulePatch` per `retire_candidate` hook: `operation="retire_rule"`, `target_tool=<tool>`, `payload={"hook_kind": <kind>, "arg": <str | None>}`, `source_failure_type=FailureType.NO_FAILURE`, `evidence={"failure_count": n_active, "support_share": 1.0, "example_trace_ids": [...≤5], "confidence": 1 − cp95_upper}` (4 dp), `patch_id = make_patch_id(catalog_id, "retire_rule", tool, payload)` from [[analyzer_rule_synthesis]]. The composite appends them to `proposed_patches.jsonl`.
  - Emit `analyzer.correction.attributed(run_id, em_f0, em_fk)` once per run with payload `{n, unattributable, em_f0, em_fk, rescue, regression, n_retire_candidates}` (overall, not per origin), correlation `{catalog_id, run_id}`, through [[analyzer_event_ledger]].
- **out-of-scope**:
  - Stripping `custom_validator` or attributing to individual correctors inside a validator chain — attribution stops at `(tool, kind)`; finer granularity needs named `CorrectionRule`s in Module 3 (a [[contract_catalog]] follow-up).
  - Applying a `retire_rule` — [[contract_patch_apply]] previews it; the removal is a reviewed edit, gated by [[analyzer_patch_decision]].
  - Cross-run non-inferiority (`rescues_A > 0` on the parent run, `EM⁰_B − EMᴷ_A ≥ −δ` with a bootstrap bound, ≥ 3 training seeds) — the measurement protocol layers that on [[analyzer_compare]]; the predicate here is single-run.
  - Repair-loop attribution (attempts ≥ 1) — [[analyzer_repair_policy]] and `summary.json` `repair_*`.
  - Choosing golds — `resolve_gold` in [[analyzer_label_store]] is the only join; this doc consumes `(ActionPlan, origin)` pairs.
  - Hooks that are not on the catalog (proposing *new* hooks) — [[analyzer_rule_synthesis]].
- **on violation**: if a caller passes the *last* attempt or the validated `plan` instead of `attempts[0]`, the attribution is meaningless — the function must read `attempts[0]` itself from the `Trace` and never accept a pre-parsed plan. A hook verdict of `retire_candidate` below `n_active = 30` is a bug (fail the test), not a tunable. A conservative hook with `regressions > 0` on a fixture is a contract violation of the hook's class, surfaced as a failing assertion, not silently reclassified.

## Procedure

```
summarize_corrections(catalog, traces, golds):
    f0_catalog ← strip_hooks(catalog, ALL_HOOK_KINDS); ablated[k] ← strip_hooks(catalog, (k,)) for k in ALL_HOOK_KINDS
    for trace in traces:
        gold, origin ← golds.get(trace.case_id, (None, "none"))
        if not trace.attempts or gold is None: record Attribution(unattributable=True); continue
        raw ← trace.attempts[0]["content"]
        f0 ← parse_or_none(f0_catalog, raw, trace.prompt); fk ← parse_or_none(catalog, raw, trace.prompt)
        f0_ok, fk_ok ← f0 == gold, fk == gold
        for k in ALL_HOOK_KINDS:
            pk ← parse_or_none(ablated[k], raw, trace.prompt)
            if pk != fk:                       hooks_active[(tool, k)] += 1; necessary_hooks += k
            if fk_ok and (pk != gold):         rescued_by += "tool:k"
            if (not fk_ok) and (pk == gold):   regressed_by += "tool:k"
        if len(rescued_by) ≥ 2: shared += 1
        attributions.append(Attribution(...))
    by_gold_origin[origin] ← n, em_f0, em_fk, rescue, regression
    by_hook["tool:kind"] ← class, n_active, necessary, rescues, regressions, cp95_upper, verdict
    return summary

on parse raising anything but DSLValidationError:  re-raise (a catalog bug, not a model failure)
on a trace whose attempts[0] content is not JSON:  f0 = fk = None; counted as graded fail on both sides (no rescue possible)
on gold coverage < 50 % of the run:                still compute; consumers hide the panel (gold_coverage in analyzer_label_store)
```

## Contract

- **in**: the run's traces from [[analyzer_trace_store]], the `Catalog` from `resolve_catalog(catalog_id)` (builtin or `compiled/<sha12>`; `bfcl/*` is not resolvable and is skipped by the composite), `golds` from `resolve_gold` per trace.
- **out**:
  - `<base>/<catalog_id>/<run_id>/corrections.jsonl` — one `Attribution` per trace.
  - `<base>/<catalog_id>/<run_id>/corrections.summary.json` — the summary dict above.
  - `retire_candidates_as_patches(...)` list — appended by [[analyzer_analyze]] to `proposed_patches.jsonl`.
- **event**: consume `analyzer.trace.recorded` (input traces) and `analyzer.label.recorded` (golds may change); emit `analyzer.correction.attributed(run_id, em_f0, em_fk)`; the `retire_rule` rows are announced by the composite as `analyzer.rule.proposed`.
- **failure**:
  - No traces → summary with `n = 0`, empty `by_hook`; files written; event emitted with zeros.
  - All traces unattributable (chat run without labels) → `unattributable == n`, `by_gold_origin` empty; not an error.
  - Catalog not resolvable → `CatalogNotResolvable` propagates (the composite reports 409).
  - IO error on write → re-raise; no event.
- **success** (`pytest tests/test_analyzer_corrections.py`):
  - On `iot_light_5` with a raw `{"calls":[{"action":"set_light","args":{"room":"living","brightness":70}}]}` and gold `state="on"`: `f0_ok=False`, `fk_ok=True`, `rescued_by == ("set_light:defaults_when_missing",)`.
  - A raw carrying an echoed `id` arg with `strip_unknown_args` on the tool: `rescued_by` names `strip_unknown_args`; the hook's `class == "conservative"` and its `regressions == 0` across the fixture.
  - A scene alias fixture with no hook involvement gives `f0 == fk` and empty `necessary_hooks`.
  - A trace with `attempts == ()` is `unattributable` and does not change `em_f0` / `em_fk`.
  - 40 traces where a hook is active with 0 rescues → `verdict == "retire_candidate"`, `cp95_upper < 0.10`, and `retire_candidates_as_patches` returns one `retire_rule` patch whose `patch_id` starts with `rs-iot_light_5-`; 20 such traces → `insufficient_evidence` and no patch.

## Observation

- `em_f0[run_id]`, `em_fk[run_id]` = from `corrections.summary.json` (overall); `Δem = em_fk − em_f0 = rescue − regression` (asserted).
- `rescue_rate[tool:kind]`, `regression_rate[tool:kind]` = `rescues ÷ n_active`, `regressions ÷ n_active`.
- `conservative_regressions` = Σ `regressions` over hooks with `class == "conservative"`; must be 0 — non-zero means a hook is misclassified or a validator rewrites.
- `retire_candidates[run_id]` = hooks with `verdict == "retire_candidate"`; `insufficient_evidence_share` = hooks below the sample threshold ÷ hooks active.
- `shared_share` = `shared ÷ (rescues total)`; high values mean single-kind ablation under-attributes and the hooks interact.
- `unattributable_share[run_id]` = `unattributable ÷ n`; > 0.5 hides the contract panel.

Related: [[analyzer_rule_synthesis]], [[analyzer_patch_decision]], [[analyzer_label_store]], [[analyzer_trace_store]], [[analyzer_compare]], [[analyzer_analyze]], [[contract_patch_apply]], [[contract_catalog]], [[analyzer_event_ledger]].
