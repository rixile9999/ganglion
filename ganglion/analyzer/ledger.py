"""Append-only JSONL event ledger.

Implements [[analyzer_event_ledger]] (docs/tasks/analyzer_event_ledger.md) —
the durable substrate for every ``Contract.event`` clause in the task-doc
tree. It is deliberately **not** an in-process bus: nothing subscribes,
nothing fans out. A row is a fact; consumers (the console, the composites)
observe by reading files.

Public API:
    Event        — frozen, content-addressed dataclass (``to_dict`` / ``from_dict``).
    EVENT_NAMES  — the closed vocabulary; ``emit`` raises ``ValueError`` otherwise.
    emit         — idempotent append of one event row.
    read_events  — read rows back filtered by ``(catalog_id, run_id)``.
    events_path  — the target file an event with a given correlation lands in.

Layout: ``<base>/<catalog_id>/<run_id>/events.jsonl`` when the correlation
carries both ``catalog_id`` and ``run_id``; otherwise ``<base>/ledger/global.jsonl``.

Out of scope here (per spec): subscribing / fan-out, the semantics of each
event name (declared by the emitting task doc), a derived global index,
cross-process locking, retention.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from ganglion.analyzer.trace import now_iso

__all__ = [
    "EVENT_NAMES",
    "Event",
    "emit",
    "events_path",
    "read_events",
]

#: Closed event vocabulary — one entry per declaring task doc (see
#: [[analyzer_event_ledger]] §Scope). ``emit`` refuses any other name.
EVENT_NAMES: frozenset[str] = frozenset(
    {
        "lm.inference.completed",
        "lm.inference.failed",
        "analyzer.run.recorded",
        "analyzer.trace.recorded",
        "analyzer.label.recorded",
        "analyzer.failure.classified",
        "analyzer.rule.proposed",
        "analyzer.correction.attributed",
        "analyzer.metrics.summarized",
        "analyzer.patch.decided",
        "analyzer.compare.completed",
        "contract.catalog.compiled",
    }
)

_EVENTS_BASENAME = "events.jsonl"
_GLOBAL_LEDGER = ("ledger", "global.jsonl")

# Per-process lock: every append (read-ids + write) happens under it so two
# console threads never interleave lines in one file.
_LOCK = threading.RLock()


def _stable_json(obj: Any) -> str:
    """``json.dumps(obj, sort_keys=True, ensure_ascii=False)`` — the hash input."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _event_id(name: str, correlation: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        (name + _stable_json(correlation) + _stable_json(payload)).encode("utf-8")
    ).hexdigest()
    return f"ev-{digest[:16]}"


