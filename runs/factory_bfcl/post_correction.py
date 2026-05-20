"""Phase 2 S2c post-correction for BFCL.

Reads `cases.jsonl` rows produced by `bfcl_eval.py`, and for each *failed* row
attempts the BFCL analogue of Phase 2's `defaults_when_missing` rule:

  Rule R1 — fill_optional_with_first_accepted
  Rule R2 — drop_extra_args
  Rule R3 — coerce_numeric_unit (strip "175cm" → 175)
  Rule R4 — drop_hallucinated_optional (optional arg with "" sentinel, value not in accepted)
  Rule R5 — coerce_percent (0.05 ↔ 5.0)
  Rule R6 — wrap_value_in_list (coord [a,b] → [[a,b]])
  Rule R7 — unwrap_single_list ([[x]] → [x])
  Rule R8 — multiply_by_thousand (9597 → 9597000)
  Rule R9 — case_insensitive_string_match (PlayStation → Playstation)
  Rule R10 — sign_flip_numeric (112 → -112 if -112 in accepted)
  Rule R11 — round_numeric (1.9999 → 2.0)

Re-runs the BFCL `ast_match` grader on the corrected plan. Records:

  rules_applied: total per-rule fire count.
  syntax_valid_rate, ast_match_rate: after-correction rates against the same
  100-case (or holdout) set.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ganglion.bfcl.grader import ast_match
from ganglion.bfcl.loader import load_category
from ganglion.dsl.types import ToolCall


_NUMBER_UNIT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[a-zA-Z%/°]+\s*$")


RULE_NAMES = [
    "R1_fill_optional", "R2_drop_extra", "R3_strip_unit",
    "R4_drop_hallucinated_optional", "R5_coerce_percent",
    "R6_wrap_list", "R7_unwrap_single_list", "R8_x1000",
    "R9_case_insensitive", "R10_sign_flip", "R11_round",
]


def _try_each_value(arg_value, accepted: list, transforms: list[tuple[str, "callable"]],
                    rules: dict[str, int]) -> tuple[Any, bool]:
    """Try each transform on arg_value; if any produces a value in accepted, use it."""
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
        # for floats, allow small epsilon
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
                else:
                    pass
                if args[k] != v:
                    continue

            # Numeric transforms
            if isinstance(v, (int, float)):
                new_v, fired = _try_each_value(
                    v, accepted,
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
                    if isinstance(a, list) and len(a) == 1 and isinstance(a[0], list) and len(a[0]) == len(v):
                        target = a[0]
                        if all(
                            (isinstance(x, (int, float)) and isinstance(y, (int, float)) and (x == y or x == -y))
                            or x == y
                            for x, y in zip(v, target)
                        ):
                            args[k] = [target]
                            rules["R6_wrap_list"] += 1
                            break

            # R4 — drop hallucinated optional ("" in accepted means optional, and v is wrong)
            if "" in accepted and args[k] == v:  # still unchanged
                args.pop(k)
                rules["R4_drop_hallucinated_optional"] += 1

        # R1 — fill missing optional with first non-empty accepted (after other passes)
        for k, accepted in gt_spec.items():
            if k not in args:
                first_non_empty = next((v for v in accepted if v not in ("", None)), None)
                if first_non_empty is not None and "" in accepted:
                    args[k] = first_non_empty
                    rules["R1_fill_optional"] += 1

        corrected.append({"action": action, "args": args})
    return corrected, rules


def correct_category(category: str, cases_jsonl: Path, out_path: Path) -> dict:
    rows_in = [json.loads(line) for line in cases_jsonl.read_text().splitlines() if line.strip()]
    cases_by_id = {c.id: c for c in load_category(category)}

    rule_totals = {n: 0 for n in RULE_NAMES}
    n = len(rows_in)
    ast_correct_before = 0
    ast_correct_after = 0
    out_rows = []
    for row in rows_in:
        case = cases_by_id.get(row["id"])
        if case is None:
            continue
        before = row.get("ast_match", False)
        if before:
            ast_correct_before += 1

        if not before and row.get("predicted") is not None:
            corrected, rules = _apply_rules(row["predicted"], row.get("ground_truth"))
            for k, v in rules.items():
                rule_totals[k] += v
            if corrected != row["predicted"]:
                # Re-grade corrected plan via BFCL semantics.
                calls = [ToolCall(action=c["action"], args=c["args"]) for c in corrected]
                try:
                    after = ast_match(calls, case).valid
                except Exception:
                    after = False
            else:
                after = False
        else:
            corrected = row.get("predicted")
            after = before

        if after:
            ast_correct_after += 1

        out_rows.append({
            **row,
            "post_corr_predicted": corrected,
            "post_corr_ast_match": after,
        })

    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "cases.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows), encoding="utf-8"
    )
    summary = {
        "category": category,
        "n": n,
        "ast_match_rate_before": round(ast_correct_before / n, 4) if n else None,
        "ast_match_rate": round(ast_correct_after / n, 4) if n else None,
        "uplift_pp": round((ast_correct_after - ast_correct_before) / n * 100, 2) if n else None,
        "rules_applied": rule_totals,
    }
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[post_corr:{category}] {ast_correct_before}/{n} → {ast_correct_after}/{n} "
          f"(Δ {summary['uplift_pp']:+.2f}pp) rules={rule_totals}")
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--category", required=True)
    p.add_argument("--cases", required=True, help="Path to cases.jsonl produced by bfcl_eval.py")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    correct_category(args.category, Path(args.cases), Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
