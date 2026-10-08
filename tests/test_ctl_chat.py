"""[[cli_operator]] ``ganglion session new`` + ``ganglion ask``.

Offline end to end: the ``rules`` model, no ``DASHSCOPE_API_KEY``, no network.
``ask`` is the CLI's only two-call command, so the assertions here are mostly
about *how many* sessions a call opens, and about the two contracts the chat
route carries that nothing else does: errata E7 (the same prompt twice is two
traces, ``repeat_index`` 0 then 1) and "a failed inference must still produce a
full trace" (exit 1, raw preserved, nothing on stderr).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ganglion.console.seed import seed_runs  # the one console import a ctl test may make
from ganglion.ctl.main import main

CATALOG = "iot_light_5"
MODEL = "rules"

#: A real ``examples/iot_light/dataset.jsonl`` row.
OK_PROMPT = "거실 불 켜줘"
#: ``at`` becomes ``99:00`` — the catalog rejects it, so the rules client
#: raises ``ModelOutputError`` with the payload attached (errata E9).
FAIL_PROMPT = "99시에 불 켜줘"
#: The one offline prompt whose F⁰ and Fᴷ differ: the rules client's clock
#: regex ignores the ``N분`` part (``15:00``) while ``schedule_light``'s
#: ``prompt_correction`` reads the whole ``오후 3시 30분`` (``15:30``). Both
#: parse, so ``hooks_diff`` is non-empty instead of ``f0_error`` being set.
HOOKED_PROMPT = "오후 3시 30분에 거실 불 켜줘"

#: Per-invocation chat fields; everything else must match the route byte for byte.
_VOLATILE = frozenset({"trace_id", "latency_ms", "repeat_index", "event_ids"})

_ENV = (
    "GANGLION_CATALOG",
    "GANGLION_MODEL",
    "GANGLION_SESSION",
    "GANGLION_RUN",
    "GANGLION_RUNS",
    "GANGLION_MODELS",
    "GANGLION_LABELER",
    "DASHSCOPE_API_KEY",
)


@dataclass(frozen=True)
class Workspace:
    root: Path
    runs: Path


@pytest.fixture()
def workspace(tmp_path, monkeypatch, capsys) -> Workspace:
    """A seeded runs dir plus a context pointing at it, with ``cwd = tmp_path``.

    Seeding happens *before* the chdir: ``seed_runs`` resolves
    ``examples/iot_light/dataset.jsonl`` relative to the cwd.
    """
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    runs = tmp_path / "runs"
    seed_runs(str(runs), catalog_id=CATALOG, limit=20)
    monkeypatch.chdir(tmp_path)
    assert main(["use", "--runs", str(runs), "--catalog", CATALOG, "--model", MODEL, "--json"]) == 0
    capsys.readouterr()
    return Workspace(root=tmp_path, runs=runs)


def _payload(argv: list[str], capsys) -> tuple[int, dict]:
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    return code, json.loads(captured.out)


def _context(root: Path) -> dict:
    return json.loads((root / ".ganglion" / "context.json").read_text(encoding="utf-8"))


def _sessions(runs: Path) -> list[str]:
    """Session directories under the catalog — one per ``POST /api/sessions``."""
    base = runs / CATALOG / "console"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


def _traces(runs: Path, run_id: str) -> list[dict]:
    path = runs / CATALOG / run_id / "traces.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# session new → ask, and the session arithmetic
# ---------------------------------------------------------------------------


def test_session_new_saves_the_context_and_ask_reuses_it(workspace, capsys):
    code, session = _payload(["session", "new", "--json"], capsys)
    assert code == 0
    assert set(session) == {"session_id", "run_id", "manifest_path"}
    assert session["run_id"] == f"console/{session['session_id']}"
    assert Path(session["manifest_path"]).is_file()

    stored = _context(workspace.root)
    assert stored["session"] == session["session_id"]
    assert stored["run"] == session["run_id"]

    code, chat = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0
    assert chat["session_id"] == session["session_id"]
    assert chat["run_id"] == session["run_id"]
    # `ask` found a session in the context, so it must not have opened a second.
    assert _sessions(workspace.runs) == [session["session_id"]]


def test_ask_opens_a_session_when_none_is_resolvable(workspace, capsys):
    code, first = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0
    assert _sessions(workspace.runs) == [first["session_id"]]
    assert _context(workspace.root)["session"] == first["session_id"]

    code, second = _payload(["ask", "--json", "--new-session", OK_PROMPT], capsys)
    assert code == 0
    assert second["session_id"] != first["session_id"]
    assert _sessions(workspace.runs) == sorted([first["session_id"], second["session_id"]])
    # The fresh session becomes the one the next `ask` continues.
    assert _context(workspace.root)["session"] == second["session_id"]


def test_a_one_off_flag_never_mutates_the_saved_context(workspace, monkeypatch, capsys):
    """Resolution is flag > env > file **for this invocation**; `use` is the writer.

    A one-off ``--catalog`` / ``--runs`` that leaked into the context file
    would silently move the operator's saved catalog and store, and a one-off
    ``--model`` would make every later bare `ask` fail. Only the two fields
    the session itself produced may be written.
    """
    before = _context(workspace.root)
    other = workspace.root / "other-runs"

    code, created = _payload(
        ["ask", "--json", "--new-session", "--catalog", "home_iot_20", "--runs", str(other), OK_PROMPT],
        capsys,
    )
    assert code == 0
    assert created["catalog_id"] == "home_iot_20"
    # The run bundle went to the one-off store, not to the saved one.
    assert (other / "home_iot_20" / created["run_id"] / "traces.jsonl").is_file()
    assert not (workspace.runs / "home_iot_20").exists()

    after = _context(workspace.root)
    assert [after["catalog"], after["model"], after["runs_dir"]] == \
        [before["catalog"], before["model"], before["runs_dir"]]
    assert (after["session"], after["run"]) == (created["session_id"], created["run_id"])

    # Same rule for the environment, which `load_context` also outranks the file with.
    monkeypatch.setenv("GANGLION_CATALOG", "home_iot_20")
    code, env_created = _payload(["ask", "--json", "--new-session", "--runs", str(other), OK_PROMPT], capsys)
    assert code == 0 and env_created["catalog_id"] == "home_iot_20"
    assert _context(workspace.root)["catalog"] == before["catalog"]


def test_ask_leaves_no_writer_thread_behind(workspace, capsys):
    """`writer_threads_leaked` must be 0 — `ask` is the leaf that starts a Writer."""
    import threading

    before = threading.active_count()
    assert main(["ask", "--json", OK_PROMPT]) == 0
    capsys.readouterr()
    assert threading.active_count() == before


def test_missing_context_is_exit_1_with_no_route_call(tmp_path, monkeypatch, capsys):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["ask", OK_PROMPT]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing_context" in captured.err and "ganglion use" in captured.err
    assert not (tmp_path / ".ganglion").exists()


# ---------------------------------------------------------------------------
# the plan, the trace, the F⁰/Fᴷ rendering
# ---------------------------------------------------------------------------


def test_korean_prompt_produces_a_plan_and_a_trace(workspace, capsys):
    # nargs="+" — a Korean prompt needs no quoting.
    code, payload = _payload(["ask", "--json", "거실", "불", "켜줘"], capsys)
    assert code == 0
    assert payload["error"] is None
    assert payload["plan"] == {"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}
    assert payload["tool_calls"] == [{"name": "set_light", "arguments": {"room": "living", "state": "on"}}]
    assert payload["f0_plan"] == payload["plan"]
    assert payload["hooks_diff"] == []
    assert payload["catalog_fingerprint"].startswith("cf-")

    rows = _traces(workspace.runs, payload["run_id"])
    assert [row["trace_id"] for row in rows] == [payload["trace_id"]]
    assert rows[0]["prompt"] == OK_PROMPT  # the words joined with a single space
    assert rows[0]["source"] == "lm.invoke"


def test_human_mode_renders_the_f0_fk_pair_and_the_next_step(workspace, capsys):
    code = main(["ask", OK_PROMPT])
    out = capsys.readouterr().out
    assert code == 0
    assert "Fᴷ (catalog accepted)" in out
    assert "F⁰ (model alone)" in out
    assert out.count("1. set_light(room=living, state=on)") == 2  # once per side of the pair
    assert "no hook rewrote the model output" in out
    assert "next  ganglion label add tr-" in out
    # --raw is a human-mode selector: off by default, the raw text stays out.
    assert '"calls"' not in out


def test_raw_prints_the_model_output_and_every_attempt(workspace, capsys):
    code = main(["ask", OK_PROMPT, "--raw"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"action": "set_light"' in out
    assert "attempt 0" in out


def test_raw_is_human_only_and_leaves_the_json_payload_alone(workspace, capsys):
    """A human selector may not reshape the machine contract (`ctl_payload_drift`)."""
    code, plain = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0
    code, with_raw = _payload(["ask", "--json", "--raw", OK_PROMPT], capsys)
    assert code == 0
    assert list(plain) == list(with_raw)  # key order too, not just the set
    for key in sorted(set(plain) - _VOLATILE):
        assert plain[key] == with_raw[key], key


def test_a_rewritten_arg_is_one_hooks_diff_line(workspace, capsys):
    """The F⁰ ≠ Fᴷ branch: `schedule_light`'s prompt_correction rewrites ``at``."""
    code, payload = _payload(["ask", "--json", HOOKED_PROMPT], capsys)
    assert code == 0
    assert payload["hooks_diff"] == ["schedule_light.at: 15:00 → 15:30"]
    assert payload["f0_plan"] != payload["plan"]

    code = main(["ask", HOOKED_PROMPT])
    out = capsys.readouterr().out
    assert code == 0
    assert "  1. schedule_light(at=15:30, room=living, state=on)" in out  # Fᴷ
    assert "  1. schedule_light(at=15:00, room=living, state=on)" in out  # F⁰
    # The serve-side line is printed verbatim, and the empty-case note is gone.
    assert "  schedule_light.at: 15:00 → 15:30" in out
    assert "no hook rewrote" not in out


