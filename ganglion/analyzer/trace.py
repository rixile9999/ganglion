"""Append-only JSONL store of every inference trace.

Implements [[analyzer_trace_store]] (docs/tasks/analyzer_trace_store.md) — the
substrate every other analyzer primitive reads from. Today's per-run artifacts
(`runs/m{2,3,4}/*.json`, `runs/bfcl/*_cases.jsonl`,
`runs/factory_bfcl/**/eval_holdout_cases.jsonl`) hold the same logical thing in
three scattered shapes; this module canonicalises one shape.

Public API:
    Trace       — frozen, content-addressed dataclass for one inference attempt
                  (with its full repair chain).
    TraceStore  — append-only JSONL writer / reader rooted at runs/traces/.

Out of scope here (per spec):
    - Failure-type classification ([[analyzer_failure_taxonomy]]).
    - Aggregation / metrics ([[analyzer_metrics]]).
    - Event-bus wiring (handled by the [[factory_pipeline]] composite later).
    - GC / retention.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = ["Trace", "TraceStore"]


def _stable_dumps(payload: Any) -> str:
    """JSON dump with stable key order so hashes are deterministic across runs."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _derive_trace_id(
    *,
    case_id: str,
    model_id: str,
    run_id: str,
    attempts: tuple[Mapping[str, Any], ...],
) -> str:
    """Content-hash of (case_id, model_id, run_id, attempt_index_chain).

    Re-running the same `(case_id, model_id, run_id)` with the same repair
    chain produces the same id, so duplicate appends are no-ops.
    """
    attempt_chain = [
        {
            "attempt": int(att.get("attempt", idx)),
            "content": att.get("content", ""),
            "input_tokens": int(att.get("input_tokens", 0)),
            "output_tokens": int(att.get("output_tokens", 0)),
            "error_msg": att.get("error_msg"),
        }
        for idx, att in enumerate(attempts)
    ]
    payload = {
        "case_id": case_id,
        "model_id": model_id,
        "run_id": run_id,
        "attempts": attempt_chain,
    }
    digest = hashlib.sha256(_stable_dumps(payload).encode("utf-8")).hexdigest()
    return f"tr-{digest[:16]}"


@dataclass(frozen=True)
class Trace:
    """One inference attempt with its full repair chain.

    Content-addressed by `trace_id`. Re-ingesting the same logical inference
    collides on `trace_id` and `TraceStore.append` becomes a no-op, so
    benchmark replays are safe.
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
    trace_id: str = ""

    def __post_init__(self) -> None:
        # Auto-fill trace_id from content hash if caller didn't supply one.
        # Frozen dataclass: bypass __setattr__ via object.__setattr__.
        if not self.trace_id:
            tid = _derive_trace_id(
                case_id=self.case_id,
                model_id=self.model_id,
                run_id=self.run_id,
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
            "latency_ms": self.latency_ms,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Trace":
        """Inverse of `to_dict`; tolerant to missing optional fields."""
        attempts_raw = payload.get("attempts") or ()
        attempts = tuple(dict(att) for att in attempts_raw)
        return cls(
            case_id=str(payload["case_id"]),
            catalog_id=str(payload["catalog_id"]),
            run_id=str(payload["run_id"]),
            source=str(payload["source"]),
            prompt=str(payload.get("prompt", "")),
            raw_output=str(payload.get("raw_output", "")),
            parse_strategy=str(payload.get("parse_strategy", "failed")),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            input_tokens_total=int(payload.get("input_tokens_total", 0)),
            output_tokens_total=int(payload.get("output_tokens_total", 0)),
            model_id=str(payload["model_id"]),
            timestamp=str(payload.get("timestamp", "")),
            attempts=attempts,
            expected_plan=payload.get("expected_plan"),
            plan=payload.get("plan"),
            error_type=payload.get("error_type"),
            trace_id=str(payload.get("trace_id", "")),
        )


class TraceStore:
    """Append-only JSONL writer rooted at `runs/traces/`.

    Layout: ``<base_dir>/<catalog_id>/<run_id>/traces.jsonl``.

    Idempotent on `trace_id` collisions: a duplicate append is silently
    skipped, so benchmark replays don't double-count.

    Not thread-safe; intended for single-process append-only workflows.
    """

    def __init__(self, base_dir: Path | str = "runs/traces") -> None:
        self._base_dir = Path(base_dir)
        # Per-shard (catalog_id, run_id) → set of known trace_ids. Populated
        # lazily on first read/write of each shard, then kept in sync by append.
        self._shard_index: dict[tuple[str, str], set[str]] = {}
        # Global trace_id → Trace cache for `by_id`. Built lazily on first
        # lookup, then updated incrementally on every append.
        self._by_id_cache: dict[str, Trace] | None = None

    def _shard_path(self, catalog_id: str, run_id: str) -> Path:
        # catalog_id may contain "/" (e.g. "bfcl/simple_python/<case_id>");
        # the nesting is preserved verbatim so the tree mirrors the id.
        return self._base_dir / catalog_id / run_id / "traces.jsonl"

    def _load_shard_index(self, catalog_id: str, run_id: str) -> set[str]:
        key = (catalog_id, run_id)
        cached = self._shard_index.get(key)
        if cached is not None:
            return cached
        seen = {tr.trace_id for tr in self._iter_file(self._shard_path(catalog_id, run_id))}
        self._shard_index[key] = seen
        return seen

    def append(self, trace: Trace) -> str:
        """Write one JSONL line; idempotent on duplicate `trace_id`.

        Returns the trace_id either way.
        """
        index = self._load_shard_index(trace.catalog_id, trace.run_id)
        if trace.trace_id in index:
            return trace.trace_id
        path = self._shard_path(trace.catalog_id, trace.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        index.add(trace.trace_id)
        if self._by_id_cache is not None:
            self._by_id_cache[trace.trace_id] = trace
        return trace.trace_id

    def iter(
        self,
        catalog_id: str | None = None,
        run_id: str | None = None,
    ) -> Iterator[Trace]:
        """Yield Trace objects under the requested subtree.

        With both filters supplied: reads exactly one JSONL file.
        With `catalog_id` only: walks every `run_id` under that catalog.
        With neither: walks the whole `runs/traces/` tree.
        """
        if catalog_id is not None and run_id is not None:
            yield from self._iter_file(self._shard_path(catalog_id, run_id))
            return
        # Walk the tree, ignoring non-`traces.jsonl` files so sidecars written
        # by [[analyzer_failure_taxonomy]] etc. don't leak in.
        root = self._base_dir / catalog_id if catalog_id is not None else self._base_dir
        if not root.exists():
            return
        for path in sorted(root.rglob("traces.jsonl")):
            if run_id is not None and path.parent.name != run_id:
                continue
            yield from self._iter_file(path)

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
                yield Trace.from_dict(payload)

    def by_id(self, trace_id: str) -> Trace | None:
        """Direct lookup; builds a lazy global index on first call.

        Subsequent `append` calls update the index incrementally, so repeated
        lookup/append interleavings stay O(1) per lookup after the initial
        scan.
        """
        if self._by_id_cache is None:
            self._by_id_cache = {tr.trace_id: tr for tr in self.iter()}
        return self._by_id_cache.get(trace_id)
