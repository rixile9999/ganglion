"""Build the per-row SFT training pool for the BFCL factory arc (S2a').

Reads `examples/bfcl/v4/train/*.jsonl` (740 source) ∪
`examples/bfcl/v4/train/synth.jsonl` (2,220 paraphrases), and for each
BFCLCase emits one training row of the form

    {"id": <case_id>, "messages": [system, user, assistant]}

* ``system``     = the per-case DSL prompt from
                   ``compile_tool_calling_schema(case.function).render_json_dsl()``
                   with ``allow_empty_calls=True`` for irrelevance.
* ``user``       = the user message.
* ``assistant``  = canonical Action-IR JSON derived from
                   ``case.ground_truth`` (or ``{"calls": []}`` for irrelevance).

The canonical IR is built by picking the first non-empty accepted value
for every required argument (skipping optionals whose accepted set
includes ``""``). This matches the BFCL grader's notion of "any answer in
the accepted list is correct" and mirrors ``tests/test_bfcl_smoke.py:_replay_plan``.

The output JSONL is consumed by ``train_sft_bfcl.py``; we deliberately
keep the messages as a flat list of {role, content} dicts so the SFT
trainer can serve them directly via the chat template without further
massaging.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from ganglion.bfcl.loader import BFCLCase, load_cases
from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.dsl.types import ActionPlan, ToolCall

SYSTEM_PROMPT_TEMPLATE = (
    "You convert user requests into the JSON DSL below. "
    "The response must be valid JSON.\n\n{catalog_dsl}"
)

CATEGORIES = (
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
)


def _replay_plan(case: BFCLCase) -> ActionPlan:
    """Mirror of tests/test_bfcl_smoke.py::_replay_plan.

    Picks the first non-empty accepted value per required arg, skipping
    optionals whose accepted set includes ``""``. Promotes ``int`` to
    ``float`` when the schema declares ``"type": "float"`` (BFCL alias).
    """
    if case.ground_truth is None:
        return ActionPlan(calls=())
    func_descriptions = {tool["name"]: tool for tool in case.tools}
    calls: list[ToolCall] = []
    for entry in case.ground_truth:
        func_name = next(iter(entry.keys()))
        accepted = entry[func_name]
        required = func_descriptions[func_name]["parameters"].get("required", [])
        param_details = func_descriptions[func_name]["parameters"]["properties"]
        args: dict[str, Any] = {}
        for param, options in accepted.items():
            non_empty = [o for o in options if o != ""]
            if not non_empty:
                continue
            if param not in required and "" in options:
                continue
            value = non_empty[0]
            if (
                param_details.get(param, {}).get("type") == "float"
                and isinstance(value, int)
            ):
                value = float(value)
            args[param] = value
        calls.append(ToolCall(action=func_name, args=args))
    return ActionPlan(calls=tuple(calls))


def _expected_dsl_string(plan: ActionPlan) -> str:
    """Render the canonical Action IR as the JSON string the model must emit.

    Match the lenient parser's strict-strategy expectation: pure JSON,
    no markdown, no commentary, no trailing whitespace.
    """
    return json.dumps(plan.to_jsonable(), ensure_ascii=False, separators=(",", ":"))


def _build_row(case: BFCLCase, *, allow_empty_calls: bool = True) -> dict[str, Any]:
    """Compile one training row.

    ``allow_empty_calls`` must match the evaluation-time setting. v1
    ran this as ``case.category == "irrelevance"`` and triggered a mode
    collapse: the eval runner always passes ``--bfcl-allow-empty-calls``,
    so every inference-time system prompt carries the no-call clause,
    but only 19% of training rows did → the model learned "no-call
    clause in prompt ⇒ emit []." Forcing the flag to ``True`` everywhere
    fixes train/inference distribution drift; the model must use the user
    message (not just the prompt suffix) to decide callable vs abstain.
    """
    catalog = compile_tool_calling_schema(
        list(case.tools), allow_empty_calls=allow_empty_calls,
    ).catalog
    plan = _replay_plan(case)
    expected_dsl = _expected_dsl_string(plan)
    system_content = SYSTEM_PROMPT_TEMPLATE.format(catalog_dsl=catalog.render_json_dsl())
    return {
        "id": case.id,
        "category": case.category,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": case.user_message},
            {"role": "assistant", "content": expected_dsl},
        ],
    }


def _iter_cases(train_root: Path, include_synth: bool) -> Iterable[BFCLCase]:
    for cat in CATEGORIES:
        yield from load_cases(train_root / f"{cat}.jsonl")
    if include_synth:
        yield from load_cases(train_root / "synth.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-root", default="examples/bfcl/v4/train")
    parser.add_argument("--out", default="examples/bfcl/v4/train/sft_pool.jsonl")
    parser.add_argument(
        "--no-synth", action="store_true",
        help="Skip synth.jsonl (use only the 740 source cases).",
    )
    parser.add_argument(
        "--allow-empty-mode",
        choices=("always", "irrelevance-only"),
        default="always",
        help=(
            "How to set the catalog's allow_empty_calls flag per training row. "
            "'always' (default, post-v1 fix) matches the eval runner which "
            "passes --bfcl-allow-empty-calls globally. 'irrelevance-only' is "
            "the v1 setting that caused mode collapse and is kept only for "
            "regression reproducibility."
        ),
    )
    args = parser.parse_args()

    train_root = Path(args.train_root)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {c: 0 for c in CATEGORIES}
    n_total = 0
    n_skipped = 0

    with out_path.open("w", encoding="utf-8") as fh:
        for case in _iter_cases(train_root, include_synth=not args.no_synth):
            try:
                allow_empty = (
                    True if args.allow_empty_mode == "always"
                    else case.category == "irrelevance"
                )
                row = _build_row(case, allow_empty_calls=allow_empty)
            except Exception as exc:
                n_skipped += 1
                print(f"[build_sft_pool] skip {case.id}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[case.category] = counts.get(case.category, 0) + 1
            n_total += 1

    stats = {
        "n_rows": n_total,
        "n_skipped": n_skipped,
        "by_category": counts,
        "include_synth": not args.no_synth,
        "train_root": str(train_root.resolve()),
    }
    stats_path = out_path.with_name(out_path.stem + "_stats.json")
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[build_sft_pool] wrote {n_total} rows ({n_skipped} skipped) to {out_path}")
    print(f"[build_sft_pool] by_category={counts}")
    print(f"[build_sft_pool] stats: {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
