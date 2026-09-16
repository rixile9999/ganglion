"""F⁰ / Fᴷ correction attribution and hook retirement.

Implements [[analyzer_correction_attribution]]
(docs/tasks/analyzer_correction_attribution.md) — the *contract* side of the
rule lifecycle. [[analyzer_rule_synthesis]] expands a catalog's correction
hooks; this module measures whether an existing hook still earns its place.
Each trace's **first model output** (``attempts[0]["content"]``) is
re-parsed twice — through the catalog with every hook stripped (F⁰, "the
model alone") and through the full catalog (Fᴷ) — and every pass/fail flip
is attributed to the ``(tool, hook_kind)`` that caused it by single-kind
ablation. Hooks that rescue nothing over a large enough sample become
``retire_rule`` proposals for the same ``proposed_patches.jsonl`` that
[[analyzer_patch_decision]] gates.

Hook kinds are exactly ``ALL_HOOK_KINDS`` from [[contract_patch_apply]]
(``defaults_when_missing``, ``strip_unknown_args`` — including
``Catalog.default_strip_unknown_args`` — and ``prompt_correction``).
``custom_validator`` is never stripped.

Two activity notions per hook and trace:

* **active** (``n_active``) — the hook *fired*: a ``defaults_when_missing``
  predicate returned True for a missing arg, ``strip_unknown_args`` found
  an undeclared arg, or ``prompt_correction`` returned different args.
  Detected with an instrumented copy of the catalog whose callables record
  themselves (bodies untouched — the plan is byte-identical).
* **necessary** — removing that kind alone changes the plan (ablation).
  ``rescued_by`` / ``regressed_by`` are the necessary hooks whose removal
  flips the graded outcome.

Public API:
    Attribution, attribute, summarize_corrections, write_corrections,
    retire_candidates_as_patches, hook_class, RETIRE_MIN_ACTIVE,
    RETIRE_MAX_UPPER.

Out of scope (per spec): attributing inside a validator chain, applying a
``retire_rule``, cross-run non-inferiority, repair-loop attribution
(attempts ≥ 1), choosing golds (``resolve_gold`` in [[analyzer_label_store]]).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from ganglion.analyzer.rules import RulePatch, make_patch_id
from ganglion.analyzer.taxonomy import FailureType
from ganglion.analyzer.trace import Trace, now_iso
from ganglion.contract.catalog import Catalog
from ganglion.contract.patch import ALL_HOOK_KINDS, strip_hooks
from ganglion.contract.tool_spec import DSLValidationError, ToolSpec
from ganglion.contract.types import ActionPlan

__all__ = [
    "RETIRE_MAX_UPPER",
    "RETIRE_MIN_ACTIVE",
    "Attribution",
    "attribute",
    "hook_class",
    "retire_candidates_as_patches",
    "summarize_corrections",
    "write_corrections",
]

#: Retire predicate: ``n_active ≥ 30``, zero rescues, and the one-sided 95 %
#: Clopper–Pearson upper bound of the rescue rate below 0.10.
RETIRE_MIN_ACTIVE = 30
RETIRE_MAX_UPPER = 0.10

_CORRECTIONS = "corrections.jsonl"
_CORRECTIONS_SUMMARY = "corrections.summary.json"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    """One trace's F⁰ / Fᴷ outcome and hook attribution.

    ``f0_ok`` / ``fk_ok`` are ``None`` when the trace is ungraded (no gold).
    Hook names are ``"<tool>:<kind>"`` strings (e.g.
    ``"set_light:strip_unknown_args"``). ``unattributable`` is set when the
    trace has no attempts or no gold; such traces count in ``n`` only.
    """

    trace_id: str
    case_id: str
    gold_origin: str
    f0_plan: dict[str, Any] | None
    fk_plan: dict[str, Any] | None
    f0_ok: bool | None
    fk_ok: bool | None
    rescued: bool
    regressed: bool
    rescued_by: tuple[str, ...]
    regressed_by: tuple[str, ...]
    necessary_hooks: tuple[str, ...]
    unattributable: bool
    active_hooks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the ``corrections.jsonl`` line format."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                value = list(value)
            out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Attribution":
        """Inverse of :meth:`to_dict`; tolerant of missing optional keys."""
        return cls(
            trace_id=str(payload["trace_id"]),
            case_id=str(payload.get("case_id", "")),
            gold_origin=str(payload.get("gold_origin", "none")),
            f0_plan=payload.get("f0_plan"),
            fk_plan=payload.get("fk_plan"),
            f0_ok=payload.get("f0_ok"),
            fk_ok=payload.get("fk_ok"),
            rescued=bool(payload.get("rescued", False)),
            regressed=bool(payload.get("regressed", False)),
            rescued_by=tuple(payload.get("rescued_by") or ()),
            regressed_by=tuple(payload.get("regressed_by") or ()),
            necessary_hooks=tuple(payload.get("necessary_hooks") or ()),
            unattributable=bool(payload.get("unattributable", False)),
            active_hooks=tuple(payload.get("active_hooks") or ()),
        )


# ---------------------------------------------------------------------------
# Hook classes
# ---------------------------------------------------------------------------


def hook_class(tool: ToolSpec | None, kind: str) -> str:
    """``conservative`` | ``rewriting`` per [[analyzer_correction_attribution]].

    ``strip_unknown_args`` → conservative; ``defaults_when_missing`` →
    conservative iff every defaulted arg is required on the tool, else
    rewriting; ``prompt_correction`` → rewriting.
    """
    if kind == "strip_unknown_args":
        return "conservative"
    if kind == "defaults_when_missing":
        if tool is None or not tool.defaults_when_missing:
            return "rewriting"
        for arg_name, _value, _pred in tool.defaults_when_missing:
            spec = tool.get_arg(arg_name)
            if spec is None or not spec.required:
                return "rewriting"
        return "conservative"
    return "rewriting"


def _defaulted_arg(tool: ToolSpec | None) -> str | None:
    """The single defaulted arg name when the tool has exactly one rule."""
    if tool is None or len(tool.defaults_when_missing) != 1:
        return None
    return tool.defaults_when_missing[0][0]


# ---------------------------------------------------------------------------
# Instrumented catalog (fire detection)
# ---------------------------------------------------------------------------


class _FireSink:
    """Collects ``"<tool>:<kind>"`` names of hooks that fired during one parse."""

    def __init__(self) -> None:
        self.fired: set[str] = set()

    def clear(self) -> None:
        self.fired.clear()

    def add(self, tool: str, kind: str) -> None:
        self.fired.add(f"{tool}:{kind}")


def _wrap_predicate(tool_name: str, predicate: Callable[..., bool], sink: _FireSink) -> Callable[..., bool]:
    def wrapped(args: Mapping[str, Any]) -> bool:
        result = bool(predicate(args))
        if result:
            sink.add(tool_name, "defaults_when_missing")
        return result

    return wrapped


def _wrap_prompt_correction(
    tool_name: str, fn: Callable[..., Mapping[str, Any]], sink: _FireSink,
) -> Callable[..., dict[str, Any]]:
    def wrapped(args: dict[str, Any], prompt: str) -> dict[str, Any]:
        out = dict(fn(args, prompt))
        if out != dict(args):
            sink.add(tool_name, "prompt_correction")
        return out

    return wrapped


def _instrumented(catalog: Catalog, sink: _FireSink) -> Catalog:
    """Copy of ``catalog`` whose default predicates and prompt corrections
    report into ``sink``. Hook bodies are unchanged, so the parsed plan is
    identical to the original catalog's."""
    tools = []
    for tool in catalog.tools:
        defaults = tuple(
            (arg, value, _wrap_predicate(tool.name, pred, sink))
            for arg, value, pred in tool.defaults_when_missing
        )
        pc = (
            _wrap_prompt_correction(tool.name, tool.prompt_correction, sink)
            if tool.prompt_correction is not None
            else None
        )
        tools.append(replace(tool, defaults_when_missing=defaults, prompt_correction=pc))
    return replace(catalog, tools=tuple(tools))


