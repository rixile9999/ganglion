"""Paired, per-case comparison of two runs on one catalog.

Implements [[analyzer_compare]] (docs/tasks/analyzer_compare.md) — the
cross-run statistics [[analyzer_metrics]] deferred. It answers "what
changed between iteration N−1 and N?" with a transition matrix
(``fixed / regressed / same_pass / same_fail``), a paired-bootstrap 95 %
confidence interval on ΔEM, and a ``manifest_diff`` naming the training /
decoding changes between the runs. It **refuses** (``ValueError``) to
compare runs whose manifests differ in ``dataset_sha256``, ``metric_kind``
or the ``decoding`` block unless the caller passes ``allow_diff=True``
(then each differing key becomes a ``warnings`` row); ``catalog_fingerprint``
drift is always a warning — it is the thing the loop changes on purpose.

Public API:
    CompareResult, compare_runs, safe_run_name, DIRECTIONS.

Out of scope (per spec): other significance tests, cross-catalog
comparison, ``repeat_index > 0`` aggregation, family-level comparison of
chat sessions, producing golds / labels / classifications / manifests.
"""

from __future__ import annotations

import json
import random
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ganglion.analyzer import ledger
from ganglion.analyzer.analyze import read_classified
from ganglion.analyzer.labels import LabelStore, plan_from_dict, resolve_gold
from ganglion.analyzer.manifest import RunManifest, read_manifest, run_dir
from ganglion.analyzer.trace import Trace, TraceStore

__all__ = ["DIRECTIONS", "CompareResult", "compare_runs", "safe_run_name"]

DIRECTIONS: tuple[str, ...] = (
    "fixed",
    "regressed",
    "same_pass",
    "same_fail",
    "only_a",
    "only_b",
    "ungraded",
)

_REFUSAL_KEYS: tuple[str, ...] = ("dataset_sha256", "metric_kind", "decoding")
_DIFF_KEYS: tuple[str, ...] = (
    "decoding",
    "model_id",
    "model_fingerprint",
    "base_model",
    "adapter_sha256",
    "train_provenance",
    "labels_used",
    "catalog_fingerprint",
    "dataset_sha256",
    "iteration",
)


def safe_run_name(run_id: str) -> str:
    """``"/"`` → ``"__"`` so a run id becomes one path segment."""
    return run_id.replace("/", "__")


@dataclass(frozen=True)
class CompareResult:
    """Outcome of :func:`compare_runs`.

    ``em_a`` / ``em_b`` / ``em_delta`` are over the *paired graded* case
    set (both sides graded), so ``em_delta == (fixed − regressed) ÷
    n_graded``. ``counts`` has exactly the seven direction keys; ``n`` is
    the number of joined cases (``len(per_case)``).
    """

    catalog_id: str
    run_a: str
    run_b: str
    n: int
    em_a: float
    em_b: float
    em_delta: float
    ci95: tuple[float, float] | None
    counts: dict[str, int]
    per_case: tuple[dict[str, Any], ...]
    manifest_diff: dict[str, Any]
    warnings: tuple[str, ...]
    n_graded: int = 0
    path: str = ""
    bootstrap: int | None = None
    seed: int = 42

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the ``compare-<run_a>.json`` content."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                value = [dict(v) if isinstance(v, Mapping) else v for v in value]
            elif isinstance(value, Mapping):
                value = dict(value)
            out[f.name] = value
        return out


def _status(trace: Trace, labels: Mapping[str, Any]) -> tuple[str, bool | None]:
    """``(status, passed)``: ``pass`` | ``fail`` | ``invalid`` | ``ungraded``."""
    gold, _origin = resolve_gold(trace, labels)
    if gold is None:
        return "ungraded", None
    if trace.plan is None:
        return "invalid", False
    predicted = plan_from_dict(trace.plan)
    if predicted is not None and predicted == gold:
        return "pass", True
    return "fail", False


def _direction(status_a: str | None, status_b: str | None) -> str:
    if status_a is None:
        return "only_b"
    if status_b is None:
        return "only_a"
    if status_a == "ungraded" or status_b == "ungraded":
        return "ungraded"
    pass_a = status_a == "pass"
    pass_b = status_b == "pass"
    if pass_a and pass_b:
        return "same_pass"
    if not pass_a and not pass_b:
        return "same_fail"
    return "fixed" if pass_b else "regressed"


def _paired_bootstrap(
    diffs: list[int], n_resamples: int, seed: int,
) -> tuple[float, float] | None:
    """Percentile CI of the mean of ``diffs`` under resampling with replacement."""
    n = len(diffs)
    if n < 2 or n_resamples <= 0:
        return None
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_resamples):
        total = 0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        stats.append(total / n)
    stats.sort()
    lo = stats[int(round(0.025 * (n_resamples - 1)))]
    hi = stats[int(round(0.975 * (n_resamples - 1)))]
    return (round(lo, 4), round(hi, 4))


def _manifest_value(manifest: RunManifest, key: str) -> Any:
    value = getattr(manifest, key, None)
    return dict(value) if isinstance(value, Mapping) else value


def _load_manifest(base: Path, catalog_id: str, run_id: str) -> RunManifest:
    path = run_dir(base, catalog_id, run_id) / "manifest.json"
    try:
        return read_manifest(path)
    except FileNotFoundError as exc:
        raise ValueError(
            f"run {catalog_id}/{run_id} has no manifest.json — an unmanifested shard is not a run"
        ) from exc


