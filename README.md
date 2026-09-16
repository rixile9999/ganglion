# Ganglion

The proposed architecture for a continuously updated domain-specialized model
factory is documented in [Architecture v2](docs/architecture_v2.md), with an
[interactive diagram](web/architecture.html) and a
[standalone SVG](docs/diagrams/factory-v2.svg). This is a design proposal;
the POC implementation described below predates it.

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

```bash
# 1. fill a runs dir with two offline runs (no API key, no GPU) and analyse both
python -m ganglion.console seed  --runs runs/traces --limit 100

# 2. serve the pages and the API on http://127.0.0.1:8766
python -m ganglion.console serve --runs runs/traces --web web
```

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
