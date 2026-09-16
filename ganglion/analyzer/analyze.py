"""One-run analysis pass: classify → attribute → synthesise.

Implements [[analyzer_analyze]] (docs/tasks/analyzer_analyze.md), the
composite the console's ``POST /api/runs/{cat}/{run}/analyze``, the
``analyze`` / ``seed`` subcommands and [[factory_pipeline]]'s per-iteration
step all call. It wires exactly three primitives by their public entry
points — [[analyzer_failure_taxonomy]] ``classify_traces``,
[[analyzer_correction_attribution]] ``summarize_corrections`` /
``retire_candidates_as_patches``, [[analyzer_rule_synthesis]]
``synthesize_rules`` — with one gold map from [[analyzer_label_store]],
leaves every sidecar in place, and emits each primitive's declared event
through [[analyzer_event_ledger]]. Nothing is computed inline here.

Public API:
    analyze_run, read_classified, read_patches, histogram, HISTOGRAM_KEYS.

``histogram`` (errata E8): all 14 ``FailureType`` names plus
``unclassified`` — a ``no_failure`` row with ``confidence == 0.0`` is the
taxonomy's degenerate fall-through and is counted as ``unclassified``,
never as a pass. The console's trace ``counts`` use the same rule.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ganglion.analyzer import ledger
from ganglion.analyzer.catalogs import CatalogNotResolvable, resolve_catalog
from ganglion.analyzer.corrections import (
    Attribution,
    retire_candidates_as_patches,
    summarize_corrections,
    write_corrections,
)
from ganglion.analyzer.labels import LabelStore, gold_map, resolve_gold
from ganglion.analyzer.rules import (
    RulePatch,
    RuleSynthConfig,
    synthesize_rules,
    write_proposed_patches_sidecar,
    write_synthesis_summary,
)
from ganglion.analyzer.taxonomy import (
    FailureType,
    classify_traces,
    write_classified_sidecar,
)
from ganglion.analyzer.trace import TraceStore
from ganglion.contract.types import ActionPlan

__all__ = [
    "HISTOGRAM_KEYS",
    "UNCLASSIFIED",
    "analyze_run",
    "histogram",
    "is_unclassified",
    "read_classified",
    "read_patches",
]

_CLASSIFIED = "classified.jsonl"
_PATCHES = "proposed_patches.jsonl"
_PATCHES_SUMMARY = "proposed_patches.summary.json"

UNCLASSIFIED = "unclassified"

#: Every histogram key, in taxonomy order, plus ``unclassified`` last.
HISTOGRAM_KEYS: tuple[str, ...] = tuple(ft.value for ft in FailureType) + (UNCLASSIFIED,)

_PRODUCER = "analyzer.analyze"


def is_unclassified(row: Mapping[str, Any]) -> bool:
    """E8: ``no_failure`` with ``confidence == 0.0`` is *unclassified*, not a pass."""
    return (
        row.get("failure_type") == FailureType.NO_FAILURE.value
        and float(row.get("confidence", 0.0) or 0.0) == 0.0
    )


def histogram(classified: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """``{failure_type: count}`` over classification rows; every key present.

    Rows keyed by ``trace_id`` (as :func:`read_classified` returns them);
    the values are the sidecar rows. Unknown ``failure_type`` strings are
    counted under ``unclassified`` too.
    """
    counts = {key: 0 for key in HISTOGRAM_KEYS}
    for row in classified.values():
        if is_unclassified(row):
            counts[UNCLASSIFIED] += 1
            continue
        ftype = str(row.get("failure_type", ""))
        if ftype in counts:
            counts[ftype] += 1
        else:
            counts[UNCLASSIFIED] += 1
    return counts


def _run_dir(base_dir: Path | str, catalog_id: str, run_id: str) -> Path:
    return Path(base_dir) / catalog_id / run_id


def read_classified(base_dir: Path | str, catalog_id: str, run_id: str) -> dict[str, dict[str, Any]]:
    """``trace_id → classification row`` from ``classified.jsonl`` (``{}`` when absent)."""
    path = _run_dir(base_dir, catalog_id, run_id) / _CLASSIFIED
    out: dict[str, dict[str, Any]] = {}
    try:
        fh = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping) and "trace_id" in row:
                out[str(row["trace_id"])] = dict(row)
    return out


def read_patches(base_dir: Path | str, catalog_id: str, run_id: str) -> list[dict[str, Any]]:
    """Rows of ``proposed_patches.jsonl`` in file order (``[]`` when absent)."""
    path = _run_dir(base_dir, catalog_id, run_id) / _PATCHES
    out: list[dict[str, Any]] = []
    try:
        fh = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping):
                out.append(dict(row))
    return out


def _patch_event_payload(patch: RulePatch) -> dict[str, Any]:
    # Content-stable (no created_at) so re-analysing an unchanged run dedups.
    return {
        "patch_id": patch.patch_id,
        "operation": patch.operation,
        "target_tool": patch.target_tool,
        "source_failure_type": patch.source_failure_type.value,
        "confidence": patch.evidence.get("confidence", 0.0),
        "failure_count": patch.evidence.get("failure_count", 0),
    }


def analyze_run(
    base_dir: Path | str,
    catalog_id: str,
    run_id: str,
    *,
    store: TraceStore | None = None,
    label_store: LabelStore | None = None,
    config: RuleSynthConfig = RuleSynthConfig(),
) -> dict[str, Any]:
    """Classify, attribute and synthesise one ``(catalog_id, run_id)``.

    1. ``classify_traces(traces, catalog, golds=gold_map)`` →
       ``classified.jsonl``; event ``analyzer.failure.classified``.
    2. ``summarize_corrections`` → ``corrections.jsonl`` +
       ``corrections.summary.json``; event ``analyzer.correction.attributed``
       (computed before step 3's write so retire candidates join the same
       patch file).
    3. ``synthesize_rules(..., golds=)`` + ``retire_candidates_as_patches``
       → ``proposed_patches.jsonl`` + ``proposed_patches.summary.json``;
       one ``analyzer.rule.proposed`` event per patch.

    Returns ``{"n_traces", "n_classified", "histogram", "n_patches",
    "corrections", "paths"}``. Raises :class:`CatalogNotResolvable` for
    ``bfcl/*`` (per-case catalogs) before touching any sidecar. Sidecars
    are derived artifacts and are overwritten on every call; ledger rows
    dedupe by content.
    """
    base = Path(base_dir)
    catalog = resolve_catalog(catalog_id, base_dir=base)  # raises CatalogNotResolvable
    store = store or TraceStore(base)
    label_store = label_store or LabelStore(base)
    traces = list(store.iter(catalog_id, run_id))
    labels = label_store.latest_by_trace(catalog_id, run_id)
    golds = gold_map(traces, labels)
    directory = _run_dir(base, catalog_id, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    correlation = {"catalog_id": catalog_id, "run_id": run_id}

    # 1. classify
    classifications = classify_traces(traces, catalog, golds=golds)
    classified_path = directory / _CLASSIFIED
    write_classified_sidecar(classifications, classified_path)
    classified_rows = {
        c.trace_id: {
            "trace_id": c.trace_id,
            "failure_type": c.failure_type.value,
            "confidence": c.confidence,
        }
        for c in classifications
    }
    hist = histogram(classified_rows)
    ledger.emit(
        base,
        "analyzer.failure.classified",
        {"n_traces": len(traces), "n_classified": len(classifications), "histogram": hist},
        producer=_PRODUCER,
        correlation=correlation,
        refs={"classified": str(classified_path)},
    )

    # 2. attribute (before the patch write so retire candidates join it)
    golds_with_origin: dict[str, tuple[ActionPlan, str]] = {}
    for trace in sorted(traces, key=lambda t: t.repeat_index, reverse=True):
        gold, origin = resolve_gold(trace, labels)
        if gold is not None:
            golds_with_origin[trace.case_id] = (gold, origin)
    attributions: list[Attribution] = []
    corrections_summary = summarize_corrections(
        catalog, traces, golds_with_origin, attributions_out=attributions,
    )
    corrections_path, corrections_summary_path = write_corrections(
        base, catalog_id, run_id, attributions, corrections_summary,
    )
    ledger.emit(
        base,
        "analyzer.correction.attributed",
        {
            "n": corrections_summary["n"],
            "unattributable": corrections_summary["unattributable"],
            "em_f0": corrections_summary["em_f0"],
            "em_fk": corrections_summary["em_fk"],
            "rescue": corrections_summary["rescue"],
            "regression": corrections_summary["regression"],
            "n_retire_candidates": corrections_summary["n_retire_candidates"],
        },
        producer=_PRODUCER,
        correlation=correlation,
        refs={
            "corrections": str(corrections_path),
            "corrections_summary": str(corrections_summary_path),
        },
    )

    # 3. synthesise (+ retire candidates)
    patches = synthesize_rules(classifications, traces, catalog, config, golds=golds)
    patches.extend(retire_candidates_as_patches(catalog_id, corrections_summary))
    patches_path = directory / _PATCHES
    patches_summary_path = directory / _PATCHES_SUMMARY
    write_proposed_patches_sidecar(patches, patches_path)
    write_synthesis_summary(patches, patches_summary_path)
    for patch in patches:
        ledger.emit(
            base,
            "analyzer.rule.proposed",
            _patch_event_payload(patch),
            producer=_PRODUCER,
            correlation={**correlation, "patch_id": patch.patch_id},
            refs={"proposed_patches": str(patches_path)},
        )

    return {
        "n_traces": len(traces),
        "n_classified": len(classifications),
        "histogram": hist,
        "n_patches": len(patches),
        "corrections": corrections_summary,
        "paths": {
            "classified": str(classified_path),
            "proposed_patches": str(patches_path),
            "proposed_patches_summary": str(patches_summary_path),
            "corrections": str(corrections_path),
            "corrections_summary": str(corrections_summary_path),
        },
    }
