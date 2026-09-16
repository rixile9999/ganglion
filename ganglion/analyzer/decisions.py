"""Append-only decision ledger for proposed patches.

Implements [[analyzer_patch_decision]] (docs/tasks/analyzer_patch_decision.md).
[[analyzer_rule_synthesis]] *proposes*; nobody *applies*. This module
records what the human did with each proposal — staged (``blind`` before
any preview, ``after_preview`` optionally after, ``ported`` when a reviewed
code edit lands) — so the analyzer's precision is measured, not asserted.
Accepting changes nothing: builtin catalogs are Python modules, and a
patch is applied only through a reviewed edit referenced by ``commit_sha``.

Public API:
    PatchDecision, DecisionStore, STAGES, DECISIONS,
    make_decision_id, precision_summary.

Out of scope (per spec): applying a patch ([[contract_patch_apply]] is a
pure preview), computing the preview, proposing patches, inferring
``ported`` from ``describe()`` diffs, multi-reviewer voting.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ganglion.analyzer import ledger

__all__ = [
    "DECISIONS",
    "STAGES",
    "DecisionStore",
    "PatchDecision",
    "make_decision_id",
    "precision_summary",
]

STAGES: tuple[str, ...] = ("blind", "after_preview", "ported")
DECISIONS: tuple[str, ...] = ("accept", "hold", "reject", "retire", "ported")

_DECISIONS_BASENAME = "patch_decisions.jsonl"


def make_decision_id(patch_id: str, stage: str, decision: str, decided_at: str) -> str:
    """``"pd-" + sha256(stable_json([patch_id, stage, decision, decided_at]))[:16]``."""
    blob = json.dumps([patch_id, stage, decision, decided_at], sort_keys=True, ensure_ascii=False)
    return "pd-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PatchDecision:
    """One immutable decision on a ``patch_id`` at one stage.

    ``stage`` ∈ ``blind | after_preview | ported``; ``decision`` ∈
    ``accept | hold | reject | retire | ported``. A ``ported`` decision
    needs a non-empty ``commit_sha`` — the only way a patch is marked as
    applied.
    """

    decision_id: str
    patch_id: str
    catalog_id: str
    run_id: str
    catalog_fingerprint: str
    stage: str
    decision: str
    reason: str
    decided_by: str
    time_to_decide_ms: int | None
    commit_sha: str | None
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the on-disk line format."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PatchDecision":
        """Inverse of :meth:`to_dict`; tolerant of missing optional keys."""
        ttd = payload.get("time_to_decide_ms")
        return cls(
            decision_id=str(payload["decision_id"]),
            patch_id=str(payload["patch_id"]),
            catalog_id=str(payload["catalog_id"]),
            run_id=str(payload["run_id"]),
            catalog_fingerprint=str(payload.get("catalog_fingerprint", "") or ""),
            stage=str(payload["stage"]),
            decision=str(payload["decision"]),
            reason=str(payload.get("reason", "") or ""),
            decided_by=str(payload.get("decided_by", "") or ""),
            time_to_decide_ms=int(ttd) if ttd is not None else None,
            commit_sha=payload.get("commit_sha"),
            decided_at=str(payload.get("decided_at", "") or ""),
        )


def _validate(dec: PatchDecision) -> None:
    if dec.stage not in STAGES:
        raise ValueError(f"stage {dec.stage!r} not in {STAGES}")
    if dec.decision not in DECISIONS:
        raise ValueError(f"decision {dec.decision!r} not in {DECISIONS}")
    if dec.stage == "ported" or dec.decision == "ported":
        if dec.stage != "ported" or dec.decision != "ported":
            raise ValueError("stage 'ported' and decision 'ported' go together")
        if not dec.commit_sha:
            raise ValueError("a 'ported' decision requires a non-empty commit_sha")
    if not dec.decision_id or not dec.patch_id or not dec.catalog_id or not dec.run_id:
        raise ValueError("decision needs decision_id, patch_id, catalog_id and run_id")


class DecisionStore:
    """Append-only ``patch_decisions.jsonl`` per run.

    ``append`` is idempotent on ``decision_id`` and emits one
    ``analyzer.patch.decided`` ledger row per newly appended decision
    (exposed as ``last_event``; ``None`` on an idempotent skip). The store
    never validates that ``patch_id`` is in ``proposed_patches.jsonl`` —
    the producer (console) answers 404 for unknown patches.
    """

    def __init__(self, base_dir: Path | str = "runs/traces") -> None:
        self._base_dir = Path(base_dir)
        self._lock = threading.RLock()
        self.last_event: ledger.Event | None = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def path(self, catalog_id: str, run_id: str) -> Path:
        """``<base>/<catalog_id>/<run_id>/patch_decisions.jsonl``."""
        return self._base_dir / catalog_id / run_id / _DECISIONS_BASENAME

    def append(self, dec: PatchDecision) -> str:
        """Append one line; idempotent on ``decision_id``. Returns the id.

        ``ValueError`` for a stage / decision outside the vocabulary or a
        ``ported`` decision without ``commit_sha`` — nothing written.
        """
        _validate(dec)
        with self._lock:
            self.last_event = None
            path = self.path(dec.catalog_id, dec.run_id)
            for existing in self._iter_file(path):
                if existing.decision_id == dec.decision_id:
                    return dec.decision_id
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(dec.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
            self.last_event = ledger.emit(
                self._base_dir,
                "analyzer.patch.decided",
                {
                    "patch_id": dec.patch_id,
                    "stage": dec.stage,
                    "decision": dec.decision,
                    "decided_by": dec.decided_by,
                    "decision_id": dec.decision_id,
                },
                producer="analyzer.decisions",
                correlation={
                    "catalog_id": dec.catalog_id,
                    "run_id": dec.run_id,
                    "patch_id": dec.patch_id,
                },
                refs={"patch_decisions": str(path)},
            )
            return dec.decision_id

    @staticmethod
    def _iter_file(path: Path) -> Iterator[PatchDecision]:
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
                    yield PatchDecision.from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    continue

    def iter(self, catalog_id: str, run_id: str) -> Iterator[PatchDecision]:
        """Yield the run's decisions in file order."""
        with self._lock:
            rows = list(self._iter_file(self.path(catalog_id, run_id)))
        yield from rows

    def latest_by_patch(
        self, catalog_id: str, run_id: str,
    ) -> dict[str, dict[str, PatchDecision]]:
        """``patch_id → {stage → latest decision}`` (newest ``decided_at`` wins)."""
        out: dict[str, dict[str, PatchDecision]] = {}
        for dec in self.iter(catalog_id, run_id):
            stages = out.setdefault(dec.patch_id, {})
            current = stages.get(dec.stage)
            if current is None or dec.decided_at >= current.decided_at:
                stages[dec.stage] = dec
        return out


