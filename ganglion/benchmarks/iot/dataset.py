from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ganglion.contract.catalog import Catalog
from ganglion.contract.parse import parse_json_dsl
from ganglion.contract.types import ActionPlan

DEFAULT_DATASET = Path("examples/iot_light/dataset.jsonl")
ADVERSARIAL_DATASET = Path("examples/iot_light/adversarial_cases.jsonl")

# Tiers whose expected plans are not the iot_light shape carry their own
# derived dataset. Every other tier reuses DEFAULT_DATASET (the intents are a
# subset of the larger catalogs — see docs/tasks/benchmark_iot.md).
TIER_DATASETS: dict[str, Path] = {
    "home_assistant_4": Path("examples/home_assistant/dataset.jsonl"),
}


def default_dataset_for(tier: str) -> Path:
    return TIER_DATASETS.get(tier, DEFAULT_DATASET)


@dataclass(frozen=True)
class EvalCase:
    id: str
    prompt: str
    expected: ActionPlan


def load_dataset(
    path: Path = DEFAULT_DATASET,
    limit: int | None = None,
    *,
    catalog: Catalog | None = None,
) -> list[EvalCase]:
    """Load ``EvalCase`` rows, parsing ``expected`` against ``catalog``.

    ``catalog=None`` keeps the historical behaviour (the ``iot_light_5``
    default catalog), which is correct for the three scaling tiers. Tiers
    whose expected plans use a different tool vocabulary (``home_assistant_4``)
    must pass their own catalog or every row fails with ``unsupported action``.
    """
    parse = parse_json_dsl if catalog is None else catalog.parse_json_dsl
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
                    expected=parse(row["expected"]),
                )
            )
            if limit is not None and len(cases) >= limit:
                break
    return cases
