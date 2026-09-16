"""`ganglion.cli --trace-store` ([[benchmark_iot]] / [[benchmark_bfcl]] persistence).

Runs the CLI in-process with the offline `rules` model (IoT) and a stubbed
client (BFCL). The run bundle assertions need `ganglion.analyzer.manifest`;
the trace assertions do not.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ganglion import cli
from ganglion.analyzer.trace import Trace
from ganglion.contract.types import ActionPlan
from ganglion.lm.client import ModelOutputError, ModelResult

manifest_mod = pytest.importorskip("ganglion.analyzer.manifest")
ledger_mod = pytest.importorskip("ganglion.analyzer.ledger")


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# argparse surface
# ---------------------------------------------------------------------------


def test_llm_and_model_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--llm", "rules", "--model", "rules", "--limit", "1"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_unknown_model_exits_listing_known_ids() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--model", "ghost@nowhere", "--limit", "1"])
    assert "unknown model_id" in str(excinfo.value) and "rules" in str(excinfo.value)


def test_rules_with_bfcl_exits() -> None:
    with pytest.raises(SystemExit, match="no BFCL adapter"):
        cli.main(["--llm", "rules", "--bfcl", "simple_python", "--bfcl-per-category", "1"])


def test_default_run_id_is_sanitised() -> None:
    when = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    assert cli.default_run_id("qwen3.6-plus@dashscope", when) == "qwen3.6-plus-dashscope-20260916-120000"
    assert cli.default_run_id("local:runs/x", when) == "local-runs-x-20260916-120000"


def test_build_client_legacy_wrapper() -> None:
    from ganglion.contract.builtins import get_catalog
    from ganglion.lm.rules import RuleBasedJSONDSLClient

    assert isinstance(cli.build_client("rules", get_catalog("iot_light_5")), RuleBasedJSONDSLClient)
    with pytest.raises(ValueError, match="unknown llm/model"):
        cli.build_client("nope", get_catalog("iot_light_5"))


# ---------------------------------------------------------------------------
# IoT bundle
# ---------------------------------------------------------------------------


def test_iot_trace_store_writes_bundle_and_is_idempotent(tmp_path, capsys) -> None:
    argv = [
        "--model", "rules", "--tier", "iot_light_5", "--limit", "5",
        "--trace-store", str(tmp_path), "--run-id", "t1", "--iteration", "0",
    ]
    cli.main(argv)
    out = capsys.readouterr()
    run_dir = tmp_path / "iot_light_5" / "t1"
    assert str(run_dir) in out.err
    summary_stdout = json.loads(out.out)
    assert summary_stdout["exact_match_rate"] == 1.0 and summary_stdout["model_id"] == "rules"

    traces = _lines(run_dir / "traces.jsonl")
    assert len(traces) == 5
    for row in traces:
        tr = Trace.from_dict(row)
        assert tr.catalog_id == "iot_light_5" and tr.run_id == "t1" and tr.model_id == "rules"
        assert tr.source == "benchmark.iot" and tr.plan is not None and tr.expected_plan is not None
        assert tr.attempts and tr.raw_plan is not None

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark"] == "iot" and manifest["metric_kind"] == "exact_match"
    assert manifest["n_cases"] == 5 and manifest["limit"] == 5 and manifest["iteration"] == 0
    assert manifest["catalog_fingerprint"].startswith("cf-")
    assert manifest["model_id"] == "rules" and manifest["client_kind"] == "rules"
    assert manifest["model_fingerprint"] == "mf-rules"
    assert manifest["dataset_path"].endswith("dataset.jsonl") and len(manifest["dataset_sha256"]) == 64
    assert manifest["decoding"] == {
        "repair": False, "repair_max_attempts": 1, "thinking": False, "repeat": 1, "grammar_mask": False,
    }
    assert manifest["run_id"] == "t1" and manifest["catalog_id"] == "iot_light_5"
    assert manifest["started_at"].endswith("Z") and manifest["finished_at"].endswith("Z")
    assert manifest["python"]

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["exact_match_rate"] == 1.0 and summary["tier"] == "iot_light_5"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# ") and "exact" in report.lower()
    describe = json.loads((run_dir / "describe.json").read_text(encoding="utf-8"))
    assert describe["name"] == "iot_light_5"

    events = _lines(run_dir / "events.jsonl")
    assert [e["name"] for e in events] == ["analyzer.run.recorded", "analyzer.metrics.summarized"]
    assert events[0]["payload"]["n_cases"] == 5
    assert events[1]["payload"]["n_traces"] == 5
    assert events[0]["correlation"] == {"catalog_id": "iot_light_5", "run_id": "t1"}
    assert events[1]["correlation"] == {"catalog_id": "iot_light_5", "run_id": "t1"}

    listed = manifest_mod.list_runs(tmp_path)
    assert [m.run_id for m in listed] == ["t1"]

    # Re-running the same command adds 0 traces and 0 events.
    cli.main(argv)
    capsys.readouterr()
    assert len(_lines(run_dir / "traces.jsonl")) == 5
    assert len(_lines(run_dir / "events.jsonl")) == 2


def test_iot_without_trace_store_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("ganglion.analyzer.trace.TraceStore.append", lambda *a, **k: pytest.fail("no writes"))
    cli.main(["--llm", "rules", "--limit", "2"])
    assert json.loads(capsys.readouterr().out)["total"] == 2
    assert not any(tmp_path.iterdir())


def test_persist_iot_run_helper_records_failures(tmp_path) -> None:
    """The reusable helper (console `seed` path) keeps failed invocations."""
    from ganglion.analyzer.repair import RepairConfig
    from ganglion.benchmarks.iot.dataset import load_dataset
    from ganglion.benchmarks.iot.runner import run_iot
    from ganglion.contract.builtins import get_catalog
    from ganglion.lm.registry import RULES_SPEC

    catalog = get_catalog("iot_light_5")
    cases = load_dataset(Path("examples/iot_light/dataset.jsonl"), limit=3, catalog=catalog)
    bad = '{"calls":[{"action":"set_light","args":{"room":"living"}}]}'

    class _Failing:
        def invoke(self, prompt: str) -> ModelResult:
            raise ModelOutputError("set_light.state is required", raw=bad)

    results = run_iot(_Failing(), cases)
    run_dir = cli.persist_iot_run(
        tmp_path, results=results, cases=cases, catalog=catalog, catalog_id="iot_light_5",
        spec=RULES_SPEC, run_id="fail-run", dataset_path="examples/iot_light/dataset.jsonl",
        limit=3, repeat=1, repair=RepairConfig(), iteration=1, parent_run="t1",
    )
    assert run_dir == tmp_path / "iot_light_5" / "fail-run"
    traces = [Trace.from_dict(r) for r in _lines(run_dir / "traces.jsonl")]
    assert len(traces) == 3
    assert all(t.plan is None and t.raw_plan == json.loads(bad) and t.attempts for t in traces)
    assert all(t.error_type.startswith("ModelOutputError") for t in traces)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["iteration"] == 1 and manifest["parent_run_id"] == "t1"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["syntax_valid_rate"] == 0.0


# ---------------------------------------------------------------------------
# BFCL bundle (stubbed client, one category)
# ---------------------------------------------------------------------------


class _StubBFCLClient:
    def __init__(self, catalog) -> None:
        self.catalog = catalog

    def invoke(self, prompt: str) -> ModelResult:
        if "irrelevant" in prompt:
            raise RuntimeError("transport down")
        return ModelResult(
            plan=ActionPlan(calls=()),
            raw='{"calls":[]}',
            latency_ms=2.0,
            input_tokens=4,
            output_tokens=2,
        )


def test_bfcl_trace_store_writes_per_category_bundle(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_client_from_spec", lambda spec, catalog, *, repair=None: _StubBFCLClient(catalog))
    cli.main([
        "--model", "qwen3.6-plus@dashscope", "--bfcl", "simple_python", "--bfcl-per-category", "3",
        "--trace-store", str(tmp_path), "--run-id", "t2", "--bfcl-allow-empty-calls",
    ])
    out = capsys.readouterr()
    run_dir = tmp_path / "bfcl" / "simple_python" / "t2"
    assert str(run_dir) in out.err
    assert json.loads(out.out)["total"] == 3

    traces = [Trace.from_dict(r) for r in _lines(run_dir / "traces.jsonl")]
    assert len(traces) == 3
    for tr in traces:
        assert tr.catalog_id == "bfcl/simple_python" and tr.source == "benchmark.bfcl"
        assert tr.expected_plan is None and tr.model_id == "qwen3.6-plus@dashscope"
        assert tr.plan == {"calls": []} and tr.raw_plan == {"calls": []}

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark"] == "bfcl" and manifest["metric_kind"] == "ast_match"
    assert manifest["catalog_id"] == "bfcl/simple_python" and manifest["catalog_fingerprint"] == ""
    assert manifest["client_kind"] == "json-dsl" and manifest["n_cases"] == 3 and manifest["limit"] == 3
    assert manifest["decoding"]["allow_empty_calls"] is True
    assert manifest["dataset_path"].endswith("simple_python.jsonl") and len(manifest["dataset_sha256"]) == 64
    assert manifest["model_fingerprint"].startswith("mf-")
    assert not (run_dir / "report.md").exists()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["total"] == 3 and summary["bfcl_category"] == "simple_python"
    events = _lines(run_dir / "events.jsonl")
    assert [e["name"] for e in events] == ["analyzer.run.recorded", "analyzer.metrics.summarized"]
    assert [m.catalog_id for m in manifest_mod.list_runs(tmp_path)] == ["bfcl/simple_python"]
