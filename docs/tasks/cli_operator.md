[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[console_operator]] · [[analyzer_catalogs]] · [[lm_request_serve]]

# cli_operator (consumer)

[[console_operator]] originally exposed 22 `/api/*` routes, of which only three (`analyze`, `compare`, `export-labels`) had a terminal entry point in `ganglion/console/__main__.py`. This task supplies the terminal half of that surface and the Architecture v2 program routes: one `ganglion` executable whose commands project the shared console route table. ApiBridge calls `ConsoleAPI.handle()` in-process by default, or a running loopback console when `GANGLION_CONSOLE_URL` is set.

It is a *consumer*, like `benchmarks/` and `console/`, not a fourth module. It owns no verdict: every status, histogram, gold join, `repeat_index` and event row comes from the primitive the console route already calls.

## Role

Expose the [[console_operator]] route table as a `ganglion <noun> <verb>` CLI that resolves catalog / model / session / run from a persisted context, renders each payload for a terminal, and computes nothing the route table does not already return.

## Scope

- **in-scope**:
  - Package `ganglion/ctl/`, standard library only (`argparse`, `json`, `unicodedata`, `pathlib`):
    - `bridge.py` — `ApiBridge`: `get(path, **query)` / `post(path, body)` return shared route payloads and raise `CtlError(status, error, detail)` on non-2xx. With `GANGLION_CONSOLE_URL=http://127.0.0.1:8766`, requests reach the existing server over HTTP; only loopback HTTP endpoints are accepted. Otherwise ConsoleAPI is created lazily in-process and closed on exit. **The single seam**: no `ctl` module imports an analyzer / lm / contract primitive directly. Remote requests use the server's runs directory, models and labeler rather than replacing them with local CLI settings.
    - `context.py` — `Context` + `.ganglion/context.json`. Resolution order per field: **flag → environment → context file → default**. Fields `catalog` / `model` / `session` / `run` (env `GANGLION_CATALOG` / `GANGLION_CTL_MODEL` / `GANGLION_SESSION` / `GANGLION_RUN`; the model field deliberately **not** `GANGLION_MODEL`, which `configs/models.yaml` already expands as the DashScope served-model name), plus `runs_dir` (`GANGLION_RUNS`, default `runs/traces`) and `models_path` (`--models`, else the registry's own `$GANGLION_MODELS` → `configs/models.yaml` resolution). `Context.require(field)` raises `CtlError(400, "missing_context", …)` naming `ganglion use`.
    - `render.py` — `Output` (the one place a payload becomes stdout bytes) plus pure formatters: `Column` / `table` / `kv` / `plan_lines` / `bar` / `truncate` / `fmt` / `width`. `width` counts `east_asian_width in "WF"` as 2 columns, because every IoT-tier prompt is Korean and `len()` misaligns the table.
    - `main.py` — global argv routing, `common_parser()` (the parent parser every leaf inherits), `Deps`, the `health` and `use` commands, and the `CtlError` → stderr → exit 1 boundary.
    - `commands/{catalogs,models,chat,runs,traces,labels,patches,programs}.py` — one module per noun. Each exposes `add_parser(sub, common) -> None`, registering its top-level parser(s) and setting `_handler` on every leaf via `set_defaults`; a handler is `(argparse.Namespace, Deps) -> int`. `program` consumes Architecture v2 ApplicationSpecs through the shared ProgramService routes.
  - Command surface (`[x]` = optional positional falling back to context; every leaf also accepts the common flags):

    | Command | Route(s) | Kind |
    |---|---|---|
    | `ganglion health` | `GET /api/health` | P |
    | `ganglion use [--catalog] [--model] [--session] [--run] [--runs] [--clear] [--show]` | none — writes `.ganglion/context.json` | local |
    | `ganglion catalog list` | `GET /api/catalogs` | P |
    | `ganglion catalog show [catalog_id] [--part all\|describe\|dsl\|tools\|prompt\|schema\|source]` | `GET /api/catalogs/{id}` | P |
    | `ganglion catalog compile <tools.json\|-> [--name] [--allow-empty-calls]` | `POST /api/catalogs/compile` | W |
    | `ganglion model list` | `GET /api/models?catalog_id=` | P |
    | `ganglion model health [model_id]` | `GET /api/models/{id}/health` | P |
    | `ganglion model load [model_id]` | `POST /api/models/{model_id}/load` | W |
    | `ganglion model unload [model_id]` | `POST /api/models/{model_id}/unload` | W |
    | `ganglion session new` | `POST /api/sessions` | W |
    | `ganglion ask <prompt…> [--new-session] [--repair] [--repair-max-attempts N] [--raw]` | `POST /api/sessions` (only when no session is resolvable or `--new-session`) then `POST /api/chat` | W |
    | `ganglion run list` | `GET /api/runs?catalog_id=` | P |
    | `ganglion run show [run_id]` | `GET /api/runs/{c}/{r}` | P |
    | `ganglion run analyze [run_id]` | `POST /api/runs/{c}/{r}/analyze` | W |
    | `ganglion run events [run_id] [--tail N] [--name EVENT]` | `GET /api/runs/{c}/{r}/events` | P |
    | `ganglion compare <run_a> <run_b> [--allow-diff] [--per-case]` | `GET /api/compare` | W† |
    | `ganglion trace list [run_id] [--filter all\|invalid\|wrong\|unlabelled\|changed] [--failure-type] [--parent] [--limit] [--offset]` | `GET /api/runs/{c}/{r}/traces` | P |
    | `ganglion trace show <trace_id>` | `GET /api/runs/{c}/{r}/traces/{trace_id}` | P |
    | `ganglion label add <trace_id> --verdict V [--expected PATH\|-] [--endorsed/--not-endorsed] [--saw-f0] [--note] [--failure-hint] [--unordered] [--supersedes]` | `POST /api/labels` | W |
    | `ganglion label export [catalog_id] [--zones train]` | `GET /api/export/labels` | W† |
    | `ganglion patch list [run_id]` | `GET /api/runs/{c}/{r}/patches` | P |
    | `ganglion patch decide <patch_id> --stage S --decision D [--reason]` | `POST /api/patches/{id}/decision` | W |
    | `ganglion patch ported <patch_id> --commit SHA` | `POST /api/patches/{id}/ported` | W |
    | `ganglion program list` | `GET /api/v2/specs` | P |
    | `ganglion program show <spec_id>` | `GET /api/v2/specs/{spec_id}` | P |
    | `ganglion program register <spec.json>` | `POST /api/v2/specs` | W |
    | `ganglion program run <spec_id> [--inputs FILE] [--file FILE] [--public-key FILE] [--plan] [--output-dir DIR]` | `POST /api/v2/uploads`, `POST /api/v2/jobs`, `GET /api/v2/jobs/{job_id}`, `GET /api/v2/jobs/{job_id}/artifacts/{name}`, `POST /api/v2/uploads/{upload_id}/discard` | W |
    | `ganglion program jobs` | `GET /api/v2/jobs` | P |
    | `ganglion program job <job_id>` | `GET /api/v2/jobs/{job_id}` | P |
    | `ganglion program cancel <job_id>` | `POST /api/v2/jobs/{job_id}/cancel` | W |
    | `ganglion program artifact <job_id> <name> --output FILE` | `GET /api/v2/jobs/{job_id}/artifacts/{name}` | P |
    | `ganglion program keygen --private FILE --public FILE` | `POST /api/v2/keys` | W |
    | `ganglion program restore <document> <recovery> --key FILE --output FILE` | `POST /api/v2/uploads`, `POST /api/v2/restore`, `GET /api/v2/jobs/{job_id}/artifacts/{name}`, `POST /api/v2/uploads/{upload_id}/discard` | W |
    | `ganglion program feedback <job_id> <feedback.json>` | `POST /api/v2/jobs/{job_id}/feedback` | W |

  - Common flags on every leaf (`parents=[common_parser()]`): `--runs`, `--models`, `--catalog`, `--model`, `--session`, `--run`, `--labeler`, and the mutually exclusive `--json` / `--jsonl`. They are registered on leaves only, never on the top-level parser, so a leaf default cannot shadow a value given before the subcommand.
  - Output contract: default mode renders a terminal projection; `--json` prints the route payload **verbatim** (`json.dumps(indent=2, ensure_ascii=False)`) so scripts bind to the console's shapes and not to `ctl`; `--jsonl` prints one JSON object per row of the payload's primary list. File-producing `program keygen`, `artifact` and `restore` return local destination metadata instead of printing private keys or binary chunks. `program run` returns the completed job payload after polling.
  - Program runtime ownership: a runs root has one active writer process. If the UI server is already running, set `GANGLION_CONSOLE_URL` before running CLI mutations so both consumers share its queue and loaded model. `GANGLION_PII_CHECKPOINT` configures the native checkpoint in the owning process; default `runs/pii/qwen-0.8b-v2`.
  - Document preprocessing is an optional versioned spec, not runtime fields in the built-in program input form: `{"adapter":"utf8-windows","version":1,"config":{"strategy","max_chars","overlap_chars","max_tokens"}}`. `program show` exposes these settings and `program register` validates them. The rule baseline uses fixed 1024-character windows with 128-character overlap; the native model uses semantic paragraph/sentence splitting with budgets of 256 characters, 64-character overlap and 128 tokens. Hard overlapping windows are the fallback when a semantic unit exceeds its budget. Legacy `"utf8-windows"` aliases remain compatible with earlier custom specs. `--plan` omits execution; an execution run also publishes a public `plan.jsonl` containing UTF-8 byte coordinates and entity types, without original PII strings.
  - `program feedback` reads `{"verdict", "expected_spans"?, "gold_complete"?}`. Gold spans contain only `{start,end,type}` with UTF-8 byte coordinates. A verdict without spans is unclassified; an explicit span list is partial unless `gold_complete: true` declares exhaustive annotation. Partial gold does not establish false positives, precision or F1. The shared domain analyzer reports exact matches and typed failures, with rescue/regression attribution when raw predictions and gold are available. Feedback remains awaiting independent validation and does not automatically update rules or model weights.
  - `[project.scripts] ganglion = "ganglion.ctl.main:main"`; the dead `ganglion-factory = "ganglion.factory.cli:main"` entry (the `ganglion/factory/` sub-package was removed in the redesign) is dropped.
  - `.ganglion/` added to `.gitignore` — the context file is per-checkout operator state, never committed.
  - Tests: `tests/test_ctl_smoke.py` (Phase 0 — parser, context, render, error boundary), `tests/test_ctl_catalog_model.py`, `tests/test_ctl_runs_traces.py`, `tests/test_ctl_labels_patches.py`, `tests/test_ctl_chat.py`. All run offline against a `tmp_path` runs dir seeded by `ganglion.console.seed.seed_runs` or the analyzer fixtures, with the `rules` model.
- **out-of-scope**:
  - Re-implementing any route. No `ctl` module computes a status, histogram, precision, gold join, preview, fingerprint or plan equality; it does not import `TraceStore`, `LabelStore`, `DecisionStore`, `resolve_gold`, `analyze_run`, `compare_runs` or `Server`. If a command needs a value no route returns, the route is what changes ([[console_operator]] §on violation), not `ctl`.
  - The `python -m ganglion.cli` benchmark runner and the `serve` / `seed` subcommands: wrapping them as `ganglion bench` / `ganglion serve` / `ganglion seed` is a later convergence step. Until then both existing entry points keep working verbatim and `python -m ganglion.console <sub>` keeps its JSON-to-stdout default.
  - Remote hosts, endpoint authentication and a `--remote` flag. The supported HTTP transport is selected by `GANGLION_CONSOLE_URL` and restricted to a local loopback console.
  - Shell completion, colour, TTY detection, pagers, `$PAGER`, spinners, interactive prompts. Output is plain text on a dumb terminal; a command never blocks on input.
  - Auth, remote binding, multi-user — inherited from [[console_operator]]: loopback and the local filesystem only.
  - Editing `web/` or `ganglion/console/api.py` route semantics; `ctl` is a second consumer of that table, not a fork of it.
  - Retention / GC of `runs/traces/**`, and any write to a run bundle that is not the primitive one route calls.
- **on violation**: domain computation belongs in the owning service, never in `ctl`. Legacy commands use one `ApiBridge` call plus rendering, except `ask` (session then chat). Architecture v2 `program run` and `program restore` additionally orchestrate the same bounded upload, job polling and artifact download protocol as the workbench; key files and downloaded artifacts are local client outputs. They never import a detector, interpreter, executor or training primitive.

## Procedure

```
main(argv):
    parser ← top-level; common ← common_parser()
    for module in (catalogs, models, chat, runs, traces, labels, patches, programs): module.add_parser(sub, common)
    args ← parser.parse_args(argv)                       # argparse exits 2 on a usage error
    ctx  ← load_context(flags=args, env=os.environ, file=.ganglion/context.json)
    out  ← Output(mode from --json/--jsonl)
    with ApiBridge(ctx.runs_dir, ctx.models_path, labeler=args.labeler) as api:
        try:    return args._handler(args, Deps(api, ctx, out))
        except CtlError as exc:  print to stderr; return 1

<any P command>:   api.get(route, **query)  → out.emit(payload, renderer)
<any W command>:   api.post(route, body)    → out.emit(payload, renderer)
ganglion use:      merge flags into .ganglion/context.json; print the resolved context
ganglion ask:      session ← args.session or ctx.session; if absent or --new-session:
                       session ← api.post("/api/sessions", {catalog_id, model_id, repair…})["session_id"]
                       save_context(session=session)     # so the next `ask` continues the same run
                   api.post("/api/chat", {session_id, catalog_id, model_id, prompt, repair…})
```

`ApiBridge` constructs `ConsoleAPI` lazily on the first `get`/`post`, so `ganglion --help`, a usage error and `ganglion use` never start a `Writer` thread; `close()` is unconditional in the `finally`, because a leaked writer thread hangs the process at exit.

## Contract

- **in**: argv; `.ganglion/context.json`; `GANGLION_RUNS` / `GANGLION_CATALOG` / `GANGLION_CTL_MODEL` / `GANGLION_SESSION` / `GANGLION_RUN` / `GANGLION_MODELS` / `GANGLION_LABELER`; the run-bundle tree and the model registry, both read **only** through `ApiBridge`.
- **out**:
  - stdout: in `--json` mode the route payload verbatim; in `--jsonl` mode one row per line; otherwise a terminal rendering. stderr: `error: <error> — <detail>` for a `CtlError`, nothing else.
  - exit status: `0` success; `1` any non-2xx route response, a missing context field, or a **recorded model failure** (`ask` only — the route answers 200 with `error` set because a failed inference is still a trace, but the command produced no plan); `2` argparse usage error; `130` `KeyboardInterrupt`.
  - `.ganglion/context.json` — `{"catalog","model","session","run","runs_dir"}`, written only by `ganglion use`, `ganglion session new`, and `ganglion ask`'s implicit session creation. `models_path` is never persisted — it stays a per-invocation flag so a context file cannot freeze one checkout's registry.
  - Files under the runs dir: **exactly** those the invoked route writes, by the primitive it calls. A `P` command creates no file, including no directory.
- **event**: emit none directly. Every ledger row is emitted by the primitive behind the route ([[analyzer_event_ledger]]'s closed vocabulary): `contract.catalog.compiled`, `analyzer.run.recorded`, `lm.inference.{completed,failed}`, `analyzer.trace.recorded`, `analyzer.label.recorded`, `analyzer.{failure.classified,rule.proposed,correction.attributed}`, `analyzer.patch.decided`, `analyzer.compare.completed`. `ganglion run events` is the read side of the same rows.
- **failure**:

  | signal | branch |
  |---|---|
  | route answers non-2xx | `CtlError(status, error, detail)` → `error: <error> — <detail>` on stderr, exit 1. The route's own `{"error","detail"}` is never reworded |
  | required context field unresolved | `CtlError(400, "missing_context", "…; set it with --<field> or `ganglion use --<field> …`")`, exit 1, no route call |
  | `--json` and `--jsonl` together | argparse mutually-exclusive error, exit 2 |
  | `catalog compile` file unreadable / not JSON | `CtlError(400, "bad_input", …)` before the route call; an invalid tool list is the route's `422 invalid_tools` |
  | `label add --expected` file unreadable / not JSON | same; a plan the catalog rejects is the route's `422 invalid_expected_plan`, never auto-fixed |
  | `bfcl/*` catalog | whatever the route answers (`404 unknown_catalog` for `catalog show`, `409` for `run analyze`) surfaced unchanged — never a silent success |
  | `ask` against a `local_hf` model this machine cannot run | the route's `503 model_unavailable`, exit 1 |
  | `ask` where the model emitted no valid plan | the route's 200 payload is printed in full (`raw` / `attempts` / `raw_plan` survive) and the trace is recorded; exit 1 |
  | writer thread dead | the route's `503 writer_unavailable`, exit 1 |
  | `KeyboardInterrupt` | exit 130 after `ApiBridge.close()` |

- **success** (`pytest tests/test_ctl_*.py`): every leaf's `--help` exits 0 and every leaf carries the common flags; context resolves flag > env > file > default and `use --clear` empties the file; `width("거실 불 켜줘") == 2 * 6 + …` so a Korean prompt column aligns; every `P` command leaves the runs dir's `(path, size, mtime)` set unchanged; every command's `--json` output equals `ConsoleAPI.handle()`'s payload for the same route and arguments, byte for byte; the offline end-to-end chain `seed_runs` → `ask` → `trace list --filter unlabelled` → `label add` → `run analyze` → `patch list` → `patch decide` → `compare` → `label export` completes with the `rules` model and no `DASHSCOPE_API_KEY`, each `W` step appending exactly the rows its primitive declares; a non-2xx route yields exit 1 with the route's own `error` string on stderr and nothing on stdout.

## Observation

- `ctl_commands_unreachable` = leaves in the command table with no test that invokes them ÷ leaves — must be 0; a leaf reachable only by hand is how a route shape drifts unnoticed.
- `ctl_payload_drift` = leaves whose `--json` output differs from the route payload — must be 0. Non-zero means `ctl` started reshaping, which is the first step toward re-deriving.
- `primitive_imports_in_ctl` = modules under `ganglion/ctl/` importing `ganglion.{analyzer,lm,contract}.*` — must be 0 (`bridge.py`'s `ganglion.console.api` import is the only permitted crossing).
- `p_command_writes` = files created under the runs dir by a `P` command — must be 0; the same invariant [[console_operator]] asserts for its GETs, re-asserted through the second consumer.
- `missing_context_rate` = `CtlError(400, "missing_context")` ÷ invocations. Persistently high means `ganglion use` is not discoverable enough, which is a help-text defect, not an operator one.
- `writer_threads_leaked` = processes exiting with a live `Writer` — must be 0; a `ganglion` that hangs after printing is this metric.

Related: [[console_operator]], [[analyzer_catalogs]], [[analyzer_label_store]], [[analyzer_patch_decision]], [[analyzer_compare]], [[analyzer_analyze]], [[analyzer_event_ledger]], [[analyzer_run_manifest]], [[lm_request_serve]], [[lm_model_registry]], [[contract_describe]], [[contract_patch_apply]].
