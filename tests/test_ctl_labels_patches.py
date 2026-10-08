"""[[cli_operator]] ``label`` + ``patch``: the human-review write path.

Offline end to end — the ``rules`` model, a ``tmp_path`` runs dir seeded by
``ganglion.console.seed``, no ``DASHSCOPE_API_KEY``, no network. Every leaf is
checked twice: once for the payload (``--json`` must be byte-identical to what
``ConsoleAPI.handle`` answers for the same route — the ``ctl_payload_drift``
observation) and once for the file it appends, or for the files a projection
must *not* create.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ganglion.ctl.main import main

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = "iot_light_5"
GOOD_RUN = "rules-seed"
DEGRADED_RUN = "rules-degraded-seed"

#: A pinned route clock, so a `decision_id` / `created_at` — and the event id
#: content-hashed over it — is reproducible across two identical trees. Without
#: it a `W` leaf's payload can only be compared key by key.
FROZEN_CLOCK = "2026-01-02T03:04:05.000000+00:00"

_ENV_VARS = (
    "GANGLION_CATALOG", "GANGLION_MODEL", "GANGLION_SESSION", "GANGLION_RUN",
    "GANGLION_RUNS", "GANGLION_MODELS", "GANGLION_LABELER", "DASHSCOPE_API_KEY",
)


@pytest.fixture()
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh ``rules-seed`` + ``rules-degraded-seed`` tree under ``tmp_path``."""
    from ganglion.console.seed import seed_runs  # test-only: the seeder is not a ctl dependency

    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    base = tmp_path / "traces"
    # `seed_runs` resolves examples/iot_light/dataset.jsonl relatively.
    monkeypatch.chdir(REPO_ROOT)
    seed_runs(str(base), catalog_id=CATALOG, limit=20)
    # …then out of the repo, so a stray `.ganglion/context.json` cannot land in it.
    monkeypatch.chdir(tmp_path)
    return base


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _route(base: Path, method: str, path: str, query: dict[str, list[str]] | None = None,
           body: Any = None, labeler: str | None = None) -> tuple[int, Any]:
    """The same route through ``ConsoleAPI`` directly — the drift reference."""
    from ganglion.console.api import ConsoleAPI

    api = ConsoleAPI(base_dir=base, labeler=labeler)
    try:
        return api.handle(method, path, query or {}, body)
    finally:
        api.close()


def _as_ctl_json(payload: Any) -> str:
    """What ``--json`` must print for ``payload`` — ``render.Output.json``'s bytes."""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def _mirror(base: Path, tag: str) -> Path:
    """A second, byte-identical seeded tree: a ``W`` leaf cannot be replayed in one."""
    destination = base.parent / tag / base.name
    shutil.copytree(base, destination)
    return destination


def _freeze_route_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from ganglion.console import api as api_module  # test-only, as for `seed_runs`

    monkeypatch.setattr(api_module, "now_iso", lambda: FROZEN_CLOCK)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _traces(base: Path, run_id: str) -> list[dict[str, Any]]:
    return _rows(base / CATALOG / run_id / "traces.jsonl")


def _passing_trace(base: Path) -> dict[str, Any]:
    return next(t for t in _traces(base, GOOD_RUN) if t["plan"] == t["expected_plan"])


def _patch_ids(base: Path) -> list[str]:
    return [p["patch_id"] for p in _rows(base / CATALOG / DEGRADED_RUN / "proposed_patches.jsonl")]


def _labels(base: Path, run_id: str = GOOD_RUN) -> list[dict[str, Any]]:
    return _rows(base / CATALOG / run_id / "labels.jsonl")


def _snapshot(base: Path) -> set[tuple[str, int]]:
    return {(str(p.relative_to(base)), p.stat().st_size) for p in base.rglob("*")}


def _plan_file(directory: Path, name: str, payload: Any) -> str:
    path = directory / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _ctx(base: Path, run_id: str) -> list[str]:
    return ["--runs", str(base), "--catalog", CATALOG, "--run", run_id]


