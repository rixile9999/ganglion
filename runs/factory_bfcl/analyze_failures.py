"""Failure-pattern analyzer for post-SFT BFCL evals.

Scans cases.jsonl from a categoy's SFT eval_full, isolates the rows where
predicted_plan is non-empty and ast_match=False, then categorizes the failure
by comparing predicted args to the BFCL accepted-list ground_truth.

Categories:
  W1 wrong_action_set      — predicted action names ≠ ground_truth action names
                              (post-corr can't help)
  W2 missing_required_arg  — predicted missing a key that's in gt spec
  W3 extra_arg             — predicted has a key not in gt spec
  W4 value_type_mismatch   — value is right semantic but wrong type (int vs str etc.)
  W5 value_string_case     — string value differs only in case/whitespace/aliases
  W6 value_numeric_off     — numeric value differs from any accepted value
  W7 value_wrong_choice    — string value doesn't match any accepted (real semantic)
  W8 list_order_or_count   — array arg ordering/length differs
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _accepted_values(gt_spec: dict, arg_name: str) -> list:
    """Return BFCL accepted list for an arg, or [] if not in spec."""
    return gt_spec.get(arg_name, [])


def _normalize_str(s):
    if not isinstance(s, str):
        return s
    return s.strip().lower()


def categorize(pred_call: dict, gt_call: dict) -> list[str]:
    """Return list of failure tags for one predicted call against one gt call."""
    tags: list[str] = []
    pred_args = pred_call.get("args", {}) or {}
    gt_fn, gt_spec = next(iter(gt_call.items()))
    if pred_call["action"] != gt_fn:
        return ["W1"]

    pred_keys = set(pred_args.keys())
    gt_keys = set(gt_spec.keys())

    # Required = keys whose accepted list does NOT contain "" sentinel (BFCL convention)
    required = {k for k, v in gt_spec.items() if "" not in v}

    if required - pred_keys:
        tags.append("W2")
    if pred_keys - gt_keys:
        tags.append("W3")

    common = pred_keys & gt_keys
    for k in common:
        pv = pred_args[k]
        accepted = _accepted_values(gt_spec, k)
        if not accepted:
            continue
        if pv in accepted:
            continue
        # type mismatch: type of pv != type of any accepted
        pv_types = {type(pv).__name__}
        acc_types = {type(a).__name__ for a in accepted if a != ""}
        if pv_types.isdisjoint(acc_types):
            tags.append("W4")
            continue
        # string case/whitespace
        if isinstance(pv, str):
            if any(isinstance(a, str) and _normalize_str(pv) == _normalize_str(a) for a in accepted):
                tags.append("W5")
                continue
            # not matching any accepted string
            tags.append("W7")
            continue
        # numeric drift
        if isinstance(pv, (int, float)):
            tags.append("W6")
            continue
        if isinstance(pv, list):
            tags.append("W8")
            continue
    return tags or ["WOK"]


def analyze_category(cat: str, cases_path: Path) -> dict:
    rows = [json.loads(l) for l in cases_path.read_text().splitlines() if l.strip()]
    failed_rows = [r for r in rows if not r["ast_match"] and r.get("predicted") is not None]

    tag_counter = Counter()
    examples: dict[str, list[dict]] = {}
    arg_examples: list[dict] = []

    for r in failed_rows:
        pred_calls = r["predicted"]
        gt_calls = r.get("ground_truth") or []

        # Order-independent pairing by action name (BFCL parallel grader does same)
        gt_by_action = {next(iter(gt.keys())): gt for gt in gt_calls}
        pred_by_action = {p["action"]: p for p in pred_calls}

        pred_action_set = sorted(pred_by_action.keys())
        gt_action_set = sorted(gt_by_action.keys())
        if pred_action_set != gt_action_set:
            tag_counter["W1"] += 1
            examples.setdefault("W1", []).append({
                "id": r["id"], "pred_actions": pred_action_set, "gt_actions": gt_action_set,
            })
            continue

        # Same actions, compare args call-by-call
        for action_name in pred_by_action:
            pc = pred_by_action[action_name]
            gc = {action_name: gt_by_action[action_name][action_name]}
            tags = categorize(pc, gc)
            for t in tags:
                tag_counter[t] += 1
                if len(examples.get(t, [])) < 3:
                    examples.setdefault(t, []).append({
                        "id": r["id"],
                        "action": action_name,
                        "pred_args": pc.get("args"),
                        "gt_spec": gc[action_name],
                    })

    return {
        "category": cat,
        "n_total": len(rows),
        "n_failed": len(failed_rows),
        "tags": dict(tag_counter.most_common()),
        "examples": examples,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="runs/factory_bfcl/phase2/sft",
                   help="Root dir containing <cat>/eval_full/cases.jsonl")
    p.add_argument("--out", default="runs/factory_bfcl/phase2/failure_analysis.json")
    args = p.parse_args()

    root = Path(args.root)
    out = {}
    for cat in ["simple_python","multiple","parallel","parallel_multiple","irrelevance"]:
        cases = root / cat / "eval_full" / "cases.jsonl"
        if not cases.exists():
            continue
        out[cat] = analyze_category(cat, cases)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Summary print
    for cat, a in out.items():
        print(f"\n== {cat} == failed={a['n_failed']}/{a['n_total']}")
        for tag, n in a["tags"].items():
            print(f"  {tag}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
