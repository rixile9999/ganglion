"""Append-only JSONL store of every inference trace.

Implements [[analyzer_trace_store]] (docs/tasks/analyzer_trace_store.md) — the
substrate every other analyzer primitive reads from. Today's per-run artifacts
(`runs/m{2,3,4}/*.json`, `runs/bfcl/*_cases.jsonl`,
`runs/factory_bfcl/**/eval_holdout_cases.jsonl`) hold the same logical thing in
three scattered shapes; this module canonicalises one shape.

Public API:
    Trace                   — frozen, content-addressed dataclass for one
                              inference attempt (with its full repair chain).
    TraceStore              — append-only JSONL writer / reader rooted at
                              runs/traces/ (thread-safe, cross-process aware).
    attempts_from_raw       — normalise any client's ``raw`` into the attempt
                              chain shape written by ``run_dsl_with_repair``.
    raw_plan_from_attempts  — decode the last attempt that is a JSON mapping.
    now_iso                 — ISO-8601 UTC timestamp with a trailing ``Z``.

Out of scope here (per spec):
    - Failure-type classification ([[analyzer_failure_taxonomy]]).
    - Aggregation / metrics ([[analyzer_metrics]]).
    - Ledger rows ([[analyzer_event_ledger]]; producers emit them).
    - GC / retention.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = [
    "TRACE_SOURCES",
    "Trace",
    "TraceStore",
    "attempts_from_raw",
    "now_iso",
    "raw_plan_from_attempts",
]


#: Accepted ``Trace.source`` values (see [[analyzer_trace_store]] §Scope).
#: ``benchmark.iot`` / ``benchmark.bfcl`` are stamped by the benchmark adapters;
#: ``lm.invoke`` by the console chat path. Not enforced on ``from_dict`` so
#: older shards stay readable.
TRACE_SOURCES: frozenset[str] = frozenset({"benchmark.iot", "benchmark.bfcl", "lm.invoke"})

#: Top-level directories under ``base_dir`` whose catalog id spans TWO path
#: segments (``compiled/<sha12>``, ``bfcl/<category>``) — errata E13.
_TWO_SEGMENT_CATALOG_PREFIXES: frozenset[str] = frozenset({"compiled", "bfcl"})

_SHARD_BASENAME = "traces.jsonl"


def _stable_dumps(payload: Any) -> str:
    """JSON dump with stable key order so hashes are deterministic across runs."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def now_iso() -> str:
    """UTC ISO-8601 timestamp with second precision and a trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Raw normalisation ([[analyzer_trace_store]] §Scope "Raw normalisation helpers")
# ---------------------------------------------------------------------------


def _native_calls_to_action_ir(items: Any) -> list[dict[str, Any]]:
    """Convert native tool-call items to Action-IR ``{"action", "args"}`` calls.

    Accepts items in either the OpenAI ``{name, arguments}`` shape or the
    already-converted ``{action, args}`` shape (errata E12). ``arguments`` given
    as a JSON string is decoded when possible; anything else becomes ``{}``.
    """
    calls: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if "action" in item:
            action = item.get("action")
            args: Any = item.get("args", {})
        elif "name" in item:
            action = item.get("name")
            args = item.get("arguments", {})
        else:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError, ValueError):
                args = {}
        if not isinstance(args, Mapping):
            args = {}
        calls.append({"action": str(action), "args": dict(args)})
    return calls


def attempts_from_raw(raw: Any) -> tuple[dict[str, Any], ...]:
    """Normalise a client's ``ModelResult.raw`` (or ``exc.raw``) into attempts.

    The five raw shapes ([[analyzer_trace_store]] / [[lm_client]]):

    - ``str`` → one attempt ``{"attempt": 0, "content": raw}``.
    - ``{"attempts": [...], "final_content": str}`` (``run_dsl_with_repair`` /
      ``RepairExhaustedError``) → the attempts, verbatim (``attempt`` /
      ``content`` back-filled when an item lacks them).
    - ``{"content": str, "parse_strategy": ...}`` (freeform / local clients)
      → one attempt with that content.
    - ``list`` (native tool calls; items in ``{name, arguments}`` or
      ``{action, args}`` shape) → one attempt whose content is
      ``json.dumps({"calls": [...]})`` in Action-IR form.
    - ``None`` → ``()`` — the client attached no raw at all.

    Any other mapping (e.g. the rules client's ``{"calls": [...]}`` payload)
    becomes one attempt with ``content = json.dumps(raw)``; any other scalar is
    stringified.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        return ({"attempt": 0, "content": raw},)
    if isinstance(raw, Mapping):
        attempts_raw = raw.get("attempts")
        if isinstance(attempts_raw, (list, tuple)):
            out: list[dict[str, Any]] = []
            for idx, att in enumerate(attempts_raw):
                if isinstance(att, Mapping):
                    record = dict(att)
                    record.setdefault("attempt", idx)
                    content = record.get("content", "")
                    record["content"] = content if isinstance(content, str) else _stable_dumps(content)
                else:
                    record = {"attempt": idx, "content": str(att)}
                out.append(record)
            return tuple(out)
        content = raw.get("content")
        if isinstance(content, str):
            return ({"attempt": 0, "content": content},)
        return ({"attempt": 0, "content": _stable_dumps(raw)},)
    if isinstance(raw, (list, tuple)):
        calls = _native_calls_to_action_ir(raw)
        content = json.dumps({"calls": calls}, ensure_ascii=False, default=str)
        return ({"attempt": 0, "content": content},)
    return ({"attempt": 0, "content": str(raw)},)


def raw_plan_from_attempts(attempts: Any) -> dict[str, Any] | None:
    """``json.loads`` of the LAST attempt whose content decodes to a mapping.

    Returns ``None`` when no attempt carries decodable JSON-object content.
    This is what [[analyzer_failure_taxonomy]] matches on when validation
    failed (``Trace.plan is None``); it is *never* validated here.
    """
    if not attempts:
        return None
    for att in reversed(tuple(attempts)):
        if not isinstance(att, Mapping):
            continue
        content = att.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


# ---------------------------------------------------------------------------
# Trace record
# ---------------------------------------------------------------------------


def _derive_trace_id(
    *,
    case_id: str,
    model_id: str,
    run_id: str,
    catalog_id: str,
    repeat_index: int,
    attempts: tuple[Mapping[str, Any], ...],
) -> str:
    """Content-hash of ``(case_id, model_id, run_id, catalog_id, repeat_index, attempt chain)``.

    Re-running the same identity tuple with the same repair chain produces
    the same id, so duplicate appends are no-ops. ``repeat_index`` is part
    of the payload so ``--repeat N`` runs (and a console re-asking the same
    prompt) yield distinct traces even when the model output is identical.
    """
    attempt_chain = [
        {
            "attempt": int(att.get("attempt", idx)),
            "content": att.get("content", ""),
            "input_tokens": int(att.get("input_tokens", 0) or 0),
            "output_tokens": int(att.get("output_tokens", 0) or 0),
            "error_msg": att.get("error_msg"),
        }
        for idx, att in enumerate(attempts)
    ]
    payload = {
        "case_id": case_id,
        "model_id": model_id,
        "run_id": run_id,
        "catalog_id": catalog_id,
        "repeat_index": int(repeat_index),
        "attempts": attempt_chain,
    }
    digest = hashlib.sha256(_stable_dumps(payload).encode("utf-8")).hexdigest()
    return f"tr-{digest[:16]}"


@dataclass(frozen=True)
class Trace:
    """One inference attempt with its full repair chain.

    Content-addressed by ``trace_id``. Re-ingesting the same logical inference
    collides on ``trace_id`` and ``TraceStore.append`` becomes a no-op, so
    benchmark replays are safe.

    ``plan`` is the **validated** ``ActionPlan`` as JSON (``None`` whenever
    validation failed). ``raw_plan`` is the decoded-but-unvalidated JSON of the
    last attempt that parsed as an object — set even on failure, so the
    taxonomy can classify what the model actually said. Never write an
    unvalidated dict into ``plan`` ([[analyzer_trace_store]] §on violation).
    """

    case_id: str
    catalog_id: str
    run_id: str
    source: str
    prompt: str
    raw_output: str
    parse_strategy: str
    latency_ms: float
    input_tokens_total: int
    output_tokens_total: int
    model_id: str
    timestamp: str
    attempts: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    expected_plan: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    error_type: str | None = None
    raw_plan: dict[str, Any] | None = None
    repeat_index: int = 0
    trace_id: str = ""

    def __post_init__(self) -> None:
        # Auto-fill trace_id from content hash if caller didn't supply one.
        # Frozen dataclass: bypass __setattr__ via object.__setattr__.
        if not self.trace_id:
            tid = _derive_trace_id(
                case_id=self.case_id,
                model_id=self.model_id,
                run_id=self.run_id,
                catalog_id=self.catalog_id,
                repeat_index=self.repeat_index,
                attempts=self.attempts,
            )
            object.__setattr__(self, "trace_id", tid)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict representation; the on-disk JSONL line format."""
        return {
            "trace_id": self.trace_id,
            "case_id": self.case_id,
            "catalog_id": self.catalog_id,
            "run_id": self.run_id,
            "source": self.source,
            "prompt": self.prompt,
            "expected_plan": self.expected_plan,
            "raw_output": self.raw_output,
            "attempts": [dict(att) for att in self.attempts],
            "parse_strategy": self.parse_strategy,
            "error_type": self.error_type,
            "plan": self.plan,
            "raw_plan": self.raw_plan,
            "repeat_index": self.repeat_index,
            "latency_ms": self.latency_ms,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Trace":
        """Inverse of ``to_dict``; tolerant to missing optional fields.

        Shards written before the 2026-09-16 revision lack ``raw_plan`` and
        ``repeat_index``; they load as ``None`` / ``0``.
        """
        attempts_raw = payload.get("attempts") or ()
        attempts = tuple(dict(att) for att in attempts_raw)
        raw_plan = payload.get("raw_plan")
        if not isinstance(raw_plan, dict):
            raw_plan = None
        return cls(
            case_id=str(payload["case_id"]),
            catalog_id=str(payload["catalog_id"]),
            run_id=str(payload["run_id"]),
            source=str(payload["source"]),
            prompt=str(payload.get("prompt", "")),
            raw_output=str(payload.get("raw_output", "")),
            parse_strategy=str(payload.get("parse_strategy", "failed")),
            latency_ms=float(payload.get("latency_ms", 0.0) or 0.0),
            input_tokens_total=int(payload.get("input_tokens_total", 0) or 0),
            output_tokens_total=int(payload.get("output_tokens_total", 0) or 0),
            model_id=str(payload["model_id"]),
            timestamp=str(payload.get("timestamp", "")),
            attempts=attempts,
            expected_plan=payload.get("expected_plan"),
            plan=payload.get("plan"),
            error_type=payload.get("error_type"),
            raw_plan=raw_plan,
            repeat_index=int(payload.get("repeat_index", 0) or 0),
            trace_id=str(payload.get("trace_id", "")),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _split_shard_parts(parts: tuple[str, ...]) -> tuple[str, str] | None:
    """Apply the errata-E13 split rule to the path parts before ``traces.jsonl``.

    ``parts[0] in {"compiled", "bfcl"}`` → the catalog id is the first TWO
    segments, else ONE; the remainder joined by ``/`` is the run id. Returns
    ``None`` when there is no run segment left.
    """
    if not parts:
        return None
    n_catalog = 2 if parts[0] in _TWO_SEGMENT_CATALOG_PREFIXES and len(parts) >= 2 else 1
    run_parts = parts[n_catalog:]
    if not run_parts:
        return None
    return "/".join(parts[:n_catalog]), "/".join(run_parts)


class TraceStore:
    """Append-only JSONL writer rooted at ``runs/traces/``.

    Layout: ``<base_dir>/<catalog_id>/<run_id>/traces.jsonl``. Sidecars
    (``manifest.json``, ``labels.jsonl``, ``classified.jsonl``, …) live beside
    the shard and are ignored here — the store reads only ``traces.jsonl``.

    Idempotent on ``trace_id`` collisions: a duplicate append is silently
    skipped, so benchmark replays don't double-count.

    Thread-safe: a ``threading.RLock`` guards ``append`` / ``iter`` / ``by_id``.
    Cross-process aware: ``_maybe_refresh`` re-reads a shard whose
    ``(size, mtime)`` changed since it was last loaded, so a console process
    sees runs written by a CLI process. Cross-process *writers* must never
    share a shard (each run id is written by exactly one producer).
    """

    def __init__(self, base_dir: Path | str = "runs/traces") -> None:
        self._base_dir = Path(base_dir)
        self._lock = threading.RLock()
        # Per-shard (catalog_id, run_id) → set of known trace_ids. Populated
        # lazily on first read/write of each shard, then kept in sync by
        # append and by `_maybe_refresh`.
        self._shard_index: dict[tuple[str, str], set[str]] = {}
        # Per-shard (size, mtime_ns) observed at last load; None = absent.
        self._shard_stat: dict[tuple[str, str], tuple[int, int] | None] = {}
        # Global trace_id → Trace cache for `by_id`. Built lazily on first
        # lookup, then updated incrementally on every append / refresh.
        self._by_id_cache: dict[str, Trace] | None = None
        # Observability: number of shard reloads caused by external writes.
        self.shard_refresh_count: int = 0

    # -- paths -------------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        """Root directory of the store (``runs/traces`` by default)."""
        return self._base_dir

    def shard_path(self, catalog_id: str, run_id: str) -> Path:
        """``<base>/<catalog_id>/<run_id>/traces.jsonl``.

        ``catalog_id`` / ``run_id`` may contain ``/`` (``bfcl/<cat>``,
        ``compiled/<sha12>``, ``console/<session>``); the nesting is preserved
        verbatim so the tree mirrors the ids.
        """
        return self._base_dir / catalog_id / run_id / _SHARD_BASENAME

    # Back-compat private alias (pre-revision callers).
    _shard_path = shard_path

    def list_shards(self) -> list[tuple[str, str]]:
        """Every ``traces.jsonl`` under base as sorted ``(catalog_id, run_id)``.

        Split rule (errata E13) over the relative path parts before
        ``traces.jsonl``: ``compiled/…`` and ``bfcl/…`` catalog ids span two
        segments, every other catalog id one; the rest joined by ``/`` is the
        run id (so ``iot_light_5/console/s-1`` → ``("iot_light_5", "console/s-1")``).
        """
        root = self._base_dir
        if not root.exists():
            return []
        shards: set[tuple[str, str]] = set()
        for path in root.rglob(_SHARD_BASENAME):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts[:-1]
            split = _split_shard_parts(tuple(parts))
            if split is not None:
                shards.add(split)
        return sorted(shards)

    # -- shard index / refresh -------------------------------------------

    @staticmethod
    def _stat(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def _maybe_refresh(self, catalog_id: str, run_id: str) -> bool:
        """(Re)load a shard's id index when its ``(size, mtime)`` changed.

        Returns ``True`` when the shard was (re)read. First touch of a shard
        always loads it; later calls are no-ops unless another process wrote
        to the file.
        """
        key = (catalog_id, run_id)
        path = self.shard_path(catalog_id, run_id)
        current = self._stat(path)
        already_loaded = key in self._shard_index
        if already_loaded and self._shard_stat.get(key) == current:
            return False
        traces = list(self._iter_file(path))
        self._shard_index[key] = {tr.trace_id for tr in traces}
        self._shard_stat[key] = current
        if self._by_id_cache is not None:
            for tr in traces:
                self._by_id_cache[tr.trace_id] = tr
        if already_loaded:
            self.shard_refresh_count += 1
        return True

    def _refresh_all(self) -> None:
        for catalog_id, run_id in self.list_shards():
            self._maybe_refresh(catalog_id, run_id)

    # -- write ---------------------------------------------------------------

    def append(self, trace: Trace) -> str:
        """Write one JSONL line; idempotent on duplicate ``trace_id``.

        Returns the trace_id either way.
        """
        with self._lock:
            key = (trace.catalog_id, trace.run_id)
            self._maybe_refresh(*key)
            index = self._shard_index[key]
            if trace.trace_id in index:
                return trace.trace_id
            path = self.shard_path(*key)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            index.add(trace.trace_id)
            # Record our own write so the next `_maybe_refresh` is a no-op.
            self._shard_stat[key] = self._stat(path)
            if self._by_id_cache is not None:
                self._by_id_cache[trace.trace_id] = trace
            return trace.trace_id

    # -- read ----------------------------------------------------------------

    def _select_shards(
        self,
        catalog_id: str | None,
        run_id: str | None,
    ) -> list[tuple[str, str]]:
        if catalog_id is not None and run_id is not None:
            return [(catalog_id, run_id)]
        if catalog_id is not None:
            # Walk under the catalog directory directly so catalog ids with
            # arbitrary nesting (legacy `bfcl/<cat>/<case>`) still resolve.
            root = self._base_dir / catalog_id
            if not root.exists():
                return []
            found: list[tuple[str, str]] = []
            for path in sorted(root.rglob(_SHARD_BASENAME)):
                rel_parts = path.relative_to(root).parts[:-1]
                if not rel_parts:
                    continue
                found.append((catalog_id, "/".join(rel_parts)))
            return found
        shards = self.list_shards()
        if run_id is not None:
            shards = [s for s in shards if s[1] == run_id]
        return shards

    def iter(
        self,
        catalog_id: str | None = None,
        run_id: str | None = None,
    ) -> Iterator[Trace]:
        """Yield Trace objects under the requested subtree.

        With both filters supplied: reads exactly one JSONL file.
        With ``catalog_id`` only: walks every ``run_id`` under that catalog.
        With ``run_id`` only: matches the *derived* run id from
        ``list_shards`` (so ``run_id="console/s-1"`` works).
        With neither: walks the whole ``runs/traces/`` tree.

        Each shard is read under the lock and yielded outside it, so a
        partially-consumed iterator never blocks writers on other threads.
        """
        with self._lock:
            shards = self._select_shards(catalog_id, run_id)
        for shard_catalog, shard_run in shards:
            with self._lock:
                self._maybe_refresh(shard_catalog, shard_run)
                rows = list(self._iter_file(self.shard_path(shard_catalog, shard_run)))
            yield from rows

    @staticmethod
    def _iter_file(path: Path) -> Iterator[Trace]:
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
                    yield Trace.from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    # Malformed record → drop (spec: log + `ingest_dropped_rate`).
                    continue

    def by_id(
        self,
        trace_id: str,
        *,
        catalog_id: str | None = None,
        run_id: str | None = None,
    ) -> Trace | None:
        """Direct lookup.

        When the ``(catalog_id, run_id)`` pair is given only that shard is
        opened. Otherwise a lazy global index is built on first call and
        updated incrementally by ``append`` / ``_maybe_refresh``; a miss
        triggers one refresh sweep so traces written by another process are
        found.
        """
        with self._lock:
            if catalog_id is not None and run_id is not None:
                self._maybe_refresh(catalog_id, run_id)
                if self._by_id_cache is not None:
                    hit = self._by_id_cache.get(trace_id)
                    if hit is not None and hit.catalog_id == catalog_id and hit.run_id == run_id:
                        return hit
                for tr in self._iter_file(self.shard_path(catalog_id, run_id)):
                    if tr.trace_id == trace_id:
                        return tr
                return None
            if self._by_id_cache is None:
                self._by_id_cache = {}
                self._refresh_all()
                for tr in self.iter():
                    self._by_id_cache[tr.trace_id] = tr
            hit = self._by_id_cache.get(trace_id)
            if hit is None:
                self._refresh_all()
                hit = self._by_id_cache.get(trace_id)
            return hit