# ---------------------------------------------------------------------------
# label add
# ---------------------------------------------------------------------------


def test_label_add_correct_records_the_verdict(runs, capsys):
    trace = _passing_trace(runs)
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--endorsed", "--saw-f0", "--note", "거실 불 — looks right",
                 "--labeler", "alice", *_ctx(runs, GOOD_RUN), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"label_id", "zone", "family_id", "graded_score", "event_ids"}
    assert payload["graded_score"] == 1.0  # the gold is the dataset's own plan
    assert payload["zone"] in {"train", "dev", "release"}

    rows = _labels(runs)
    assert len(rows) == 1
    row = rows[0]
    assert (row["verdict"], row["endorsed"], row["saw_f0"]) == ("correct", True, True)
    assert row["order_sensitive"] is True  # the route's default; --unordered flips it
    assert row["expected_plan"] is None and row["labeler"] == "alice"
    assert row["label_id"] == payload["label_id"]
    events = {row["event_id"] for row in _rows(runs / CATALOG / GOOD_RUN / "events.jsonl")}
    assert payload["event_ids"][0] in events  # the primitive emitted its own row


def test_label_add_json_is_byte_identical_to_the_route(runs, monkeypatch, capsys):
    """``ctl_payload_drift == 0`` for a ``W`` leaf: one write per tree, pinned clock."""
    _freeze_route_clock(monkeypatch)
    mirror = _mirror(runs, "mirror-label")
    trace = _passing_trace(runs)
    body = {"catalog_id": CATALOG, "run_id": GOOD_RUN, "trace_id": trace["trace_id"],
            "verdict": "correct", "saw_f0": True, "note": "거실 불 — ok"}

    assert main(["label", "add", trace["trace_id"], "--verdict", "correct", "--saw-f0",
                 "--note", "거실 불 — ok", "--labeler", "alice",
                 *_ctx(runs, GOOD_RUN), "--json"]) == 0
    printed = capsys.readouterr().out

    status, reference = _route(mirror, "POST", "/api/labels", body=body, labeler="alice")
    assert status == 200
    assert printed == _as_ctl_json(reference)


def test_label_add_human_mode_names_the_zone_and_the_score(runs, capsys):
    trace = _passing_trace(runs)
    assert main(["label", "add", trace["trace_id"], "--verdict", "unsure",
                 "--not-endorsed", "--unordered", *_ctx(runs, GOOD_RUN)]) == 0
    out = capsys.readouterr().out
    assert "label recorded" in out
    for field in ("label_id", "zone", "family_id", "graded_score"):
        assert field in out
    row = _labels(runs)[0]
    assert row["endorsed"] is False and row["order_sensitive"] is False