def compare_runs(
    base_dir: Path | str,
    catalog_id: str,
    run_a: str,
    run_b: str,
    *,
    store: TraceStore | None = None,
    exclude_case_ids: Collection[str] = (),
    bootstrap: int | None = 2000,
    seed: int = 42,
    allow_diff: bool = False,
) -> CompareResult:
    """Join two runs on ``case_id`` (``repeat_index == 0``), grade each side
    with its own latest labels via ``resolve_gold``, tabulate transitions,
    bootstrap the paired EM delta, write ``<run_b dir>/compare-<safe(run_a)>.json``
    and emit ``analyzer.compare.completed``.

    Refuses (``ValueError``, no file, no event) when either manifest is
    missing, or when the manifests differ in ``dataset_sha256`` /
    ``metric_kind`` / ``decoding`` and ``allow_diff`` is False.
    ``failure_type_a/b`` come from each run's ``classified.jsonl`` when
    present.
    """
    base = Path(base_dir)
    manifest_a = _load_manifest(base, catalog_id, run_a)
    manifest_b = _load_manifest(base, catalog_id, run_b)

    diff_keys = [
        key for key in _REFUSAL_KEYS
        if _manifest_value(manifest_a, key) != _manifest_value(manifest_b, key)
    ]
    if diff_keys and not allow_diff:
        raise ValueError(
            f"manifests of {run_a!r} and {run_b!r} differ in {diff_keys}; "
            "pass allow_diff=True to compare anyway"
        )
    warnings = [f"manifest differs in {key}" for key in diff_keys]
    if manifest_a.catalog_fingerprint != manifest_b.catalog_fingerprint:
        warnings.append("catalog_fingerprint drift")
    manifest_diff = {
        key: {"a": _manifest_value(manifest_a, key), "b": _manifest_value(manifest_b, key)}
        for key in _DIFF_KEYS
        if _manifest_value(manifest_a, key) != _manifest_value(manifest_b, key)
    }

    store = store or TraceStore(base)
    excluded = set(exclude_case_ids)

    def _first_repeats(run_id: str) -> dict[str, Trace]:
        out: dict[str, Trace] = {}
        for trace in store.iter(catalog_id, run_id):
            if trace.repeat_index != 0 or trace.case_id in excluded:
                continue
            out.setdefault(trace.case_id, trace)
        return out

    traces_a = _first_repeats(run_a)
    traces_b = _first_repeats(run_b)
    label_store = LabelStore(base, check_trace=False)
    labels_a = label_store.latest_by_trace(catalog_id, run_a)
    labels_b = label_store.latest_by_trace(catalog_id, run_b)
    classified_a = read_classified(base, catalog_id, run_a)
    classified_b = read_classified(base, catalog_id, run_b)

    counts = {key: 0 for key in DIRECTIONS}
    per_case: list[dict[str, Any]] = []
    diffs: list[int] = []
    passes_a = passes_b = 0
    for case_id in sorted(set(traces_a) | set(traces_b)):
        ta = traces_a.get(case_id)
        tb = traces_b.get(case_id)
        status_a = pass_a = None
        status_b = pass_b = None
        if ta is not None:
            status_a, pass_a = _status(ta, labels_a)
        if tb is not None:
            status_b, pass_b = _status(tb, labels_b)
        direction = _direction(status_a, status_b)
        counts[direction] += 1
        if pass_a is not None and pass_b is not None:
            diffs.append(int(pass_b) - int(pass_a))
            passes_a += int(pass_a)
            passes_b += int(pass_b)
        per_case.append(
            {
                "case_id": case_id,
                "trace_a": ta.trace_id if ta is not None else None,
                "trace_b": tb.trace_id if tb is not None else None,
                "status_a": status_a,
                "status_b": status_b,
                "direction": direction,
                "failure_type_a": (
                    classified_a.get(ta.trace_id, {}).get("failure_type") if ta is not None else None
                ),
                "failure_type_b": (
                    classified_b.get(tb.trace_id, {}).get("failure_type") if tb is not None else None
                ),
            }
        )

    n_graded = len(diffs)
    em_a = round(passes_a / n_graded, 4) if n_graded else 0.0
    em_b = round(passes_b / n_graded, 4) if n_graded else 0.0
    em_delta = round((passes_b - passes_a) / n_graded, 4) if n_graded else 0.0
    if not n_graded:
        warnings.append("no graded cases")
    ci95 = _paired_bootstrap(diffs, bootstrap, seed) if bootstrap is not None else None

    path = run_dir(base, catalog_id, run_b) / f"compare-{safe_run_name(run_a)}.json"
    result = CompareResult(
        catalog_id=catalog_id,
        run_a=run_a,
        run_b=run_b,
        n=len(per_case),
        em_a=em_a,
        em_b=em_b,
        em_delta=em_delta,
        ci95=ci95,
        counts=counts,
        per_case=tuple(per_case),
        manifest_diff=manifest_diff,
        warnings=tuple(warnings),
        n_graded=n_graded,
        path=str(path),
        bootstrap=bootstrap,
        seed=seed,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger.emit(
        base,
        "analyzer.compare.completed",
        {
            "run_a": run_a,
            "run_b": run_b,
            "path": str(path),
            "n": result.n,
            "em_a": em_a,
            "em_b": em_b,
            "em_delta": em_delta,
            "ci95": list(ci95) if ci95 is not None else None,
            "counts": counts,
        },
        producer="analyzer.compare",
        correlation={"catalog_id": catalog_id, "run_id": run_b},
        refs={"compare": str(path)},
    )
    return result
