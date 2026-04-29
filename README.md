# ToolCallOpt

> *compiler-guided optimization for LLM tool calling*

ToolCallOpt compiles verbose tool schemas into compact Action IRs that language
models can emit with lower token cost. The current POC uses a compact JSON DSL
as the first IR, then validates and emits deterministic tool calls from that IR.

The first implementation targets IoT light control and uses Qwen's
OpenAI-compatible DashScope API for JSON structured output. The Python package
namespace is still `ganglion` during the rename transition.

For a research-team oriented summary of the verification results, see
[docs/poc_verification_report.md](docs/poc_verification_report.md).
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
python -m ganglion.eval.runner --llm rules
```

## Run Qwen JSON DSL Evaluation

Set `DASHSCOPE_API_KEY` in the environment. The default model is
`qwen3.6-plus`.

```bash
python -m ganglion.eval.runner --llm qwen --limit 5
```

Additional Qwen comparison paths:

```bash
python -m ganglion.eval.runner --llm qwen-text
python -m ganglion.eval.runner --llm qwen-thinking
python -m ganglion.eval.runner --llm qwen-native
```

Optional environment variables:

```bash
export GANGLION_MODEL=qwen3.6-plus
export DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

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