def test_label_add_incorrect_takes_an_expected_plan(runs, tmp_path, capsys):
    trace = next(t for t in _traces(runs, DEGRADED_RUN) if t["plan"] != t["expected_plan"])
    gold = trace["expected_plan"]
    path = _plan_file(tmp_path, "gold.json", gold)
    code = main(["label", "add", trace["trace_id"], "--verdict", "incorrect",
                 "--expected", path, "--failure-hint", "missing_required_arg",
                 *_ctx(runs, DEGRADED_RUN), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"label_id", "zone", "family_id", "graded_score", "event_ids"}

    row = _labels(runs, DEGRADED_RUN)[0]
    assert row["verdict"] == "incorrect" and row["expected_plan"] == gold
    assert row["failure_hint"] == "missing_required_arg"


def test_label_add_incorrect_without_a_plan_is_the_routes_422(runs, capsys):
    trace = _passing_trace(runs)
    code = main(["label", "add", trace["trace_id"], "--verdict", "incorrect", *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "invalid_expected_plan" in captured.err
    assert _labels(runs) == []


def test_label_add_should_abstain_needs_no_expected_plan(runs, capsys):
    """The empty plan *is* the gold for an abstention verdict ([[analyzer_label_store]])."""
    trace = _passing_trace(runs)
    assert main(["label", "add", trace["trace_id"], "--verdict", "should_abstain",
                 *_ctx(runs, GOOD_RUN), "--json"]) == 0
    capsys.readouterr()
    row = _labels(runs)[0]
    assert row["verdict"] == "should_abstain"
    assert row["expected_plan"] == {"calls": []}


def test_label_add_expected_from_stdin(runs, monkeypatch, capsys):
    trace = _passing_trace(runs)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(trace["expected_plan"])))
    assert main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", "-", *_ctx(runs, GOOD_RUN), "--json"]) == 0
    capsys.readouterr()
    assert _labels(runs)[0]["expected_plan"] == trace["expected_plan"]


@pytest.mark.parametrize("name,body", [("bad.json", "{not json"), ("empty.json", "")])
def test_label_add_unreadable_expected_is_local_bad_input(runs, tmp_path, capsys, name, body):
    trace = _passing_trace(runs)
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", str(path), *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "bad_input" in captured.err
    assert _labels(runs) == []  # the route was never called


def test_label_add_binary_expected_is_bad_input_not_a_traceback(runs, tmp_path, capsys):
    """A non-UTF-8 file is unreadable too: ``UnicodeDecodeError`` is not an ``OSError``."""
    trace = _passing_trace(runs)
    path = tmp_path / "plan.bin"
    path.write_bytes(b"\xff\xfe\x00{\"calls\": []}")
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", str(path), *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: bad_input — ")
    assert "Traceback" not in captured.err
    assert _labels(runs) == []


def test_label_add_missing_expected_file_is_bad_input(runs, tmp_path, capsys):
    trace = _passing_trace(runs)
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", str(tmp_path / "nope.json"), *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "bad_input" in captured.err
    assert _labels(runs) == []


def test_label_add_plan_the_catalog_rejects_is_the_routes_422(runs, tmp_path, capsys):
    trace = _passing_trace(runs)
    path = _plan_file(tmp_path, "reject.json", {"calls": [{"action": "no_such_tool", "args": {}}]})
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", path, *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "invalid_expected_plan" in captured.err  # never auto-fixed, never retried
    assert _labels(runs) == []


def test_label_add_non_object_expected_is_the_routes_verdict(runs, tmp_path, capsys):
    """Valid JSON that is not an object reaches the route — the plan shape is its call."""
    trace = _passing_trace(runs)
    path = _plan_file(tmp_path, "array.json", [{"action": "set_light"}])
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 "--expected", path, *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "invalid_expected_plan" in captured.err  # not a local bad_input
    assert _labels(runs) == []


