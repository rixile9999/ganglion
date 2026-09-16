"""Sidecar store for human verdicts on recorded traces.

Implements [[analyzer_label_store]] (docs/tasks/analyzer_label_store.md).
A benchmark trace carries its dataset gold in ``Trace.expected_plan``; a
console chat trace carries none. This module gives both a second,
human-authored gold channel (``labels.jsonl`` next to ``traces.jsonl``) and
defines the *single* join point, :func:`resolve_gold`, through which every
gold-dependent consumer ([[analyzer_failure_taxonomy]],
[[analyzer_rule_synthesis]], [[analyzer_compare]],
[[analyzer_correction_attribution]]) picks a gold. It also exports labels
as corrected-SFT rows in the exact shape ``lm/synth/pipeline.write_jsonl``
produces (errata E15: exactly five keys).

Public API:
    LabelRecord, LabelStore, make_label_id, family_id_for, zone_for,
    resolve_gold, gold_map, export_sft, write_exports, VERDICTS.

Out of scope (per spec): validating ``expected_plan`` (the producer — the
console's ``POST /api/labels`` — must have passed it through
``Catalog.parse_json_dsl(expected_plan, prompt=trace.prompt)``), DPO
pairs, running SFT, embedding-based families, multi-labeler adjudication,
mutating ``traces.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ganglion.analyzer import ledger
from ganglion.analyzer.trace import Trace, TraceStore, now_iso
from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.contract.types import ActionPlan, ToolCall

__all__ = [
    "VERDICTS",
    "LabelRecord",
    "LabelStore",
    "export_sft",
    "family_id_for",
    "gold_map",
    "make_label_id",
    "plan_from_dict",
    "resolve_gold",
    "write_exports",
    "zone_for",
]

#: The four verdict values ([[analyzer_label_store]] §Scope).
VERDICTS: frozenset[str] = frozenset({"correct", "incorrect", "should_abstain", "unsure"})

_LABELS_BASENAME = "labels.jsonl"
_TWO_SEGMENT_CATALOG_PREFIXES = frozenset({"compiled", "bfcl"})


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelRecord:
    """One immutable human verdict against a ``trace_id``.

    ``expected_plan`` is Action IR (``{"calls": [{"action", "args"}]}``);
    required for ``incorrect``, ``{"calls": []}`` for ``should_abstain``.
    ``endorsed`` says which plan the labeler endorsed when F⁰ ≠ Fᴷ
    (``"f0"`` | ``"fk"`` | ``"custom"`` | ``None``); ``saw_f0`` is the
    anti-circularity guard for [[analyzer_correction_attribution]].
    ``supersedes`` names an earlier ``label_id`` this one replaces (undo);
    lines are never deleted.
    """

    label_id: str
    trace_id: str
    case_id: str
    catalog_id: str
    run_id: str
    catalog_fingerprint: str
    model_id: str
    dataset_sha256: str
    origin: str  # "human:<alias>" | "dataset:<sha>" | "teacher:<model>"
    verdict: str  # "correct" | "incorrect" | "should_abstain" | "unsure"
    expected_plan: dict[str, Any] | None
    endorsed: str | None
    saw_f0: bool
    failure_hint: str | None
    order_sensitive: bool
    note: str
    family_id: str
    zone: str
    labeler: str
    label_batch_id: str
    time_to_label_ms: int | None
    supersedes: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the on-disk JSONL line format."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Mapping):
                value = dict(value)
            out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LabelRecord":
        """Inverse of :meth:`to_dict`; tolerant of missing optional keys."""
        expected = payload.get("expected_plan")
        ttl = payload.get("time_to_label_ms")
        return cls(
            label_id=str(payload["label_id"]),
            trace_id=str(payload["trace_id"]),
            case_id=str(payload.get("case_id", "")),
            catalog_id=str(payload["catalog_id"]),
            run_id=str(payload["run_id"]),
            catalog_fingerprint=str(payload.get("catalog_fingerprint", "") or ""),
            model_id=str(payload.get("model_id", "") or ""),
            dataset_sha256=str(payload.get("dataset_sha256", "") or ""),
            origin=str(payload.get("origin", "") or ""),
            verdict=str(payload["verdict"]),
            expected_plan=dict(expected) if isinstance(expected, Mapping) else None,
            endorsed=payload.get("endorsed"),
            saw_f0=bool(payload.get("saw_f0", False)),
            failure_hint=payload.get("failure_hint"),
            order_sensitive=bool(payload.get("order_sensitive", False)),
            note=str(payload.get("note", "") or ""),
            family_id=str(payload.get("family_id", "") or ""),
            zone=str(payload.get("zone", "") or ""),
            labeler=str(payload.get("labeler", "") or ""),
            label_batch_id=str(payload.get("label_batch_id", "") or ""),
            time_to_label_ms=int(ttl) if ttl is not None else None,
            supersedes=payload.get("supersedes"),
            created_at=str(payload.get("created_at", "") or ""),
        )


def make_label_id(
    trace_id: str,
    origin: str,
    verdict: str,
    expected_plan: Mapping[str, Any] | None,
    labeler: str,
    label_batch_id: str,
) -> str:
    """``"lb-" + sha256(stable_json([trace_id, origin, verdict, expected_plan,
    labeler, label_batch_id]))[:16]`` — re-recording the same verdict is a no-op."""
    blob = _stable_json(
        [
            trace_id,
            origin,
            verdict,
            dict(expected_plan) if isinstance(expected_plan, Mapping) else None,
            labeler,
            label_batch_id,
        ]
    )
    return "lb-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Families and zones
# ---------------------------------------------------------------------------

_HASH_TOKEN_RE = re.compile(r"(?:^|\s)#\d+\b")
_TRAILING_PUNCT_RE = re.compile(r"[\s\.\,\!\?\;\:。！？…~\-]+$")
_WS_RE = re.compile(r"\s+")


def _normalise_prompt(prompt: str) -> str:
    text = unicodedata.normalize("NFKC", prompt).casefold()
    text = _HASH_TOKEN_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    text = _TRAILING_PUNCT_RE.sub("", text)
    return text.strip()


def family_id_for(prompt: str, template_id: str | None = None) -> str:
    """Paraphrase family id.

    ``template_id`` → ``"fam-" + sha256("tpl:" + template_id)[:12]``; else the
    normalised prompt (NFKC → casefold → collapse whitespace → strip trailing
    punctuation and ``#N`` tokens) → ``"fam-" + sha256[:12]``.
    """
    if template_id:
        blob = "tpl:" + template_id
    else:
        blob = _normalise_prompt(prompt)
    return "fam-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def zone_for(
    family_id: str,
    *,
    train_share: int = 80,
    release_families: Collection[str] = (),
) -> str:
    """``"release"`` if in ``release_families``; else ``"train"`` when
    ``int(sha256(family_id)[:8], 16) % 100 < train_share``; else ``"dev"``.
    Deterministic — no first-seen ordering."""
    if family_id in set(release_families):
        return "release"
    bucket = int(hashlib.sha256(family_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < train_share else "dev"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _is_action_ir(plan: Any) -> bool:
    if not isinstance(plan, Mapping) or "calls" not in plan:
        return False
    calls = plan.get("calls")
    if not isinstance(calls, (list, tuple)):
        return False
    for call in calls:
        if not isinstance(call, Mapping) or not isinstance(call.get("action"), str):
            return False
        args = call.get("args", {})
        if args is not None and not isinstance(args, Mapping):
            return False
    return True


class LabelStore:
    """Append-only ``labels.jsonl`` per run; mirrors ``TraceStore``.

    ``append`` is idempotent on ``label_id``, validates the verdict and the
    ``expected_plan`` shape (never its content), refuses a ``trace_id`` the
    shard does not know (``check_trace=True``), and emits one
    ``analyzer.label.recorded`` ledger row per *newly* appended label — the
    :class:`~ganglion.analyzer.ledger.Event` is exposed as ``last_event``
    (``None`` on an idempotent skip) so producers can report its id
    without emitting a second row.
    """

    def __init__(self, base_dir: Path | str = "runs/traces", *, check_trace: bool = True) -> None:
        self._base_dir = Path(base_dir)
        self._lock = threading.RLock()
        self._check_trace = check_trace
        self._trace_store: TraceStore | None = None
        self.last_event: ledger.Event | None = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def path(self, catalog_id: str, run_id: str) -> Path:
        """``<base>/<catalog_id>/<run_id>/labels.jsonl``."""
        return self._base_dir / catalog_id / run_id / _LABELS_BASENAME

    def _traces(self) -> TraceStore:
        if self._trace_store is None:
            self._trace_store = TraceStore(self._base_dir)
        return self._trace_store

    def _validate(self, rec: LabelRecord) -> None:
        if rec.verdict not in VERDICTS:
            raise ValueError(f"verdict {rec.verdict!r} not in {sorted(VERDICTS)}")
        if rec.expected_plan is not None and not _is_action_ir(rec.expected_plan):
            raise ValueError("expected_plan must be Action IR: {'calls': [{'action', 'args'}]}")
        if rec.verdict == "incorrect" and rec.expected_plan is None:
            raise ValueError("verdict 'incorrect' requires an expected_plan")
        if not rec.label_id or not rec.trace_id or not rec.catalog_id or not rec.run_id:
            raise ValueError("label needs label_id, trace_id, catalog_id and run_id")
        if self._check_trace:
            trace = self._traces().by_id(
                rec.trace_id, catalog_id=rec.catalog_id, run_id=rec.run_id,
            )
            if trace is None:
                raise ValueError(
                    f"trace {rec.trace_id!r} is unknown to shard "
                    f"{rec.catalog_id}/{rec.run_id}"
                )

    def append(self, rec: LabelRecord) -> str:
        """Append one line; idempotent on ``label_id``. Returns the label id.

        Raises ``ValueError`` for a bad verdict, a malformed
        ``expected_plan``, or an unknown ``trace_id`` — nothing written,
        no event. ``OSError`` propagates.
        """
        self._validate(rec)
        with self._lock:
            self.last_event = None
            path = self.path(rec.catalog_id, rec.run_id)
            for existing in self._iter_file(path):
                if existing.label_id == rec.label_id:
                    return rec.label_id
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec.to_dict(), sort_keys=True, ensure_ascii=False)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self.last_event = ledger.emit(
                self._base_dir,
                "analyzer.label.recorded",
                {
                    "label_id": rec.label_id,
                    "trace_id": rec.trace_id,
                    "verdict": rec.verdict,
                    "origin": rec.origin,
                    "zone": rec.zone,
                },
                producer="analyzer.labels",
                correlation={
                    "catalog_id": rec.catalog_id,
                    "run_id": rec.run_id,
                    "trace_id": rec.trace_id,
                },
                refs={"labels": str(path)},
            )
            return rec.label_id

    @staticmethod
    def _iter_file(path: Path) -> Iterator[LabelRecord]:
        try:
            fh = path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    yield LabelRecord.from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    continue

    def _select(self, catalog_id: str | None, run_id: str | None) -> list[Path]:
        if catalog_id is not None and run_id is not None:
            return [self.path(catalog_id, run_id)]
        root = self._base_dir / catalog_id if catalog_id is not None else self._base_dir
        if not root.exists():
            return []
        paths = sorted(root.rglob(_LABELS_BASENAME))
        if run_id is None:
            return paths
        from ganglion.analyzer.trace import _split_shard_parts

        out: list[Path] = []
        for path in paths:
            split = _split_shard_parts(tuple(path.relative_to(self._base_dir).parts[:-1]))
            if split is not None and split[1] == run_id:
                out.append(path)
        return out

    def iter(
        self,
        catalog_id: str | None = None,
        run_id: str | None = None,
    ) -> Iterator[LabelRecord]:
        """Yield labels under the requested subtree, file order."""
        with self._lock:
            paths = self._select(catalog_id, run_id)
        for path in paths:
            with self._lock:
                rows = list(self._iter_file(path))
            yield from rows

    def latest_by_trace(self, catalog_id: str, run_id: str) -> dict[str, LabelRecord]:
        """``trace_id → latest label`` with ``supersedes`` chains resolved.

        A label named by another label's ``supersedes`` is dropped; among
        the survivors the newest ``created_at`` (then file order) wins.
        """
        rows = list(self.iter(catalog_id, run_id))
        superseded = {rec.supersedes for rec in rows if rec.supersedes}
        latest: dict[str, LabelRecord] = {}
        for rec in rows:
            if rec.label_id in superseded:
                continue
            current = latest.get(rec.trace_id)
            if current is None or rec.created_at >= current.created_at:
                latest[rec.trace_id] = rec
        return latest

    def count(self, catalog_id: str, run_id: str) -> int:
        """Number of label lines in the run's ``labels.jsonl``."""
        return sum(1 for _ in self.iter(catalog_id, run_id))


# ---------------------------------------------------------------------------
# Gold resolution (the single join point)
# ---------------------------------------------------------------------------


def plan_from_dict(plan: Mapping[str, Any] | None) -> ActionPlan | None:
    """Catalog-free ``ActionPlan`` from Action IR — **no validation**."""
    if not isinstance(plan, Mapping):
        return None
    calls_raw = plan.get("calls")
    if calls_raw is None and "action" in plan:
        calls_raw = [plan]
    if not isinstance(calls_raw, (list, tuple)):
        return None
    calls: list[ToolCall] = []
    for call in calls_raw:
        if not isinstance(call, Mapping):
            return None
        args = call.get("args", {}) or {}
        calls.append(
            ToolCall(
                action=str(call.get("action", "")),
                args=dict(args) if isinstance(args, Mapping) else {},
            )
        )
    return ActionPlan(calls=tuple(calls))


def resolve_gold(
    trace: Trace,
    labels: Mapping[str, LabelRecord],
) -> tuple[ActionPlan | None, str]:
    """The only gold join.

    Latest human label with verdict ``incorrect`` / ``should_abstain`` →
    its ``expected_plan`` (origin = the label's ``origin``); verdict
    ``correct`` → ``trace.plan`` as gold (same origin); else
    ``trace.expected_plan`` (origin ``"dataset"``); else ``(None, "none")``
    — the consumer treats that trace as *ungraded*, never as a failure.
    """
    label = labels.get(trace.trace_id)
    if label is not None:
        if label.verdict in {"incorrect", "should_abstain"}:
            plan = plan_from_dict(label.expected_plan)
            if plan is not None:
                return plan, label.origin or "human"
        elif label.verdict == "correct" and trace.plan is not None:
            plan = plan_from_dict(trace.plan)
            if plan is not None:
                return plan, label.origin or "human"
    if trace.expected_plan is not None:
        plan = plan_from_dict(trace.expected_plan)
        if plan is not None:
            return plan, "dataset"
    return None, "none"


def gold_map(
    traces: Iterable[Trace],
    labels: Mapping[str, LabelRecord],
) -> dict[str, ActionPlan]:
    """``case_id → gold`` for ``classify_traces(golds=)`` / ``synthesize_rules(golds=)``.

    Traces without a resolvable gold are omitted (ungraded). When several
    traces share a ``case_id`` (repeats) the lowest ``repeat_index`` wins.
    """
    out: dict[str, ActionPlan] = {}
    seen_index: dict[str, int] = {}
    for trace in traces:
        gold, _origin = resolve_gold(trace, labels)
        if gold is None:
            continue
        previous = seen_index.get(trace.case_id)
        if previous is not None and previous <= trace.repeat_index:
            continue
        out[trace.case_id] = gold
        seen_index[trace.case_id] = trace.repeat_index
    return out


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _lookup_trace(traces_by_id: Any, trace_id: str) -> Trace | None:
    if isinstance(traces_by_id, Mapping):
        return traces_by_id.get(trace_id)
    by_id = getattr(traces_by_id, "by_id", None)
    if callable(by_id):
        return by_id(trace_id)
    return None


def _sft_row(intent: str, plan: ActionPlan, verdict: str) -> dict[str, Any]:
    expected = json.dumps(plan.to_jsonable(), sort_keys=True, ensure_ascii=False)
    first_tool = plan.calls[0].action if plan.calls else "none"
    return {
        "intent": intent,
        "expected": expected,
        "strategy": f"human_label:{verdict}:{first_tool}",
        "teacher_score": 1.0,
        "id": hashlib.sha1((intent + "|" + expected).encode("utf-8")).hexdigest()[:12],
    }


def export_sft(
    labels: Iterable[LabelRecord],
    traces_by_id: Mapping[str, Trace] | TraceStore,
    *,
    catalog: Catalog,
    zones: Collection[str] = ("train",),
    include_correct: bool = False,
) -> list[dict[str, Any]]:
    """Corrected-SFT rows in the exact ``lm/synth/pipeline.write_jsonl`` shape.

    Exactly five keys per row (errata E15): ``intent``, ``expected``,
    ``strategy`` (``human_label:<verdict>:<first_tool>``), ``teacher_score``
    (``1.0``), ``id`` (``sha1(intent + "|" + expected)[:12]``). ``plan`` is
    the resolved gold's ``to_jsonable()``.

    Never exported: labels whose ``zone ∉ zones``, verdict ``unsure``, or
    (unless ``include_correct``) verdict ``correct``. Every row's
    ``expected`` must round-trip through ``catalog.parse_json_dsl``; rows
    that do not are dropped and counted in ``export_sft.dropped`` (a
    function attribute reset per call) — export continues.
    """
    zone_set = set(zones)
    rows: list[dict[str, Any]] = []
    dropped = 0
    seen_ids: set[str] = set()
    for label in labels:
        if label.zone not in zone_set or label.verdict == "unsure":
            continue
        if label.verdict == "correct" and not include_correct:
            continue
        trace = _lookup_trace(traces_by_id, label.trace_id)
        if trace is None:
            dropped += 1
            continue
        gold, _origin = resolve_gold(trace, {trace.trace_id: label})
        if gold is None:
            dropped += 1
            continue
        row = _sft_row(trace.prompt, gold, label.verdict)
        try:
            catalog.parse_json_dsl(row["expected"], prompt=trace.prompt)
        except (DSLValidationError, ValueError, TypeError):
            dropped += 1
            continue
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        rows.append(row)
    export_sft.dropped = dropped  # type: ignore[attr-defined]
    return rows


export_sft.dropped = 0  # type: ignore[attr-defined]


def write_exports(
    base_labels_dir: Path | str,
    catalog_id: str,
    sft_rows: Iterable[Mapping[str, Any]],
    hard_rows: Iterable[Mapping[str, Any]],
    *,
    zones: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Write ``<base_labels_dir>/<catalog_id>/{human_sft,hard_pool,zones}.jsonl``.

    ``hard_rows`` = the ``incorrect`` / ``should_abstain`` labels whose zone
    is ``dev`` (same row shape). ``zones`` (``family_id → zone``) fills
    ``zones.jsonl`` with one ``{family_id, zone}`` line per family; when
    omitted the file is written empty. Returns ``{"human_sft", "hard_pool",
    "zones"}`` → paths.
    """
    directory = Path(base_labels_dir) / catalog_id
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "human_sft": directory / "human_sft.jsonl",
        "hard_pool": directory / "hard_pool.jsonl",
        "zones": directory / "zones.jsonl",
    }
    with paths["human_sft"].open("w", encoding="utf-8") as fh:
        for row in sft_rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    with paths["hard_pool"].open("w", encoding="utf-8") as fh:
        for row in hard_rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    with paths["zones"].open("w", encoding="utf-8") as fh:
        for family_id, zone in sorted((zones or {}).items()):
            fh.write(
                json.dumps({"family_id": family_id, "zone": zone}, sort_keys=True, ensure_ascii=False)
                + "\n"
            )
    return {key: str(path) for key, path in paths.items()}
