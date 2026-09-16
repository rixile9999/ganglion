"""Per-run ``manifest.json`` and the run bundle.

Implements [[analyzer_run_manifest]] (docs/tasks/analyzer_run_manifest.md).
A run is one ``(catalog_id, run_id)`` shard under the runs dir; the manifest
records *what produced it* — model, decoding block, dataset hash, loop
position — and "scan every ``manifest.json``" is the single source of truth
for listing runs. A shard without a manifest is not a run.

Public API:
    RunManifest        — frozen dataclass (``to_dict`` / ``from_dict``).
    run_dir            — ``<base>/<catalog_id>/<run_id>/``.
    write_manifest     — atomic write (tmp + ``os.replace``).
    read_manifest      — load one manifest.
    list_runs          — scan ``**/manifest.json``; the listing SSOT.
    write_run_bundle   — manifest + ``summary.json`` + ``report.md``
                         (+ ``analyzer.run.recorded`` ledger row).
    read_summary       — the persisted ``summarize()`` dict.
    dataset_sha256     — sha256 of a dataset file (``""`` when missing).
    git_head           — HEAD commit (``""`` outside a git checkout).

Out of scope (per spec): computing the summary ([[analyzer_metrics]]),
producing traces ([[analyzer_trace_store]]), model fingerprints
([[lm_model_registry]]), backfilling legacy ``runs/m*`` outputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from ganglion.analyzer import ledger
from ganglion.analyzer.trace import now_iso

__all__ = [
    "RunManifest",
    "dataset_sha256",
    "git_head",
    "list_runs",
    "read_manifest",
    "read_summary",
    "run_dir",
    "write_manifest",
    "write_run_bundle",
]

_LOG = logging.getLogger(__name__)

_MANIFEST = "manifest.json"
_SUMMARY = "summary.json"
_REPORT = "report.md"

_BENCHMARKS = frozenset({"iot", "bfcl", "chat"})


@dataclass(frozen=True)
class RunManifest:
    """Immutable description of one run bundle.

    Identity: ``run_id`` (plain name, at most one ``/`` for
    ``console/<session_id>``), ``catalog_id``, ``catalog_fingerprint``,
    ``model_id``. ``decoding`` is the block [[analyzer_compare]] requires
    equal: ``{"repair", "repair_max_attempts", "thinking", "repeat",
    "grammar_mask"}``. Read-time aggregates (label counts, precision,
    human minutes) are never stored here.
    """

    run_id: str
    catalog_id: str
    catalog_fingerprint: str
    model_id: str
    benchmark: str  # "iot" | "bfcl" | "chat"
    metric_kind: str = "exact_match"  # or "ast_match"
    client_kind: str = ""  # "json-dsl" | "freeform" | "thinking" | "native" | "rules"
    model_fingerprint: str = ""
    served_model_version: str = ""
    dataset_path: str = ""
    dataset_sha256: str = ""
    n_cases: int = 0
    limit: int | None = None
    decoding: Mapping[str, Any] = field(default_factory=dict)
    iteration: int | None = None
    parent_run_id: str | None = None
    base_model: str | None = None
    adapter_dir: str | None = None
    adapter_sha256: str | None = None
    train_provenance: Mapping[str, Any] | None = None
    labels_used: Mapping[str, int] | None = None
    git_head: str = ""
    python: str = ""
    accelerator: str = ""
    started_at: str = ""
    finished_at: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able dict; the on-disk ``manifest.json`` content."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Mapping):
                value = dict(value)
            out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunManifest":
        """Inverse of :meth:`to_dict`; tolerant of missing optional keys."""
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in payload.items():
            if key in known:
                kwargs[key] = value
        for key in ("decoding", "extra"):
            if kwargs.get(key) is None:
                kwargs[key] = {}
            else:
                kwargs[key] = dict(kwargs[key])
        for key in ("train_provenance", "labels_used"):
            if kwargs.get(key) is not None:
                kwargs[key] = dict(kwargs[key])
        for key in ("n_cases",):
            kwargs[key] = int(kwargs.get(key, 0) or 0)
        for key in ("limit", "iteration"):
            if kwargs.get(key) is not None:
                kwargs[key] = int(kwargs[key])
        return cls(
            run_id=str(payload["run_id"]),
            catalog_id=str(payload["catalog_id"]),
            catalog_fingerprint=str(payload.get("catalog_fingerprint", "") or ""),
            model_id=str(payload.get("model_id", "") or ""),
            benchmark=str(payload.get("benchmark", "") or ""),
            **{k: v for k, v in kwargs.items() if k not in {"run_id", "catalog_id", "catalog_fingerprint", "model_id", "benchmark"}},
        )


def run_dir(base_dir: Path | str, catalog_id: str, run_id: str) -> Path:
    """``<base>/<catalog_id>/<run_id>/`` — same rule as ``TraceStore.shard_path().parent``."""
    return Path(base_dir) / catalog_id / run_id


def _validate_identity(manifest: RunManifest) -> None:
    run_id = manifest.run_id
    if not run_id or not manifest.catalog_id:
        raise ValueError("manifest needs a non-empty run_id and catalog_id")
    if run_id.count("/") > 1:
        raise ValueError(f"run_id {run_id!r} may contain at most one '/'")
    if run_id.startswith(manifest.catalog_id + "/") or run_id.startswith("/"):
        raise ValueError(f"run_id {run_id!r} must not carry a catalog prefix")
    if manifest.benchmark and manifest.benchmark not in _BENCHMARKS:
        raise ValueError(
            f"benchmark {manifest.benchmark!r} not in {sorted(_BENCHMARKS)}"
        )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_manifest(
    base_dir: Path | str,
    manifest: RunManifest,
    *,
    force: bool = False,
) -> Path:
    """Write ``manifest.json`` atomically (tmp + rename); returns its path.

    Overwrites an existing manifest only when it names the same run
    identity: a different ``model_id`` or ``catalog_fingerprint`` (both
    non-empty on both sides) raises ``ValueError`` — a shard is one run;
    use a new ``run_id``. ``force=True`` skips that check. The run-id
    vocabulary is [[analyzer_trace_store]]'s: at most one ``/``, no
    catalog prefix.
    """
    _validate_identity(manifest)
    path = run_dir(base_dir, manifest.catalog_id, manifest.run_id) / _MANIFEST
    if path.exists() and not force:
        try:
            existing = read_manifest(path)
        except (ValueError, OSError, KeyError):
            existing = None
        if existing is not None:
            for key in ("model_id", "catalog_fingerprint"):
                old = getattr(existing, key)
                new = getattr(manifest, key)
                if old and new and old != new:
                    raise ValueError(
                        f"run {manifest.catalog_id}/{manifest.run_id} already has "
                        f"{key}={old!r}; refusing to overwrite with {new!r} "
                        "(use a new run_id or force=True)"
                    )
    text = json.dumps(manifest.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)
    _atomic_write_text(path, text + "\n")
    return path


def read_manifest(path: Path | str) -> RunManifest:
    """Load one ``manifest.json``; ``FileNotFoundError`` / ``ValueError`` on failure."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: manifest must be a JSON object")
    return RunManifest.from_dict(payload)


