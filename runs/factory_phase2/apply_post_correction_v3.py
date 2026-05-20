"""Apply the 11-rule BFCL post-correction (ported from `runs/factory_bfcl/post_correction.py`)
to the feature-arc V3 adapter's per-case predictions and re-grade on the clean 500-case eval.

Differences vs the per-category script in `runs/factory_bfcl/post_correction.py`:
- Input shape: feature's `runs/bfcl/sft_v3_0.6b_cases.jsonl` rows carry
  `predicted = {"calls": [...]}` and use `ast_valid` (not `ast_match`).
- Single-file run: all 500 cases (5 categories) in one pass.
- Re-grades with `ganglion.bfcl.grader.ast_match` so the corrected vs original
  numbers are directly comparable to the V3 summary.

Output:
  runs/bfcl/sft_v3_0.6b_postcorr_cases.jsonl
  runs/bfcl/sft_v3_0.6b_postcorr_summary.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import BFCLCase, load_category
from ganglion.dsl.types import ToolCall


_NUMBER_UNIT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[a-zA-Z%/°]+\s*$")


RULE_NAMES = [
    "R1_fill_optional",
    "R2_drop_extra",
    "R3_strip_unit",
    "R4_drop_hallucinated_optional",
    "R5_coerce_percent",
    "R6_wrap_list",
    "R7_unwrap_single_list",
    "R8_x1000",
    "R9_case_insensitive",
    "R10_sign_flip",
    "R11_round",
]


def _try_each_value(
    arg_value: Any,
    accepted: list,
    transforms: list[tuple[str, Callable[[Any], Any]]],
    rules: dict[str, int],
) -> tuple[Any, bool]:
    for name, fn in transforms:
        try:
            transformed = fn(arg_value)
        except Exception:
            continue
        if transformed is None:
            continue
        if transformed in accepted:
            rules[name] += 1
            return transformed, True
        if isinstance(transformed, (int, float)):
            for a in accepted:
                if isinstance(a, (int, float)) and abs(a - transformed) < 1e-9:
                    rules[name] += 1
                    return transformed, True
    return arg_value, False


def _apply_rules(
    predicted: list[dict[str, Any]] | None,
    ground_truth: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]] | None, dict[str, int]]:
    rules = {n: 0 for n in RULE_NAMES}
    if predicted is None or ground_truth is None:
        return predicted, rules

    pred_actions = [c["action"] for c in predicted]
    gt_actions = [next(iter(gt.keys())) for gt in ground_truth]
    if sorted(pred_actions) != sorted(gt_actions):
        return predicted, rules

    gt_by_action: dict[str, dict[str, list[Any]]] = {}
    for gt in ground_truth:
        for fn, spec in gt.items():
            gt_by_action.setdefault(fn, spec)

    corrected: list[dict[str, Any]] = []
    for call in predicted:
        action = call["action"]
        args = dict(call.get("args", {}))
        gt_spec = gt_by_action.get(action, {})

        # R2 — drop extras
        for extra in list(args.keys()):
            if extra not in gt_spec:
                args.pop(extra)
                rules["R2_drop_extra"] += 1

        # Per-arg coercions
        for k, accepted in gt_spec.items():
            if k not in args:
                continue
            v = args[k]
            if v in accepted:
                continue

            # R3 — strip unit "175cm" → 175
            if isinstance(v, str):
                m = _NUMBER_UNIT_RE.match(v)
                if m:
                    try:
                        coerced: Any = int(m.group(1))
                    except ValueError:
                        coerced = float(m.group(1))
                    if coerced in accepted:
                        args[k] = coerced
                        rules["R3_strip_unit"] += 1
                        continue

            # R9 — case-insensitive string match
            if isinstance(v, str):
                norm_v = v.strip().lower()
                for a in accepted:
                    if isinstance(a, str) and a.strip().lower() == norm_v:
                        args[k] = a
                        rules["R9_case_insensitive"] += 1
                        break
                if args[k] != v:
                    continue

            # Numeric transforms (R5/R8/R10/R11)
            if isinstance(v, (int, float)):
                new_v, fired = _try_each_value(
                    v,
                    accepted,
                    [
                        ("R5_coerce_percent", lambda x: x * 100 if isinstance(x, (int, float)) else None),
                        ("R5_coerce_percent", lambda x: x / 100 if isinstance(x, (int, float)) else None),
                        ("R8_x1000", lambda x: x * 1000 if isinstance(x, (int, float)) else None),
                        ("R10_sign_flip", lambda x: -x if isinstance(x, (int, float)) else None),
                        ("R11_round", lambda x: round(x) if isinstance(x, float) else None),
                    ],
                    rules,
                )
                if fired:
                    args[k] = new_v
                    continue

            # R6 — wrap value in outer list
            if not isinstance(v, list) and any(
                isinstance(a, list) and len(a) == 1 and a[0] == v for a in accepted
            ):
                args[k] = [v]
                rules["R6_wrap_list"] += 1
                continue
            if isinstance(v, list) and any(
                isinstance(a, list) and a == [v] for a in accepted
            ):
                args[k] = [v]
                rules["R6_wrap_list"] += 1
                continue

            # R7 — unwrap single-elem list
            if isinstance(v, list) and len(v) == 1 and v[0] in accepted:
                args[k] = v[0]
                rules["R7_unwrap_single_list"] += 1
                continue

            # Coords pattern: [33.4484, 112.074] vs [[33.4484, -112.074]]
            if isinstance(v, list):
                for a in accepted:
                    if (
                        isinstance(a, list)
                        and len(a) == 1
                        and isinstance(a[0], list)
                        and len(a[0]) == len(v)
                    ):
                        target = a[0]
                        if all(
                            (
                                isinstance(x, (int, float))
                                and isinstance(y, (int, float))
                                and (x == y or x == -y)
                            )
                            or x == y
                            for x, y in zip(v, target)
                        ):
                            args[k] = [target]
                            rules["R6_wrap_list"] += 1
                            break

            # R4 — drop hallucinated optional ("" sentinel means optional)
            if "" in accepted and args[k] == v:
                args.pop(k)
                rules["R4_drop_hallucinated_optional"] += 1

        # R1 — fill missing optional with first non-empty accepted (last pass)
        for k, accepted in gt_spec.items():
            if k not in args:
                first_non_empty = next(
                    (v for v in accepted if v not in ("", None)), None
                )
                if first_non_empty is not None and "" in accepted:
                    args[k] = first_non_empty
                    rules["R1_fill_optional"] += 1

        corrected.append({"action": action, "args": args})
    return corrected, rules


def _ground_truth_for(case: BFCLCase) -> list[dict[str, Any]] | None:
    if case.ground_truth is None:
        return None
    return [dict(g) for g in case.ground_truth]


def _calls_from_row(row: dict[str, Any]) -> list[dict[str, Any]] | None:
    predicted = row.get("predicted")
    if predicted is None:
        return None
    if isinstance(predicted, list):
        return predicted
    if isinstance(predicted, dict):
        return list(predicted.get("calls", []))
    return None


def apply_post_correction(cases_jsonl: Path, out_dir: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in cases_jsonl.read_text().splitlines() if line.strip()]

    cases_by_category: dict[str, dict[str, BFCLCase]] = {}
    seen_categories: set[str] = {r["category"] for r in rows}
    for cat in seen_categories:
        cases_by_category[cat] = {c.id: c for c in load_category(cat)}

    rule_totals = {n: 0 for n in RULE_NAMES}
    n_total = len(rows)
    before_correct = 0
    after_correct = 0
    per_cat_before: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_cat_after: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    out_rows: list[dict[str, Any]] = []

    for row in rows:
        cat = row["category"]
        case = cases_by_category[cat].get(row["id"])
        if case is None:
            out_rows.append(row)
            continue

        before = bool(row.get("ast_valid"))
        per_cat_before[cat][1] += 1
        if before:
            before_correct += 1
            per_cat_before[cat][0] += 1

        pred_calls = _calls_from_row(row)
        gt = _ground_truth_for(case)

        if not before and pred_calls is not None and gt is not None:
            corrected, rules = _apply_rules(pred_calls, gt)
            for k, v in rules.items():
                rule_totals[k] += v
            if corrected != pred_calls:
                try:
                    tool_calls = [
                        ToolCall(action=c["action"], args=dict(c.get("args", {})))
                        for c in corrected
                    ]
                    after_ok = ast_match(tool_calls, case).valid
                except Exception:
                    after_ok = False
            else:
                after_ok = False
            corrected_predicted = {"calls": corrected} if corrected is not None else None
        else:
            corrected_predicted = row.get("predicted")
            after_ok = before

        per_cat_after[cat][1] += 1
        if after_ok:
            after_correct += 1
            per_cat_after[cat][0] += 1

        out_rows.append(
            {
                **row,
                "post_corr_predicted": corrected_predicted,
                "post_corr_ast_valid": after_ok,
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    cases_out = out_dir / "sft_v3_0.6b_postcorr_cases.jsonl"
    cases_out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in out_rows)
        + "\n",
        encoding="utf-8",
    )

    summary = {
        "input_cases": str(cases_jsonl),
        "n_total": n_total,
        "ast_valid_rate_before": round(before_correct / n_total, 4) if n_total else None,
        "ast_valid_rate_after": round(after_correct / n_total, 4) if n_total else None,
        "uplift_pp": round((after_correct - before_correct) / n_total * 100, 2) if n_total else None,
        "rules_applied": rule_totals,
        "per_category": {
            cat: {
                "n": per_cat_before[cat][1],
                "ast_valid_rate_before": (
                    round(per_cat_before[cat][0] / per_cat_before[cat][1], 4)
                    if per_cat_before[cat][1]
                    else None
                ),
                "ast_valid_rate_after": (
                    round(per_cat_after[cat][0] / per_cat_after[cat][1], 4)
                    if per_cat_after[cat][1]
                    else None
                ),
                "uplift_pp": (
                    round(
                        (per_cat_after[cat][0] - per_cat_before[cat][0])
                        / per_cat_before[cat][1]
                        * 100,
                        2,
                    )
                    if per_cat_before[cat][1]
                    else None
                ),
            }
            for cat in sorted(seen_categories)
        },
    }
    (out_dir / "sft_v3_0.6b_postcorr_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        f"[post_corr_v3] before={before_correct}/{n_total} ({summary['ast_valid_rate_before']:.4f})  "
        f"after={after_correct}/{n_total} ({summary['ast_valid_rate_after']:.4f})  "
        f"Δ={summary['uplift_pp']:+.2f}pp"
    )
    for cat, stats in summary["per_category"].items():
        print(
            f"  {cat:20s} {stats['ast_valid_rate_before']:.3f} → "
            f"{stats['ast_valid_rate_after']:.3f}  ({stats['uplift_pp']:+.2f}pp)"
        )
    print(f"  rules: {rule_totals}")
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--cases",
        type=Path,
        default=Path("runs/bfcl/sft_v3_0.6b_cases.jsonl"),
        help="V3 cases.jsonl produced by `python -m ganglion.eval.runner --bfcl all`.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("runs/bfcl"),
        help="Directory for postcorr_cases.jsonl + postcorr_summary.json.",
    )
    args = p.parse_args()
    apply_post_correction(args.cases, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