def test_same_prompt_twice_is_two_traces(workspace, capsys):
    """Errata E7: a deterministic model asked twice must not collapse into one trace."""
    code, first = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0
    code, second = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0

    assert first["case_id"] == second["case_id"]
    assert (first["repeat_index"], second["repeat_index"]) == (0, 1)
    assert first["trace_id"] != second["trace_id"]

    rows = _traces(workspace.runs, first["run_id"])
    assert [row["repeat_index"] for row in rows] == [0, 1]
    assert [row["trace_id"] for row in rows] == [first["trace_id"], second["trace_id"]]


# ---------------------------------------------------------------------------
# a failed inference is still a trace
# ---------------------------------------------------------------------------


def test_failed_inference_is_exit_1_in_both_modes_and_still_traced(workspace, capsys):
    code = main(["ask", FAIL_PROMPT, "--raw"])
    human = capsys.readouterr()
    assert code == 1
    # The route answered 200: a model failure is not a route error.
    assert human.err == ""
    assert "at must be HH:MM" in human.out
    assert "99:00" in human.out  # --raw kept the output that failed validation
    assert "(no plan)" in human.out

    code, payload = _payload(["ask", "--json", FAIL_PROMPT], capsys)
    assert code == 1
    assert payload["plan"] is None
    assert payload["error"] and payload["f0_plan"] is None
    assert payload["raw_plan"]["calls"][0]["args"]["at"] == "99:00"
    assert payload["parse_strategy"] == "failed"

    rows = _traces(workspace.runs, payload["run_id"])
    assert len(rows) == 2
    assert [row["plan"] for row in rows] == [None, None]
    assert all("99:00" in row["raw_output"] for row in rows)
    assert all(row["error_type"] for row in rows)


