"""Apply Phase 2 S2c 11-rule post-correction to Phase 3 v1.5 / v2 eval outputs.

Reads each `runs/factory_bfcl/phase3/{sft_v1_5,sft_v2}/<cat>/eval_full/cases.jsonl`
and writes `phase3/post_corr/{v1_5,v2}/<cat>/summary.json` with the after-rule
metrics so the aggregator can pick up `phase3_post_corr_*` columns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
from post_correction import correct_category

ROOT = Path("/home/hyoseok/workspace/ganglion/runs/factory_bfcl/phase3")

for src in ("sft_v1_5", "sft_v2"):
    label = src.replace("sft_", "")
    for cat in ("simple_python", "multiple", "parallel", "parallel_multiple", "irrelevance"):
        cases = ROOT / src / cat / "eval_full" / "cases.jsonl"
        if not cases.exists():
            continue
        out = ROOT / "post_corr" / label / cat
        correct_category(cat, cases, out)
