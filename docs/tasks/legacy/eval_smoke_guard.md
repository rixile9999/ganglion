[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# eval_smoke_guard

Deterministic CI guard that blocks PRs whose offline (`rules`-backed) evaluation regresses below a pinned baseline. **No API key required** — fully reproducible on stock CI runners.

## Role

Run a deterministic smoke evaluation on every PR head and post a status check that fails when `exact_match_rate` or `syntax_valid_rate` falls below the recorded baseline beyond tolerance.

## Scope

- **in-scope**:
  - Running `python -m ganglion.eval.runner --llm rules --tier iot_light_5 --json` against PR head.
  - Comparing the resulting `exact_match_rate` and `syntax_valid_rate` against `runs/baselines/iot_light_5_rules.json` within configured tolerance.
  - Posting a GitHub status check `eval-smoke / iot_light_5` with pass / fail / error.
  - Uploading `smoke.json` as a PR artifact.
- **out-of-scope**:
  - API-backed runs (`qwen*` clients) — secrets are not available on PR CI, and they would not be deterministic anyway.
  - Other tiers (`home_iot_20`, `smart_home_50`) — those exist for the M2 scaling experiment, not as merge gates.
  - Gating on latency, token usage, or repair statistics — orthogonal axes, separate task if needed.
  - Updating `runs/baselines/*.json` — owned by a separate `baseline_refresh` task (TBD); this guard only reads.
  - Catalog-shape regressions — that is [`catalog_spec_sync`](./catalog_spec_sync.md).
- **on violation**: if the PR author intentionally moves a baseline, they add commit trailer `Baseline-Bump: iot_light_5_rules <reason>`. The guard still fails but downgrades severity to a `request-human-review` comment instead of a hard fail; **never auto-updates the baseline file**.

## Procedure

```
on pull_request (any branch → main):
    pip install -e .
    python -m ganglion.eval.runner --llm rules --tier iot_light_5 --json > smoke.json
    baseline ← load runs/baselines/iot_light_5_rules.json
        # (PLACEHOLDER — values pinned in a follow-up PR; until then guard runs
        #  in observe-only mode and emits eval_smoke_bootstrap_required.)

    regressions ← []
    for metric in [exact_match_rate, syntax_valid_rate]:
        if smoke[metric] < baseline[metric] - tolerance[metric]:
            regressions.append((metric, baseline[metric], smoke[metric]))

    has_bump_trailer ← grep "Baseline-Bump: iot_light_5_rules" in PR commit messages

    if regressions and not has_bump_trailer:
        status ← failure
        emit eval_smoke_regressed(sha, regressions)
        post PR comment with metric table
    elif regressions and has_bump_trailer:
        status ← neutral
        post PR comment "Baseline bump requested — human review required"
    else:
        status ← success
        emit eval_smoke_passed(sha)

on runner crash:
    status ← error
    emit eval_smoke_failed(sha, cause)
```

## Contract

- **in**: PR head SHA; `runs/baselines/iot_light_5_rules.json` (placeholder until pinned).
- **out**: GitHub status check `eval-smoke / iot_light_5`; `smoke.json` artifact; optional PR comment with metric diff table.
- **event**: consume `pull_request`; emit `eval_smoke_passed(sha) | eval_smoke_regressed(sha, regressions) | eval_smoke_failed(sha, cause) | eval_smoke_bootstrap_required(sha)`.
- **failure**:
  - Runner non-zero exit → status `error` (not `failure`); `eval_smoke_failed(cause=runner_exit_<n>)`.
  - Baseline file missing → status `neutral`; emit `eval_smoke_bootstrap_required`; guard runs in observe-only mode.
  - Baseline JSON malformed → status `error`; `eval_smoke_failed(cause=baseline_malformed)`; no retry.
- **success**: status check posted with deterministic pass/fail derived purely from numeric comparison; exactly one event per PR head SHA.

## Baseline (TBD)

`runs/baselines/iot_light_5_rules.json` schema:

```json
{
  "metrics": {
    "exact_match_rate":  {"value": <float>, "tolerance": <float>},
    "syntax_valid_rate": {"value": <float>, "tolerance": <float>}
  },
  "pinned_from_run": "<path under runs/ used as source>",
  "pinned_at_sha":   "<sha>",
  "pinned_at":       "<ISO-8601 date>"
}
```

Values to be pinned in a follow-up PR once a representative `runs/m{2,3,4}/` snapshot is selected. Until then, this guard emits `eval_smoke_bootstrap_required` and runs in observe-only mode.

## Observation

- `smoke_pass_rate` = passes ÷ total PR head evaluations (rolling 30d).
- `regression_caught_per_month` — count of `eval_smoke_regressed` events.
- `false_block_rate` = regressions overridden by `Baseline-Bump:` trailer ÷ total regressions — signals tolerance is too tight if persistently > 0.2.
- `runner_error_rate` — runner crashes ÷ total runs; should be ≈ 0.
