from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Protocol

from rlm_poc.dsl.catalog import Catalog
from rlm_poc.eval.dataset import DEFAULT_DATASET, load_dataset
from rlm_poc.eval.metrics import CaseResult, RunResult, summarize
from rlm_poc.runtime.qwen import (
    QwenFreeformJSONDSLClient,
    QwenJSONDSLClient,
    QwenNativeToolClient,
    RepairConfig,
)
from rlm_poc.runtime.rules import RuleBasedJSONDSLClient
from rlm_poc.runtime.types import ModelResult
from rlm_poc.schema import get_catalog


class ModelClient(Protocol):
    def invoke(self, user_prompt: str) -> ModelResult: ...


def build_client(
    name: str,
    catalog: Catalog,
    *,
    repair: RepairConfig | None = None,
) -> ModelClient:
    if name == "rules":
        return RuleBasedJSONDSLClient()
    if name == "qwen":
        return QwenJSONDSLClient(catalog=catalog, repair=repair)
    if name == "qwen-text":
        return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=False)
    if name == "qwen-thinking":
        return QwenFreeformJSONDSLClient(catalog=catalog, enable_thinking=True)
    if name == "qwen-native":
        return QwenNativeToolClient(catalog=catalog)
    raise ValueError(f"unknown llm: {name}")


def run_eval(
    client: ModelClient,
    dataset_path: Path,
    limit: int | None,
    *,
    repeat: int = 1,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in load_dataset(dataset_path, limit=limit):
        runs: list[RunResult] = []
        for _ in range(max(1, repeat)):
            try:
                model_result = client.invoke(case.prompt)
                runs.append(
                    RunResult(
                        plan=model_result.plan,
                        raw=model_result.raw,
                        latency_ms=model_result.latency_ms,
                        input_tokens=model_result.input_tokens,
                        output_tokens=model_result.output_tokens,
                    )
                )
            except Exception as exc:
                runs.append(
                    RunResult(
                        plan=None,
                        raw=None,
                        latency_ms=None,
                        input_tokens=None,
                        output_tokens=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        results.append(
            CaseResult(
                id=case.id,
                prompt=case.prompt,
                expected=case.expected,
                runs=tuple(runs),
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
        "--tier",
        default="iot_light_5",
        help="Catalog tier: iot_light_5 | home_iot_20 | smart_home_50.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL dataset path.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each case N times for latency stats (M3).",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Enable validator-error repair loop (qwen path only, M4).",
    )
    parser.add_argument(
        "--repair-max-attempts",
        type=int,
        default=1,
        help="Max repair retry attempts after the initial call.",
    )
    args = parser.parse_args()

    catalog = get_catalog(args.tier)
    repair = RepairConfig(
        enabled=args.repair,
        max_attempts=max(1, args.repair_max_attempts),
    )
    client = build_client(args.llm, catalog, repair=repair)
    results = run_eval(client, args.dataset, args.limit, repeat=args.repeat)
    summary = summarize(results)
    summary["tier"] = args.tier
    summary["llm"] = args.llm
    summary["dsl_catalog_chars"] = len(catalog.render_json_dsl())
    summary["openai_tools_chars"] = len(json.dumps(catalog.render_openai_tools()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
