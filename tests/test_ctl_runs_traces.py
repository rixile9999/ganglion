"""[[cli_operator]] Phase 2 — the ``run`` / ``trace`` / ``compare`` leaves.

Fully offline: the two ``rules`` seed runs, no ``DASHSCOPE_API_KEY``, no
network. Every leaf is asserted three ways, one per observation of the spec:

* ``ctl_commands_unreachable`` — each of ``run list/show/analyze/events``,
  ``compare`` and ``trace list/show`` is invoked through
  :func:`ganglion.ctl.main.main`.
* ``ctl_payload_drift`` — its ``--json`` output equals what
  ``ConsoleAPI.handle`` answers for the same route, and a human-mode selector
  (``--tail`` / ``--name`` / ``--per-case``) never changes it.
* ``p_command_writes`` — a projection leaves the runs dir's
  ``(path, size, mtime)`` set untouched, and does not create the runs dir when
  it is absent. ``run analyze`` and ``compare`` are the two write leaves and
  are asserted to write instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# The one console import a ctl test may make: seeding the fixture runs, and
# replaying a route as the drift oracle. No ctl module imports either.
from ganglion.console.api import ConsoleAPI
from ganglion.console.seed import seed_runs
from ganglion.console.server import default_web_dir
from ganglion.ctl.main import main

CATALOG = "iot_light_5"
RUN_A = "rules-seed"
RUN_B = "rules-degraded-seed"

#: ``seed_runs`` opens ``examples/iot_light/dataset.jsonl`` relative to cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]

_CTL_ENV = (
    "GANGLION_RUNS",
    "GANGLION_CATALOG",
    "GANGLION_MODEL",
    "GANGLION_SESSION",
    "GANGLION_RUN",
    "GANGLION_MODELS",
    "GANGLION_LABELER",
)


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _flags_only_context(monkeypatch, tmp_path):
    """Context resolves from flags alone: no env, no checked-out context file."""
    for name in _CTL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _seed(base: Path, **kwargs: Any) -> Path:
    cwd = Path.cwd()
    os.chdir(_REPO_ROOT)
    try:
        seed_runs(str(base), catalog_id=CATALOG, limit=20, **kwargs)
    finally:
        os.chdir(cwd)
    return base


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> Path:
    """``rules-seed`` + ``rules-degraded-seed``, analysed and compared (~0.05s)."""
    return _seed(tmp_path_factory.mktemp("seeded"))


@pytest.fixture()
def unanalysed(tmp_path) -> Path:
    """The same two runs with no sidecars — what ``run analyze`` fills in."""
    return _seed(tmp_path / "runs", analyze=False, compare=False)


def _q(value: Any) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def _route(base: Path, method: str, path: str, **query: Any) -> Any:
    """``ConsoleAPI.handle`` for the same route — the payload-drift oracle."""
    api = ConsoleAPI(base_dir=base, web_dir=default_web_dir())
    try:
        status, payload = api.handle(
            method, path, {k: [_q(v)] for k, v in query.items() if v not in (None, "")}, None
        )
    finally:
        api.close()
    assert 200 <= status < 300, (status, payload)
    return payload


def _run(argv: list[str], base: Path) -> list[str]:
    return [*argv, "--runs", str(base)]


def _stdout_json(capsys) -> Any:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _snapshot(base: Path) -> set[tuple[str, int, int]]:
    """``(path, size, mtime_ns)`` per entry — a rewrite of equal length shows up."""
    return {
        (str(p.relative_to(base)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in base.rglob("*")
    }


# ---------------------------------------------------------------------------
# run list
# ---------------------------------------------------------------------------


def test_run_list_json_is_the_route_payload(seeded, capsys):
    before = _snapshot(seeded)
    assert main(_run(["run", "list", "--catalog", CATALOG, "--json"], seeded)) == 0
    assert _stdout_json(capsys) == _route(seeded, "GET", "/api/runs", catalog_id=CATALOG)
    assert _snapshot(seeded) == before


def test_run_list_without_a_catalog_lists_every_catalog(seeded, capsys):
    assert main(_run(["run", "list", "--json"], seeded)) == 0
    payload = _stdout_json(capsys)
    assert payload == _route(seeded, "GET", "/api/runs")
    assert {row["run_id"] for row in payload["runs"]} == {RUN_A, RUN_B}


def test_run_list_human_marks_the_context_run(seeded, capsys):
    assert main(_run(["run", "list", "--catalog", CATALOG, "--run", RUN_B], seeded)) == 0
    out = capsys.readouterr().out
    marked = [line for line in out.splitlines() if line.startswith("*")]
    assert len(marked) == 1 and RUN_B in marked[0]
    assert RUN_A in out and "cpx" in out  # sidecar letters, all three present


def test_run_list_jsonl_is_one_run_per_line(seeded, capsys):
    assert main(_run(["run", "list", "--catalog", CATALOG, "--jsonl"], seeded)) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["run_id"] for row in rows] == [RUN_B, RUN_A]


# ---------------------------------------------------------------------------
# run show
# ---------------------------------------------------------------------------


def test_run_show_json_is_the_route_payload(seeded, capsys):
    before = _snapshot(seeded)
    assert main(_run(["run", "show", RUN_B, "--catalog", CATALOG, "--json"], seeded)) == 0
    assert _stdout_json(capsys) == _route(seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}")
    assert _snapshot(seeded) == before


def test_run_show_takes_the_run_from_context(seeded, capsys):
    assert main(_run(["run", "show", "--catalog", CATALOG, "--run", RUN_B, "--json"], seeded)) == 0
    from_flag = _stdout_json(capsys)
    assert main(_run(["run", "show", RUN_B, "--catalog", CATALOG, "--json"], seeded)) == 0
    assert from_flag == _stdout_json(capsys)


def test_run_show_human_charts_the_nonzero_histogram(seeded, capsys):
    assert main(_run(["run", "show", RUN_B, "--catalog", CATALOG], seeded)) == 0
    out = capsys.readouterr().out
    hist = _route(seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}")["histogram"]
    assert "manifest" in out and "summary" in out
    # The chart is its own block; an event row's payload also names failure
    # types, so the zero-row assertion has to look at the block alone.
    chart = next(block for block in out.split("\n\n") if block.startswith("failures  ("))
    assert f"({sum(hist.values())} classified" in chart  # the total is printed
    assert "█" in chart
    for name, count in hist.items():
        assert (name in chart) is (count > 0), name  # zero rows are skipped
    assert "failures[n]" in out  # the summary's long failure list stays a count


# ---------------------------------------------------------------------------
# run events
# ---------------------------------------------------------------------------


def test_run_events_json_ignores_the_human_selectors(seeded, capsys):
    before = _snapshot(seeded)
    argv = ["run", "events", RUN_B, "--catalog", CATALOG, "--json", "--tail", "1",
            "--name", "analyzer.rule.proposed"]
    assert main(_run(argv, seeded)) == 0
    payload = _stdout_json(capsys)
    assert payload == _route(seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/events")
    assert len(payload["events"]) > 1  # --tail did not truncate the payload
    assert _snapshot(seeded) == before


def test_run_events_human_tail_and_name_filter(seeded, capsys):
    assert main(_run(["run", "events", RUN_B, "--catalog", CATALOG, "--tail", "2"], seeded)) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 2 + 2 + 2  # header + rule + 2 rows + blank + note
    assert "showing 2 of" in out

    assert main(_run(["run", "events", RUN_B, "--catalog", CATALOG,
                      "--name", "analyzer.compare.completed"], seeded)) == 0
    out = capsys.readouterr().out
    assert "analyzer.rule.proposed" not in out
    assert "analyzer.compare.completed" in out


# ---------------------------------------------------------------------------
# run analyze (W)
# ---------------------------------------------------------------------------


def test_run_analyze_writes_the_sidecars(unanalysed, capsys):
    directory = unanalysed / CATALOG / RUN_B
    assert not (directory / "classified.jsonl").exists()

    assert main(_run(["run", "analyze", RUN_B, "--catalog", CATALOG, "--json"], unanalysed)) == 0
    payload = _stdout_json(capsys)
    assert set(payload) == {"n_traces", "n_classified", "histogram", "n_patches", "corrections", "paths"}
    for name in ("classified.jsonl", "proposed_patches.jsonl", "proposed_patches.summary.json",
                 "corrections.jsonl", "corrections.summary.json"):
        assert (directory / name).is_file(), name
    # Re-running is idempotent, so the route replay is a valid drift oracle.
    assert payload == _route(unanalysed, "POST", f"/api/runs/{CATALOG}/{RUN_B}/analyze")


def test_run_analyze_human_reports_counts_per_primitive(unanalysed, capsys):
    assert main(_run(["run", "analyze", RUN_B, "--catalog", CATALOG], unanalysed)) == 0
    out = capsys.readouterr().out
    assert "classify" in out and "attribute" in out and "synthesise" in out
    assert "em_f0" in out and "patches" in out


def test_run_analyze_surfaces_the_bfcl_refusal_unchanged(seeded, capsys):
    # Also proves a two-segment catalog_id is interpolated, not url-encoded.
    assert main(_run(["run", "analyze", "r1", "--catalog", "bfcl/simple_python"], seeded)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: not_analyzable — ")


# ---------------------------------------------------------------------------
# compare (W†)
# ---------------------------------------------------------------------------


def test_compare_json_is_the_route_payload(seeded, capsys):
    assert main(_run(["compare", RUN_A, RUN_B, "--catalog", CATALOG, "--json"], seeded)) == 0
    payload = _stdout_json(capsys)
    assert payload == _route(
        seeded, "GET", "/api/compare", catalog_id=CATALOG, run_a=RUN_A, run_b=RUN_B, allow_diff=False
    )
    assert Path(payload["path"]).is_file()  # W†: the route persisted the artifact


def test_compare_per_case_is_human_only(seeded, capsys):
    assert main(_run(["compare", RUN_A, RUN_B, "--catalog", CATALOG, "--json", "--per-case"], seeded)) == 0
    with_flag = _stdout_json(capsys)
    assert main(_run(["compare", RUN_A, RUN_B, "--catalog", CATALOG, "--json"], seeded)) == 0
    assert with_flag == _stdout_json(capsys)

    assert main(_run(["compare", RUN_A, RUN_B, "--catalog", CATALOG], seeded)) == 0
    plain = capsys.readouterr().out
    assert main(_run(["compare", RUN_A, RUN_B, "--catalog", CATALOG, "--per-case"], seeded)) == 0
    per_case = capsys.readouterr().out
    case_id = with_flag["per_case"][0]["case_id"]
    assert case_id in per_case and case_id not in plain
    assert "ΔEM" in plain and "transitions" in plain and "same_fail" in plain


def test_compare_refuses_a_manifest_mismatch_until_allow_diff(unanalysed, capsys):
    manifest = unanalysed / CATALOG / RUN_B / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["metric_kind"] = "ast_match"  # a refusal key ([[analyzer_compare]])
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    argv = ["compare", RUN_A, RUN_B, "--catalog", CATALOG, "--json"]
    assert main(_run(argv, unanalysed)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: compare_refused — ")

    assert main(_run([*argv, "--allow-diff"], unanalysed)) == 0
    result = _stdout_json(capsys)
    assert any("metric_kind" in warning for warning in result["warnings"])


# ---------------------------------------------------------------------------
# trace list
# ---------------------------------------------------------------------------


def test_trace_list_json_is_the_route_payload(seeded, capsys):
    before = _snapshot(seeded)
    argv = ["trace", "list", RUN_B, "--catalog", CATALOG, "--filter", "invalid", "--limit", "5", "--json"]
    assert main(_run(argv, seeded)) == 0
    payload = _stdout_json(capsys)
    assert payload == _route(
        seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/traces", filter="invalid", limit=5
    )
    assert payload["total"] == payload["counts"]["invalid"]
    assert all(row["plan"] is None for row in payload["traces"])  # the route filtered, not us
    assert _snapshot(seeded) == before


def test_trace_list_failure_type_and_offset_reach_the_route(seeded, capsys):
    argv = ["trace", "list", RUN_B, "--catalog", CATALOG, "--failure-type", "unknown_arg",
            "--parent", RUN_A, "--limit", "2", "--offset", "1", "--json"]
    assert main(_run(argv, seeded)) == 0
    payload = _stdout_json(capsys)
    assert payload == _route(
        seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/traces",
        filter="all", failure_type="unknown_arg", parent=RUN_A, limit=2, offset=1,
    )
    assert payload["offset"] == 1 and payload["limit"] == 2
    assert {row["failure_type"] for row in payload["traces"]} == {"unknown_arg"}


def test_trace_list_human_prints_counts_and_paging(seeded, capsys):
    assert main(_run(["trace", "list", RUN_B, "--catalog", CATALOG, "--limit", "3"], seeded)) == 0
    out = capsys.readouterr().out
    counts = _route(seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/traces", limit=3)["counts"]
    assert out.splitlines()[0] == "  ".join(
        f"{key} {counts[key]}" for key in ("all", "invalid", "wrong", "unlabelled", "changed")
    )
    assert f"showing 3 of {counts['all']}" in out


def test_trace_list_unknown_filter_is_a_usage_error(seeded):
    with pytest.raises(SystemExit) as excinfo:
        main(_run(["trace", "list", RUN_B, "--catalog", CATALOG, "--filter", "nope"], seeded))
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# trace show
# ---------------------------------------------------------------------------


def _first_trace_id(base: Path, run_id: str, **query: Any) -> str:
    payload = _route(base, "GET", f"/api/runs/{CATALOG}/{run_id}/traces", limit=1, **query)
    return str(payload["traces"][0]["trace_id"])


def test_trace_show_json_is_the_route_payload(seeded, capsys):
    trace_id = _first_trace_id(seeded, RUN_B, filter="invalid")
    before = _snapshot(seeded)
    assert main(_run(["trace", "show", trace_id, "--catalog", CATALOG, "--run", RUN_B, "--json"], seeded)) == 0
    assert _stdout_json(capsys) == _route(
        seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/traces/{trace_id}"
    )
    assert _snapshot(seeded) == before


def test_trace_show_human_prints_the_plans_and_the_raw_output(seeded, capsys):
    trace_id = _first_trace_id(seeded, RUN_B, filter="invalid")
    detail = _route(seeded, "GET", f"/api/runs/{CATALOG}/{RUN_B}/traces/{trace_id}")
    assert main(_run(["trace", "show", trace_id, "--catalog", CATALOG, "--run", RUN_B], seeded)) == 0
    out = capsys.readouterr().out
    assert detail["trace"]["prompt"] in out
    assert "(no plan)" in out  # the validated plan of an invalid trace
    assert detail["trace"]["error_type"] in out
    assert "raw_plan" in out and "expected" in out
    assert detail["classification"]["failure_type"] in out
    assert "(unlabelled" in out
    assert "attribution" in out


def test_trace_show_unknown_trace_is_exit_1(seeded, capsys):
    assert main(_run(["trace", "show", "tr-nope", "--catalog", CATALOG, "--run", RUN_B], seeded)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: unknown_trace — ")


# ---------------------------------------------------------------------------
# output + projection invariants
# ---------------------------------------------------------------------------


def test_json_mode_is_byte_identical_to_the_route(seeded, capsys):
    """``ctl_payload_drift``: the bytes, not merely the parsed value."""
    for argv, path in [
        (["run", "show", RUN_B, "--catalog", CATALOG, "--json"], f"/api/runs/{CATALOG}/{RUN_B}"),
        (["trace", "list", RUN_B, "--catalog", CATALOG, "--json"], f"/api/runs/{CATALOG}/{RUN_B}/traces"),
    ]:
        assert main(_run(argv, seeded)) == 0
        printed = capsys.readouterr().out
        # The Korean prompts are what makes `ensure_ascii=False` load-bearing.
        assert printed == json.dumps(_route(seeded, "GET", path), ensure_ascii=False, indent=2) + "\n"


# `run list` and `run events` answer an empty projection for a runs dir that is
# not there (the events route reads the ledger without a manifest check); the
# other four surface the route's own 404 `unknown_run`. Either way: exit code
# from the route, and nothing created.
@pytest.mark.parametrize("argv, code", [
    (["run", "list"], 0),
    (["run", "events", RUN_B, "--catalog", CATALOG], 0),
    (["run", "show", RUN_B, "--catalog", CATALOG], 1),
    (["trace", "list", RUN_B, "--catalog", CATALOG], 1),
    (["trace", "show", "tr-nope", "--catalog", CATALOG, "--run", RUN_B], 1),
    (["compare", RUN_A, RUN_B, "--catalog", CATALOG], 1),
])
def test_a_projection_never_creates_the_runs_dir(argv, code, tmp_path, capsys):
    """``p_command_writes``: an absent runs dir stays absent — `run list` included."""
    missing = tmp_path / "ghost"
    assert main(_run(argv, missing)) == code
    capsys.readouterr()
    assert not missing.exists()


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["run", "show"],
    ["run", "analyze"],
    ["run", "events"],
    ["trace", "list"],
    ["trace", "show", "tr-nope"],
    ["compare", RUN_A, RUN_B],
])
def test_missing_context_is_exit_1_and_writes_nothing(argv, seeded, capsys):
    before = _snapshot(seeded)
    assert main(_run(argv, seeded)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing_context" in captured.err and "ganglion use" in captured.err
    assert _snapshot(seeded) == before
