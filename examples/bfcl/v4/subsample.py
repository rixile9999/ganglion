"""Deterministic sub-sampler for BFCL v4 data.

Reads `full/BFCL_v4_<category>.json` (and matching `possible_answer/...` when
present), draws 100 cases per category with a fixed seed, and writes merged
records to `sample/<category>.jsonl`.

Usage:
    python examples/bfcl/v4/subsample.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 42
SAMPLE_SIZE = 100
CATEGORIES = (
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _index_by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def subsample(root: Path) -> None:
    full_dir = root / "full"
    sample_dir = root / "sample"
    sample_dir.mkdir(parents=True, exist_ok=True)

    for category in CATEGORIES:
        questions = _read_jsonl(full_dir / f"BFCL_v4_{category}.json")
        rng = random.Random(SEED)
        chosen = rng.sample(questions, k=min(SAMPLE_SIZE, len(questions)))
        chosen.sort(key=lambda row: row["id"])

        answers_path = full_dir / "possible_answer" / f"BFCL_v4_{category}.json"
        answers_by_id: dict[str, dict] = {}
        if answers_path.exists():
            answers_by_id = _index_by_id(_read_jsonl(answers_path))

        out_path = sample_dir / f"{category}.jsonl"
        with out_path.open("w") as fh:
            for row in chosen:
                merged = dict(row)
                answer = answers_by_id.get(row["id"])
                if answer is not None:
                    merged["ground_truth"] = answer["ground_truth"]
                fh.write(json.dumps(merged, ensure_ascii=False) + "\n")
        print(f"{category}: {len(chosen)} cases -> {out_path.name}")


if __name__ == "__main__":
    subsample(Path(__file__).parent)
