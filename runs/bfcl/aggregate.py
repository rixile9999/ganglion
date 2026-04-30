"""Aggregate BFCL replay run outputs into M1'-M4' tables.

Reads the per-case JSONL files in runs/bfcl/ and produces:
    - M1': accuracy + tokens per category, DSL vs native
    - M2': tool-count bin scaling (post-hoc on Phase D data)
    - M3': latency stats (Phase F repeats)
    - M4': repair on/off rescue rate (Phase G)

Run as:
    python runs/bfcl/aggregate.py [--out runs/bfcl/aggregated.json]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any


RUN_DIR = Path(__file__).parent
CATEGORIES = ("simple_python", "multiple", "parallel", "parallel_multiple", "irrelevance")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def merge_phase_c_d(path_label: str) -> list[dict[str, Any]]:
    """Concatenate Phase C + Phase D per-case rows for one path (DSL or native)."""
    rows: list[dict[str, Any]] = []
    rows.extend(load_jsonl(RUN_DIR / f"phase_c_{path_label}_cases.jsonl"))
    rows.extend(load_jsonl(RUN_DIR / f"phase_d_callable_{path_label}_cases.jsonl"))
    rows.extend(load_jsonl(RUN_DIR / f"phase_d_irrelevance_{path_label}_cases.jsonl"))
    return rows


def m1_tables(dsl: list[dict], native: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {"by_path": {}, "by_category": {}, "totals": {}}

    for label, rows in [("dsl", dsl), ("native", native)]:
        total = len(rows)
        ast_pass = sum(r["ast_valid"] for r in rows)
        syntax_pass = sum(r["syntax_valid"] for r in rows)
        in_tok = sum((r["input_tokens"] or 0) for r in rows)
        out_tok = sum((r["output_tokens"] or 0) for r in rows)
        dsl_chars = mean(r["dsl_chars"] for r in rows) if rows else 0
        nat_chars = mean(r["native_chars"] for r in rows) if rows else 0
        latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        out["by_path"][label] = {
            "total": total,
            "ast_match_rate": round(ast_pass / total, 4) if total else 0,
            "syntax_valid_rate": round(syntax_pass / total, 4) if total else 0,
            "input_tokens_total": in_tok,
            "output_tokens_total": out_tok,
            "input_tokens_mean": round(in_tok / total, 2) if total else 0,
            "output_tokens_mean": round(out_tok / total, 2) if total else 0,
            "dsl_chars_mean": round(dsl_chars, 2),
            "native_chars_mean": round(nat_chars, 2),
            "latency_p50": round(median(latencies), 2) if latencies else None,
            "latency_p95": _p95(latencies),
            "latency_mean": round(mean(latencies), 2) if latencies else None,
            "latency_stddev": round(pstdev(latencies), 2) if len(latencies) > 1 else None,
        }

    by_cat: dict[str, dict[str, Any]] = {}
    for label, rows in [("dsl", dsl), ("native", native)]:
        for cat in CATEGORIES:
            cat_rows = [r for r in rows if r["category"] == cat]
            if not cat_rows:
                continue
            stats = by_cat.setdefault(cat, {})
            ast_pass = sum(r["ast_valid"] for r in cat_rows)
            in_tok = sum((r["input_tokens"] or 0) for r in cat_rows)
            stats[label] = {
                "total": len(cat_rows),
                "ast_match_rate": round(ast_pass / len(cat_rows), 4),
                "input_tokens_mean": round(in_tok / len(cat_rows), 2),
            }
    out["by_category"] = by_cat

    # Apples-to-apples token reduction = (native_in - dsl_in) / native_in
    nat_in = out["by_path"]["native"]["input_tokens_total"]
    dsl_in = out["by_path"]["dsl"]["input_tokens_total"]
    if nat_in:
        out["totals"]["input_token_reduction"] = round((nat_in - dsl_in) / nat_in, 4)
    nat_out = out["by_path"]["native"]["output_tokens_total"]
    dsl_out = out["by_path"]["dsl"]["output_tokens_total"]
    if nat_out:
        out["totals"]["output_token_reduction"] = round((nat_out - dsl_out) / nat_out, 4)
    return out


def m2_bins(dsl: list[dict], native: list[dict]) -> dict[str, Any]:
    """Bin by per-case tool count to test scaling — Phase E."""
    bins = [(1, 1), (2, 5), (6, 15), (16, 50), (51, 200)]
    out: dict[str, Any] = {"bins": []}
    for low, high in bins:
        bin_label = f"{low}-{high}" if low != high else str(low)
        bin_data = {"range": bin_label, "low": low, "high": high}
        for label, rows in [("dsl", dsl), ("native", native)]:
            sub = [r for r in rows if low <= r["tool_count"] <= high]
            if not sub:
                continue
            ast_pass = sum(r["ast_valid"] for r in sub)
            in_tok = sum((r["input_tokens"] or 0) for r in sub)
            bin_data[label] = {
                "n": len(sub),
                "ast_match_rate": round(ast_pass / len(sub), 4),
                "input_tokens_mean": round(in_tok / len(sub), 2) if sub else 0,
                "dsl_chars_mean": round(mean(r["dsl_chars"] for r in sub), 2),
                "native_chars_mean": round(mean(r["native_chars"] for r in sub), 2),
            }
        out["bins"].append(bin_data)
    return out


def m3_latency() -> dict[str, Any] | None:
    """M3' latency from 5x repeat run — read summary if present."""
    out: dict[str, Any] = {}
    for label in ("dsl", "native"):
        path = RUN_DIR / f"phase_f_{label}_summary.json"
        if path.exists():
            out[label] = json.loads(path.read_text())
    return out or None


def m4_repair() -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for label in ("repair_on", "repair_off"):
        path = RUN_DIR / f"phase_g_{label}_summary.json"
        if path.exists():
            out[label] = json.loads(path.read_text())
    return out or None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * 0.95))
    return round(ordered[idx], 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=RUN_DIR / "aggregated.json")
    args = parser.parse_args()

    dsl_rows = merge_phase_c_d("dsl")
    native_rows = merge_phase_c_d("native")

    aggregated = {
        "m1_prime": m1_tables(dsl_rows, native_rows),
        "m2_prime": m2_bins(dsl_rows, native_rows),
        "m3_prime": m3_latency(),
        "m4_prime": m4_repair(),
    }
    args.out.write_text(json.dumps(aggregated, ensure_ascii=False, indent=2))
    print(f"wrote {args.out} (dsl={len(dsl_rows)} native={len(native_rows)})")


if __name__ == "__main__":
    main()
