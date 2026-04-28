from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Protocol

from rlm_poc.eval.dataset import DEFAULT_DATASET, load_dataset
from rlm_poc.eval.metrics import CaseResult, summarize
from rlm_poc.runtime.qwen import (
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
)
from rlm_poc.runtime.rules import RuleBasedJSONDSLClient
from rlm_poc.runtime.types import ModelResult


class ModelClient(Protocol):
    def invoke(self, user_prompt: str) -> ModelResult:
        ...


def build_client(name: str) -> ModelClient:
    if name == "rules":
        return RuleBasedJSONDSLClient()
    if name == "qwen":
        return QwenJSONDSLClient()
    if name == "qwen-text":
        return QwenFreeformJSONDSLClient(enable_thinking=False)
    if name == "qwen-thinking":
        return QwenFreeformJSONDSLClient(enable_thinking=True)
    if name == "qwen-native":
        return QwenNativeToolClient()
    raise ValueError(f"unknown llm: {name}")


def run_eval(client: ModelClient, dataset_path: Path, limit: int | None) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in load_dataset(dataset_path, limit=limit):
        try:
            model_result = client.invoke(case.prompt)
            results.append(
                CaseResult(
                    id=case.id,
                    prompt=case.prompt,
                    expected=case.expected,
                    predicted=model_result.plan,
                    raw=model_result.raw,
                    latency_ms=model_result.latency_ms,
                    input_tokens=model_result.input_tokens,
                    output_tokens=model_result.output_tokens,
                )
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    id=case.id,
                    prompt=case.prompt,
                    expected=case.expected,
                    predicted=None,
                    raw=None,
                    latency_ms=None,
                    input_tokens=None,
                    output_tokens=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--llm",
        choices=["rules", "qwen", "qwen-text", "qwen-thinking", "qwen-native"],
        default="rules",
        help="Model path to evaluate.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL dataset path.",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    results = run_eval(build_client(args.llm), args.dataset, args.limit)
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
