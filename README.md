# Reflex Language Model POC

This POC tests whether a compact intermediate representation can replace full
tool schemas in the prompt while preserving tool-call accuracy.

The first implementation targets IoT light control and uses Qwen's
OpenAI-compatible DashScope API for JSON structured output.

For a research-team oriented summary of the verification results, see
[docs/poc_verification_report.md](docs/poc_verification_report.md).

The IoT dataset is generated deterministically and currently contains 500
cases.

```bash
python examples/iot_light/generate_dataset.py
```

## Run Offline Evaluation

The offline runner uses a deterministic rule-based model so tests and metrics
can run without API cost.

```bash
python -m rlm_poc.eval.runner --llm rules
```

## Run Qwen JSON DSL Evaluation

Set `DASHSCOPE_API_KEY` in the environment. The default model is
`qwen3.6-plus`.

```bash
python -m rlm_poc.eval.runner --llm qwen --limit 5
```

Additional Qwen comparison paths:

```bash
python -m rlm_poc.eval.runner --llm qwen-text
python -m rlm_poc.eval.runner --llm qwen-thinking
python -m rlm_poc.eval.runner --llm qwen-native
```

Optional environment variables:

```bash
export RLM_MODEL=qwen3.6-plus
export DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

## Design

```text
User Prompt
  -> Qwen JSON structured output
  -> JSON DSL validation
  -> deterministic tool-call emission
  -> mock IoT executor
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
