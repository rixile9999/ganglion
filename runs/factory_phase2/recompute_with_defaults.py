"""Retroactively re-evaluate a saved grammar_ablation result against the
current `Catalog.parse_json_dsl` (which now applies
`ToolSpec.defaults_when_missing` rules).

Pure re-parse: no model inference, no GPU. Reads the failures' saved
``raw_output`` strings from ``eval_report.json``, re-parses them with the
current catalog, and checks whether each newly-parsable result matches
the expected gold plan.

Useful as a free verification step after landing a post-correction rule:
*"would this rule have rescued failures from the most recent eval?"*

Example:
    python runs/factory_phase2/recompute_with_defaults.py \\
        --catalog iot_light_5 \\
        --eval-report runs/factory_phase2/grammar_ablation/0.6B-sft-iot_light_5/mask_off/eval_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.factory.customer.ingest import ingest_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--eval-report", required=True,
                        help="Path to a previously-written eval_report.json")
    args = parser.parse_args()

    catalog = ingest_schema(args.catalog)
    report = json.loads(Path(args.eval_report).read_text(encoding="utf-8"))

    n_total = report["total"]
    old_exact = round(report["exact_match_rate"] * n_total)
    old_syntax = round(report["syntax_valid_rate"] * n_total)
    old_action = round(report["action_match_rate"] * n_total)
    failures = report.get("failures", []) or []

    # We only count *deltas* — cases whose status improved under the new rules.
    # A failure record exists for any non-exact case; some had predicted=None
    # (parse-fail) and some had predicted=dict (parse-OK but wrong). The
    # post-correction layer can ONLY help cases that previously parse-failed,
    # since defaults_when_missing rules don't override explicit values.
    delta_syntax = 0   # was parse-fail, now parses (regardless of correctness)
    delta_action = 0   # was action-mismatch (or parse-fail), now matches actions
    delta_exact = 0    # was non-exact (or parse-fail), now exact

    rescue_examples: list[tuple[str, str, dict]] = []

    for fail in failures:
        raw = fail.get("raw") or {}
        raw_output = raw.get("raw_output")
        if not raw_output:
            continue
        expected = fail.get("expected")
        old_predicted = fail.get("predicted")  # dict if old-parse-OK, None if parse-failed
        try:
            new_plan = catalog.parse_json_dsl(raw_output)
        except DSLValidationError:
            continue  # still parse-fails, no improvement
        new_jsonable = new_plan.to_jsonable()

        # syntax delta: only fires if old was parse-fail
        if old_predicted is None:
            delta_syntax += 1

        # action_match check: same action sequence
        def _action_match(p: dict | None, e: dict | None) -> bool:
            if not isinstance(p, dict) or not isinstance(e, dict):
                return False
            pc, ec = p.get("calls", []), e.get("calls", [])
            return len(pc) == len(ec) and all(
                a.get("action") == b.get("action") for a, b in zip(pc, ec)
            )
        old_action_match = _action_match(old_predicted, expected)
        new_action_match = _action_match(new_jsonable, expected)
        if new_action_match and not old_action_match:
            delta_action += 1

        # exact delta: failures by definition were not exact before
        if new_jsonable == expected:
            delta_exact += 1
            if len(rescue_examples) < 5:
                rescue_examples.append((fail["id"], fail["prompt"], expected))

    new_syntax = old_syntax + delta_syntax
    new_action = old_action + delta_action
    new_exact = old_exact + delta_exact

    print()
    print("=" * 70)
    print(f"Retroactive re-eval — {args.catalog}")
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
    n_inspected = sum(1 for f in failures if f.get('raw', {}).get('raw_output'))
    n_old_parse_fail = sum(
        1 for f in failures
        if f.get('raw', {}).get('raw_output') and f.get('predicted') is None
    )
    print(f"failures inspected:           {n_inspected}")
    print(f"  of which old parse-fail:    {n_old_parse_fail}")
    print(f"newly parses (delta_syntax):  {delta_syntax}")
    print(f"newly action-matches:         {delta_action}")
    print(f"newly exact-matches:          {delta_exact}")
    print()
    if rescue_examples:
        print("Example rescues (id / prompt / expected):")
        for case_id, prompt, expected in rescue_examples:
            print(f"  [{case_id}] {prompt}")
            calls = expected.get("calls", [])
            for c in calls[:1]:
                print(f"    → action={c.get('action')}  args={c.get('args')}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
