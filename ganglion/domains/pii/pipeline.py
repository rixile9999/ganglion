"""Streaming domain runner. Model and interpreter never receive recovery keys."""
from __future__ import annotations

import base64
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

from ganglion.adapters.documents import windows, single_window
from ganglion.adapters.recovery import RecoveryWriter
from .types import Span, interpret, reconcile


class Cancelled(Exception):
    pass


def run_document(source_path: Path, output_dir: Path, detector, *, public_key: str | None,
                 document_id: str, execute: bool = True, window_chars: int = 1024,
                 preprocess: bool = True,
                 strategy: str = "fixed",
                 overlap_chars: int = 128, max_tokens: int = 1024,
                 progress: Callable[[dict], None] = lambda _value: None,
                 cancelled: Callable[[], bool] = lambda: False) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    pending: list[Span] = []
    counts: dict[str, int] = {}
    units = edits = changes_count = input_tokens = 0
    committed = source_bytes = output_bytes = 0
    source_hash, output_hash = hashlib.sha256(), hashlib.sha256()
    writer = None
    previous_start = unit_byte_start = 0
    previous_byte_offsets = [0]
    if execute:
        if not public_key:
            raise ValueError("a recipient public key is required for reversible execution")
        writer = RecoveryWriter(output_dir / "recovery.bin", public_key, document_id)
    try:
        with source_path.open("r", encoding="utf-8", newline="") as reader, source_path.open("r", encoding="utf-8", newline="") as editor, (output_dir / ("document.txt" if execute else "plan.jsonl")).open("wb") as target, (output_dir / "trace.jsonl").open("w", encoding="utf-8") as trace, ((output_dir / "plan.jsonl").open("wb") if execute else nullcontext(None)) as plan_file:
            def emit(until: int, selected: list[Span]) -> None:
                nonlocal committed, edits, source_bytes, output_bytes
                for span in selected:
                    prefix = editor.read(span.start - committed).encode()
                    original = editor.read(span.end - span.start).encode()
                    if len(original.decode()) != span.end - span.start:
                        raise ValueError("source changed during execution")
                    source_hash.update(prefix + original)
                    start_byte = source_bytes + len(prefix)
                    source_bytes += len(prefix) + len(original)
                    operation = {"operation": "replace", "source_span": [start_byte, source_bytes], "type": span.kind}
                    encoded_plan = (json.dumps(operation) + "\n").encode()
                    (plan_file if writer else target).write(encoded_plan)
                    if writer:
                        token = writer.pseudonym(span.kind, original)
                        encoded = token.encode()
                        target.write(prefix + encoded)
                        output_hash.update(prefix + encoded)
                        writer.record({"kind": "edit", "source_span": [start_byte, source_bytes],
                                       "output_span": [output_bytes + len(prefix), output_bytes + len(prefix) + len(encoded)],
                                       "original": base64.b64encode(original).decode(), "token": token, "type": span.kind})
                        output_bytes += len(prefix) + len(encoded)
                    committed = span.end
                    edits += 1
                    counts[span.kind] = counts.get(span.kind, 0) + 1
                remaining = editor.read(until - committed).encode()
                source_hash.update(remaining)
                source_bytes += len(remaining)
                if writer:
                    target.write(remaining)
                    output_hash.update(remaining)
                    output_bytes += len(remaining)
                committed = until

            iterator = windows(reader, window_chars=window_chars, overlap_chars=overlap_chars,
                               token_count=getattr(detector, "token_count", None), max_tokens=max_tokens, strategy=strategy) if preprocess else single_window(
                                   reader, window_chars=window_chars, token_count=getattr(detector, "token_count", None), max_tokens=max_tokens)
            for unit in iterator:
                if cancelled():
                    raise Cancelled()
                # A bounded per-window index converts Unicode character offsets
                # into global UTF-8 offsets without retaining document text.
                advance = unit.start - previous_start
                if not 0 <= advance < len(previous_byte_offsets):
                    raise ValueError("document windows must be contiguous")
                unit_byte_start += previous_byte_offsets[advance]
                byte_offsets = [0]
                for character in unit.text:
                    byte_offsets.append(byte_offsets[-1] + len(character.encode("utf-8")))
                previous_start, previous_byte_offsets = unit.start, byte_offsets
                unit_started = time.perf_counter()
                raw = detector.detect(unit.text)
                local, changes = interpret(unit.text, raw)
                units += 1
                if hasattr(detector, "token_count"):
                    input_tokens += detector.token_count(unit.text)
                changes_count += len(changes)
                pending.extend(Span(s.start + unit.start, s.end + unit.start, s.kind, s.score, s.origin) for s in local if s.end + unit.start > committed)
                # Work in a bounded coordinate interval rather than materializing the document.
                pending = [Span(max(s.start, committed), s.end, s.kind, s.score, s.origin) for s in pending if s.end > committed]
                if pending:
                    base = min(s.start for s in pending)
                    length = max(s.end for s in pending) - base
                    relative, _ = reconcile(length, [Span(s.start - base, s.end - base, s.kind, s.score, s.origin) for s in pending])
                    pending = [Span(s.start + base, s.end + base, s.kind, s.score, s.origin) for s in relative]
                safe = unit.commit_until
                crossing = [s for s in pending if s.start < safe < s.end]
                if crossing and not unit.final:
                    safe = min(s.start for s in crossing)
                if unit.final:
                    safe = unit.start + len(unit.text)
                if unit.start + len(unit.text) - safe > window_chars * 3:
                    raise ValueError("unresolved document boundary")
                selected = [s for s in pending if s.end <= safe]
                emit(safe, selected)
                pending = [s for s in pending if s.end > safe]
                trace.write(json.dumps({"unit": units, "source_character_start": unit.start, "characters": len(unit.text),
                                        "source_byte_start": unit_byte_start,
                                        "raw_byte_spans": [{"start": unit_byte_start + byte_offsets[s.start],
                                                            "end": unit_byte_start + byte_offsets[s.end], "type": s.kind} for s in raw],
                                        "raw_spans": [s.to_dict() for s in raw], "final_spans": [s.to_dict() for s in local],
                                        "corrections": changes, "latency_ms": (time.perf_counter() - unit_started) * 1000}, ensure_ascii=False) + "\n")
                progress({"units": units, "edits": edits, "processed_bytes": source_bytes})
            if pending:
                raise ValueError("unresolved document spans")
            if writer:
                writer.record({"kind": "final", "edits": edits, "source_sha256": source_hash.hexdigest(),
                               "output_sha256": output_hash.hexdigest()}, final=True)
        result = {"status": "complete", "mode": "execute" if execute else "plan", "domain": "pii-text",
                  "backend": detector.backend, "score_kind": detector.score_kind, "units": units,
                  "preprocessor": {"adapter": "utf8-windows", "version": 1,
                                   "config": {"strategy": strategy, "max_chars": window_chars,
                                              "overlap_chars": overlap_chars, "max_tokens": max_tokens}} if preprocess else None,
                  "edits": edits, "entity_counts": counts, "corrections": changes_count,
                  "processed_bytes": source_bytes, "input_tokens": input_tokens if hasattr(detector, "token_count") else None,
                  "output_tokens": 0, "latency_ms": (time.perf_counter() - started) * 1000,
                  "artifacts": ["document.txt", "recovery.bin", "plan.jsonl", "trace.jsonl"] if execute else ["plan.jsonl", "trace.jsonl"],
                  "quality_status": "experimental"}
        # Original text hashes stay inside authenticated recovery records.
        (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        if writer:
            writer.close()
