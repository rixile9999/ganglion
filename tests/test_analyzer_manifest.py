"""Tests for ``ganglion.analyzer.manifest`` — [[analyzer_run_manifest]].

Fixtures come from the real rules client through ``run_iot`` →
``traces_from_results`` → ``write_run_bundle`` (see ``analyzer_fixtures``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ganglion.analyzer.ledger import read_events
from ganglion.analyzer.manifest import (
    RunManifest,
    dataset_sha256,
    git_head,
    list_runs,
    read_manifest,
    read_summary,
    run_dir,
    write_manifest,
    write_run_bundle,
)
from ganglion.analyzer.trace import TraceStore

from analyzer_fixtures import CATALOG, CATALOG_ID, DATASET_PATH, load_cases, seed_run


def _manifest(run_id: str, catalog_id: str = CATALOG_ID, **overrides) -> RunManifest:
    base = dict(
        run_id=run_id,
        catalog_id=catalog_id,
        catalog_fingerprint=CATALOG.fingerprint(),
        model_id="rules",
        benchmark="iot",
        decoding={"repair": False, "repair_max_attempts": 0, "thinking": False, "repeat": 1, "grammar_mask": False},
        train_provenance={"dataset_sha256": "abc", "run_id": "sft-1"},
        labels_used={"train": 12, "dev": 3},
        started_at="2026-09-16T00:00:00Z",
    )
    base.update(overrides)
    return RunManifest(**base)


def test_bundle_round_trip_via_real_run(tmp_path: Path) -> None:
    traces, manifest, results = seed_run(tmp_path, "seed-a", load_cases(6))
    assert len(traces) == 6
    directory = run_dir(tmp_path, CATALOG_ID, "seed-a")
    assert (directory / "manifest.json").exists()
    assert (directory / "summary.json").exists()
    assert (directory / "report.md").read_text(encoding="utf-8").startswith("# fixture")
    assert (directory / "traces.jsonl").exists()
    [listed] = list_runs(tmp_path)
    assert listed.to_dict() == manifest.to_dict()
    summary = read_summary(tmp_path, CATALOG_ID, "seed-a")
    assert summary is not None and summary["total"] == 6
    assert summary["exact_match_rate"] == 1.0  # rules client is valid on the dataset
    # One analyzer.run.recorded row per bundle write; idempotent on re-write.
    write_run_bundle(tmp_path, manifest, summary, None)
    events = [e for e in read_events(tmp_path, CATALOG_ID, "seed-a") if e.name == "analyzer.run.recorded"]
    assert len(events) == 1
    assert events[0].payload["n_cases"] == 6
    assert list(TraceStore(tmp_path).iter(CATALOG_ID, "seed-a")) == traces


def test_to_dict_from_dict_round_trip() -> None:
    manifest = _manifest("r1", iteration=2, parent_run_id="r0", limit=50, extra={"note": "x"})
    payload = json.loads(json.dumps(manifest.to_dict(), sort_keys=True))
    again = RunManifest.from_dict(payload)
    assert again == manifest
    assert again.decoding["repair"] is False
    assert again.train_provenance == {"dataset_sha256": "abc", "run_id": "sft-1"}
    assert again.labels_used == {"train": 12, "dev": 3}
    # Tolerant of missing optional keys.
    minimal = RunManifest.from_dict({"run_id": "r", "catalog_id": "c", "benchmark": "chat"})
    assert minimal.decoding == {} and minimal.iteration is None and minimal.metric_kind == "exact_match"


def test_list_runs_order_and_unmanifested_shards(tmp_path: Path) -> None:
    write_manifest(tmp_path, _manifest("iter1", iteration=1, started_at="2026-01-03T00:00:00Z"))
    write_manifest(tmp_path, _manifest("none", iteration=None, started_at="2026-01-02T00:00:00Z"))
    write_manifest(tmp_path, _manifest("iter0", iteration=0, started_at="2026-01-01T00:00:00Z"))
    write_manifest(tmp_path, _manifest("other", catalog_id="home_iot_20", iteration=None))
    # A shard with traces but no manifest is not a run.
    orphan = tmp_path / CATALOG_ID / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "traces.jsonl").write_text("", encoding="utf-8")
    runs = list_runs(tmp_path)
    assert [(m.catalog_id, m.run_id) for m in runs] == [
        ("home_iot_20", "other"),
        (CATALOG_ID, "none"),
        (CATALOG_ID, "iter0"),
        (CATALOG_ID, "iter1"),
    ]
    assert [m.run_id for m in list_runs(tmp_path, CATALOG_ID)] == ["none", "iter0", "iter1"]
    assert list_runs(tmp_path, "smart_home_50") == []
    assert list_runs(tmp_path / "missing") == []


def test_write_manifest_is_atomic_and_guards_identity(tmp_path: Path) -> None:
    path = write_manifest(tmp_path, _manifest("r1"))
    assert path.name == "manifest.json"
    assert not path.with_name("manifest.json.tmp").exists()
    # Same identity → overwrite ok.
    write_manifest(tmp_path, _manifest("r1", n_cases=9))
    assert read_manifest(path).n_cases == 9
    # Different model on the same shard → refuse.
    with pytest.raises(ValueError):
        write_manifest(tmp_path, _manifest("r1", model_id="other-model"))
    write_manifest(tmp_path, _manifest("r1", model_id="other-model"), force=True)
    assert read_manifest(path).model_id == "other-model"
    # Run-id vocabulary.
    with pytest.raises(ValueError):
        write_manifest(tmp_path, _manifest("a/b/c"))
    with pytest.raises(ValueError):
        write_manifest(tmp_path, _manifest(f"{CATALOG_ID}/r1"))
    with pytest.raises(ValueError):
        write_manifest(tmp_path, _manifest("r2", benchmark="nope"))
    # console/<session> is fine.
    p = write_manifest(tmp_path, _manifest("console/s-20260916-abcd", benchmark="chat"))
    assert p.parent == tmp_path / CATALOG_ID / "console" / "s-20260916-abcd"


def test_list_runs_skips_unreadable_manifest(tmp_path: Path) -> None:
    write_manifest(tmp_path, _manifest("good"))
    bad = tmp_path / CATALOG_ID / "bad"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{broken", encoding="utf-8")
    assert [m.run_id for m in list_runs(tmp_path)] == ["good"]


def test_read_summary_absent_returns_none(tmp_path: Path) -> None:
    assert read_summary(tmp_path, CATALOG_ID, "nope") is None


def test_dataset_sha256_and_git_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = dataset_sha256(DATASET_PATH)
    assert len(first) == 64 and first == dataset_sha256(DATASET_PATH)
    assert dataset_sha256(tmp_path / "missing.jsonl") == ""
    assert dataset_sha256("") == ""
    assert git_head(tmp_path) == ""
    monkeypatch.chdir(tmp_path)
    assert git_head() == ""
    repo_head = git_head(Path(__file__).resolve().parents[1])
    assert repo_head == "" or len(repo_head) == 40
