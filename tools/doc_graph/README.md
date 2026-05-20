# docs/tasks graph viewer

Visualise how the redesigned Ganglion task docs under `docs/tasks/` compose
into the three-module architecture (`contract/`, `lm/`, `analyzer/`) and its
two composites (`factory_pipeline`, `factory_evaluation`).

The viewer renders the docs as a three-level interactive graph:

| Level | What you see | Trigger |
|---|---|---|
| **L0** Overview | 5 module bubbles, each containing its atomic task docs. Composites sit standalone. | Initial load. |
| **L1** Module zoom | One module fills the viewport; its atomic docs and their inter-doc event edges become legible. | Click a module compound node. |
| **L2** Doc body | The 6-section markdown of the task doc renders in a right-side drawer; the node's fan-in / fan-out event edges are highlighted in the graph. | Click an atomic / composite doc node. |

Two edge kinds:

- **event edges** (solid + label like `lm.synth.completed`) — the *runtime*
  dependency DAG, derived from each doc's `## Contract`'s `event:` clause by
  matching `emit X` ↔ `consume X` across docs. **On by default.**
- **wikilink edges** (dashed) — design-time `[[...]]` cross-references found
  anywhere in a doc's body. **Off by default**; toggle in the top-right.

## Running it

```bash
# 1) regenerate the manifest from the current docs/tasks/*.md
python tools/doc_graph/extract.py

# 2) serve the viewer (any static server works)
python -m http.server -d tools/doc_graph/viewer 8765

# 3) open http://localhost:8765/
```

`extract.py` writes:

- `tools/doc_graph/viewer/data/manifest.json` — the nodes / edges / modules
  payload the viewer reads.
- `tools/doc_graph/viewer/docs/<doc_id>.md` — a mirror of each task doc so the
  drawer can `fetch()` it without serving the repo root.

Run it again any time `docs/tasks/*.md` changes.

## What the extractor pulls from each doc

| Field | Source |
|---|---|
| `id`, `title` | filename stem + first `#` heading |
| `kind` | `(composite)` suffix in title → composite, else atomic |
| `module` | filename prefix (`contract_` / `lm_` / `analyzer_` / `benchmark_` / `factory_`) |
| `summary` | first non-empty paragraph after the title |
| `role` | body of the `## Role` section |
| `in_scope` / `out_of_scope` | indented bullets under `**in-scope**` / `**out-of-scope**` in `## Scope` |
| `events_consumed` / `events_emitted` | dotted identifiers inside the `**event**:` sub-bullet of `## Contract`, bucketed by `consume` vs `emit` |
| `wikilinks` | every `[[name]]` in the body |
| `lines`, `file` | line count and repo-relative path |

Module-level cross-edges are computed by matching emitted events to consumed
events across docs. Events that have one side only (e.g. terminal verdicts
like `factory.pipeline.iterated`) appear as `dangling_events` in the manifest
and as a footer notice in the viewer — they are usually *not* a bug, just a
spec surface no downstream doc subscribes to yet.

## Extending the viewer

- **New module group**: add a key to `MODULE_PREFIX` and `MODULE_LABEL` in
  `extract.py`. The viewer picks up the new bucket automatically and colours
  the compound from `MODULE_COLOR` in `viewer/app.js` (add an entry there too).
- **New edge kind**: add an `edges` builder in `build_manifest()`, push to the
  output JSON, then add a `style` entry plus a toggle in `index.html` /
  `app.js`. Mirror the `event-edge` / `wiki-edge` pattern.
- **Schema drift**: the extractor is intentionally tolerant — missing
  sections leave fields empty rather than crashing. If a doc's section layout
  is unusual (e.g. inline `**event**: consumes X; emits Y.`), the inline form
  is handled too; check `extract_events()` if a new shape is needed.

## Why this lives under `tools/`

It is a read-only auxiliary viewer for the existing task specs. It does not
generate code, does not write back to `docs/`, and is not part of the
`ganglion` Python package. Treat it like `tools/eval/` analysis scripts.
