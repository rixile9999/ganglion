# `ganglion` CLI reference site

A generated reference for the operator CLI ([`docs/tasks/cli_operator.md`](../../docs/tasks/cli_operator.md)),
served as one page inside the existing console: **`web/cli.html`**.

Nothing on the page is written by hand except the four prose guides. The command
surface, every flag, the route each command calls, the CLI-vs-browser coverage
matrix and **every output block** are generated into `web/assets/cli.json`, which
the page fetches. Add a command to the CLI, regenerate, and it appears — that is
the whole point, because the CLI is still growing.

## Running it

```bash
# 1) regenerate web/assets/cli.json from the current CLI
python tools/cli_docs/build.py

# 2) serve it — either the console, or any static server
python -m ganglion.console serve          # http://127.0.0.1:8766/cli.html
python -m http.server -d web 8767         # http://127.0.0.1:8767/cli.html

# staleness gate (changes nothing; also run by tests/test_cli_docs.py)
python tools/cli_docs/build.py --check

# parser + routes only, no example capture (fast, for a quick look)
python tools/cli_docs/build.py --no-run

# one self-contained file — no server, opens over file://
python tools/cli_docs/build.py --standalone /tmp/ganglion-cli.html
```

The served page fetches its data, so `file://` will not work for
`web/cli.html` — use `--standalone` for that.

A full build takes four to six seconds — it creates a throwaway run store, seeds
it offline with the `rules` model, and executes every documented command.

**Generate from the project environment** (`conda activate ganglion`). Two
captured blocks — `model list` and `model health` on a `local_hf` entry — report
what the machine can actually run, so their content depends on whether `torch` /
`transformers` are importable. The build stamps which optional extras it saw in
`_generated.environment`, `--check` says *environment mismatch* rather than
*stale* when they differ, and `tests/test_cli_docs.py` skips the gate with that
reason instead of failing. Regenerating from a bare interpreter would silently
downgrade those two blocks to "not importable".

## What it extracts

