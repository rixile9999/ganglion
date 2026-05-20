"""Deterministic failure-type classifier for analyzer traces.

Implements [[analyzer_failure_taxonomy]] (docs/tasks/analyzer_failure_taxonomy.md).

Takes one :class:`~ganglion.analyzer.trace.Trace` and applies a priority-ordered
chain of pure deterministic matchers against the trace's recorded fields plus
(optionally) the catalog the trace was produced under and an expected
``ActionPlan``. Each matcher returns either ``None`` or a
``(FailureType, confidence, evidence)`` triple; the first non-``None`` matcher
wins. The result is a :class:`Classification` value — the trace store is never
mutated (append-only contract from [[analyzer_trace_store]]).

This module is deterministic-rules-only. LLM-judge classification, rule
synthesis from classifications, and cross-case statistics are explicitly
out-of-scope and live in sibling tasks.

Public surface:
    FailureType            — string-valued enum covering the taxonomy.
    Classification         — frozen dataclass; one per trace.
    classify               — single-trace entrypoint.
    classify_traces        — bulk variant over an iterable of traces.
    write_classified_sidecar — JSONL sidecar writer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ganglion.analyzer.trace import Trace
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import (
    EnumArg,
    IntArg,
    NumberArg,
    StringArg,
    TimeArg,
    ToolSpec,
)
from ganglion.contract.types import ActionPlan

__all__ = [
    "FailureType",
    "Classification",
    "classify",
    "classify_traces",
    "write_classified_sidecar",
]


class FailureType(Enum):
    """Taxonomy buckets — one of these is assigned to every classified trace.

    Priority order (used by :func:`classify`): higher entries in this list
    take precedence over later ones. See ``MATCHERS_IN_PRIORITY_ORDER`` for
    the matcher chain that encodes the order.
    """

    SYNTAX_INVALID = "syntax_invalid"
    UNKNOWN_TOOL = "unknown_tool"
    WRONG_ACTION = "wrong_action"
    MISSING_REQUIRED_ARG = "missing_required_arg"
    UNKNOWN_ARG = "unknown_arg"
    TYPE_MISMATCH = "type_mismatch"
    VALUE_OUT_OF_ENUM = "value_out_of_enum"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    ALIAS_UNRECOGNISED = "alias_unrecognised"
    ABSTENTION_MISS_SHOULD_CALL = "abstention_miss_should_call"
    ABSTENTION_MISS_SHOULD_ABSTAIN = "abstention_miss_should_abstain"
    PARALLEL_ORDER_MISMATCH = "parallel_order_mismatch"
    PARTIAL_ARG_VALUE_MISMATCH = "partial_arg_value_mismatch"
    NO_FAILURE = "no_failure"


@dataclass(frozen=True)
class Classification:
    """One trace's failure-type assignment.

    ``confidence`` is rule-source derived (deterministic matches = 1.0,
    heuristics like alias-look-alike detection sit in 0.7-0.9). ``evidence``
    is matcher-specific but always JSON-serialisable so it round-trips through
    :func:`write_classified_sidecar`.
    """

    trace_id: str
    failure_type: FailureType
    confidence: float
    evidence: Mapping[str, Any]


# Heuristic-confidence ceilings used by the alias-look-alike matcher. The
# deterministic matchers all use 1.0. If we add more heuristic matchers later,
# their confidence should be expressed in this band so dashboards can flag a
# drop in ``classification_confidence_mean`` to the right matcher family.
_ALIAS_HEURISTIC_CONFIDENCE = 0.8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calls_from_plan(plan: Any) -> list[dict[str, Any]]:
    """Coerce a plan payload (dict / ActionPlan / None) into a list of calls."""
    if plan is None:
        return []
    if isinstance(plan, ActionPlan):
        return [{"action": c.action, "args": dict(c.args)} for c in plan.calls]
    if isinstance(plan, Mapping):
        calls = plan.get("calls", []) or []
        out: list[dict[str, Any]] = []
        for call in calls:
            if isinstance(call, Mapping):
                args = call.get("args", {}) or {}
                out.append(
                    {
                        "action": str(call.get("action", "")),
                        "args": dict(args) if isinstance(args, Mapping) else {},
                    }
                )
        return out
    return []


def _kind_of(spec: Any) -> str:
    """Return ArgSpec.kind, or 'unknown' if the spec is not recognised."""
    return getattr(spec, "kind", "unknown")


def _expected_python_types(spec: Any) -> tuple[type, ...]:
    """Map an ArgSpec to the Python runtime types that satisfy it."""
    if isinstance(spec, IntArg):
        # bools are accepted via bool_true/bool_false on EnumArg only; IntArg
        # rejects them (see contract/catalog.py).
        return (int,)
    if isinstance(spec, NumberArg):
        return (int, float)
    if isinstance(spec, (EnumArg, StringArg, TimeArg)):
        return (str,)
    # RawArg / BoolArg / unknown — fall through and treat as anything.
    return ()


def _looks_like_alias(value: str) -> bool:
    """Heuristic for the alias_unrecognised matcher.

    True when ``value`` looks like a short noun-style label the user might
    have meant as an alias — short, no whitespace/punctuation explosion, not
    obviously a number / structural token.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or len(stripped) > 40:
        return False
    if stripped.isdigit():
        return False
    # Reject anything that looks like a structural JSON fragment.
    if any(ch in stripped for ch in "{}[]:"):
        return False
    return True


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def _match_syntax_invalid(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    parse_strategy = (trace.parse_strategy or "").lower()
    error_type = (trace.error_type or "").lower()
    json_markers = ("json", "parse", "invalid syntax")
    syntax_signal = parse_strategy == "failed" or (
        trace.plan is None and any(m in error_type for m in json_markers)
    )
    if not syntax_signal:
        return None
    return (
        FailureType.SYNTAX_INVALID,
        1.0,
        {"raw": trace.raw_output, "parse_error": trace.error_type or ""},
    )


def _match_unknown_tool(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    if not pred_calls:
        return None
    known = {tool.name for tool in catalog.tools}
    for call in pred_calls:
        action = call.get("action", "")
        if action and action not in known:
            return (
                FailureType.UNKNOWN_TOOL,
                1.0,
                {
                    "predicted_action": action,
                    "expected_action": None,
                    "known_actions": sorted(known),
                },
            )
    return None


def _match_wrong_action(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    gold_calls = _calls_from_plan(gold)
    if not pred_calls or not gold_calls:
        return None
    if len(pred_calls) != len(gold_calls):
        return None
    for pred, expected in zip(pred_calls, gold_calls):
        if pred.get("action") != expected.get("action"):
            return (
                FailureType.WRONG_ACTION,
                1.0,
                {
                    "predicted_action": pred.get("action", ""),
                    "expected_action": expected.get("action", ""),
                    "known_actions": (
                        sorted(t.name for t in catalog.tools) if catalog else []
                    ),
                },
            )
    return None


def _match_abstention_should_call(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    gold_calls = _calls_from_plan(gold)
    if not pred_calls and gold_calls:
        return (
            FailureType.ABSTENTION_MISS_SHOULD_CALL,
            1.0,
            {"predicted_count": 0, "expected_count": len(gold_calls)},
        )
    return None


def _match_abstention_should_abstain(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    gold_calls = _calls_from_plan(gold)
    if pred_calls and not gold_calls:
        return (
            FailureType.ABSTENTION_MISS_SHOULD_ABSTAIN,
            1.0,
            {"predicted_count": len(pred_calls), "expected_count": 0},
        )
    return None


def _resolve_tool(catalog: Catalog | None, action: str) -> ToolSpec | None:
    if catalog is None or not action:
        return None
    return catalog.get_tool(action)


def _match_missing_required_arg(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        provided = set(call.get("args", {}).keys())
        for arg_name in tool.required_arg_names():
            if arg_name not in provided:
                return (
                    FailureType.MISSING_REQUIRED_ARG,
                    1.0,
                    {
                        "action": tool.name,
                        "arg_name": arg_name,
                        "declared_args": [name for name, _ in tool.args],
                    },
                )
    return None


def _match_unknown_arg(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        declared = {name for name, _ in tool.args}
        for arg_name in call.get("args", {}).keys():
            if arg_name not in declared:
                return (
                    FailureType.UNKNOWN_ARG,
                    1.0,
                    {
                        "action": tool.name,
                        "arg_name": arg_name,
                        "declared_args": sorted(declared),
                    },
                )
    return None


def _match_type_mismatch(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        for arg_name, value in call.get("args", {}).items():
            spec = tool.get_arg(arg_name)
            if spec is None:
                continue
            expected = _expected_python_types(spec)
            if not expected:
                continue
            # Reject bools where IntArg expects an int — Python conflates the
            # two via subclassing, but the contract validator does not.
            bool_in_int_slot = (
                isinstance(value, bool) and int in expected and bool not in expected
            )
            if not bool_in_int_slot and isinstance(value, expected):
                continue
            actual_type = "boolean" if bool_in_int_slot else type(value).__name__
            return (
                FailureType.TYPE_MISMATCH,
                1.0,
                {
                    "action": tool.name,
                    "arg_name": arg_name,
                    "expected_type": _kind_of(spec),
                    "actual_type": actual_type,
                    "value_repr": repr(value),
                },
            )
    return None


def _match_value_out_of_enum(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        for arg_name, value in call.get("args", {}).items():
            spec = tool.get_arg(arg_name)
            if not isinstance(spec, EnumArg):
                continue
            if not isinstance(value, str):
                continue
            if value in spec.values or value in spec.aliases:
                continue
            return (
                FailureType.VALUE_OUT_OF_ENUM,
                1.0,
                {
                    "action": tool.name,
                    "arg_name": arg_name,
                    "value": value,
                    "allowed": list(spec.values),
                },
            )
    return None


def _match_alias_unrecognised(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        for arg_name, value in call.get("args", {}).items():
            spec = tool.get_arg(arg_name)
            if not isinstance(spec, StringArg) or not spec.aliases:
                continue
            if not isinstance(value, str) or value in spec.aliases.values():
                continue
            if value in spec.aliases:
                continue
            if not _looks_like_alias(value):
                continue
            return (
                FailureType.ALIAS_UNRECOGNISED,
                _ALIAS_HEURISTIC_CONFIDENCE,
                {
                    "action": tool.name,
                    "arg_name": arg_name,
                    "value": value,
                    "nearest_alias": "",
                    "edit_distance": -1,
                },
            )
    return None


def _match_value_out_of_range(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if catalog is None:
        return None
    for call in _calls_from_plan(trace.plan):
        tool = _resolve_tool(catalog, call.get("action", ""))
        if tool is None:
            continue
        for arg_name, value in call.get("args", {}).items():
            spec = tool.get_arg(arg_name)
            if not isinstance(spec, (IntArg, NumberArg)):
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            below_min = spec.min_value is not None and value < spec.min_value
            above_max = spec.max_value is not None and value > spec.max_value
            if below_min or above_max:
                return (
                    FailureType.VALUE_OUT_OF_RANGE,
                    1.0,
                    {
                        "action": tool.name,
                        "arg_name": arg_name,
                        "value": value,
                        "min": spec.min_value,
                        "max": spec.max_value,
                    },
                )
    return None


def _match_parallel_order_mismatch(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    gold_calls = _calls_from_plan(gold)
    if len(pred_calls) < 2 or len(pred_calls) != len(gold_calls):
        return None
    pred_actions = sorted(c.get("action", "") for c in pred_calls)
    gold_actions = sorted(c.get("action", "") for c in gold_calls)
    if pred_actions != gold_actions:
        return None
    # Same multiset of action names but call-by-call pairing fails. Try the
    # permutation: if any reordering of pred lines up exactly with gold, it
    # IS an order mismatch (and only that).
    in_order_equal = all(p == g for p, g in zip(pred_calls, gold_calls))
    if in_order_equal:
        return None
    # Order-insensitive signature: JSON-dump with sorted keys so we can compare
    # arbitrary nested arg values (lists, dicts) without hashability worries.
    def _sig(call: dict[str, Any]) -> str:
        return json.dumps(
            {"action": call.get("action", ""), "args": call.get("args", {})},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    if sorted(_sig(c) for c in pred_calls) != sorted(_sig(c) for c in gold_calls):
        return None
    return (
        FailureType.PARALLEL_ORDER_MISMATCH,
        1.0,
        {"predicted": pred_calls, "expected": gold_calls},
    )


def _match_partial_arg_value_mismatch(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is None:
        return None
    pred_calls = _calls_from_plan(trace.plan)
    gold_calls = _calls_from_plan(gold)
    if not pred_calls or len(pred_calls) != len(gold_calls):
        return None
    for pred, expected in zip(pred_calls, gold_calls):
        if pred.get("action") != expected.get("action"):
            return None
        pred_args = pred.get("args", {})
        gold_args = expected.get("args", {})
        if set(pred_args.keys()) != set(gold_args.keys()):
            return None
        differing = {
            k: {"predicted": pred_args[k], "expected": gold_args[k]}
            for k in pred_args
            if pred_args[k] != gold_args[k]
        }
        if differing:
            return (
                FailureType.PARTIAL_ARG_VALUE_MISMATCH,
                1.0,
                {
                    "predicted": pred_calls,
                    "expected": gold_calls,
                    "differing": differing,
                },
            )
    return None


def _match_no_failure(
    trace: Trace,
    catalog: Catalog | None,
    gold: ActionPlan | None,
) -> tuple[FailureType, float, dict[str, Any]] | None:
    if gold is not None:
        pred_calls = _calls_from_plan(trace.plan)
        gold_calls = _calls_from_plan(gold)
        if pred_calls == gold_calls:
            return (FailureType.NO_FAILURE, 1.0, {})
        # Gold supplied but did not match — leave classification to upstream
        # matchers; we fall through to a degenerate NO_FAILURE only if
        # nothing else fired.
        return (FailureType.NO_FAILURE, 0.0, {})
    if catalog is None:
        # Degenerate case from the spec: no catalog and no gold means we
        # cannot detect any failure structurally. Surface low confidence so
        # dashboards can flag it.
        return (FailureType.NO_FAILURE, 0.0, {})
    return (FailureType.NO_FAILURE, 1.0, {})


# Priority order encoded as a tuple of (name, matcher) pairs. First non-None
# return wins. The fall-through ``_match_no_failure`` is always last and
# guarantees classify() is total.
MATCHERS_IN_PRIORITY_ORDER: tuple[
    tuple[str, Any], ...
] = (
    ("syntax_invalid", _match_syntax_invalid),
    ("unknown_tool", _match_unknown_tool),
    ("wrong_action", _match_wrong_action),
    ("abstention_miss_should_call", _match_abstention_should_call),
    ("abstention_miss_should_abstain", _match_abstention_should_abstain),
    ("missing_required_arg", _match_missing_required_arg),
    ("unknown_arg", _match_unknown_arg),
    ("type_mismatch", _match_type_mismatch),
    ("value_out_of_enum", _match_value_out_of_enum),
    ("alias_unrecognised", _match_alias_unrecognised),
    ("value_out_of_range", _match_value_out_of_range),
    ("parallel_order_mismatch", _match_parallel_order_mismatch),
    ("partial_arg_value_mismatch", _match_partial_arg_value_mismatch),
    ("no_failure", _match_no_failure),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(
    trace: Trace,
    catalog: Catalog | None = None,
    gold: ActionPlan | None = None,
) -> Classification:
    """Classify a single trace into one :class:`FailureType`.

    Priority-ordered matchers run in :data:`MATCHERS_IN_PRIORITY_ORDER`; the
    first non-``None`` return wins. ``catalog`` enables tool / arg / value
    matchers; ``gold`` enables comparison matchers (wrong_action, abstention,
    partial value mismatch). When both are ``None`` the result is a degenerate
    ``NO_FAILURE`` with ``confidence=0.0`` so dashboards can flag the gap.
    """
    for _name, matcher in MATCHERS_IN_PRIORITY_ORDER:
        try:
            hit = matcher(trace, catalog, gold)
        except Exception:
            # Per spec: fail loud + low confidence; the metric
            # `classification_confidence_mean` will dip and surface the bug.
            return Classification(
                trace_id=trace.trace_id,
                failure_type=FailureType.NO_FAILURE,
                confidence=0.0,
                evidence={"matcher_error": _name},
            )
        if hit is not None:
            ftype, confidence, evidence = hit
            return Classification(
                trace_id=trace.trace_id,
                failure_type=ftype,
                confidence=confidence,
                evidence=evidence,
            )
    # Defensive: the no_failure matcher always returns a hit, so this branch
    # is unreachable. Keep it for total-function correctness.
    return Classification(
        trace_id=trace.trace_id,
        failure_type=FailureType.NO_FAILURE,
        confidence=0.0,
        evidence={},
    )


def classify_traces(
    traces: Iterable[Trace],
    catalog: Catalog,
    golds: Mapping[str, ActionPlan] | None = None,
) -> list[Classification]:
    """Classify a stream of traces against a single catalog.

    ``golds`` maps ``case_id`` → ground-truth :class:`ActionPlan`; missing
    entries are treated as "no gold available" and the matchers that depend
    on gold are skipped for that trace.
    """
    golds = golds or {}
    return [classify(tr, catalog=catalog, gold=golds.get(tr.case_id)) for tr in traces]


def write_classified_sidecar(
    classifications: Iterable[Classification],
    path: Path,
) -> None:
    """Write classifications as one JSONL row per classification.

    Sidecar file; never mutates the trace store. Parent directory is created
    if missing. Rows follow the schema:
    ``{trace_id, failure_type, confidence, evidence}``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for cls in classifications:
            row = {
                "trace_id": cls.trace_id,
                "failure_type": cls.failure_type.value,
                "confidence": cls.confidence,
                "evidence": dict(cls.evidence),
            }
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