def list_runs(base_dir: Path | str, catalog_id: str | None = None) -> list[RunManifest]:
    """Scan ``**/manifest.json`` — the listing SSOT.

    Sorted by ``(catalog_id, iteration or -1, started_at)``. Unreadable
    manifests are logged and skipped (the run is invisible until fixed).
    """
    base = Path(base_dir)
    root = base / catalog_id if catalog_id is not None else base
    if not root.exists():
        return []
    manifests: list[RunManifest] = []
    for path in sorted(root.rglob(_MANIFEST)):
        try:
            manifest = read_manifest(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _LOG.warning("skipping unreadable manifest %s: %s", path, exc)
            continue
        if catalog_id is not None and manifest.catalog_id != catalog_id:
            continue
        manifests.append(manifest)
    manifests.sort(
        key=lambda m: (
            m.catalog_id,
            m.iteration if m.iteration is not None else -1,
            m.started_at,
            m.run_id,
        )
    )
    return manifests


def write_run_bundle(
    base_dir: Path | str,
    manifest: RunManifest,
    summary: dict | None,
    report_md: str | None,
    *,
    producer: str = "analyzer.manifest",
) -> Path:
    """Write ``manifest.json`` + ``summary.json`` + ``report.md``; return the run dir.

    ``summary`` (the [[analyzer_metrics]] ``summarize()`` dict) and
    ``report_md`` may be ``None`` (chat sessions). Emits one
    ``analyzer.run.recorded`` ledger row (idempotent by content) with
    payload ``{manifest_path, benchmark, metric_kind, n_cases, iteration,
    parent_run_id}`` — producers that call this need not emit it again.
    """
    manifest_path = write_manifest(base_dir, manifest)
    directory = manifest_path.parent
    if summary is not None:
        _atomic_write_text(
            directory / _SUMMARY,
            json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        )
    if report_md is not None:
        _atomic_write_text(directory / _REPORT, report_md)
    ledger.emit(
        base_dir,
        "analyzer.run.recorded",
        {
            "manifest_path": str(manifest_path),
            "benchmark": manifest.benchmark,
            "metric_kind": manifest.metric_kind,
            "n_cases": manifest.n_cases,
            "iteration": manifest.iteration,
            "parent_run_id": manifest.parent_run_id,
        },
        producer=producer,
        correlation={"catalog_id": manifest.catalog_id, "run_id": manifest.run_id},
        refs={"manifest": str(manifest_path)},
    )
    return directory


def read_summary(base_dir: Path | str, catalog_id: str, run_id: str) -> dict | None:
    """The persisted ``summary.json`` dict, or ``None`` when absent / unreadable."""
    path = run_dir(base_dir, catalog_id, run_id) / _SUMMARY
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def dataset_sha256(path: Path | str | None) -> str:
    """sha256 hex of the file bytes; ``""`` when ``path`` is empty or missing."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_dir(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            # Worktree / submodule: `gitdir: <path>`.
            try:
                text = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                target = Path(text[len("gitdir:"):].strip())
                if not target.is_absolute():
                    target = candidate / target
                return target if target.exists() else None
            return None
    return None


def git_head(cwd: Path | str | None = None) -> str:
    """HEAD commit sha of the checkout containing ``cwd`` (default: the
    process cwd); ``""`` outside a git checkout. Reads ``.git`` directly —
    no subprocess.
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    git_dir = _git_dir(start.resolve())
    if git_dir is None:
        return ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return head
    ref = head[len("ref:"):].strip()
    # Worktrees keep refs in the common dir.
    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            rel = commondir_file.read_text(encoding="utf-8").strip()
            common_dir = (git_dir / rel).resolve()
        except OSError:
            pass
    for root in (git_dir, common_dir):
        ref_path = root / ref
        if ref_path.is_file():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
    packed = common_dir / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        except OSError:
            return ""
    return ""


def environment_stamp() -> dict[str, str]:
    """``{"python", "accelerator", "git_head"}`` for producers filling a manifest."""
    accelerator = ""
    try:  # torch is optional; never import it eagerly elsewhere.
        import torch  # type: ignore

        if torch.cuda.is_available():
            accelerator = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001 — absent / broken torch is "no accelerator"
        accelerator = ""
    return {
        "python": platform.python_version(),
        "accelerator": accelerator,
        "git_head": git_head(),
    }


def accelerator_stamp(*, local: bool, device: str = "") -> str:
    """Accelerator name for a manifest.

    ``local`` runs (a ``local_hf`` spec: in-process transformers) stamp the
    real CUDA device name, refined by an explicit ``device`` when the spec
    pins one; everything else stamps ``""`` — an API-served run does not
    execute on this box's accelerator, and ``"auto"`` is a placement policy,
    not a device.
    """
    if not local:
        return ""
    name = environment_stamp()["accelerator"]
    pinned = device if device and device != "auto" else ""
    if name and pinned:
        return f"{name} ({pinned})"
    return name or pinned


def new_started_at() -> str:
    """Convenience: ISO-8601 UTC timestamp for ``started_at`` / ``finished_at``."""
    return now_iso()