@dataclass(frozen=True)
class Event:
    """One ledger row.

    ``event_id`` is content-addressed over ``(name, correlation, payload)``
    so re-emitting the same fact is a no-op. ``refs`` holds ``{name: path}``
    pointers to the artifacts the event is about — the payload itself never
    carries raw model output or file contents.
    """

    event_id: str
    name: str
    ts: str
    producer: str
    correlation: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    refs: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the on-disk line format."""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "ts": self.ts,
            "producer": self.producer,
            "correlation": dict(self.correlation),
            "payload": dict(self.payload),
            "refs": {str(k): str(v) for k, v in dict(self.refs).items()},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Event":
        """Inverse of :meth:`to_dict`; tolerant of missing optional keys."""
        return cls(
            event_id=str(payload["event_id"]),
            name=str(payload["name"]),
            ts=str(payload.get("ts", "")),
            producer=str(payload.get("producer", "")),
            correlation=dict(payload.get("correlation") or {}),
            payload=dict(payload.get("payload") or {}),
            refs={str(k): str(v) for k, v in dict(payload.get("refs") or {}).items()},
        )


def events_path(base_dir: Path | str, correlation: Mapping[str, Any]) -> Path:
    """Target file for an event with ``correlation``.

    ``<base>/<catalog_id>/<run_id>/events.jsonl`` when both ids are present
    and non-empty; else ``<base>/ledger/global.jsonl``.
    """
    base = Path(base_dir)
    catalog_id = correlation.get("catalog_id")
    run_id = correlation.get("run_id")
    if isinstance(catalog_id, str) and catalog_id and isinstance(run_id, str) and run_id:
        return base / catalog_id / run_id / _EVENTS_BASENAME
    return base.joinpath(*_GLOBAL_LEDGER)


def _iter_file(path: Path) -> Iterator[Event]:
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
                row = json.loads(line)
            except json.JSONDecodeError:
                # Spec: skipped and counted in `ledger_corrupt_lines`.
                continue
            if not isinstance(row, Mapping):
                continue
            try:
                yield Event.from_dict(row)
            except (KeyError, TypeError, ValueError):
                continue


def emit(
    base_dir: Path | str,
    name: str,
    payload: Mapping[str, Any],
    *,
    producer: str,
    correlation: Mapping[str, Any],
    refs: Mapping[str, str] | None = None,
) -> Event:
    """Append one event row; idempotent on ``event_id``.

    ``event_id = "ev-" + sha256(name + stable_json(correlation) +
    stable_json(payload))[:16]``. When that id already exists in the target
    file nothing is written and the existing :class:`Event` is returned.

    Raises ``ValueError`` for a name outside :data:`EVENT_NAMES` (before any
    I/O) and ``TypeError`` for a payload that is not JSON-serialisable.
    ``OSError`` on append propagates (fail loud).
    """
    if name not in EVENT_NAMES:
        raise ValueError(
            f"unknown event name {name!r}; expected one of {sorted(EVENT_NAMES)}"
        )
    correlation = dict(correlation)
    payload = dict(payload)
    # Serialise eagerly so a non-JSON payload fails before the lock / write.
    event_id = _event_id(name, correlation, payload)
    target = events_path(base_dir, correlation)
    with _LOCK:
        for existing in _iter_file(target):
            if existing.event_id == event_id:
                return existing
        event = Event(
            event_id=event_id,
            name=name,
            ts=now_iso(),
            producer=producer,
            correlation=correlation,
            payload=payload,
            refs={str(k): str(v) for k, v in dict(refs or {}).items()},
        )
        line = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            # One write of the full line: no partial line on failure.
            fh.write(line)
    return event


def read_events(
    base_dir: Path | str,
    catalog_id: str | None = None,
    run_id: str | None = None,
) -> list[Event]:
    """Read events back, in file order.

    Both filters → that run's ``events.jsonl`` only. ``catalog_id`` only →
    every ``events.jsonl`` under ``<base>/<catalog_id>/``. ``run_id`` only →
    every run file whose derived run id matches. Neither → the whole tree
    plus ``ledger/global.jsonl`` (global rows last).
    """
    base = Path(base_dir)
    if catalog_id is not None and run_id is not None:
        return list(_iter_file(base / catalog_id / run_id / _EVENTS_BASENAME))
    out: list[Event] = []
    if catalog_id is not None:
        root = base / catalog_id
        if not root.exists():
            return []
        for path in sorted(root.rglob(_EVENTS_BASENAME)):
            out.extend(_iter_file(path))
        return out
    if not base.exists():
        return []
    global_path = base.joinpath(*_GLOBAL_LEDGER)
    for path in sorted(base.rglob(_EVENTS_BASENAME)):
        if path == global_path:
            continue
        if run_id is not None:
            # Derived run id: the path parts between the catalog id and the
            # file; the shard split rule is the trace store's (E13) — reuse
            # its helper so `console/<session>` resolves.
            from ganglion.analyzer.trace import _split_shard_parts

            split = _split_shard_parts(tuple(path.relative_to(base).parts[:-1]))
            if split is None or split[1] != run_id:
                continue
        out.extend(_iter_file(path))
    if run_id is None:
        out.extend(_iter_file(global_path))
    return out
