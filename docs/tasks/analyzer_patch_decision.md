[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# analyzer_patch_decision

[[analyzer_rule_synthesis]] *proposes*; nobody *applies*. This primitive records what the human did with each proposal — an append-only decision ledger keyed by `patch_id` — so the analyzer's precision can be measured instead of asserted, and so `patch_acceptance_rate` (declared in [[analyzer_rule_synthesis]]'s Observation but never computed) has a data source. Three properties are load-bearing: decisions are **staged** (`blind` before any preview, `after_preview` optionally after, `ported` when a reviewed code edit lands), **accept changes nothing** (builtin catalogs are Python modules; an accepted patch is "approved, awaiting port"), and porting is an **explicit reference** (`commit_sha`), because a `describe()` diff cannot distinguish a ported patch from an independently hand-written alias.

Status: spec, implementation in progress (2026-09-16) — target `ganglion/analyzer/decisions.py`, tests `tests/test_analyzer_decisions.py`.

## Role

Append one immutable decision per `(patch_id, stage)` action, resolve the latest decision per patch and stage, and fold proposals plus decisions into a precision summary.

## Scope

- **in-scope**:
  - `PatchDecision` frozen dataclass: `decision_id`, `patch_id`, `catalog_id`, `run_id`, `catalog_fingerprint`, `stage` ∈ `"blind" | "after_preview" | "ported"`, `decision` ∈ `"accept" | "hold" | "reject" | "retire" | "ported"`, `reason: str`, `decided_by: str`, `time_to_decide_ms: int | None`, `commit_sha: str | None`, `decided_at: str`.
  - `decision_id = "pd-" + sha256(patch_id, stage, decision, decided_at)[:16]`.
  - `DecisionStore(base_dir)`: `append(dec) -> str` idempotent on `decision_id`, path `<base>/<catalog_id>/<run_id>/patch_decisions.jsonl`; `iter(catalog_id, run_id)`; `latest_by_patch(catalog_id, run_id) -> dict[patch_id, dict[stage, PatchDecision]]` (newest `decided_at` wins per stage).
  - Stage rules:
    - `blind` — the first decision on a patch; the console hides the preview (expected rescues / regressions from `apply_patch` re-parsing, see [[contract_patch_apply]]) until a `blind` decision exists for that `patch_id`.
    - `after_preview` — optional second decision once the preview is visible; both are reported so anchoring can be measured.
    - `ported` — `decision="ported"` with a non-empty `commit_sha`; the only way a patch is marked as applied. `describe()` diffs ([[contract_describe]]) may hint `detected_in` but never set `ported`.
    - `retire` is the decision vocabulary for `operation="retire_rule"` proposals from [[analyzer_correction_attribution]]; the removal is still a reviewed code edit followed by `ported`.
  - `precision_summary(patches, decisions, *, conf_threshold=0.7) -> dict` — `{n_proposed, n_conf_ge_threshold, accepted_blind, accepted_after_preview, held, rejected, retired, ported, patch_acceptance_rate, precision_at_conf, by_operation: {op: {proposed, accepted}}}` where `patch_acceptance_rate = accepted_blind ÷ n_proposed` and `precision_at_conf = accepted_blind among patches with evidence.confidence ≥ conf_threshold ÷ n_conf_ge_threshold` (both `0.0` when the denominator is 0; always reported together with `n_proposed`).
  - Emit `analyzer.patch.decided(patch_id, stage, decision, decided_by)` per newly appended decision, correlation `{catalog_id, run_id, patch_id}`, through [[analyzer_event_ledger]]. This is the declared "human approval signal" that [[factory_pipeline]]'s gate reads (`decision == "accept"`); in this cycle the gate has nothing to publish, so the pipeline still halts at `pending_patches`.
- **out-of-scope**:
  - Applying a patch to any catalog — `apply_patch` ([[contract_patch_apply]]) is a pure preview function; builtin catalogs change only through reviewed edits under `ganglion/contract/builtins/`.
  - Computing the preview itself (rescue / regression counts from re-parsing `attempts[0]`) — the console computes it with `apply_patch`; this doc only says *when* it may be shown.
  - Proposing patches, evidence, confidence — [[analyzer_rule_synthesis]] and [[analyzer_correction_attribution]].
  - Detecting that a hand-written catalog edit "matches" a proposal (`applied_in` inference from `describe()` diffs) — impossible in general (predicates in `set_default` are opaque); the explicit `ported` reference replaces it.
  - Recall of the proposer against an operator label space — a measurement-protocol concern layered on this ledger, not stored here.
  - Multi-reviewer voting or conflict resolution — one operator; later decisions supersede earlier ones per stage.
