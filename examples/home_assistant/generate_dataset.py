"""Derive ``examples/home_assistant/dataset.jsonl`` from the iot_light dataset.

Deterministic: every row of ``examples/iot_light/dataset.jsonl`` is parsed
with the ``iot_light_5`` catalog, projected through
``ganglion.contract.builtins.home_assistant.translate_plan``, and written out
when the projection exists. Rows whose intent Home Assistant's Assist API
cannot express (``schedule_light``, ``create_scene``) are skipped and counted
on stderr. Never hand-edit the output; rerun this script.

See ``docs/tasks/contract_tier_home_assistant.md``.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from ganglion.benchmarks.iot.dataset import load_dataset
from ganglion.contract.builtins.home_assistant import CATALOG, translate_plan

HERE = Path(__file__).resolve().parent
SOURCE_PATH = HERE.parent / "iot_light" / "dataset.jsonl"
DATASET_PATH = HERE / "dataset.jsonl"


def derive_rows(source: Path = SOURCE_PATH) -> tuple[list[dict], Counter]:
    rows: list[dict] = []
    skipped: Counter = Counter()
    for case in load_dataset(source):
        plan = translate_plan(case.expected)
        if plan is None:
            skipped[case.expected.calls[0].action] += 1
            continue
        expected = plan.to_jsonable()
        # Round-trip guard: the derived expectation must validate against the
        # tier it is written for.
        assert CATALOG.parse_json_dsl(expected) == plan, case.id
        rows.append(
            {
                "id": f"ha-{len(rows) + 1:03d}",
                "source_id": case.id,
                "prompt": case.prompt,
                "expected": expected,
            }
        )
    return rows, skipped


def main() -> None:
    rows, skipped = derive_rows()
    with DATASET_PATH.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    actions = Counter(row["expected"]["calls"][0]["action"] for row in rows)
    print(f"wrote {len(rows)} rows to {DATASET_PATH}", file=sys.stderr)
    print(f"by action: {dict(actions)}", file=sys.stderr)
    print(f"skipped (unsupported by Assist): {dict(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main()
