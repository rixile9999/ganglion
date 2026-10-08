"""Source-free PII failure classification and exact-span correction attribution.

Overlapping ranges diagnose errors; they never receive exact-match credit.
Incomplete annotation cannot establish false positives or document-wide F1.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import heapq
from typing import Mapping

from .types import ENTITY_TYPES


@dataclass(frozen=True, order=True)
class Entity:
    start: int
    end: int
    kind: str

    def public(self):
        return {"start": self.start, "end": self.end, "type": self.kind}


def _fields(row):
    if isinstance(row, Mapping):
        return row.get("start"), row.get("end"), row.get("type", row.get("kind"))
    return getattr(row, "start", None), getattr(row, "end", None), getattr(row, "kind", None)


def _reason(start, end, kind, length):
    if type(start) is not int or type(end) is not int:
        return "non_integer_coordinate"
    if not 0 <= start < end or length is not None and end > length:
        return "out_of_range"
    if not isinstance(kind, str) or kind not in ENTITY_TYPES:
        return "unknown_entity_type"
    return None


def _failure(stage, kind, predicted=(), expected=(), **evidence):
    types = {s.kind for s in (*predicted, *expected)}
    return {"stage": stage, "kind": kind,
            "entity_type": next(iter(types)) if len(types) == 1 else None,
            "predicted_range": [min(s.start for s in predicted), max(s.end for s in predicted)] if predicted else None,
            "expected_range": [min(s.start for s in expected), max(s.end for s in expected)] if expected else None,
            "evidence": {"predicted": [s.public() for s in predicted],
                         "expected": [s.public() for s in expected], **evidence}}


def _parse(rows, stage, length, *, gold=False):
    unique, failures = set(), []
    if not isinstance(rows, (list, tuple)):
        if gold:
            raise ValueError("gold spans must be a list of typed ranges")
        return unique, [_failure(stage, "invalid_span", reason="predictions_not_a_list")]
    for index, row in enumerate(rows):
        start, end, kind = _fields(row)
        reason = _reason(start, end, kind, length)
        if reason:
            if gold:
                raise ValueError(f"invalid gold span at index {index}: {reason}")
            failures.append(_failure(stage, "invalid_span", index=index, reason=reason))
            continue
        entity = Entity(start, end, kind)
        if entity in unique:
            if not gold:
                failures.append(_failure(stage, "duplicate", [entity], index=index))
        unique.add(entity)
    return unique, failures


def _components(predicted, expected):
    """Bipartite overlap components, with a sweep avoiding all-pairs scans.

    Same-side overlaps do not independently connect errors. Exact matches are
    removed before this operation, so one gold entity cannot be counted twice.
    """
    nodes = sorted([(p.start, p.end, 0, p) for p in predicted] +
                   [(g.start, g.end, 1, g) for g in expected])
    parents = list(range(len(nodes)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    active = ({}, {})
    endings = []
    for index, (start, end, side, _entity) in enumerate(nodes):
        while endings and endings[0][0] <= start:
            _end, old_side, old_index = heapq.heappop(endings)
            active[old_side].pop(old_index, None)
        for other in active[1 - side]:
            left, right = find(index), find(other)
            if left != right:
                parents[max(left, right)] = min(left, right)
        active[side][index] = True
        heapq.heappush(endings, (end, side, index))
    groups = {}
    for index, (_start, _end, side, entity) in enumerate(nodes):
        groups.setdefault(find(index), ([], []))[side].append(entity)
    return list(groups.values())


def _classify(stage, predicted, expected, complete):
    exact = predicted & expected
    failures = []
    for ps, gs in _components(predicted - exact, expected - exact):
        if not gs:
            failures.append(_failure(stage, "fp" if complete else "unclassified", ps,
                                     reason="no_annotated_overlap"))
        elif not ps:
            failures.append(_failure(stage, "fn", expected=gs))
        elif len(ps) > 1 and len(gs) == 1:
            failures.append(_failure(stage, "split", ps, gs, exact_match=False))
        elif len(ps) == 1 and len(gs) > 1:
            failures.append(_failure(stage, "merge", ps, gs, exact_match=False))
        elif len(ps) == len(gs) == 1:
            p, g = ps[0], gs[0]
            kind = "type_mismatch" if p.kind != g.kind else "boundary_mismatch"
            failures.append(_failure(stage, kind, ps, gs, exact_match=False,
                                     boundary_changed=(p.start, p.end) != (g.start, g.end),
                                     type_changed=p.kind != g.kind))
        else:
            failures.append(_failure(stage, "boundary_mismatch", ps, gs,
                                     exact_match=False, complex_component=True))
    return failures


def _metrics(predicted, expected, complete, diagnostics):
    if expected is None:
        tp = fp = fn = precision = recall = f1 = None
    else:
        tp, fn = len(predicted & expected), len(expected - predicted)
        fp = len(predicted - expected) if complete else None
        recall = tp / len(expected) if expected else (1.0 if complete else None)
        precision = tp / len(predicted) if predicted else (1.0 if not expected else 0.0)
        f1 = (2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0) if complete else None
        if not complete:
            precision = f1 = None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
            "predicted": len(predicted), "gold": len(expected) if expected is not None else None,
            "duplicate_count": sum(d["kind"] == "duplicate" for d in diagnostics),
            "invalid_count": sum(d["kind"] == "invalid_span" for d in diagnostics),
            "exact_match": True, "valid_unique_spans_only": True,
            "gold_complete": complete if expected is not None else False}


def analyze(raw_spans, final_spans, gold_spans, *, coordinate="character",
            gold_complete=True, document_length=None):
    """Return exact metrics, one diagnostic per unmatched overlap component.

    ``raw_spans=None`` means that the raw stage was not captured, so raw
    metrics and correction attribution are unavailable. Final predictions must
    still be a span list. Span dictionaries may carry additional fields, but no original text,
    confidence or free-form metadata is copied into analysis. Span instances
    with ``start``, ``end`` and ``kind`` are accepted as well.
    """
    if coordinate not in {"character", "utf8-byte"}:
        raise ValueError("unsupported span coordinate")
    if type(gold_complete) is not bool:
        raise ValueError("gold_complete must be boolean")
    if document_length is not None and (type(document_length) is not int or document_length < 0):
        raise ValueError("document_length must be a nonnegative integer")
    gold = None if gold_spans is None else _parse(gold_spans, "gold", document_length, gold=True)[0]
    stages, failures, sets = {}, [], {}
    for stage, rows in (("raw", raw_spans), ("final", final_spans)):
        if stage == "raw" and rows is None:
            stages[stage] = None
            continue
        spans, diagnostics = _parse(rows, stage, document_length)
        sets[stage] = spans
        errors = _classify(stage, spans, gold, gold_complete) if gold is not None else []
        failures.extend(diagnostics + errors)
        metrics = _metrics(spans, gold, gold_complete, diagnostics)
        metrics["unclassified_predictions"] = sum(len(d["evidence"]["predicted"]) for d in errors if d["kind"] == "unclassified") if gold is not None else len(spans)
        metrics["per_type"] = {
            kind: _metrics({s for s in spans if s.kind == kind},
                           {s for s in gold if s.kind == kind} if gold is not None else None,
                           gold_complete, [d for d in diagnostics if d["entity_type"] == kind])
            for kind in ENTITY_TYPES}
        stages[stage] = metrics
    final_errors = [d for d in failures if d["stage"] == "final"]
    status = "unclassified" if gold is None else "partial" if not gold_complete else "fail" if final_errors else "pass"
    if raw_spans is None or gold is None:
        attribution = None
        attribution_status = "unavailable" if raw_spans is None else "unclassified"
    else:
        raw, final = sets["raw"], sets["final"]
        rescued, regressed = sorted((final & gold) - raw), sorted((raw & gold) - final)
        attribution = {"status": "complete" if gold_complete else "partial",
                       "rescued": [s.public() for s in rescued], "regressed": [s.public() for s in regressed],
                       "rescued_count": len(rescued), "regressed_count": len(regressed),
                       "false_positives_removed": [s.public() for s in sorted((raw - gold) - final)] if gold_complete else None,
                       "false_positives_introduced": [s.public() for s in sorted((final - gold) - raw)] if gold_complete else None}
        attribution_status = attribution["status"]
    return {"coordinate": coordinate, "status": status, "gold_complete": gold_complete if gold is not None else False,
            **stages, "failures": failures,
            "histogram": dict(sorted(Counter(d["kind"] for d in final_errors).items())),
            "stage_histograms": {stage: dict(sorted(Counter(d["kind"] for d in failures if d["stage"] == stage).items())) if stages[stage] is not None else None for stage in stages},
            "attribution": attribution, "attribution_status": attribution_status}
