# BFCL v4 Vendored Data

Source: https://github.com/ShishirPatil/gorilla
Subdirectory: `berkeley-function-call-leaderboard/bfcl_eval/data/`
Commit: `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`
Date: 2026-03-23
License: Apache License 2.0 (see `LICENSE` in this directory)

## Files

```
full/
  BFCL_v4_simple_python.json         399 cases    283 KB
  BFCL_v4_multiple.json              199 cases    317 KB
  BFCL_v4_parallel.json              199 cases    172 KB
  BFCL_v4_parallel_multiple.json     199 cases    347 KB
  BFCL_v4_irrelevance.json           239 cases    160 KB
  possible_answer/
    BFCL_v4_simple_python.json       399 entries
    BFCL_v4_multiple.json            199 entries
    BFCL_v4_parallel.json            199 entries
    BFCL_v4_parallel_multiple.json   199 entries

sample/
  simple_python.jsonl                100 cases    deterministic seed=42
  multiple.jsonl                     100 cases
  parallel.jsonl                     100 cases
  parallel_multiple.jsonl            100 cases
  irrelevance.jsonl                  100 cases    ground_truth omitted
```

`irrelevance` has no `possible_answer/` because the correct outcome is "no function call". Ganglion's M5' abstention milestone uses this category.

## File format

Each line of the upstream `BFCL_v4_<category>.json` is one JSON object:

```json
{"id": "simple_python_0",
 "question": [[{"role": "user", "content": "..."}]],
 "function": [{"name": "...", "description": "...",
               "parameters": {"type": "dict", "properties": {...}, "required": [...]}}]}
```

Note: BFCL uses `"type": "dict"` instead of standard JSON Schema `"type": "object"`. The Ganglion compiler treats these as equivalent.

`possible_answer/` lines look like:

```json
{"id": "simple_python_0",
 "ground_truth": [{"calculate_triangle_area": {"base": [10], "height": [5], "unit": ["units", ""]}}]}
```

Each argument maps to a list of accepted values. An empty string `""` in the list means the argument may be omitted.

## Sub-sample

The `sample/` directory holds 100 cases per category drawn deterministically from
`full/` via `subsample.py` with `seed=42`. Re-run that script after pulling
fresh upstream data to regenerate.

```bash
python examples/bfcl/v4/subsample.py
```

The sub-sample lines merge question + ground_truth into one record per line:

```json
{"id": "...", "question": [...], "function": [...], "ground_truth": [...]}
```

`ground_truth` is omitted for `irrelevance` cases (no expected call).

## Why vendored

We commit the full upstream files (only ~1.3 MB total) so all evaluations are
reproducible without a network fetch. The upstream commit SHA above is the
authoritative source of truth; if it diverges from these files the SOURCE.md
header must be updated in the same commit that refreshes the data.