- **on violation**: a decision that would *mutate* state (edit a `ToolSpec`, rewrite `proposed_patches.jsonl`, delete a prior decision) is out of contract — stop. A `ported` decision without `commit_sha` is a `ValueError`. A `blind` decision for a patch whose preview has already been served in this session is recorded with `stage="after_preview"` by the producer, never silently as `blind`.

## Procedure

```
on POST /api/patches/{patch_id}/decision {catalog_id, run_id, stage, decision, reason?, time_to_decide_ms?}:
    patch ← read_patches(base, catalog_id, run_id) ∋ patch_id        # 404 otherwise
    if stage == "blind" and a blind decision already exists:  stage stays "blind" (newest wins) — the preview gate is already open
    dec ← PatchDecision(decision_id=..., catalog_fingerprint=current, decided_by=$GANGLION_LABELER or "operator", decided_at=now_utc, commit_sha=None)
    DecisionStore.append(dec)          # idempotent
    emit analyzer.patch.decided(patch_id, stage, decision, decided_by)

on POST /api/patches/{patch_id}/ported {catalog_id, run_id, commit_sha}:
    same, with stage="ported", decision="ported", commit_sha required (ValueError if empty)

precision_summary(patches, decisions, conf_threshold=0.7):
    for p in patches: tally by latest decisions[p.patch_id] per stage; by_operation[p.operation]
    rates computed only over n_proposed > 0; else 0.0

on unknown patch_id:     reject at the producer (404); the store never records a decision for a patch that is not in proposed_patches.jsonl
on duplicate decision_id: idempotent skip, no event
```

## Contract

- **in**: a decision `{catalog_id, run_id, patch_id, stage, decision, reason?, time_to_decide_ms?, commit_sha?}`; the run's `proposed_patches.jsonl` for validation and for `precision_summary`.
- **out**:
  - `<base>/<catalog_id>/<run_id>/patch_decisions.jsonl` — one line per decision; append-only.
  - `precision_summary(...)` dict — served by the console as `precision` on run detail and on the patches endpoint; not persisted (read-time aggregate).
- **event**: consume `analyzer.rule.proposed` (a decision targets a proposed patch); emit `analyzer.patch.decided(patch_id, stage, decision, decided_by)`.
- **failure**:
  - `stage` / `decision` outside the vocabulary, or `ported` without `commit_sha` → `ValueError`; nothing written.
  - `patch_id` not in the run's proposals → producer returns 404; store untouched.
  - Duplicate `decision_id` → skip; no event.
  - `OSError` on append → re-raise.
- **success** (`pytest tests/test_analyzer_decisions.py`):
  - Appending `blind:accept` then `after_preview:hold` for one patch yields two lines and `latest_by_patch` exposes both stages.
  - Re-appending an identical decision yields no new line.
  - `precision_summary` over 4 proposals (confidences 0.9, 0.8, 0.5, 0.3) with two `blind:accept` (0.9, 0.3) reports `n_proposed=4`, `n_conf_ge_threshold=2`, `accepted_blind=2`, `patch_acceptance_rate=0.5`, `precision_at_conf=0.5`, and `by_operation` totals summing to 4.
  - `ported` without `commit_sha` raises `ValueError`.
  - Appending decisions changes no byte of `proposed_patches.jsonl` and no catalog fingerprint.

## Observation

- `patch_acceptance_rate[catalog_id, run_id]` = `accepted_blind ÷ n_proposed` (from `precision_summary`; the metric [[analyzer_rule_synthesis]] declared).
- `precision_at_conf[catalog_id, run_id]` = blind acceptances among proposals with `confidence ≥ 0.7` ÷ such proposals; reported only alongside `n_proposed`.
- `preview_anchoring_rate` = patches whose `after_preview` decision differs from their `blind` decision ÷ patches with both; the reason the two stages exist.
- `port_lag_days[patch_id]` = `ported.decided_at − blind.decided_at` for accepted patches; accepted-but-never-ported patches surface as an open backlog.
- `decision_minutes[run_id]` = Σ `time_to_decide_ms` ÷ 60 000 (read-time; part of the operator-cost figure next to `human_minutes` from [[analyzer_label_store]]).
- `escalate_routed_share` = `ESCALATE` proposals that received any decision ÷ `ESCALATE` proposals (they route to the label queue rather than to apply).

Related: [[analyzer_rule_synthesis]], [[analyzer_correction_attribution]], [[contract_patch_apply]], [[contract_describe]], [[analyzer_event_ledger]], [[console_operator]], [[factory_pipeline]].
