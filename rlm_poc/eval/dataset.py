from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rlm_poc.dsl.types import ActionPlan
from rlm_poc.dsl.validator import parse_json_dsl

DEFAULT_DATASET = Path("examples/iot_light/dataset.jsonl")


@dataclass(frozen=True)
class EvalCase:
    id: str
    prompt: str
    expected: ActionPlan


def load_dataset(path: Path = DEFAULT_DATASET, limit: int | None = None) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row: dict[str, Any] = json.loads(line)
            cases.append(
                EvalCase(
                    id=row["id"],
                    prompt=row["prompt"],
                    expected=parse_json_dsl(row["expected"]),
                )
            )
            if limit is not None and len(cases) >= limit:
                break
    return cases
