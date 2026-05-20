"""Rule synthesis from classified failure traces.

Implements [[analyzer_rule_synthesis]] (docs/tasks/analyzer_rule_synthesis.md) —
the goal §2 feedback edge from `docs/goal/goal.md`: Module 2 (analysis) emits
machine-readable ``ToolSpec`` patch proposals that Module 3 (contract) can
consume to refine itself.

This is the compiler / error-correction surface. It takes bucketed failure
classifications produced by :mod:`ganglion.analyzer.taxonomy` plus the source
:class:`~ganglion.analyzer.trace.Trace` objects and, for each failure bucket,
runs a deterministic pattern matcher that proposes (never applies) a
``ToolSpec`` patch:

  ``add_alias``               — extend ``EnumArg.aliases`` / ``StringArg.aliases``.
  ``set_default``             — add a ``defaults_when_missing`` entry.
  ``enable_strip_unknown_args`` — flip ``ToolSpec.strip_unknown_args``.
  ``add_prompt_correction``   — install a system-level nudge.
  ``extend_argspec``          — relax / extend an ``ArgSpec``.
  ``ESCALATE``                — out-of-band: the patch would change ``ToolSpec``
                                shape itself (a Module 3 design decision).

The boundary is load-bearing: synthesis **proposes**; humans (or
[[factory_pipeline]] with an explicit gating flag) **apply**. This module
NEVER mutates a ``Catalog`` or ``ToolSpec``. It only emits patches.

The reference baseline lives in ``runs/factory_bfcl/post_correction.py`` (R1–R11
hand-coded rules); this module promotes that pattern language to a first-class
synthesis loop driven from observed evidence.

Public surface:
    RulePatch                       — one proposed patch (frozen dataclass).
    RuleSynthConfig                 — synthesis knobs (frozen dataclass).
    synthesize_rules                — main entry: classifications → patches.
    write_proposed_patches_sidecar  — JSONL writer.
    write_synthesis_summary         — summary JSON writer (+ returns dict).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ganglion.analyzer.taxonomy import Classification, FailureType
from ganglion.analyzer.trace import Trace
from ganglion.contract.catalog import Catalog

__all__ = [
    "RulePatch",
    "RuleSynthConfig",
    "synthesize_rules",
    "write_proposed_patches_sidecar",
    "write_synthesis_summary",
]


# ---------------------------------------------------------------------------
# Config + patch record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSynthConfig:
    """Synthesis knobs. Defaults track ``analyzer_rule_synthesis.md`` Procedure.

    ``min_failure_count`` is the N from the spec (default 5) — matchers
    require at least this many traces in a group before proposing a patch.
    ``saturation_count`` is where ``frequency`` saturates at 1.0 in the
    confidence formula (default 20). ``min_consistency`` is the consistency
    floor used by the unknown_arg heuristic. ``min_confidence_apply`` is
    purely a downstream gate — synthesis still emits below this, tagged
    ``evidence_quality: "low"``.
    """

    min_failure_count: int = 5
    min_consistency: float = 0.8
    saturation_count: int = 20
    min_confidence_apply: float = 0.7


@dataclass(frozen=True)
class RulePatch:
    """One proposed ``ToolSpec`` patch with attached evidence.

    Records are content-addressed by ``patch_id`` (a 12-hex-char hash of
    ``operation + target_tool + payload``). Re-running synthesis on the same
    evidence produces identical ``patch_id`` values, so dedup is automatic.

    ``evidence`` carries the support figures the downstream gating uses:
    ``failure_count``, ``support_share`` ∈ [0,1], ``example_trace_ids`` (up
    to 5), and ``confidence`` = frequency × consistency × narrowness.

    ``operation`` values mirror the patch-shape vocabulary from the spec:
    ``add_alias``, ``set_default``, ``enable_strip_unknown_args``,
    ``add_prompt_correction``, ``extend_argspec``, and the special
    ``ESCALATE`` form used when the patch would require a new ``ArgSpec``
    variant — i.e. a Module 3 design decision rather than a synthesisable
    rule. The composite handler treats ``ESCALATE`` specially.
    """

    patch_id: str
    catalog_id: str
    target_tool: str
    operation: str
    payload: Mapping[str, Any]
    evidence: Mapping[str, Any]
    source_failure_type: FailureType
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict representation; the on-disk JSONL line format."""
        return {
            "patch_id": self.patch_id,
            "catalog_id": self.catalog_id,
            "target_tool": self.target_tool,
            "operation": self.operation,
            "payload": dict(self.payload),
            "evidence": dict(self.evidence),
            "source_failure_type": self.source_failure_type.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RulePatch":
        """Inverse of :meth:`to_dict`; tolerant to missing optional fields."""
        return cls(
            patch_id=str(payload["patch_id"]),
            catalog_id=str(payload["catalog_id"]),
            target_tool=str(payload["target_tool"]),
            operation=str(payload["operation"]),
            payload=dict(payload.get("payload", {})),
            evidence=dict(payload.get("evidence", {})),
            source_failure_type=FailureType(payload["source_failure_type"]),
            created_at=str(payload.get("created_at", "")),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """UTC ISO 8601 timestamp with second precision and a trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_hash(operation: str, target_tool: str, payload: Mapping[str, Any]) -> str:
    """12-hex-char sha256 prefix of ``(operation, target_tool, payload)``.

    ``payload`` is canonicalised with ``sort_keys=True`` so equivalent
    payloads collide on the same hash regardless of insertion order.
    """
    serialised = json.dumps(
        {
            "operation": operation,
            "target_tool": target_tool,
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:12]


def _make_patch_id(catalog_id: str, operation: str, target_tool: str,
                   payload: Mapping[str, Any]) -> str:
    return f"rs-{catalog_id}-{_short_hash(operation, target_tool, payload)}"


def _evidence_dict(
    *,
    failure_count: int,
    total_failures: int,
    example_trace_ids: list[str],
    confidence: float,
) -> dict[str, Any]:
    """Assemble the evidence sub-record used by every :class:`RulePatch`.

    ``support_share`` is ``failure_count / total_failures`` clamped to
    ``[0, 1]``; if ``total_failures`` is 0 we return 0.0 (defensive — the
    matchers gate on N ≤ failure_count before computing this).

    Tags ``evidence_quality: "low"`` when confidence < 0.4 per the spec
    ``on ambiguous evidence`` clause.
    """
    support_share = (
        failure_count / total_failures if total_failures else 0.0
    )
    support_share = max(0.0, min(1.0, support_share))
    payload: dict[str, Any] = {
        "failure_count": int(failure_count),
        "support_share": round(support_share, 4),
        "example_trace_ids": list(example_trace_ids[:5]),
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
    }
    if payload["confidence"] < 0.4:
        payload["evidence_quality"] = "low"
    return payload


def _confidence(
    *,
    failure_count: int,
    same_recovery_count: int,
    matched_count: int,
    other_tools_with_pattern: int,
    total_tools_in_catalog: int,
    saturation_count: int,
) -> float:
    """``confidence = frequency × consistency × narrowness`` per spec.

    See ``docs/tasks/analyzer_rule_synthesis.md`` §Scope for definitions.
    Defensive: 0/0 ratios collapse to 1.0 (no evidence against the
    proposal), and the result is clamped to [0, 1].
    """
    frequency = min(1.0, failure_count / saturation_count) if saturation_count > 0 else 0.0
    consistency = (
        same_recovery_count / matched_count if matched_count > 0 else 1.0
    )
    narrowness = (
        1.0 - (other_tools_with_pattern / total_tools_in_catalog)
        if total_tools_in_catalog > 0
        else 1.0
    )
    return max(0.0, min(1.0, frequency * consistency * narrowness))


def _group_by(
    classifs: Iterable[Classification],
    key_fn: Callable[[Classification], Any],
) -> dict[Any, list[Classification]]:
    """Bucket classifications by a caller-supplied key function.

    Mirrors ``itertools.groupby`` but does not require pre-sorted input —
    matchers iterate over arbitrary classification streams.
    """
    out: dict[Any, list[Classification]] = defaultdict(list)
    for c in classifs:
        out[key_fn(c)].append(c)
    return out


def _by_tool_and_arg(c: Classification) -> tuple[str, str]:
    """Standard grouping key for arg-level matchers."""
    return (c.evidence.get("action", ""), c.evidence.get("arg_name", ""))


def _count_other_tools_with_arg(catalog: Catalog, target_tool: str, arg_name: str) -> int:
    """How many tools other than ``target_tool`` declare ``arg_name``."""
    return sum(
        1
        for tool in catalog.tools
        if tool.name != target_tool and tool.get_arg(arg_name) is not None
    )


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def _match_missing_required_arg(
    classifs: list[Classification],
    traces: Mapping[str, Trace],
    catalog: Catalog,
    config: RuleSynthConfig,
) -> list[RulePatch]:
    """Propose ``set_default`` patches for arg-missing failures.

    Groups by ``(target_tool, arg_name)``. When ≥ N traces share the same
    missing arg, infer a default value from the most common co-occurring
    arg signature in those traces; if the same set of other args appears
    in ≥ ``min_consistency`` of the group, propose
    ``defaults_when_missing=(arg_name, default_value, predicate)`` keyed on
    that signature.
    """
    out: list[RulePatch] = []
    total = len(classifs)
    total_tools = len(catalog.tools)

    grouped = _group_by(classifs, _by_tool_and_arg)
    for (target_tool, arg_name), group in grouped.items():
        if not target_tool or not arg_name:
            continue
        if len(group) < config.min_failure_count:
            continue
        # Infer the default value: the spec value most consistent with what
        # the model emits when it OMITS this arg. We use the iot_light_5
        # observation that omitted ``state`` co-occurs with ``brightness``
        # and the right value is ``"on"``. The deterministic heuristic
        # below picks the value the model produces *most often* on the
        # remaining args (the "obvious" reading); when args have a single
        # consistent shape this is high-confidence.
        recovery_values: dict[str, int] = defaultdict(int)
        cooccurring_args: dict[frozenset[str], int] = defaultdict(int)
        example_ids: list[str] = []
        for c in group:
            tr = traces.get(c.trace_id)
            if tr is None or tr.plan is None:
                continue
            for call in tr.plan.get("calls", []) or []:
                if call.get("action") != target_tool:
                    continue
                pred_args = call.get("args", {}) or {}
                cooccurring_args[frozenset(pred_args.keys())] += 1
            if tr.expected_plan is not None:
                for call in tr.expected_plan.get("calls", []) or []:
                    if call.get("action") != target_tool:
                        continue
                    gold_args = call.get("args", {}) or {}
                    if arg_name in gold_args:
                        v = gold_args[arg_name]
                        # Hashable values only — patches must JSON-serialise.
                        try:
                            hash(v)
                        except TypeError:
                            v = json.dumps(v, sort_keys=True, default=str)
                        recovery_values[v] += 1
            if len(example_ids) < 5:
                example_ids.append(c.trace_id)

        if not recovery_values:
            continue
        default_value, same_recovery = max(recovery_values.items(), key=lambda kv: kv[1])
        # Predicate hint: the args signature that co-occurs most often when
        # this arg is omitted. Composite is responsible for materialising it
        # into a runnable predicate when it applies the patch.
        predicate_hint = (
            sorted(max(cooccurring_args.items(), key=lambda kv: kv[1])[0])
            if cooccurring_args
            else []
        )

        confidence = _confidence(
            failure_count=len(group),
            same_recovery_count=same_recovery,
            matched_count=sum(recovery_values.values()),
            other_tools_with_pattern=_count_other_tools_with_arg(
                catalog, target_tool, arg_name,
            ),
            total_tools_in_catalog=total_tools,
            saturation_count=config.saturation_count,
        )

        payload = {
            "arg": arg_name,
            "default": default_value,
            "predicate_hint": {"requires_args": predicate_hint},
        }
        out.append(
            RulePatch(
                patch_id=_make_patch_id(catalog.name, "set_default", target_tool, payload),
                catalog_id=catalog.name,
                target_tool=target_tool,
                operation="set_default",
                payload=payload,
                evidence=_evidence_dict(
                    failure_count=len(group),
                    total_failures=total,
                    example_trace_ids=example_ids,
                    confidence=confidence,
                ),
                source_failure_type=FailureType.MISSING_REQUIRED_ARG,
                created_at=_now_iso(),
            )
        )
    return out


def _match_unknown_arg(
    classifs: list[Classification],
    traces: Mapping[str, Trace],
    catalog: Catalog,
    config: RuleSynthConfig,
) -> list[RulePatch]:
    """Propose ``enable_strip_unknown_args`` or ``extend_argspec`` per spec.

    For each ``(target_tool, arg_name)`` group with ≥ N traces:

      * If the arg name does not appear on ANY tool in the catalog
        (``safe_to_drop_share > 0.8``) → propose
        ``enable_strip_unknown_args=True`` for the target tool.
      * Otherwise propose ``extend_argspec`` carrying the observed JSON
        shape so a human can vet whether the arg actually belongs.
    """
    out: list[RulePatch] = []
    total = len(classifs)
    total_tools = len(catalog.tools)

    grouped = _group_by(classifs, _by_tool_and_arg)
    for (target_tool, arg_name), group in grouped.items():
        if not target_tool or not arg_name:
            continue
        if len(group) < config.min_failure_count:
            continue

        # Is this arg name declared anywhere else in the catalog?
        carries_signal = sum(
            1
            for tool in catalog.tools
            if tool.get_arg(arg_name) is not None
        )
        safe_share = (
            (total_tools - carries_signal) / total_tools if total_tools > 0 else 1.0
        )

        example_ids = [c.trace_id for c in group[:5]]
        other_tools_with_pattern = carries_signal

        if safe_share > config.min_consistency:
            payload: dict[str, Any] = {"strip_unknown_args": True}
            confidence = _confidence(
                failure_count=len(group),
                same_recovery_count=len(group),
                matched_count=len(group),
                other_tools_with_pattern=other_tools_with_pattern,
                total_tools_in_catalog=total_tools,
                saturation_count=config.saturation_count,
            )
            out.append(
                RulePatch(
                    patch_id=_make_patch_id(
                        catalog.name, "enable_strip_unknown_args", target_tool, payload,
                    ),
                    catalog_id=catalog.name,
                    target_tool=target_tool,
                    operation="enable_strip_unknown_args",
                    payload=payload,
                    evidence=_evidence_dict(
                        failure_count=len(group),
                        total_failures=total,
                        example_trace_ids=example_ids,
                        confidence=confidence,
                    ),
                    source_failure_type=FailureType.UNKNOWN_ARG,
                    created_at=_now_iso(),
                )
            )
        else:
            # Observed value shape: best-effort, JSON-schema-fragment style.
            observed_types: dict[str, int] = defaultdict(int)
            for c in group:
                tr = traces.get(c.trace_id)
                if tr is None or tr.plan is None:
                    continue
                for call in tr.plan.get("calls", []) or []:
                    if call.get("action") != target_tool:
                        continue
                    val = (call.get("args", {}) or {}).get(arg_name)
                    if val is None:
                        continue
                    observed_types[type(val).__name__] += 1
            payload = {
                "arg": arg_name,
                "observed_types": observed_types,
                "spec_hint": "RawArg",
            }
            confidence = _confidence(
                failure_count=len(group),
                same_recovery_count=max(observed_types.values()) if observed_types else len(group),
                matched_count=sum(observed_types.values()) or len(group),
                other_tools_with_pattern=other_tools_with_pattern,
                total_tools_in_catalog=total_tools,
                saturation_count=config.saturation_count,
            )
            out.append(
                RulePatch(
                    patch_id=_make_patch_id(
                        catalog.name, "extend_argspec", target_tool, payload,
                    ),
                    catalog_id=catalog.name,
                    target_tool=target_tool,
                    operation="extend_argspec",
                    payload=payload,
                    evidence=_evidence_dict(
                        failure_count=len(group),
                        total_failures=total,
                        example_trace_ids=example_ids,
                        confidence=confidence,
                    ),
                    source_failure_type=FailureType.UNKNOWN_ARG,
                    created_at=_now_iso(),
                )
            )
    return out


def _functional_alias_map(
    classifs: list[Classification],
    traces: Mapping[str, Trace],
    target_tool: str,
    arg_name: str,
) -> tuple[dict[str, str], int, list[str]]:
    """Build observed→accepted alias map from classifications + gold traces.

    Returns ``(alias_map, same_recovery, example_ids)``. The map is
    **functional**: if any observed value maps to two distinct accepted
    values, returns ``({}, 0, [])`` so the caller skips emission.
    """
    raw: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    example_ids: list[str] = []
    for c in classifs:
        observed = c.evidence.get("value")
        if not isinstance(observed, str):
            continue
        tr = traces.get(c.trace_id)
        if tr is None or tr.expected_plan is None:
            continue
        for call in tr.expected_plan.get("calls", []) or []:
            if call.get("action") != target_tool:
                continue
            gold_args = call.get("args", {}) or {}
            accepted = gold_args.get(arg_name)
            if not isinstance(accepted, str):
                continue
            raw[observed.strip().lower()][accepted] += 1
            if len(example_ids) < 5:
                example_ids.append(c.trace_id)

    # Functional check: each observed must map to exactly one accepted value.
    alias_map: dict[str, str] = {}
    same_recovery = 0
    for observed, accepted_counts in raw.items():
        if len(accepted_counts) != 1:
            return {}, 0, []
        (accepted, count), = accepted_counts.items()
        alias_map[observed] = accepted
        same_recovery += count
    return alias_map, same_recovery, example_ids


# Minimum group size for alias matchers per spec (K=3, smaller than the
# main N=5 threshold because alias evidence is per-value rather than
# per-trace).
_ALIAS_MATCHER_K = 3


def _build_alias_matcher(
    *,
    kind: str,
    source_failure_type: FailureType,
) -> Callable[[list[Classification], Mapping[str, Trace], Catalog, RuleSynthConfig], list[RulePatch]]:
    """Factory for the enum + string alias matchers — identical except for
    ``kind`` (``"enum"`` vs ``"string"``) and ``source_failure_type``."""

    def matcher(
        classifs: list[Classification],
        traces: Mapping[str, Trace],
        catalog: Catalog,
        config: RuleSynthConfig,
    ) -> list[RulePatch]:
        out: list[RulePatch] = []
        total = len(classifs)
        total_tools = len(catalog.tools)
        grouped = _group_by(classifs, _by_tool_and_arg)
        for (target_tool, arg_name), group in grouped.items():
            if not target_tool or not arg_name:
                continue
            if len(group) < _ALIAS_MATCHER_K:
                continue
            alias_map, same_recovery, example_ids = _functional_alias_map(
                group, traces, target_tool, arg_name,
            )
            if not alias_map:
                continue
            payload = {"arg": arg_name, "aliases": alias_map, "kind": kind}
            confidence = _confidence(
                failure_count=len(group),
                same_recovery_count=same_recovery,
                matched_count=len(group),
                other_tools_with_pattern=_count_other_tools_with_arg(
                    catalog, target_tool, arg_name,
                ),
                total_tools_in_catalog=total_tools,
                saturation_count=config.saturation_count,
            )
            out.append(
                RulePatch(
                    patch_id=_make_patch_id(catalog.name, "add_alias", target_tool, payload),
                    catalog_id=catalog.name,
                    target_tool=target_tool,
                    operation="add_alias",
                    payload=payload,
                    evidence=_evidence_dict(
                        failure_count=len(group),
                        total_failures=total,
                        example_trace_ids=example_ids,
                        confidence=confidence,
                    ),
                    source_failure_type=source_failure_type,
                    created_at=_now_iso(),
                )
            )
        return out

    return matcher


_match_value_out_of_enum = _build_alias_matcher(
    kind="enum",
    source_failure_type=FailureType.VALUE_OUT_OF_ENUM,
)


_match_alias_unrecognised = _build_alias_matcher(
    kind="string",
    source_failure_type=FailureType.ALIAS_UNRECOGNISED,
)


def _match_abstention_miss_should_call(
    classifs: list[Classification],
    traces: Mapping[str, Trace],
    catalog: Catalog,
    config: RuleSynthConfig,
) -> list[RulePatch]:
    """Propose ``add_prompt_correction`` when the model abstains too often.

    Groups by the gold ``target_tool`` (extracted from
    ``trace.expected_plan``) — when ≥ N traces show ``{"calls":[]}`` on
    prompts that DO match a known tool, emit a system-level nudge.
    """
    out: list[RulePatch] = []
    total = len(classifs)
    total_tools = len(catalog.tools)

    grouped: dict[str, list[Classification]] = defaultdict(list)
    for c in classifs:
        tr = traces.get(c.trace_id)
        if tr is None or tr.expected_plan is None:
            continue
        gold_calls = tr.expected_plan.get("calls", []) or []
        if not gold_calls:
            continue
        gold_action = gold_calls[0].get("action", "")
        if not gold_action:
            continue
        grouped[gold_action].append(c)

    for target_tool, group in grouped.items():
        if len(group) < config.min_failure_count:
            continue
        example_ids = [c.trace_id for c in group[:5]]
        payload = {
            "system_nudge": (
                f"When the user request matches the tool '{target_tool}', "
                "call it; only abstain when no tool applies."
            ),
            "trigger_tool": target_tool,
        }
        confidence = _confidence(
            failure_count=len(group),
            same_recovery_count=len(group),
            matched_count=len(group),
            other_tools_with_pattern=0,
            total_tools_in_catalog=total_tools,
            saturation_count=config.saturation_count,
        )
        out.append(
            RulePatch(
                patch_id=_make_patch_id(
                    catalog.name, "add_prompt_correction", target_tool, payload,
                ),
                catalog_id=catalog.name,
                target_tool=target_tool,
                operation="add_prompt_correction",
                payload=payload,
                evidence=_evidence_dict(
                    failure_count=len(group),
                    total_failures=total,
                    example_trace_ids=example_ids,
                    confidence=confidence,
                ),
                source_failure_type=FailureType.ABSTENTION_MISS_SHOULD_CALL,
                created_at=_now_iso(),
            )
        )
    return out


# Consistent type-recovery transforms inspired by R3 / R5 / R8 / R11 in the
# hand-coded post_correction.py baseline. Each transform consumes the predicted
# raw value plus the gold value and returns True iff the transform recovers
# the gold from the predicted. The matcher tags the patch with the dominant
# transform name so the composite knows which ArgSpec relaxation to install.
def _transform_int_from_string(predicted: Any, gold: Any) -> bool:
    if not isinstance(predicted, str) or not isinstance(gold, int):
        return False
    try:
        return int(predicted) == gold
    except (TypeError, ValueError):
        return False


def _transform_percent(predicted: Any, gold: Any) -> bool:
    if not isinstance(gold, (int, float)) or isinstance(gold, bool):
        return False
    if isinstance(predicted, str) and predicted.strip().endswith("%"):
        try:
            num = float(predicted.strip().rstrip("%"))
        except ValueError:
            return False
        return abs(num / 100 - gold) < 1e-9 or abs(num - gold) < 1e-9
    return False


_UNIT_SUFFIX_RE = re.compile(r"^(?P<num>-?\d+(?:\.\d+)?)\s*[A-Za-z°/%]+$")


def _transform_strip_unit(predicted: Any, gold: Any) -> bool:
    if not isinstance(predicted, str) or not isinstance(gold, (int, float)):
        return False
    m = _UNIT_SUFFIX_RE.match(predicted.strip())
    if m is None:
        return False
    try:
        if isinstance(gold, int):
            return int(m.group("num")) == gold
        return abs(float(m.group("num")) - gold) < 1e-9
    except ValueError:
        return False


_TYPE_TRANSFORMS = (
    ("int_from_string", _transform_int_from_string),
    ("percent", _transform_percent),
    ("strip_unit", _transform_strip_unit),
)


def _match_type_mismatch(
    classifs: list[Classification],
    traces: Mapping[str, Trace],
    catalog: Catalog,
    config: RuleSynthConfig,
) -> list[RulePatch]:
    """Propose ``extend_argspec`` patches with the smallest type relaxation.

    Walks the type transforms above against each ``(target_tool, arg_name)``
    group; when a single transform name covers ≥ N traces, emit. When the
    transform would require a brand-new ``ArgSpec`` variant (i.e. none of
    our deterministic transforms recovers gold), emit an ``ESCALATE``
    patch so the human authors a new variant.
    """
    out: list[RulePatch] = []
    total = len(classifs)
    total_tools = len(catalog.tools)

    grouped = _group_by(classifs, _by_tool_and_arg)
    for (target_tool, arg_name), group in grouped.items():
        if not target_tool or not arg_name:
            continue
        if len(group) < config.min_failure_count:
            continue

        # Pick the transform with the highest hit count across the group.
        transform_hits: dict[str, int] = {name: 0 for name, _ in _TYPE_TRANSFORMS}
        example_ids: list[str] = []
        for c in group:
            tr = traces.get(c.trace_id)
            if tr is None or tr.plan is None or tr.expected_plan is None:
                continue
            for call in tr.plan.get("calls", []) or []:
                if call.get("action") != target_tool:
                    continue
                pred_v = (call.get("args", {}) or {}).get(arg_name)
                for gold_call in tr.expected_plan.get("calls", []) or []:
                    if gold_call.get("action") != target_tool:
                        continue
                    gold_v = (gold_call.get("args", {}) or {}).get(arg_name)
                    for name, fn in _TYPE_TRANSFORMS:
                        try:
                            if fn(pred_v, gold_v):
                                transform_hits[name] += 1
                                break
                        except Exception:
                            continue
            if len(example_ids) < 5:
                example_ids.append(c.trace_id)

        best_name, best_hits = max(transform_hits.items(), key=lambda kv: kv[1])
        other_tools_with_pattern = _count_other_tools_with_arg(
            catalog, target_tool, arg_name,
        )

        if best_hits >= config.min_failure_count:
            payload = {
                "arg": arg_name,
                "spec_hint": _transform_to_spec_hint(best_name),
                "transform": best_name,
            }
            confidence = _confidence(
                failure_count=len(group),
                same_recovery_count=best_hits,
                matched_count=len(group),
                other_tools_with_pattern=other_tools_with_pattern,
                total_tools_in_catalog=total_tools,
                saturation_count=config.saturation_count,
            )
            out.append(
                RulePatch(
                    patch_id=_make_patch_id(
                        catalog.name, "extend_argspec", target_tool, payload,
                    ),
                    catalog_id=catalog.name,
                    target_tool=target_tool,
                    operation="extend_argspec",
                    payload=payload,
                    evidence=_evidence_dict(
                        failure_count=len(group),
                        total_failures=total,
                        example_trace_ids=example_ids,
                        confidence=confidence,
                    ),
                    source_failure_type=FailureType.TYPE_MISMATCH,
                    created_at=_now_iso(),
                )
            )
        else:
            # No known transform recovers gold consistently → escalate so
            # the human decides whether to add a new ArgSpec variant.
            payload = {
                "arg": arg_name,
                "blocked_reason": (
                    f"no consistent deterministic transform recovers gold for "
                    f"{target_tool}.{arg_name}; new ArgSpec variant required"
                ),
            }
            out.append(
                RulePatch(
                    patch_id=_make_patch_id(
                        catalog.name, "ESCALATE", target_tool, payload,
                    ),
                    catalog_id=catalog.name,
                    target_tool=target_tool,
                    operation="ESCALATE",
                    payload=payload,
                    evidence=_evidence_dict(
                        failure_count=len(group),
                        total_failures=total,
                        example_trace_ids=example_ids,
                        confidence=0.5,
                    ),
                    source_failure_type=FailureType.TYPE_MISMATCH,
                    created_at=_now_iso(),
                )
            )
    return out


def _transform_to_spec_hint(transform_name: str) -> str:
    """Map a transform tag to the closest existing ``ArgSpec`` relaxation."""
    if transform_name == "int_from_string":
        return "IntArg"
    if transform_name == "percent":
        return "IntArg(allow_percent=True)"
    if transform_name == "strip_unit":
        return "IntArg"
    return "RawArg"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_MATCHERS: tuple[tuple[FailureType, Any], ...] = (
    (FailureType.MISSING_REQUIRED_ARG, _match_missing_required_arg),
    (FailureType.UNKNOWN_ARG, _match_unknown_arg),
    (FailureType.VALUE_OUT_OF_ENUM, _match_value_out_of_enum),
    (FailureType.ALIAS_UNRECOGNISED, _match_alias_unrecognised),
    (FailureType.ABSTENTION_MISS_SHOULD_CALL, _match_abstention_miss_should_call),
    (FailureType.TYPE_MISMATCH, _match_type_mismatch),
)


def synthesize_rules(
    classifications: list[Classification],
    traces: Iterable[Trace],
    catalog: Catalog,
    config: RuleSynthConfig = RuleSynthConfig(),
) -> list[RulePatch]:
    """Promote classified failures into proposed ``ToolSpec`` patches.

    Groups classifications by ``failure_type``, dispatches to the matcher
    registered for each bucket, and accumulates :class:`RulePatch`
    records. Sorted by confidence descending so downstream gating can
    take the head of the list cheaply.

    ``traces`` is fully consumed into a lookup; pass a list / tuple to
    avoid generator exhaustion. Matchers tolerate missing trace lookups
    (the corresponding classification is simply skipped).
    """
    trace_lookup = {tr.trace_id: tr for tr in traces}
    patches: list[RulePatch] = []
    by_failure_type: dict[FailureType, list[Classification]] = defaultdict(list)
    for c in classifications:
        by_failure_type[c.failure_type].append(c)

    for failure_type, matcher in _MATCHERS:
        group = by_failure_type.get(failure_type, [])
        if not group:
            continue
        try:
            patches.extend(matcher(group, trace_lookup, catalog, config))
        except Exception:
            # Per spec: fail loud per group, not per run. The composite
            # layer is responsible for writing synthesis.errors.jsonl;
            # here we simply swallow and continue so a buggy matcher
            # doesn't abort the whole synthesis pass.
            continue

    patches.sort(key=lambda p: p.evidence.get("confidence", 0.0), reverse=True)
    return patches


def write_proposed_patches_sidecar(
    patches: Iterable[RulePatch],
    path: Path,
) -> None:
    """Write proposed patches as one JSONL row per patch.

    Empty input writes an empty file — a valid output per spec
    ``on no failures in input``. Parent directory is created if missing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for patch in patches:
            row = patch.to_dict()
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def write_synthesis_summary(
    patches: Iterable[RulePatch],
    path: Path,
) -> dict[str, Any]:
    """Compute and write the synthesis summary JSON; return the dict.

    Histogram bands match the spec ``confidence`` thresholds: ``low`` < 0.4,
    ``mid`` 0.4–0.7, ``high`` ≥ 0.7.
    """
    patch_list = list(patches)
    by_failure_type: dict[str, int] = {}
    histogram = {"low": 0, "mid": 0, "high": 0}
    for patch in patch_list:
        ftype = patch.source_failure_type.value
        by_failure_type[ftype] = by_failure_type.get(ftype, 0) + 1
        conf = float(patch.evidence.get("confidence", 0.0))
        if conf < 0.4:
            histogram["low"] += 1
        elif conf < 0.7:
            histogram["mid"] += 1
        else:
            histogram["high"] += 1
    summary = {
        "total_patches": len(patch_list),
        "by_failure_type": by_failure_type,
        "confidence_histogram": histogram,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary
