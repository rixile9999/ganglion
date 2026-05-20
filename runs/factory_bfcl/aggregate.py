"""Aggregate Phase 1 + Phase 2 outputs into one compact table.

Reads:
  phase1/baseline/<cat>_dsl_summary.json       — M1' baseline DSL (DashScope)
  phase1/baseline/<cat>_native_summary.json    — M1' baseline native (DashScope)
  phase1/repair/<cat>_dsl_summary.json         — M4' repair DSL (DashScope)
  phase2/grammar/<cat>_mask_{off,on}/summary.json   — S1b grammar masking (local)
  phase2/sft/<cat>/eval_{holdout,full}/summary.json — S2a SFT (local)

Writes:
  aggregated.json — flat dict {cat: {stage: {metric: value}}}
  table.md       — markdown table (rows=categories, cols=stages)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path("/home/hyoseok/workspace/ganglion/runs/factory_bfcl")
CATS = ("simple_python", "multiple", "parallel", "parallel_multiple", "irrelevance")


def _load(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _ratio(d: dict | None, key: str) -> float | None:
    if not d:
        return None
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def collect() -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for cat in CATS:
        row: dict[str, dict[str, Any]] = {}

        b_dsl = _load(ROOT / "phase1" / "baseline" / f"{cat}_dsl_summary.json")
        if b_dsl:
            row["baseline_dsl"] = {
                "ast_match": _ratio(b_dsl, "ast_match_rate"),
                "syntax_valid": _ratio(b_dsl, "syntax_valid_rate"),
                "input_tokens": b_dsl.get("input_tokens_total"),
                "output_tokens": b_dsl.get("output_tokens_total"),
            }

        b_nat = _load(ROOT / "phase1" / "baseline" / f"{cat}_native_summary.json")
        if b_nat:
            row["baseline_native"] = {
                "ast_match": _ratio(b_nat, "ast_match_rate"),
                "syntax_valid": _ratio(b_nat, "syntax_valid_rate"),
                "input_tokens": b_nat.get("input_tokens_total"),
                "output_tokens": b_nat.get("output_tokens_total"),
            }

        rep = _load(ROOT / "phase1" / "repair" / f"{cat}_dsl_summary.json")
        if rep:
            row["repair_dsl"] = {
                "ast_match": _ratio(rep, "ast_match_rate"),
                "syntax_valid": _ratio(rep, "syntax_valid_rate"),
                "repair_attempts": rep.get("repair_attempts_total"),
                "repair_successes": rep.get("repair_successes_total"),
            }

        for mask in ("off", "on"):
            mask_summary = _load(ROOT / "phase2" / "grammar" / f"{cat}_mask_{mask}" / "summary.json")
            if mask_summary:
                row[f"mask_{mask}"] = {
                    "ast_match": _ratio(mask_summary, "ast_match_rate"),
                    "syntax_valid": _ratio(mask_summary, "syntax_valid_rate"),
                    "action_match": _ratio(mask_summary, "action_match_rate"),
                    "latency_p50_ms": mask_summary.get("latency_ms_p50"),
                }

        for split in ("holdout", "full"):
            sft = _load(ROOT / "phase2" / "sft" / cat / f"eval_{split}" / "summary.json")
            if sft:
                row[f"sft_{split}"] = {
                    "ast_match": _ratio(sft, "ast_match_rate"),
                    "syntax_valid": _ratio(sft, "syntax_valid_rate"),
                    "action_match": _ratio(sft, "action_match_rate"),
                    "latency_p50_ms": sft.get("latency_ms_p50"),
                    "n": sft.get("n"),
                }

        pc = _load(ROOT / "phase2" / "post_corr" / cat / "summary.json")
        if pc:
            row["post_corr"] = {
                "ast_match": _ratio(pc, "ast_match_rate"),
                "syntax_valid": _ratio(pc, "syntax_valid_rate"),
                "rules_applied": pc.get("rules_applied"),
            }

        # Phase 3
        for ver in ("v1_5", "v2"):
            for split in ("holdout", "full"):
                p = _load(ROOT / "phase3" / f"sft_{ver}" / cat / f"eval_{split}" / "summary.json")
                if p:
                    row[f"phase3_{ver}_{split}"] = {
                        "ast_match": _ratio(p, "ast_match_rate"),
                        "syntax_valid": _ratio(p, "syntax_valid_rate"),
                        "action_match": _ratio(p, "action_match_rate"),
                    }
            pc_full = _load(ROOT / "phase3" / "post_corr" / ver / cat / "summary.json")
            if pc_full:
                row[f"phase3_{ver}_post_corr_full"] = {
                    "ast_match": _ratio(pc_full, "ast_match_rate"),
                    "uplift_pp": pc_full.get("uplift_pp"),
                }
            pc_h = _load(ROOT / "phase3" / "post_corr_holdout" / ver / cat / "summary.json")
            if pc_h:
                row[f"phase3_{ver}_post_corr_holdout"] = {
                    "ast_match": _ratio(pc_h, "ast_match_rate"),
                    "uplift_pp": pc_h.get("uplift_pp"),
                }

        out[cat] = row
    return out


def render_md(data: dict[str, dict[str, dict[str, Any]]]) -> str:
    stages = [
        ("baseline_dsl",     "M1' DSL"),
        ("baseline_native",  "M1' Native"),
        ("repair_dsl",       "M4' Repair"),
        ("mask_off",         "S1b mask off"),
        ("mask_on",          "S1b mask on"),
        ("sft_holdout",      "S2a SFT v1 (h20)"),
        ("sft_full",         "S2a SFT v1 (f100)"),
        ("post_corr",        "S2c v1+post-corr"),
        ("phase3_v1_5_holdout",        "S3c v1.5 (h20)"),
        ("phase3_v1_5_full",           "S3c v1.5 (f100)"),
        ("phase3_v1_5_post_corr_holdout", "S3c v1.5+pc (h20)"),
        ("phase3_v1_5_post_corr_full",    "S3c v1.5+pc (f100)"),
        ("phase3_v2_holdout",          "S3e v2 (h20)"),
        ("phase3_v2_full",             "S3e v2 (f100)"),
        ("phase3_v2_post_corr_holdout",   "S3e v2+pc (h20)"),
        ("phase3_v2_post_corr_full",      "S3e v2+pc (f100)"),
    ]
    lines = ["# factory_bfcl results — Qwen3-0.6B", ""]
    lines.append("## AST match rate per stage")
    lines.append("")
    head = ["category"] + [label for _, label in stages]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("| " + " | ".join(["---"] * len(head)) + " |")
    for cat in CATS:
        row = data.get(cat, {})
        cells = [cat]
        for key, _ in stages:
            v = row.get(key, {}).get("ast_match")
            cells.append(f"{v:.3f}" if isinstance(v, (int, float)) else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Syntax valid rate per stage")
    lines.append("")
    lines.append("| " + " | ".join(head) + " |")
    lines.append("| " + " | ".join(["---"] * len(head)) + " |")
    for cat in CATS:
        row = data.get(cat, {})
        cells = [cat]
        for key, _ in stages:
            v = row.get(key, {}).get("syntax_valid")
            cells.append(f"{v:.3f}" if isinstance(v, (int, float)) else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def main():
    data = collect()
    (ROOT / "aggregated.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    (ROOT / "table.md").write_text(render_md(data))
    print(render_md(data))


if __name__ == "__main__":
    main()
