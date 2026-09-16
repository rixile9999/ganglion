# `ganglion/console` — operator console

Implementation of [`docs/tasks/console_operator.md`](../../docs/tasks/console_operator.md)
(contract §5 + the local-model routes of §3b). Standard library only.

```bash
# 1. fill a runs dir with two offline runs (no API key, no GPU) and analyse both
python -m ganglion.console seed --runs runs/traces --limit 100

# 2. serve web/ + /api/* on 127.0.0.1:8766 ($GANGLION_CONSOLE_PORT overrides)
python -m ganglion.console serve --runs runs/traces --web web --models configs/models.yaml

# same surface without HTTP
python -m ganglion.console analyze iot_light_5 rules-degraded-seed
python -m ganglion.console export-labels iot_light_5 --zones train
python -m ganglion.console compare iot_light_5 rules-seed rules-degraded-seed
```

## Shape

| module | role |
|---|---|
| `server.py` | `ThreadingHTTPServer` on loopback; `/api/*` → `api.py`, everything else a static file from `--web` (root files + `assets/` + `mockups/`, traversal-safe, no listings). |
| `api.py` | the route table. Every GET is a **projection** of run-directory files; every POST calls **one** primitive through the writer and lets that primitive emit its ledger row. `GET /api/compare` and `GET /api/export/labels` are the two documented exceptions that persist an (idempotent) artifact — they go through the writer too. |
| `writer.py` | one daemon thread + queue. Every file write, and every local-model load, is a job on it; `submit` blocks the HTTP thread and re-raises. |
| `seed.py` | the two offline runs: `rules-seed` (plain rules client) and `rules-degraded-seed` (errata E8 — the plain client never fails validation, so without it the Rules page has nothing to decide on). |

## Things worth knowing

- **`repeat_index` is computed inside the writer** (errata E7). `trace_id` is content
  addressed, so without it re-asking the deterministic `rules` model the same question
  would collide with the first trace and `TraceStore.append` would no-op.
- **`no_failure` with `confidence == 0.0` is reported as `unclassified`** (errata E8), in
  the trace rows and in the histogram — a validation failure is never a pass.
- **A chat needs a session**: `POST /api/chat` 404s on a `session_id` with no
  `manifest.json`, so no shard is created that `list_runs` cannot see.
- **Labels are validated, never fixed**: `expected_plan` is parsed with
  `resolve_catalog(catalog_id).parse_json_dsl(..., prompt=trace.prompt)`; a failure is a
  422 and nothing is written. Note the parse runs the catalog's prompt-correction hooks,
  so a "wrong" room the prompt disambiguates is accepted.
- **Nothing here analyses anything.** If a handler needs a verdict no primitive exposes,
  the capability belongs in the owning primitive's doc first.
