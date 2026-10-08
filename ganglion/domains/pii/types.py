"""Canonical character spans; byte coordinates are computed at the boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

ENTITY_TYPES = ("PERSON", "ADDRESS", "PHONE", "EMAIL", "IDENTIFIER")
LABELS = ("O",) + tuple(f"{prefix}-{kind}" for kind in ENTITY_TYPES for prefix in ("B", "I"))


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    score: float | None = None
    origin: str = "model"
    probabilities: Mapping[str, float] = field(default_factory=dict)

    def validate(self, length: int) -> None:
        import math
        if type(self.start) is not int or type(self.end) is not int or self.kind not in ENTITY_TYPES or not 0 <= self.start < self.end <= length:
            raise ValueError("invalid PII span")
        if self.score is not None and (not math.isfinite(self.score) or not 0 <= self.score <= 1):
            raise ValueError("invalid PII score")
        if self.probabilities:
            if set(self.probabilities) != set((*ENTITY_TYPES, "NOT_PII")) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.probabilities.values()) or abs(sum(self.probabilities.values()) - 1) > 1e-4:
                raise ValueError("invalid PII type distribution")

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "type": self.kind,
                "score": self.score, "origin": self.origin,
                "probabilities": dict(self.probabilities)}


def interpret(text: str, spans: list[Span]) -> tuple[list[Span], list[dict]]:
    """Fix token whitespace boundaries, then reconcile overlaps; retain rule diff."""
    corrected, changes = [], []
    for span in spans:
        span.validate(len(text))
        start, end = span.start, span.end
        while start < end and text[start].isspace():
            start += 1
        while start < end and text[end - 1].isspace():
            end -= 1
        if start < end:
            after = Span(start, end, span.kind, span.score, span.origin, span.probabilities)
            corrected.append(after)
            if (start, end) != (span.start, span.end):
                changes.append({"rule": "trim-span-whitespace-v1", "before": [span.to_dict()], "after": after.to_dict()})
    final, overlap = reconcile(len(text), corrected)
    return final, changes + overlap


def reconcile(length: int, spans: list[Span]) -> tuple[list[Span], list[dict]]:
    """Coordinate-only union; does not allocate or inspect document text."""
    for span in spans:
        span.validate(length)
    ordered = sorted(spans, key=lambda s: (s.start, -s.end, s.kind))
    merged: list[Span] = []
    changes: list[dict] = []
    priority = {kind: index for index, kind in enumerate(("PERSON", "ADDRESS", "PHONE", "EMAIL", "IDENTIFIER"))}
    for span in ordered:
        if merged and span.start < merged[-1].end:
            before = merged.pop()
            kind = max((before.kind, span.kind), key=priority.get)
            merged.append(Span(before.start, max(before.end, span.end), kind,
                               max((s for s in (before.score, span.score) if s is not None), default=None),
                               "interpreter:overlap"))
            changes.append({"rule": "merge-overlapping-spans-v1", "before": [before.to_dict(), span.to_dict()],
                            "after": merged[-1].to_dict()})
        else:
            merged.append(span)
    return merged, changes
