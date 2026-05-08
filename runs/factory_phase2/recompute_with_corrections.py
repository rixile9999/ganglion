"""Retroactively apply two extra post-correction passes to a saved
grammar_ablation eval_report and re-measure exact_match.

Corrections applied:

  C1. strip_unknown_args — remove any arg that is not declared in the tool's
      ToolSpec. Triggered by failures like
        list_devices(args={'id': '8'})        # spurious id
        get_light_state(args={'at':'22:00', 'room':'kitchen'})  # spurious at
        set_light(args={'at':'08:00', ...})   # spurious at
      where the model echoes a numeric trailing token (#N) from the prompt
      back into args.

  C2. korean_time_normalization — for `schedule_light` calls, if the
      prompt contains exactly one "오전/오후 N시" expression, override
      args.at with the canonical 24h string. Targets failures like
        prompt "오후 1시에 거실 조명 꺼줘"
        gold   at='13:00'
        pred   at='23:00'

Pure dict-level correction; no model inference. CPU-only.

The script is meant for fast lever validation. Once a correction's lift is
confirmed, port the rule into ``ganglion/dsl/`` so it lives in the
inference path (where every consumer benefits, not just retro replays).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.factory.customer.ingest import ingest_schema


_KOREAN_TIME_RE = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")


def _parse_korean_time(prompt: str) -> str | None:
    """Return canonical HH:MM if the prompt has exactly one 오전/오후 N시(:M분?).

    Returns None on zero or multiple matches (ambiguous → don't correct).
    """
    matches = _KOREAN_TIME_RE.findall(prompt)
    if len(matches) != 1:
        return None
    period, hh, mm = matches[0]
    h = int(hh)
    m = int(mm) if mm else 0
    if h < 1 or h > 12 or m < 0 or m > 59:
        return None
    if period == "오전":
        # 오전 12시 = 00:00 (midnight). Korean usage typically.
        h = 0 if h == 12 else h
    else:  # 오후
        # 오후 12시 = 12:00 (noon). 오후 N시 (N<12) = N+12.
        h = 12 if h == 12 else h + 12
    return f"{h:02d}:{m:02d}"


def _allowed_args_for(catalog: Catalog, action: str) -> set[str] | None:
    for spec in catalog.tools:
        if spec.name == action:
            return {name for name, _ in spec.args}
    return None  # unknown tool — leave alone


def _correct_call(call: dict[str, Any], prompt: str, catalog: Catalog) -> dict[str, Any]:
    action = call.get("action")
    args = dict(call.get("args") or {})
    allowed = _allowed_args_for(catalog, action)
    # C1 strip unknown args
    if allowed is not None:
        args = {k: v for k, v in args.items() if k in allowed}
    # C2 korean time for schedule_light
    if action == "schedule_light":
        canon = _parse_korean_time(prompt)
        if canon is not None:
            args["at"] = canon
    # Recurse for create_scene.actions
    if action == "create_scene":
        nested = args.get("actions")
        if isinstance(nested, list):
            args["actions"] = [_correct_call(c, prompt, catalog) for c in nested]
    return {"action": action, "args": args}


def _correct_dsl(raw_obj: dict[str, Any], prompt: str, catalog: Catalog) -> dict[str, Any]:
    calls = raw_obj.get("calls") or []
    return {"calls": [_correct_call(c, prompt, catalog) for c in calls]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--eval-report", required=True)
    parser.add_argument("--out", default=None,
                        help="Optional: write a corrections summary JSON here.")
    args = parser.parse_args()

    catalog = ingest_schema(args.catalog)
    report = json.loads(Path(args.eval_report).read_text(encoding="utf-8"))

    n_total = report["total"]
    old_exact = round(report["exact_match_rate"] * n_total)
    old_syntax = round(report["syntax_valid_rate"] * n_total)
    old_action = round(report["action_match_rate"] * n_total)
    failures = report.get("failures", []) or []

    delta_syntax = 0
    delta_action = 0
    delta_exact = 0
    rescued: list[dict[str, Any]] = []
    by_correction = {"C1_strip_unknown": 0, "C2_korean_time": 0, "both": 0, "other": 0}

    for fail in failures:
        raw = fail.get("raw") or {}
        raw_output = raw.get("raw_output")
        if not raw_output:
            continue
        prompt = fail.get("prompt") or ""
        expected = fail.get("expected")
        old_predicted = fail.get("predicted")

        # Try to parse the model's raw output leniently as JSON
        try:
            raw_obj = json.loads(raw_output)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_obj, dict):
            continue

        # Apply corrections
        corrected = _correct_dsl(raw_obj, prompt, catalog)

        # Re-validate via the catalog
        try:
            new_plan = catalog.parse_json_dsl(corrected)
        except DSLValidationError:
            continue
        new_jsonable = new_plan.to_jsonable()

        if old_predicted is None:
            delta_syntax += 1

        def _action_match(p, e):
            if not isinstance(p, dict) or not isinstance(e, dict):
                return False
            pc, ec = p.get("calls", []), e.get("calls", [])
            return len(pc) == len(ec) and all(
                a.get("action") == b.get("action") for a, b in zip(pc, ec)
            )
        if _action_match(new_jsonable, expected) and not _action_match(old_predicted, expected):
            delta_action += 1

        if new_jsonable == expected:
            delta_exact += 1
            # Attribute which correction did it
            stripped = (
                json.dumps(corrected, ensure_ascii=False)
                != json.dumps(raw_obj, ensure_ascii=False)
            )
            time_changed = any(
                c.get("action") == "schedule_light"
                and (c.get("args") or {}).get("at") != ((raw_obj.get("calls") or [{}])[0].get("args") or {}).get("at")
                for c in corrected.get("calls", [])
            )
            had_unknown = False
            for c_in, c_out in zip(raw_obj.get("calls") or [], corrected.get("calls", [])):
                a_in = (c_in.get("args") or {})
                a_out = (c_out.get("args") or {})
                if set(a_in.keys()) - set(a_out.keys()):
                    had_unknown = True
            if time_changed and had_unknown:
                by_correction["both"] += 1
            elif time_changed:
                by_correction["C2_korean_time"] += 1
            elif had_unknown:
                by_correction["C1_strip_unknown"] += 1
            else:
                by_correction["other"] += 1

            if len(rescued) < 8:
                rescued.append({
                    "id": fail.get("id"),
                    "prompt": prompt,
                    "raw": raw_obj,
                    "corrected": corrected,
                    "expected": expected,
                })

    new_syntax = old_syntax + delta_syntax
    new_action = old_action + delta_action
    new_exact = old_exact + delta_exact

    print()
    print("=" * 70)
    print(f"Retroactive correction re-eval — {args.catalog}")
    print("=" * 70)
    print(f"eval_report:  {args.eval_report}")
    print(f"total cases:  {n_total}")
    print()
    print(f"{'metric':<22} {'before':>10} {'after':>10} {'Δ':>10}")
    print("-" * 56)
    for name, old, new in [
        ("syntax_valid_rate", old_syntax, new_syntax),
        ("action_match_rate", old_action, new_action),
        ("exact_match_rate",  old_exact,  new_exact),
    ]:
        before_rate = old / n_total
        after_rate = new / n_total
        delta = (after_rate - before_rate) * 100
        sign = "+" if delta >= 0 else ""
        print(f"{name:<22} "
              f"{before_rate:>9.1%}  {after_rate:>9.1%}  {sign}{delta:>5.1f}pp")
    print()
    print("Rescue attribution:")
    for k, v in by_correction.items():
        print(f"  {k:<25}  {v}")
    print()
    if rescued:
        print("Sample rescues:")
        for r in rescued[:5]:
            print(f"  [{r['id']}] {r['prompt']}")
            print(f"      raw:       {json.dumps(r['raw'], ensure_ascii=False)[:120]}")
            print(f"      corrected: {json.dumps(r['corrected'], ensure_ascii=False)[:120]}")
    print()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "catalog": args.catalog,
            "eval_report": str(Path(args.eval_report).resolve()),
            "n_total": n_total,
            "before": {
                "syntax_valid_rate": old_syntax / n_total,
                "action_match_rate": old_action / n_total,
                "exact_match_rate": old_exact / n_total,
            },
            "after": {
                "syntax_valid_rate": new_syntax / n_total,
                "action_match_rate": new_action / n_total,
                "exact_match_rate": new_exact / n_total,
            },
            "deltas": {
                "syntax": delta_syntax,
                "action": delta_action,
                "exact": delta_exact,
            },
            "rescue_by_correction": by_correction,
            "rescue_examples": rescued,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"summary written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
