"""Build BFCL v4 train split = upstream full minus the eval subsample.

Reads `full/BFCL_v4_<category>.json` and `full/possible_answer/...`, drops every
case whose `id` appears in `sample/<category>.jsonl`, and writes the remaining
cases to `train/<category>.jsonl` in the same merged shape that `subsample.py`
uses for `sample/`.

The eval subsample (500 cases) and this train split (740 cases) together cover
the entire upstream single-turn corpus with **zero overlap by `id`** — that is
the load-bearing invariant of the factory_bfcl_arc data layer. The script
re-asserts this every run and exits non-zero on drift.

Usage:
    python examples/bfcl/v4/build_train.py
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

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


def build_train(root: Path) -> dict:
    full_dir = root / "full"
    sample_dir = root / "sample"
    train_dir = root / "train"
    train_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, dict[str, int]] = OrderedDict()
    total_train = total_sample = total_full = 0

    for category in CATEGORIES:
        full_rows = _read_jsonl(full_dir / f"BFCL_v4_{category}.json")
        sample_ids = {
            json.loads(line)["id"]
            for line in (sample_dir / f"{category}.jsonl").read_text().splitlines()
            if line.strip()
        }

        missing = sample_ids - {row["id"] for row in full_rows}
        if missing:
            raise SystemExit(
                f"{category}: {len(missing)} sample ids missing from upstream full/. "
                f"SOURCE.md pinning is stale or sample/ was regenerated against a "
                f"different commit. First few missing: {sorted(missing)[:5]}"
            )

        train_rows = [row for row in full_rows if row["id"] not in sample_ids]
        train_rows.sort(key=lambda row: row["id"])

        answers_path = full_dir / "possible_answer" / f"BFCL_v4_{category}.json"
        answers_by_id: dict[str, dict] = {}
        if answers_path.exists():
            answers_by_id = _index_by_id(_read_jsonl(answers_path))

        out_path = train_dir / f"{category}.jsonl"
        with out_path.open("w") as fh:
            for row in train_rows:
                merged = dict(row)
                answer = answers_by_id.get(row["id"])
                if answer is not None:
                    merged["ground_truth"] = answer["ground_truth"]
                fh.write(json.dumps(merged, ensure_ascii=False) + "\n")

        train_count = len(train_rows)
        sample_count = len(sample_ids)
        full_count = len(full_rows)
        # Invariant: full == sample + train, disjoint by id.
        if full_count != sample_count + train_count:
            raise SystemExit(
                f"{category}: full {full_count} != sample {sample_count} + "
                f"train {train_count} — id overlap or drift detected."
            )

        stats[category] = {
            "full": full_count,
            "sample": sample_count,
            "train": train_count,
        }
        total_full += full_count
        total_sample += sample_count
        total_train += train_count

        print(
            f"{category:20s} full={full_count:4d}  sample={sample_count:4d}  "
            f"train={train_count:4d}  -> {out_path.name}"
        )

    stats["_total"] = {
        "full": total_full,
        "sample": total_sample,
        "train": total_train,
    }
    stats_path = train_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2) + "\n")
    print(f"\nTOTAL                full={total_full}  sample={total_sample}  train={total_train}")
    print(f"wrote {stats_path.relative_to(root.parent.parent.parent)}")
    return stats


if __name__ == "__main__":
    build_train(Path(__file__).parent)
