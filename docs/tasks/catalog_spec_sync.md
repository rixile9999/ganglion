[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Pattern source: [spec_sync (agent-forge)](https://github.com/EngramAICompany/agent-forge/blob/main/spec_sync.md)

# catalog_spec_sync

LLM agent that detects drift between `ganglion/schema/*.py` `ToolSpec` definitions and their downstream consumers, then opens a reconciliation PR classifying each diff as *mechanical / ambiguous / intentional*. **Catalog is SSOT — this agent never modifies a `ToolSpec`.**

## Role

Watch for changes in tier catalogs and propose mechanical impl updates in dependents that the catalog change implies, escalating ambiguous diffs to a human.

## Scope

- **in-scope**:
  - Diff between current and previously-snapshotted catalog rendering (`Catalog.render_json_dsl()` and `Catalog.render_openai_tools()`) for every tier.
  - Proposing mirror edits in:
    - `ganglion/runtime/rules.py` — only for tools that fall in `iot_light_5`.
    - `examples/<tier>/generate_dataset.py` templates that reference tool names by string.
    - `docs/*_report.md` numeric claims about tool counts per tier (e.g. "50-tool catalog").
  - Maintaining the catalog snapshot at `.forge/catalog_snapshot.json`.
  - Opening a single PR per push labelled `forge/catalog-sync` with a classified-diff table.
- **out-of-scope**:
  - Modifying `ToolSpec`, `ArgSpec`, or any `ganglion/schema/*.py` content — that is the human authoring surface.
  - Generating new dataset *rows* — only updates string references to renamed tools.
  - Touching `ganglion/runtime/qwen.py` — catalog-agnostic (renders go through `Catalog`).
  - Editing `ganglion/dsl/compiler.py` or its tests — owned by a separate `schema_compiler_sync` task (TBD).
  - Auto-merging — review is `forge_pr_review` (TBD for this repo).
- **on violation**: if a diff cannot be classified mechanically (e.g. an enum alias is removed and the rule-based client uses that alias as a pattern), emit `catalog_drift_ambiguous(diff)` and open the PR with `request-human-review` label and **no impl edits attached**. Never auto-resolve an ambiguous diff.

## Procedure

```
on push to main affecting ganglion/schema/** | ganglion/dsl/catalog.py | ganglion/dsl/tool_spec.py:
    prev ← load .forge/catalog_snapshot.json
    curr ← {tier: {json_dsl: render_json_dsl(get_catalog(tier)),
                    openai:   render_openai_tools(get_catalog(tier))}
             for tier in registry}
    diffs ← diff(prev, curr)

    for each diff entry:
        case rename with identical arg shape:
            classification ← mechanical
            propose: rename string in runtime/rules.py patterns (iot_light_5 only),
                     dataset templates, report tool tables
        case new tool added:
            classification ← mechanical
            propose: add stub branch in runtime/rules.py (iot_light_5 only) returning
                     ActionPlan with `pending` marker; flag for human follow-up
        case arg semantics changed (type / enum members / alias):
            classification ← ambiguous
            escalate; attach no impl edit
        case removal flagged by commit trailer `Catalog-Drop: <tool>`:
            classification ← intentional
            propose: remove dependent references; update reports
        otherwise:
            classification ← ambiguous

    if any diffs:
        open PR `forge/catalog-sync: <summary>`:
            body: classified-diff table (tool, kind, classification, proposed_edits[])
            files: proposed mechanical edits + updated snapshot
            label: mechanical | ambiguous | intentional (most-severe wins)
        emit catalog_sync_pr_opened(sha, classifications)
    else:
        emit catalog_sync_no_drift(sha)
```

## Contract

- **in**: git diff scoped to `ganglion/schema/**` and `ganglion/dsl/{catalog,tool_spec}.py`; prior `.forge/catalog_snapshot.json`; commit message trailers.
- **out**: a PR labelled `forge/catalog-sync` with classified-diff table in the body and (for mechanical-only diffs) impl edits; updated `.forge/catalog_snapshot.json` in the same PR.
- **event**: consume `push`; emit `catalog_sync_pr_opened(sha, classifications) | catalog_sync_no_drift(sha) | catalog_drift_ambiguous(diff)`.
- **failure**:
  - Snapshot file missing → emit `catalog_sync_bootstrap_required(sha)`; open PR with snapshot-only, no impl edits.
  - LLM classification timeout → emit `catalog_sync_failed(cause=llm_timeout)`; do not retry; surface as red status.
  - Catalog import error → emit `catalog_sync_failed(cause=catalog_import)`; no PR opened.
- **success**: exactly one of {PR opened with every diff classified, `catalog_sync_no_drift` event} per push to main.

## Observation

- `catalog_drift_per_main_push` = PRs opened ÷ pushes touching schema (rolling 30d).
- `ambiguous_classification_rate` = ambiguous diffs ÷ total diffs — high values indicate `ToolSpec` changes are not landing with the `Catalog-Drop:` trailer convention or that schema edits routinely cross semantics.
- `mechanical_pr_pass_rate` = mechanical-only PRs that merge without human edits ÷ mechanical PRs opened — directly measures whether classifier is too eager.