# ---------------------------------------------------------------------------
# ctl_payload_drift must be 0
# ---------------------------------------------------------------------------


def test_json_output_is_the_route_payload(workspace, capsys):
    from ganglion.console.api import ConsoleAPI
    from ganglion.console.server import default_web_dir

    code, ctl_session = _payload(["session", "new", "--json"], capsys)
    assert code == 0

    api = ConsoleAPI(base_dir=workspace.runs, web_dir=default_web_dir(), models_path=None, labeler=None)
    try:
        status, direct_session = api.handle(
            "POST", "/api/sessions", {},
            {"catalog_id": CATALOG, "model_id": MODEL, "repair": False, "repair_max_attempts": 1},
        )
        assert status == 200
        status, direct_chat = api.handle(
            "POST", "/api/chat", {},
            {
                "session_id": ctl_session["session_id"],
                "catalog_id": CATALOG,
                "model_id": MODEL,
                "prompt": OK_PROMPT,
                "repair": False,
                "repair_max_attempts": 1,
            },
        )
        assert status == 200
    finally:
        api.close()  # one writer thread at a time on this runs dir

    # Key order too: `--json` is the payload verbatim, so nothing may be reordered.
    assert list(ctl_session) == list(direct_session)

    code, ctl_chat = _payload(["ask", "--json", OK_PROMPT], capsys)
    assert code == 0
    assert list(ctl_chat) == list(direct_chat)
    for key in sorted(set(direct_chat) - _VOLATILE):
        assert ctl_chat[key] == direct_chat[key], key
