# Ganglion

The proposed architecture for a continuously updated domain-specialized model
factory is documented in [Architecture v2](docs/architecture_v2.md), with an
[interactive diagram](web/architecture.html) and a
[standalone SVG](docs/diagrams/factory-v2.svg). The specification-driven
workbench and reversible text prototype now implement the core/optional-adapter
boundary; full automated factory and mobile deployment remain future work.

The Korean and multilingual PII demonstration is documented in
[Reversible long-document pseudonymization](docs/pseudonymization_pipeline_design.md).
It uses a Qwen3.5-0.8B backbone with typed extraction and decision heads,
optional document preprocessing and execution adapters, and targets offline
iPhone-class deployment after H100 training and evaluation.

## Workbench v2 and reversible text prototype

The new homepage uses `ApplicationSpec` input/result schemas for both
`TextEditPlan` and existing tool-calling `ActionPlan` programs. UI and CLI share
`/api/v2` specs, private chunk uploads, jobs, artifacts and feedback. No frontend
build step or hosted API is required for PII extraction.

```bash
pip install -e '.[pii-model]'
python -m ganglion.console serve --port 8767
```

Open <http://127.0.0.1:8767>. The H100 workspace already has the trained head
checkpoint. For the deterministic baseline without Torch, install `.[pii]`
and choose `pii-rules`. `GANGLION_PII_CHECKPOINT` selects a different checkpoint
directory; its pinned backbone must match the installed native adapter.

```bash
ganglion program list
ganglion program keygen --private private-key.json --public public-key.json
ganglion program run pii-qwen --file document.txt \
  --public-key public-key.json --output-dir masked
ganglion program restore masked/document.txt masked/recovery.bin \
  --key private-key.json --output restored.txt
```

To share the running UI's jobs and model cache, set
`GANGLION_CONSOLE_URL=http://127.0.0.1:8767` before using the CLI. Without it,
the CLI runs a local API for each command and waits for completion. Each output
root has one writer runtime; clients of an active server use its loopback URL.
Private keys are new files with mode `0600`. Keys and source text are omitted
from job metadata and PII traces; streamed source uploads are removed after
execution. Recovery requires the unedited output, encrypted sidecar and private
key. Restored plaintext is an explicit artifact; remove it when no longer needed.