def _effective(stages: Mapping[str, PatchDecision]) -> PatchDecision | None:
    """The decision that stands: ``after_preview`` if present, else ``blind``."""
    return stages.get("after_preview") or stages.get("blind")


def precision_summary(
    patches: Iterable[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, PatchDecision]],
    *,
    conf_threshold: float = 0.7,
) -> dict[str, Any]:
    """Fold proposals + decisions into the precision figures.

    ``patch_acceptance_rate = accepted_blind ÷ n_proposed``;
    ``precision_at_conf = accepted_blind among patches with
    evidence.confidence ≥ conf_threshold ÷ n_conf_ge_threshold`` (both
    ``0.0`` on a zero denominator). ``held`` / ``rejected`` / ``retired``
    count the *effective* decision (``after_preview`` if present, else
    ``blind``); ``ported`` counts patches with a ``ported`` stage.
    ``by_operation[op] = {proposed, accepted}`` where ``accepted`` is the
    effective decision ``accept``.
    """
    patch_list = [dict(p) for p in patches]
    n_proposed = len(patch_list)
    n_conf = 0
    accepted_blind = 0
    accepted_after_preview = 0
    accepted_blind_at_conf = 0
    held = rejected = retired = ported = 0
    by_operation: dict[str, dict[str, int]] = {}
    for patch in patch_list:
        patch_id = str(patch.get("patch_id", ""))
        op = str(patch.get("operation", ""))
        evidence = patch.get("evidence") or {}
        conf = float(evidence.get("confidence", 0.0) or 0.0) if isinstance(evidence, Mapping) else 0.0
        at_conf = conf >= conf_threshold
        if at_conf:
            n_conf += 1
        stages = decisions.get(patch_id, {})
        blind = stages.get("blind")
        after = stages.get("after_preview")
        if blind is not None and blind.decision == "accept":
            accepted_blind += 1
            if at_conf:
                accepted_blind_at_conf += 1
        if after is not None and after.decision == "accept":
            accepted_after_preview += 1
        effective = _effective(stages)
        if effective is not None:
            if effective.decision == "hold":
                held += 1
            elif effective.decision == "reject":
                rejected += 1
            elif effective.decision == "retire":
                retired += 1
        if "ported" in stages and stages["ported"].decision == "ported":
            ported += 1
        bucket = by_operation.setdefault(op, {"proposed": 0, "accepted": 0})
        bucket["proposed"] += 1
        if effective is not None and effective.decision == "accept":
            bucket["accepted"] += 1
    return {
        "n_proposed": n_proposed,
        "n_conf_ge_threshold": n_conf,
        "accepted_blind": accepted_blind,
        "accepted_after_preview": accepted_after_preview,
        "held": held,
        "rejected": rejected,
        "retired": retired,
        "ported": ported,
        "patch_acceptance_rate": round(accepted_blind / n_proposed, 4) if n_proposed else 0.0,
        "precision_at_conf": round(accepted_blind_at_conf / n_conf, 4) if n_conf else 0.0,
        "conf_threshold": conf_threshold,
        "by_operation": by_operation,
    }