def _walk_calls(node: Any) -> Iterable[Mapping[str, Any]]:
    """Every ``{"action": str, ...}`` mapping in a payload, nested included."""
    if isinstance(node, Mapping):
        if isinstance(node.get("action"), str):
            yield node
        for value in node.values():
            yield from _walk_calls(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk_calls(item)


def _detect_strip(catalog: Catalog, payload: Mapping[str, Any], sink: _FireSink) -> None:
    """``strip_unknown_args`` is a flag, not a callable: it fires when an
    undeclared arg is present on a tool with stripping active."""
    for call in _walk_calls(payload):
        tool = catalog.get_tool(str(call.get("action", "")).strip())
        if tool is None:
            continue
        if not (tool.strip_unknown_args or catalog.default_strip_unknown_args):
            continue
        args = call.get("args", {})
        if not isinstance(args, Mapping) or not args:
            continue
        declared = {name for name, _ in tool.args}
        if any(key not in declared for key in args):
            sink.add(tool.name, "strip_unknown_args")


# ---------------------------------------------------------------------------
# Attribution engine
# ---------------------------------------------------------------------------


def _decode(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _parse_or_none(catalog: Catalog, payload: Mapping[str, Any], prompt: str) -> ActionPlan | None:
    """``DSLValidationError`` → ``None``; anything else is a catalog bug and propagates."""
    try:
        return catalog.parse_json_dsl(payload, prompt=prompt)
    except DSLValidationError:
        return None


def _first_attempt_content(trace: Trace) -> str | None:
    if not trace.attempts:
        return None
    first = trace.attempts[0]
    content = first.get("content") if isinstance(first, Mapping) else None
    return content if isinstance(content, str) else None


class _Attributor:
    """Holds the stripped / ablated / instrumented catalogs for one catalog."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.sink = _FireSink()
        self.instrumented = _instrumented(catalog, self.sink)
        self.f0_catalog = strip_hooks(catalog, ALL_HOOK_KINDS)
        self.ablated = {kind: strip_hooks(catalog, (kind,)) for kind in ALL_HOOK_KINDS}

    def _fallback_owners(self, payload: Mapping[str, Any], kind: str) -> list[str]:
        owners: list[str] = []
        for call in _walk_calls(payload):
            tool = self.catalog.get_tool(str(call.get("action", "")).strip())
            if tool is None or tool.name in owners:
                continue
            if kind == "defaults_when_missing" and tool.defaults_when_missing:
                owners.append(tool.name)
            elif kind == "strip_unknown_args" and (
                tool.strip_unknown_args or self.catalog.default_strip_unknown_args
            ):
                owners.append(tool.name)
            elif kind == "prompt_correction" and tool.prompt_correction is not None:
                owners.append(tool.name)
        return owners

    def attribute(self, trace: Trace, gold: ActionPlan | None, gold_origin: str) -> Attribution:
        raw = _first_attempt_content(trace)
        if raw is None or gold is None:
            return Attribution(
                trace_id=trace.trace_id,
                case_id=trace.case_id,
                gold_origin=gold_origin if gold is not None else "none",
                f0_plan=None,
                fk_plan=None,
                f0_ok=None,
                fk_ok=None,
                rescued=False,
                regressed=False,
                rescued_by=(),
                regressed_by=(),
                necessary_hooks=(),
                unattributable=True,
            )
        payload = _decode(raw)
        prompt = trace.prompt
        if payload is None:
            # Not JSON: graded fail on both sides; no hook can rescue it.
            return Attribution(
                trace_id=trace.trace_id,
                case_id=trace.case_id,
                gold_origin=gold_origin,
                f0_plan=None,
                fk_plan=None,
                f0_ok=False,
                fk_ok=False,
                rescued=False,
                regressed=False,
                rescued_by=(),
                regressed_by=(),
                necessary_hooks=(),
                unattributable=False,
            )

        self.sink.clear()
        fk = _parse_or_none(self.instrumented, payload, prompt)
        _detect_strip(self.catalog, payload, self.sink)
        fired = set(self.sink.fired)
        f0 = _parse_or_none(self.f0_catalog, payload, prompt)
        f0_ok = f0 is not None and f0 == gold
        fk_ok = fk is not None and fk == gold

        rescued_by: list[str] = []
        regressed_by: list[str] = []
        necessary: list[str] = []
        for kind in ALL_HOOK_KINDS:
            pk = _parse_or_none(self.ablated[kind], payload, prompt)
            if pk == fk:
                continue
            owners = sorted(
                name.split(":", 1)[0] for name in fired if name.endswith(":" + kind)
            )
            if not owners:
                owners = self._fallback_owners(payload, kind)
            hooks = [f"{tool}:{kind}" for tool in owners]
            necessary.extend(hooks)
            pk_ok = pk is not None and pk == gold
            if fk_ok and not pk_ok:
                rescued_by.extend(hooks)
            elif not fk_ok and pk_ok:
                regressed_by.extend(hooks)
        return Attribution(
            trace_id=trace.trace_id,
            case_id=trace.case_id,
            gold_origin=gold_origin,
            f0_plan=f0.to_jsonable() if f0 is not None else None,
            fk_plan=fk.to_jsonable() if fk is not None else None,
            f0_ok=f0_ok,
            fk_ok=fk_ok,
            rescued=(not f0_ok) and fk_ok,
            regressed=f0_ok and (not fk_ok),
            rescued_by=tuple(rescued_by),
            regressed_by=tuple(regressed_by),
            necessary_hooks=tuple(necessary),
            unattributable=False,
            active_hooks=tuple(sorted(fired)),
        )


def attribute(
    catalog: Catalog,
    trace: Trace,
    gold: ActionPlan | None,
    *,
    gold_origin: str,
) -> Attribution:
    """Attribute one trace.

    Reads **``attempts[0]["content"]`` only** (the model's first answer;
    native traces contribute their first ``{"calls": [...]}``).
    ``f0 = strip_hooks(catalog, ALL_HOOK_KINDS).parse_json_dsl(raw, prompt)``,
    ``fk = catalog.parse_json_dsl(raw, prompt)``; ``DSLValidationError`` →
    that side is ``None``. Single-kind ablation decides ``rescued_by`` /
    ``regressed_by`` as ``"<tool>:<kind>"`` strings. ``unattributable``
    when there are no attempts or no gold.
    """
    return _Attributor(catalog).attribute(trace, gold, gold_origin)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def cp95_upper_at_zero(n: int) -> float:
    """One-sided 95 % Clopper–Pearson upper bound of a rate at 0 successes in ``n``."""
    return 1.0 - 0.025 ** (1.0 / n) if n > 0 else 1.0


def _verdict(n_active: int, rescues: int, upper: float | None) -> str:
    if n_active < RETIRE_MIN_ACTIVE:
        return "insufficient_evidence"
    if rescues == 0:
        assert upper is not None
        return "retire_candidate" if upper < RETIRE_MAX_UPPER else "insufficient_evidence"
    return "keep"


def summarize_corrections(
    catalog: Catalog,
    traces: Iterable[Trace],
    golds: Mapping[str, tuple[ActionPlan, str]],
    *,
    attributions_out: list[Attribution] | None = None,
) -> dict[str, Any]:
    """Attribute every trace and fold the results.

    ``golds`` maps ``case_id → (gold, origin)`` (from ``resolve_gold``);
    traces without an entry are unattributable. Returns::

        {"n", "unattributable", "em_f0", "em_fk", "rescue", "regression",
         "n_rescued", "n_regressed",
         "by_gold_origin": {origin: {"n", "em_f0", "em_fk", "rescue", "regression",
                                      "n_rescued", "n_regressed"}},
         "by_hook": {"tool:kind": {"class", "arg", "n_active", "necessary", "rescues",
                                   "regressions", "verdict", "cp95_upper",
                                   "example_trace_ids"}},
         "shared", "n_retire_candidates"}

    ``em_*`` / ``rescue`` / ``regression`` are rates over attributable
    traces (so ``em_fk − em_f0 == rescue − regression``). ``cp95_upper =
    1 − 0.025 ** (1 / n_active)`` when ``rescues == 0`` else ``None``;
    ``verdict`` is ``retire_candidate`` iff ``n_active ≥ 30 and rescues ==
    0 and cp95_upper < 0.10``, ``insufficient_evidence`` iff ``n_active <
    30 or (rescues == 0 and cp95_upper ≥ 0.10)``, else ``keep``.
    Pass ``attributions_out`` to receive the per-trace records.
    """
    engine = _Attributor(catalog)
    attributions: list[Attribution] = []
    for trace in traces:
        entry = golds.get(trace.case_id)
        gold, origin = entry if entry is not None else (None, "none")
        attributions.append(engine.attribute(trace, gold, origin))
    if attributions_out is not None:
        attributions_out.extend(attributions)

    graded = [a for a in attributions if not a.unattributable]
    by_origin: dict[str, dict[str, Any]] = {}
    for a in graded:
        bucket = by_origin.setdefault(
            a.gold_origin, {"n": 0, "f0": 0, "fk": 0, "rescued": 0, "regressed": 0},
        )
        bucket["n"] += 1
        bucket["f0"] += int(bool(a.f0_ok))
        bucket["fk"] += int(bool(a.fk_ok))
        bucket["rescued"] += int(a.rescued)
        bucket["regressed"] += int(a.regressed)

    def _fold(bucket: Mapping[str, int]) -> dict[str, Any]:
        n = bucket["n"]
        return {
            "n": n,
            "em_f0": _rate(bucket["f0"], n),
            "em_fk": _rate(bucket["fk"], n),
            "rescue": _rate(bucket["rescued"], n),
            "regression": _rate(bucket["regressed"], n),
            "n_rescued": bucket["rescued"],
            "n_regressed": bucket["regressed"],
        }

    overall = {
        "n": len(graded),
        "f0": sum(int(bool(a.f0_ok)) for a in graded),
        "fk": sum(int(bool(a.fk_ok)) for a in graded),
        "rescued": sum(int(a.rescued) for a in graded),
        "regressed": sum(int(a.regressed) for a in graded),
    }

    hooks: dict[str, dict[str, Any]] = {}
    for a in graded:
        seen = set(a.active_hooks) | set(a.necessary_hooks) | set(a.rescued_by) | set(a.regressed_by)
        for name in seen:
            stats = hooks.setdefault(
                name,
                {"n_active": 0, "necessary": 0, "rescues": 0, "regressions": 0, "example_trace_ids": []},
            )
            if name in a.active_hooks or name in a.necessary_hooks:
                stats["n_active"] += 1
                if len(stats["example_trace_ids"]) < 5:
                    stats["example_trace_ids"].append(a.trace_id)
            if name in a.necessary_hooks:
                stats["necessary"] += 1
            if name in a.rescued_by:
                stats["rescues"] += 1
            if name in a.regressed_by:
                stats["regressions"] += 1

    by_hook: dict[str, dict[str, Any]] = {}
    n_retire = 0
    for name in sorted(hooks):
        stats = hooks[name]
        tool_name, kind = name.split(":", 1)
        tool = catalog.get_tool(tool_name)
        n_active = stats["n_active"]
        upper = round(cp95_upper_at_zero(n_active), 4) if stats["rescues"] == 0 else None
        verdict = _verdict(n_active, stats["rescues"], upper)
        n_retire += int(verdict == "retire_candidate")
        by_hook[name] = {
            "class": hook_class(tool, kind),
            "arg": _defaulted_arg(tool) if kind == "defaults_when_missing" else None,
            "n_active": n_active,
            "necessary": stats["necessary"],
            "rescues": stats["rescues"],
            "regressions": stats["regressions"],
            "verdict": verdict,
            "cp95_upper": upper,
            "example_trace_ids": list(stats["example_trace_ids"]),
        }

    shared = sum(
        1 for a in graded if len({h.split(":", 1)[1] for h in a.rescued_by}) >= 2
    )
    summary = _fold(overall)
    summary.update(
        {
            "n": len(attributions),
            "unattributable": len(attributions) - len(graded),
            "by_gold_origin": {origin: _fold(b) for origin, b in sorted(by_origin.items())},
            "by_hook": by_hook,
            "shared": shared,
            "n_retire_candidates": n_retire,
        }
    )
    return summary


def write_corrections(
    base_dir: Path | str,
    catalog_id: str,
    run_id: str,
    attributions: Iterable[Attribution],
    summary: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Write ``<run dir>/corrections.jsonl`` and ``corrections.summary.json``."""
    directory = Path(base_dir) / catalog_id / run_id
    directory.mkdir(parents=True, exist_ok=True)
    rows_path = directory / _CORRECTIONS
    summary_path = directory / _CORRECTIONS_SUMMARY
    with rows_path.open("w", encoding="utf-8") as fh:
        for a in attributions:
            fh.write(json.dumps(a.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
    summary_path.write_text(
        json.dumps(dict(summary), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rows_path, summary_path


def retire_candidates_as_patches(catalog_id: str, summary: Mapping[str, Any]) -> list[RulePatch]:
    """One ``retire_rule`` :class:`RulePatch` per ``retire_candidate`` hook.

    ``payload = {"hook_kind", "arg"}``, ``source_failure_type =
    NO_FAILURE``, ``evidence = {"failure_count": n_active, "support_share":
    1.0, "example_trace_ids": [...≤5], "confidence": 1 − cp95_upper}``,
    ``patch_id = make_patch_id(catalog_id, "retire_rule", tool, payload)``.
    """
    patches: list[RulePatch] = []
    by_hook = summary.get("by_hook") or {}
    for name in sorted(by_hook):
        stats = by_hook[name]
        if stats.get("verdict") != "retire_candidate":
            continue
        tool_name, kind = name.split(":", 1)
        payload = {"hook_kind": kind, "arg": stats.get("arg")}
        upper = stats.get("cp95_upper")
        confidence = round(1.0 - float(upper), 4) if upper is not None else 0.0
        patches.append(
            RulePatch(
                patch_id=make_patch_id(catalog_id, "retire_rule", tool_name, payload),
                catalog_id=catalog_id,
                target_tool=tool_name,
                operation="retire_rule",
                payload=payload,
                evidence={
                    "failure_count": int(stats.get("n_active", 0)),
                    "support_share": 1.0,
                    "example_trace_ids": list(stats.get("example_trace_ids") or [])[:5],
                    "confidence": confidence,
                },
                source_failure_type=FailureType.NO_FAILURE,
                created_at=now_iso(),
            )
        )
    return patches