| Field | Source |
|---|---|
| nouns, verbs, positionals, flags, `choices`, defaults, usage | `ganglion.ctl.main.build_parser()` — the argparse tree |
| which flags are common to every leaf | `ganglion.ctl.main.common_parser()` |
| per-command `help` / `description` | the same tree (argparse keeps a subcommand's `help=` on its *parent*) |
| the route each command calls, and its kind (`P` / `W` / `W†` / `local`) | the command table in [`cli_operator.md`](../../docs/tasks/cli_operator.md) §Scope |
| the full console route list for the coverage matrix | the route table in [`console_operator.md`](../../docs/tasks/console_operator.md) |
| environment variables | `ganglion.ctl.context._ENV` plus two named in `build.py` |
| every output block, its exit code and its stderr | **running** each step of [`examples.py`](examples.py) |
| `shipped` / `stub` / `unexercised` per command | behavioural: a command whose every example answers `not_implemented` is a stub |

Anything the generator cannot join is reported as a `warnings` entry (and on
stderr) rather than silently dropped — a command with no row in
`cli_operator.md`, a documented command the parser does not register, an example
filed under a command that does not exist, a route the console does not declare.
`tests/test_cli_docs.py::test_build_reports_no_warnings` keeps that list empty.

## Determinism, and why it matters

`--check` is only a real gate if two builds of the same tree are byte-identical,
so `Scrubber` normalises what cannot be reproduced and **nothing else**:

| Normalised | Left verbatim |
|---|---|
| absolute paths → `<TMP>` / `<REPO>` | catalog fingerprints `cf-…` |
| wall-clock timestamps → `2026-01-01T00:00:00Z` | model fingerprints `mf-…` |
| `latency_ms*`, `seconds`, `vram_mb` → `0.1234` | compiled catalog ids `compiled/<sha12>` |
| `git_head` → `<COMMIT>`, `python` → `<PYTHON>` | patch ids `rs-…` |
| the CUDA device name → `<GPU>` | availability verdicts, which are the point of those examples |
| `tr-` / `lb-` / `pd-` / `ev-` / `s-` ids → stable aliases | metrics, histograms, bootstrap CIs |

Everything in the right column is a function of its inputs, so a reader can
reproduce it. `git_head` and `python` are in the left column for a specific
reason: leaving either in would make `--check` fail on the next unrelated commit
or on a machine with a different interpreter, and the gate would become noise.

One thing is normalised *before* capture rather than after: the seeded runs'
`started_at` / `finished_at` are pinned to fixed stamps. `list_runs` orders by
`(catalog_id, iteration, started_at, run_id)`, which is deterministic — but
*whether the two seed runs land in the same wall-clock second* is not, so the
listing tie-broke on `run_id` on a fast machine and on `started_at` on a slow
one, flipping `run list`'s captured table. Pinning removes the tie and fixes the
order at parent-then-child.

Volatile ids are aliased by **order of first appearance**, which keeps a value
shown by one example matching the one the next example consumes
(`ask` prints a `trace_id`, `label add` takes it). The cost is that inserting a
step renumbers the aliases after it, so that commit's diff is larger than the
change — annoying, not wrong.

`_generated.commit` is the one field `--check` ignores.

## The standalone file

`--standalone FILE` writes one ~157 KiB HTML file with `style.css`, `cli.js` and
the whole spec inlined. It needs no server, no sibling asset and no network, so
it is the answer to "I cannot reach the console" — mail it, `scp` it, open it
from disk:

```bash
python tools/cli_docs/build.py --standalone /tmp/ganglion-cli.html
scp <server>:/tmp/ganglion-cli.html .        # then just open it
```

It is **not** checked in: it is a derived snapshot, and committing a 157 KiB
duplicate of `cli.json` on every regeneration is noise. `--standalone` therefore
takes an explicit path and has no default.

The renderer is the *same* `cli.js`. It boots from `window.__GANGLION_CLI__` when
that global is present and falls back to `fetch` otherwise, so there is no second
implementation to keep in step. Two details that matter:

- `<` is escaped to `\u003c` inside the embedded JSON. The captured output is
  full of `<TMP>` / `<trace_id>` placeholders, and a single `</script` in there
  would end the block and blank the page.
- The console navigation is replaced by a one-line provenance label — ten dead
  links next to a lone file are worse than none. Everything else is identical.

The only external reference left is the Google Fonts `@import` in `style.css`.
Offline it simply fails and the CSS falls back to `ui-monospace` /
`system-ui`, both already in the token's font stack, so the captured tables stay
aligned. `tools/cli_docs/build.py` fails loudly if `web/cli.html` is
restructured such that the three inlining points no longer match, rather than
emitting a half-inlined file.

## Extending it

| You change | You do |
|---|---|
| add a command, verb, flag or `choices` | nothing — regenerate |
| add a whole noun | nothing — the walk is generic |
| change a `help=` string | nothing — argparse is the source of truth |
| want an output block for a new command | one `Step(...)` in [`examples.py`](examples.py) |
| an example needs an id an earlier command produced | a hidden `Step(..., show=False, json_mode=True, capture=…)` |
| an example needs an input file | `Step(..., files={"name": "<contents>"})`; `{name}` in `argv` resolves to `name.json` |
| a new kind of prose section | a `GUIDES` entry in `web/assets/cli.js` (hand-written by design) |
| hand the docs to someone with no server | `--standalone FILE` (above) |
| a field the page should show that is not extracted yet | add it in `build.py`, then read it in `cli.js`; `test_renderer_only_reads_keys_the_generator_writes` keeps the two in step |

The page is **not** linked from the other console sheets' navigation bars: those
are hand-authored and owned by the web tasks, so adding an eleventh entry to ten
files is a deliberate edit someone should make on purpose. `cli.html` carries the
full nav itself, so it links back to all of them.

## Why this lives under `tools/`

Same reason as [`tools/doc_graph/`](../doc_graph/): it is a read-only auxiliary
view over surfaces that already exist. It generates no code, writes nothing under
`docs/` or `ganglion/`, and is not part of the `ganglion` package — its only
outputs are `web/assets/cli.json` and this directory. The CLI's own contract is
specified in [`cli_operator.md`](../../docs/tasks/cli_operator.md); this README is
the whole spec for the generator, as `doc_graph`'s README is for its extractor.