The first native model freezes Qwen3.5-0.8B and learns a compact bidirectional
readout plus BIO/type heads. It emits spans/probabilities with **zero generated
output tokens**. Its versioned `PreprocessorSpec` feeds complete paragraphs
sequentially, limits each unit to 256 characters / 128 tokens, and uses sentence
or line boundaries before hard overlap splits. Budgets are part of the pinned
program spec, not per-request form fields. On a 89,989-byte synthetic document,
this changes exact-span F1 from **0.543** (fixed packed windows) to **0.863**,
using **27,277** input tokens instead of **31,219**. The rules baseline achieves
**1.000** on the same simple templates. All three recover the exact original
bytes. Current unbatched H100 processing takes approximately **79 seconds** for
the semantic model path; efficient runtime and actual-device work remain open.
This is an integration experiment, not evidence of model superiority or
production anonymity. Scores are uncalibrated. [Implementation and limits](docs/pseudonymization_pipeline_design.md#12-구현-현황-2026-10-08)
includes the experiment recipe, memory and streaming measurements. Actual
iPhone inference, quantization, independent real-document evaluation and
automatic validated rule expansion remain unimplemented.

Verification:

```bash
python -m pytest -q
python tools/benchmark_pii.py
# Optional real Chromium flow, after installing playwright and its browser:
python tools/smoke_workbench.py --native
```

The [CLI reference](docs/ganglion-cli.html) includes measured examples for
spec registration, core-only plans, execution, artifacts, recovery and feedback.

Failure analysis is an existing Ganglion capability: `analyzer/taxonomy.py`
classifies tool-calling failures, `corrections.py` attributes Interpreter
changes, and `rules.py` proposes rules through `analyze.py`. PII predictions now
use the installed `pii-text` adapter in `analyzer/domain_analysis.py`. It
classifies false positives, misses, type/boundary errors, splits, merges,
duplicates and invalid spans, and compares raw with interpreted predictions.
PII feedback returns and stores this coordinate-only analysis. A verdict alone
does not become gold; incomplete annotations do not receive a document-wide F1.
To score all predictions, explicitly submit a complete original UTF-8 byte
annotation with `"gold_complete": true`. User-submitted labels still await
independent validation; automatic PII rule synthesis and adoption remain open.

```json
{
  "verdict": "missed_pii",
  "expected_spans": [{"start": 8, "end": 17, "type": "PERSON"}],
  "gold_complete": false
}
```

Pass this JSON file to `ganglion program feedback JOB_ID feedback.json` or use
the workbench's feedback form. Old traces without raw UTF-8 offsets support
final-stage analysis; correction attribution is unavailable. Feedback analysis
is capped at 10,000 predicted spans per stage and reports an unavailable result
on overflow, leaving the streaming execution path independent of analysis size.

The training CLI now supports learned character boundaries inside tokenizer
units, legal BIO decoding, joint type evidence, validation-selected checkpoints
and optional LoRA. `diverse-v1` includes all five entity types, context negatives
and disjoint text/template/entity-value partitions. Existing v2 weights and
measurements remain an archived baseline; retraining uses a new directory.

```bash
python -m ganglion.domains.pii.dataset --output runs/pii/diverse-v1 \
  --count 6000 --recipe diverse-v1 --seed 42
python -m ganglion.domains.pii.train --data runs/pii/diverse-v1 \
  --output runs/pii/qwen-lora-experiment --lora-rank 16 --epochs 6 \
  --batch-size 32 --backbone-batch-size 64 --eval-every 1
GANGLION_PII_CHECKPOINT=runs/pii/qwen-lora-experiment \
  python -m ganglion.console serve --port 8767
```

These are experimental training instructions, not a 99.5% quality claim.
Reports include per-type errors and raw/final correction attribution. An
optional `--stop-file` JSON epoch cap is checked after completed epochs;
`--evaluate-only` evaluates the checkpoint in `--output` without optimization.

Released Presidio, GLiNER/GLiNER2, Piiranha and Korean E5 configurations were
also run on that unchanged test split. The [comparison protocol and measured
results](docs/pseudonymization_pipeline_design.md#124-동일-데이터-외부-기준선-실측)
include exact spans, language subgroups, masking coverage and concrete errors.
Model runners are in `tools/pii_baselines/`; score their saved captures with
`python tools/pii_baselines/compare.py --include-examples`. Source excerpts are
enabled only for a corpus whose manifest explicitly declares synthetic data.

The optional `pii-qwen-candidates` program adds a deterministic candidate IR,
a learned 491,814-parameter classification head over the frozen v4 encoder,
and compatible-span selection. It is an experiment combining preprocessing,
additional training and interpretation; validation gains do not establish an
input-only improvement or production anonymity. [Protocol and measurements](docs/pseudonymization_pipeline_design.md#125-구조화-후보-ir-파이프라인-실험)
separate proposal coverage, raw classification, interpreted results and fallback.
On the same frozen development test, exact F1 increased from 82.44% to 93.93%,
but full non-whitespace entity masking recall fell from 91.76% to 88.76%.
The candidate path used the same input tokens and ran about twice as slowly;
it is not promoted as a safer or more efficient default.
Candidate budgets are pinned in the program spec. Overflow falls back explicitly
to the fingerprinted source encoder; the original `pii-qwen` remains available.
When using `GANGLION_CONSOLE_URL`, set the candidate checkpoint environment
variable in the server process that owns the model cache.

```bash
# Train into a fresh directory; only train/validation are read here.
python -m ganglion.domains.pii.candidate_train \
  --data runs/pii/diverse-v1 \
  --source-checkpoint runs/pii/qwen-0.8b-v4-lora16 \
  --output runs/pii/qwen-candidates-reproduced --epochs 30 --patience 8
# After completing validation-only checkpoint/configuration selection:
PYTHONPATH=. python tools/benchmark_pii_candidates.py \
  --checkpoint runs/pii/qwen-candidates-reproduced --ready \
  --output runs/pii/candidate-reproduced/comparison.json --baseline-runtime
GANGLION_PII_CANDIDATE_CHECKPOINT=runs/pii/qwen-candidates-reproduced \
  ganglion program run pii-qwen-candidates --file document.txt \
  --public-key public-key.json --output-dir candidate-masked
```

The benchmark records model/configuration hashes before loading test data.
It uses the previously measured frozen development test, which is not a new
untouched external evaluation. Candidate values/context remain transient inputs;
ordinary traces and public plans contain source coordinates and counters.

For an implementation-independent formalization of contract-based tool
calling (contract, compiler, representation cost, constrained decoding,
correction, training, evaluation, and the factory objective) see
[Pipeline formalization](docs/pipeline_formalization.md)
([LaTeX source](docs/pipeline_formalization.tex)). The commit-specific
mapping of that notation onto this codebase, with defaults and measured
numbers, is in [Implementation notes](docs/pipeline_formalization_impl.md).
An MLSys-style paper draft that combines the IR/compiler results, the
small-model factory, and the failure-feedback loop is at
[docs/paper/ganglion_mlsys_draft.md](docs/paper/ganglion_mlsys_draft.md).

> *compiler-guided optimization for LLM tool calling*

Ganglion compiles verbose tool schemas into compact Action IRs that language
models can emit with lower token cost. The current POC uses a compact JSON DSL
as the first IR, then validates and emits deterministic tool calls from that IR.

The first implementation targets IoT light control and uses Qwen's
OpenAI-compatible DashScope API for JSON structured output. The Python package
namespace is `ganglion`.

For a research-team oriented summary of the verification results, see
[docs/poc_verification_report.md](docs/poc_verification_report.md).
For the BFCL v4 M1-M4 replay assessment, see
[docs/bfcl_m1_m4_result_report.md](docs/bfcl_m1_m4_result_report.md).
For the M5 abstention/no-call follow-up, see
[docs/bfcl_m5_abstention_report.md](docs/bfcl_m5_abstention_report.md).
For the schema-to-DSL compiler process, see
[docs/tool_schema_compiler.md](docs/tool_schema_compiler.md).

The IoT dataset is generated deterministically and currently contains 500
cases.

```bash
python examples/iot_light/generate_dataset.py
```

## Run Offline Evaluation

The offline runner uses a deterministic rule-based model so tests and metrics
can run without API cost.

```bash
python -m ganglion.cli --llm rules
```

## Run Qwen JSON DSL Evaluation

Set `DASHSCOPE_API_KEY` in the environment. The default model is
`qwen3.6-plus`.

```bash
python -m ganglion.cli --llm qwen --limit 5
```

Additional Qwen comparison paths:

```bash
python -m ganglion.cli --llm qwen-text --limit 2
python -m ganglion.cli --llm qwen-thinking --limit 2
python -m ganglion.cli --llm qwen-native --limit 2
```

Models can also be addressed by registry id from `configs/models.yaml`:

```bash
python -m ganglion.cli --model qwen3.6-plus@dashscope --models configs/models.yaml --limit 2
```

Optional environment variables:

```bash
export GANGLION_MODEL=qwen3.6-plus
export DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

## Operator Console

The console is a local web UI for driving one turn of the factory loop by
hand: ask a model something, see the Action IR it produced next to the tool
calls the catalog emitted, judge the result, and look at what the analyzer
proposes from the failures. It is a thin HTTP layer over the run directories
under `runs/traces/` — every page is a projection of files on disk, and every
write calls exactly one analyzer or `lm` primitive. It adds **no dependencies**: the
server is Python's standard library and the pages are vanilla JS with no build
step.

### Starting it

From the repo root, with the package installed (`pip install -e ".[dev]"`):

```bash
# 1. fill a runs dir with two offline runs (no API key, no GPU) and analyse both
python -m ganglion.console seed  --runs runs/traces --limit 100

# 2. serve the pages and the API
python -m ganglion.console serve --runs runs/traces
```

The second command prints the address to open and then stays in the
foreground; Ctrl-C stops it.

```
[console] http://127.0.0.1:8766  runs=runs/traces  web=<repo>/web  models=configs/models.yaml
```

Open that URL and pick a page from the toolbar. `--port` (or
`$GANGLION_CONSOLE_PORT`) moves it; the host is always loopback, so nothing is
exposed to the network. Seeding takes well under a second and is optional — the
pages open fine against an empty runs dir, they just have nothing to show. Use
a scratch path (`--runs /tmp/ganglion-runs`) while trying things out; `runs/` is
checked-in experiment data, so only seed into `runs/traces` when the run is
meant to be kept.

### A first pass through it

1. **`catalog.html`** — start here. Pick `iot_light_5` and read the same five
   tools three ways: the original spec, the Action IR the model actually
   receives, and the canonicalisation table of aliases and defaults that
   neither render shows.
2. **`chat.html`** — pick a catalog and a model, type a request
   (`거실 불 70%로 켜줘`), and send. You get the ordered tool calls, the raw
   Action IR, and F⁰ next to Fᴷ when the correction hooks changed anything.
   Then judge it in the verdict bar: `y` correct, `n` incorrect (edit the
   expected plan), `u` unsure. Every send is stored as a trace and every
   verdict as a label.
3. **`traces.html`** — open a seeded run and filter to `wrong only`. Move with
   `j`/`k`, label with `y`/`n`/`u`, and `?` lists the keys.
4. **`rules.html`** — press *Analyze* on `rules-degraded-seed`. The left column
   proposes `ToolSpec` patches from the failures you just looked at; decide
   each one. The right column attributes what the existing correction hooks
   rescued, and flags hooks that rescue nothing any more.
5. **`loops.html`** — the per-run view: manifest, KPIs, failure histogram, and
   the transition matrix against the parent run.

Model choice in step 2 comes from `configs/models.yaml`. `rules` is offline and
needs nothing. A DashScope entry needs `DASHSCOPE_API_KEY`. A `local_hf` entry
(`qwen3-0.6b@local`) runs in-process on the local GPU and needs the `[factory]`
extra (`pip install -e ".[factory]"`); the picker shows a Load button and the
first send downloads the weights. LoRA adapters found under `runs/` are listed
automatically.

Five pages are live against that API:

| Page | For |
|---|---|
| `chat.html` | Send a prompt to a chosen catalog + model, read the ordered tool calls, compare F⁰ (model alone) with Fᴷ (model + correction hooks), and label the result. Each send is stored as a trace. |
| `catalog.html` | Inspect one catalog three ways from the same `ToolSpec` traversal: the original spec (the submitted tool list for a compiled catalog, the OpenAI re-render for a builtin), the Action IR renders (DSL / OpenAI tools / JSON Schema / system prompt), and the canonicalisation table (aliases, defaults, strip, prompt correction). Also compiles a pasted OpenAI/MCP tool list into a catalog. |
| `traces.html` | The label queue: filter a run's traces (all / invalid / wrong / unlabelled / changed vs. parent), diff expected against predicted, and label with the keyboard. |
| `rules.html` | Rule lifecycle. Expansion: proposed `ToolSpec` patches, decided blind, then optionally after a preview, then ported with a commit sha. Contraction: F⁰/Fᴷ attribution per correction hook and which hooks are candidates for retirement. |
| `loops.html` | A rail of runs; for the selected one, its manifest, KPIs, failure histogram, the transition matrix against its parent run with a ΔEM confidence interval, correction tables and training provenance. |

The remaining sheets (`index`, `pipeline`, `evaluation`, `events`,
`observability`, `architecture`) are still static mockups and carry a "mock"
chip in their toolbar.

The design rationale is in
[docs/refine_proposal.md](docs/refine_proposal.md) (Korean), the screen
mockups it was drawn from are in [web/mockups/](web/mockups/), and the
implementation contract is [docs/tasks/console_operator.md](docs/tasks/console_operator.md).

## Design

```text
Tool schema / catalog
  -> compact JSON DSL prompt
  -> Qwen JSON structured output
  -> Action IR validation
  -> deterministic tool-call emission
  -> evaluation metrics
```

The JSON DSL uses a short shape:

```json
{
  "calls": [
    {
      "action": "set_light",
      "args": {
        "room": "living",
        "state": "on",
        "brightness": 70
      }
    }
  ]
}
```
