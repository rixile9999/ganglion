[← Self-maintenance tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md)

# report_freshness

Deterministic CI guard that verifies every numeric claim in `docs/*_report.md` matches the underlying `runs/**/*.json` it cites, via inline `<!-- src:...#pointer -->` stamps. **Prose drift is out of scope — numbers only.**

## Role

Detect stale numbers in report markdown by comparing each `<!-- src:<path>#<json_pointer> -->`-stamped value against the resolved JSON value within tolerance.

## Scope

- **in-scope**:
  - Scanning `docs/*_report.md` for stamp markers.
  - Resolving `<path>` against the repo root and `<json_pointer>` as RFC 6901 against the loaded JSON.
  - Numeric equality within tolerance (default `1e-3` absolute for rates, `1` for counts; per-marker override via `#<pointer>?tol=<float>`).
  - Heuristic detection of unstamped numeric-looking strings (`\d+(\.\d+)?%?`) and emitting `report_unstamped` warnings for them.
- **out-of-scope**:
  - Inserting stamps automatically — stamps are human authorship surface (auto-insertion would hide silent drift).
  - Validating prose claims, qualitative wording, or table structure.
  - Updating `runs/**/*.json` — those are experiment outputs and immutable artifacts.
  - Regenerating any report — the author writes the report and stamps the numbers.
  - Reports under `docs/agent-forge/**` — those are imported seed docs, not Ganglion outputs.
- **on violation**: if a stamped number does not match the resolved JSON within tolerance, **fail loud** — do not auto-update the number in the report. Emit `report_freshness_failed(file, line, cited, actual)` and request the author rewrite the prose against the current data.

## Procedure

```
on push to main | pull_request affecting docs/*_report.md | runs/**:
    mismatches  ← []
    unstamped   ← []

    for each docs/*_report.md (excluding docs/agent-forge/**):
        for each marker  `<!-- src:<path>#<pointer> -->` adjacent to a numeric token T:
            data ← load_json(repo_root / path)
            actual ← jsonpointer.resolve(data, pointer)
            tol ← parse tol from marker query string or default
            if |float(T) - float(actual)| > tol:
                mismatches.append((file, line, cited=T, actual, pointer))
        for each unstamped numeric token matching \b\d+(\.\d+)?%?\b
            not inside a fenced code block or table-of-contents:
            unstamped.append((file, line, token))

    if mismatches:
        emit report_freshness_failed(sha, mismatches)
        post PR comment with mismatch table; fail status check
    else:
        emit report_freshness_passed(sha)
    if unstamped:
        emit report_unstamped(sha, unstamped)
        post PR comment listing locations; status remains success (warning only)
```

## Contract

- **in**: `docs/*_report.md` files; `runs/**/*.json` files; per-marker tolerance overrides.
- **out**: GitHub status check `report-freshness`; PR comment with mismatch / unstamped table when applicable.
- **event**: consume `push | pull_request`; emit `report_freshness_passed(sha) | report_freshness_failed(sha, mismatches) | report_unstamped(sha, unstamped)`.
- **failure**:
  - JSON pointer not resolvable → `report_freshness_failed(cause=stale_pointer, file, line, pointer)`.
  - Cited path not found under repo root → `report_freshness_failed(cause=missing_source)`.
  - JSON parse error → `report_freshness_failed(cause=source_malformed)`.
  - No markers found in any report → status `neutral`; emit `report_freshness_bootstrap_required(sha)` (this repo's reports predate the marker convention).
- **success**: every stamped number matches; status check posted exactly once per SHA.

## Marker syntax

```
<!-- src:runs/m2/iot_light_5_rules.json#/metrics/exact_match_rate -->
0.984

<!-- src:runs/m4/qwen_repair.json#/aggregate/p95_latency_ms?tol=5 -->
312
```

Notes:
- The marker must appear on the line **immediately before** the numeric token, or inline on the same line preceding it.
- `#<pointer>` follows RFC 6901; `?tol=<float>` is an optional absolute tolerance override.
- Multiple markers can stack on consecutive lines (e.g., a row in a table); each binds to the next numeric token.

## Observation

- `unstamped_number_density` = unstamped numeric tokens ÷ total numeric tokens across `docs/*_report.md` — target → 0 over time.
- `freshness_failures_per_month` — `report_freshness_failed` events ÷ month.
- `stale_pointer_rate` = mismatches with `cause=stale_pointer` ÷ total mismatches — signals reports cite paths that have moved.
