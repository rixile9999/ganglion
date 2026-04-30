"""Loader for BFCL v4 sample files.

Each sample line is a merged record produced by `examples/bfcl/v4/subsample.py`:

    {
      "id": "simple_python_42",
      "question": [[{"role": "user", "content": "..."}]],
      "function": [{"name": "...", "parameters": {"type": "dict", ...}}, ...],
      "ground_truth": [{"<func>": {"<arg>": [<accepted values>]}}]   # optional
    }

`question` is wrapped as a list of conversation turns; BFCL's non-multi_turn
categories always have a single turn, so `question[0]` is the message list and
`question[0][-1]` is the final user message we feed to the model.

`function` is the per-case tool catalog; it's the OpenAI/MCP-compatible shape
that `ganglion.dsl.compiler.compile_tool_calling_schema` already accepts (after
the BFCL-specific `"type": "dict"` quirk is normalised by the compiler).

`ground_truth` is absent for the `irrelevance` category — the correct outcome
is "no function call at all", which Ganglion will encode as an empty
`ActionPlan` once the M5' abstention milestone lands.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "examples" / "bfcl" / "v4" / "sample"

CATEGORIES = (
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
)


@dataclass(frozen=True)
class BFCLCase:
    """A single BFCL v4 evaluation case in Ganglion-friendly form."""

    id: str
    category: str
    user_message: str
    tools: tuple[dict[str, Any], ...]
    ground_truth: tuple[dict[str, Any], ...] | None

    @property
    def expects_call(self) -> bool:
        return self.ground_truth is not None


def load_cases(path: str | Path) -> list[BFCLCase]:
    """Load a JSONL sample file into BFCLCase records."""
    file_path = Path(path)
    raw_lines = [line for line in file_path.read_text().splitlines() if line.strip()]
    return [_to_case(json.loads(line)) for line in raw_lines]


def load_category(category: str, root: Path | None = None) -> list[BFCLCase]:
    """Load the default sub-sample for a category."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown BFCL category: {category!r}")
    base = root if root is not None else SAMPLE_ROOT
    return load_cases(base / f"{category}.jsonl")


def _to_case(record: dict[str, Any]) -> BFCLCase:
    case_id = record["id"]
    category = _category_from_id(case_id)
    user_message = _extract_user_message(record["question"])
    tools = tuple(record["function"])
    ground_truth_raw = record.get("ground_truth")
    if ground_truth_raw is None:
        ground_truth: tuple[dict[str, Any], ...] | None = None
    else:
        ground_truth = tuple(ground_truth_raw)
    return BFCLCase(
        id=case_id,
        category=category,
        user_message=user_message,
        tools=tools,
        ground_truth=ground_truth,
    )


def _category_from_id(case_id: str) -> str:
    # Sort by descending length so "parallel_multiple_*" doesn't match
    # "parallel_*" first.
    for category in sorted(CATEGORIES, key=len, reverse=True):
        prefix = category + "_"
        if case_id.startswith(prefix):
            return category
    raise ValueError(f"cannot infer BFCL category from id: {case_id!r}")


def _extract_user_message(question: Any) -> str:
    if not isinstance(question, Sequence) or isinstance(question, (str, bytes)):
        raise ValueError("BFCL `question` must be a list of turns")
    if not question:
        raise ValueError("BFCL `question` is empty")
    turn = question[0]
    if not isinstance(turn, Sequence) or isinstance(turn, (str, bytes)):
        raise ValueError("BFCL `question[0]` must be a list of messages")
    user_messages = [
        msg.get("content", "")
        for msg in turn
        if isinstance(msg, dict) and msg.get("role") == "user"
    ]
    if not user_messages:
        raise ValueError("BFCL `question[0]` has no user messages")
    return user_messages[-1]


def all_categories(root: Path | None = None) -> Iterable[tuple[str, list[BFCLCase]]]:
    for category in CATEGORIES:
        yield category, load_category(category, root=root)