def test_label_add_unknown_trace_is_exit_1(runs, capsys):
    code = main(["label", "add", "tr-nope", "--verdict", "correct", *_ctx(runs, GOOD_RUN)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "unknown_trace" in captured.err


def test_label_add_without_a_context_catalog_never_calls_a_route(runs, capsys):
    trace = _passing_trace(runs)
    before = _snapshot(runs)
    code = main(["label", "add", trace["trace_id"], "--verdict", "correct", "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "missing_context" in captured.err and "ganglion use" in captured.err
    assert _snapshot(runs) == before


def test_label_add_rejects_an_unknown_verdict_at_the_parser(runs):
    trace = _passing_trace(runs)
    with pytest.raises(SystemExit) as excinfo:
        main(["label", "add", trace["trace_id"], "--verdict", "maybe", *_ctx(runs, GOOD_RUN)])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# label export (W† — a GET that persists an idempotent artifact)
# ---------------------------------------------------------------------------


def test_label_export_writes_the_split_and_matches_the_route(runs, capsys):
    trace = _passing_trace(runs)
    assert main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 *_ctx(runs, GOOD_RUN), "--json"]) == 0
    capsys.readouterr()

    code = main(["label", "export", CATALOG, "--runs", str(runs), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"paths", "n_sft", "n_hard", "zones", "n_labels"}
    assert payload["zones"] == ["train"] and payload["n_labels"] == 1
    assert set(payload["paths"]) == {"human_sft", "hard_pool", "zones"}
    for path in payload["paths"].values():
        assert Path(path).is_file()
    assert Path(payload["paths"]["human_sft"]).parent == runs.parent / "labels" / CATALOG

    status, reference = _route(runs, "GET", "/api/export/labels",
                              {"catalog_id": [CATALOG], "zones": ["train"]})
    assert status == 200 and payload == reference  # ctl_payload_drift == 0


def test_label_export_uses_the_context_catalog_and_zone_list(runs, capsys):
    trace = _passing_trace(runs)
    assert main(["label", "add", trace["trace_id"], "--verdict", "correct",
                 *_ctx(runs, GOOD_RUN), "--json"]) == 0
    capsys.readouterr()

    assert main(["label", "export", "--runs", str(runs), "--catalog", CATALOG,
                 "--zones", "train,dev"]) == 0
    out = capsys.readouterr().out
    assert "train, dev" in out and "human_sft" in out


def test_label_export_without_a_catalog_is_missing_context(runs, capsys):
    code = main(["label", "export", "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "missing_context" in captured.err


# ---------------------------------------------------------------------------
# patch list (P — creates nothing)
# ---------------------------------------------------------------------------


def test_patch_list_is_a_projection_that_matches_the_route(runs, capsys):
    before = _snapshot(runs)
    code = main(["patch", "list", DEGRADED_RUN, "--runs", str(runs), "--catalog", CATALOG, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert _snapshot(runs) == before  # p_command_writes == 0
    assert not (runs.parent / "labels").exists()

    assert set(payload) == {"patches", "precision", "retire_candidates"}
    assert [p["patch_id"] for p in payload["patches"]] == _patch_ids(runs)
    assert payload["precision"]["n_proposed"] == len(payload["patches"])
    assert all(p["preview"] is None for p in payload["patches"])  # no decision yet

    status, reference = _route(runs, "GET", f"/api/runs/{CATALOG}/{DEGRADED_RUN}/patches")
    assert status == 200 and payload == reference


def test_patch_list_human_mode_tables_the_proposals(runs, capsys):
    assert main(["patch", "list", "--runs", str(runs), "--catalog", CATALOG,
                 "--run", DEGRADED_RUN]) == 0
    out = capsys.readouterr().out
    assert "proposed patch(es)" in out
    for patch_id in _patch_ids(runs):
        assert patch_id in out
    assert "precision" in out and "retire candidates" in out


def test_patch_list_jsonl_streams_one_patch_per_line(runs, capsys):
    assert main(["patch", "list", DEGRADED_RUN, "--runs", str(runs),
                 "--catalog", CATALOG, "--jsonl"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["patch_id"] for line in lines] == _patch_ids(runs)


def test_patch_list_on_the_clean_run_renders_an_empty_table(runs, capsys):
    """``rules-seed`` proposes little or nothing — the empty branch must still render."""
    assert main(["patch", "list", GOOD_RUN, "--runs", str(runs), "--catalog", CATALOG]) == 0
    out = capsys.readouterr().out
    assert "proposed patch(es)" in out


def test_patch_list_unknown_run_is_exit_1(runs, capsys):
    code = main(["patch", "list", "no-such-run", "--runs", str(runs), "--catalog", CATALOG])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "unknown_run" in captured.err


# ---------------------------------------------------------------------------
# patch decide / ported
# ---------------------------------------------------------------------------


def test_patch_decide_appends_a_decision_and_unlocks_the_preview(runs, capsys):
    patch_id = _patch_ids(runs)[0]
    code = main(["patch", "decide", patch_id, "--stage", "blind", "--decision", "accept",
                 "--reason", "cheap and reversible", *_ctx(runs, DEGRADED_RUN), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"decision_id", "event_ids"}

    rows = _rows(runs / CATALOG / DEGRADED_RUN / "patch_decisions.jsonl")
    assert len(rows) == 1
    assert (rows[0]["stage"], rows[0]["decision"]) == ("blind", "accept")
    assert rows[0]["reason"] == "cheap and reversible"
    assert rows[0]["decision_id"] == payload["decision_id"]

    # The route fills `preview` only for a patch that already has a blind decision.
    assert main(["patch", "list", DEGRADED_RUN, "--runs", str(runs),
                 "--catalog", CATALOG, "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    decided = next(p for p in listed["patches"] if p["patch_id"] == patch_id)
    assert decided["decisions"]["blind"]["decision"] == "accept"
    assert decided["preview"] is not None and decided["preview"]["applicable"] is True
    assert listed["precision"]["accepted_blind"] == 1

    assert main(["patch", "list", DEGRADED_RUN, "--runs", str(runs), "--catalog", CATALOG]) == 0
    out = capsys.readouterr().out
    assert "blind:accept" in out
    rescues, regressions = decided["preview"]["rescues"], decided["preview"]["regressions"]
    assert f"+{rescues}/-{regressions}" in out


def test_patch_decide_json_is_byte_identical_to_the_route(runs, monkeypatch, capsys):
    """The other ``W`` shape: ``decision_id`` reproduces once the clock is pinned."""
    _freeze_route_clock(monkeypatch)
    mirror = _mirror(runs, "mirror-decide")
    patch_id = _patch_ids(runs)[0]
    body = {"catalog_id": CATALOG, "run_id": DEGRADED_RUN,
            "stage": "blind", "decision": "accept", "reason": "cheap and reversible"}

    assert main(["patch", "decide", patch_id, "--stage", "blind", "--decision", "accept",
                 "--reason", "cheap and reversible", "--labeler", "alice",
                 *_ctx(runs, DEGRADED_RUN), "--json"]) == 0
    printed = capsys.readouterr().out

    status, reference = _route(mirror, "POST", f"/api/patches/{patch_id}/decision",
                              body=body, labeler="alice")
    assert status == 200
    assert printed == _as_ctl_json(reference)


def test_patch_ported_records_the_commit(runs, capsys):
    patch_id = _patch_ids(runs)[0]
    code = main(["patch", "ported", patch_id, "--commit", "0ff1ce0",
                 *_ctx(runs, DEGRADED_RUN), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"decision_id", "event_ids"}

    row = _rows(runs / CATALOG / DEGRADED_RUN / "patch_decisions.jsonl")[0]
    assert (row["stage"], row["decision"], row["commit_sha"]) == ("ported", "ported", "0ff1ce0")


def test_patch_ported_requires_a_commit(runs):
    patch_id = _patch_ids(runs)[0]
    with pytest.raises(SystemExit) as excinfo:
        main(["patch", "ported", patch_id, *_ctx(runs, DEGRADED_RUN)])
    assert excinfo.value.code == 2


def test_patch_decide_human_mode_echoes_the_stage(runs, capsys):
    patch_id = _patch_ids(runs)[1]
    assert main(["patch", "decide", patch_id, "--stage", "after_preview", "--decision", "hold",
                 *_ctx(runs, DEGRADED_RUN)]) == 0
    out = capsys.readouterr().out
    assert patch_id in out and "after_preview" in out and "hold" in out and "decision_id" in out


def test_patch_decide_unknown_patch_is_the_routes_404(runs, capsys):
    code = main(["patch", "decide", "rs-nope", "--stage", "blind", "--decision", "reject",
                 *_ctx(runs, DEGRADED_RUN)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "unknown_patch" in captured.err
    assert not (runs / CATALOG / DEGRADED_RUN / "patch_decisions.jsonl").exists()


def test_patch_decide_rejects_a_stage_outside_the_vocabulary(runs):
    patch_id = _patch_ids(runs)[0]
    with pytest.raises(SystemExit) as excinfo:
        main(["patch", "decide", patch_id, "--stage", "later", "--decision", "accept",
              *_ctx(runs, DEGRADED_RUN)])
    assert excinfo.value.code == 2


def test_patch_nouns_without_a_verb_exit_2(capsys):
    for noun in ("label", "patch"):
        with pytest.raises(SystemExit) as excinfo:
            main([noun])
        assert excinfo.value.code == 2, noun
        capsys.readouterr()
