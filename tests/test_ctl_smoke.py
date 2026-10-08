"""[[cli_operator]] Phase 0: parser surface, context resolution, rendering, error boundary.

Every assertion here is offline and touches no route that writes: the only
``ApiBridge`` call is ``GET /api/health``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ganglion.ctl.context import DEFAULT_RUNS_DIR, Context, clear_context, load_context, save_context
from ganglion.ctl.errors import CtlError
from ganglion.ctl.main import build_parser, main
from ganglion.ctl.render import Column, Output, bar, fmt, kv, pad, plan_lines, table, truncate, width

# The nouns every leaf must hang under, and the flags every leaf must accept.
_COMMON_FLAGS = ("--runs", "--models", "--catalog", "--model", "--session", "--run", "--labeler", "--json", "--jsonl")


def _leaves(parser, prefix=()):
    """Yield ``(argv_prefix, parser)`` for every parser that carries a handler."""
    actions = [a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict)]
    if not actions:
        yield prefix, parser
        return
    found = False
    for action in actions:
        for name, child in action.choices.items():
            found = True
            yield from _leaves(child, prefix + (name,))
    if not found:  # pragma: no cover — a subparsers action with no choices
        yield prefix, parser


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_every_leaf_has_a_handler_and_the_common_flags():
    parser = build_parser()
    leaves = list(_leaves(parser))
    assert leaves, "no leaf parsers registered"
    for argv, leaf in leaves:
        assert leaf.get_default("_handler") is not None, f"{' '.join(argv)} has no _handler"
        options = {opt for action in leaf._actions for opt in action.option_strings}
        missing = [flag for flag in _COMMON_FLAGS if flag not in options]
        assert not missing, f"{' '.join(argv)} is missing {missing}"


def test_every_leaf_help_exits_zero(capsys):
    for argv, _leaf in _leaves(build_parser()):
        with pytest.raises(SystemExit) as excinfo:
            main([*argv, "--help"])
        assert excinfo.value.code == 0, f"{' '.join(argv)} --help exited {excinfo.value.code}"
        capsys.readouterr()


def test_json_and_jsonl_are_mutually_exclusive():
    with pytest.raises(SystemExit) as excinfo:
        main(["health", "--json", "--jsonl"])
    assert excinfo.value.code == 2


def test_nouns_cover_the_documented_surface():
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(getattr(a, "choices", None), dict))
    assert {"health", "use", "catalog", "model", "ask", "run", "compare", "trace", "label", "patch"} <= set(sub.choices)


# ---------------------------------------------------------------------------
# context: flag > env > file > default
# ---------------------------------------------------------------------------


def test_context_precedence(tmp_path):
    path = tmp_path / ".ganglion" / "context.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"catalog": "from_file", "model": "m_file", "runs_dir": "runs_file"}))

    only_file = load_context(cwd=tmp_path, env={})
    assert (only_file.catalog, only_file.model, str(only_file.runs_dir)) == ("from_file", "m_file", "runs_file")

    with_env = load_context(cwd=tmp_path, env={"GANGLION_CATALOG": "from_env", "GANGLION_RUNS": "runs_env"})
    assert (with_env.catalog, with_env.model, str(with_env.runs_dir)) == ("from_env", "m_file", "runs_env")

    ctl_model = load_context(cwd=tmp_path, env={"GANGLION_CTL_MODEL": "m_env"})
    assert ctl_model.model == "m_env"
    # GANGLION_MODEL is the DashScope *served model name* expanded inside
    # configs/models.yaml, not a registry model_id — reading it here would
    # resolve an id that is not in the registry.
    assert load_context(cwd=tmp_path, env={"GANGLION_MODEL": "qwen3.6-plus"}).model == "m_file"

    with_flag = load_context(catalog="from_flag", runs_dir="runs_flag", cwd=tmp_path,
                            env={"GANGLION_CATALOG": "from_env", "GANGLION_RUNS": "runs_env"})
    assert (with_flag.catalog, str(with_flag.runs_dir)) == ("from_flag", "runs_flag")


def test_context_defaults_and_corrupt_file(tmp_path):
    assert str(load_context(cwd=tmp_path, env={}).runs_dir) == DEFAULT_RUNS_DIR
    path = tmp_path / ".ganglion" / "context.json"
    path.parent.mkdir()
    path.write_text("{not json")
    # A corrupt context is a cache miss, never a failed command.
    assert load_context(cwd=tmp_path, env={}).catalog == ""


def test_save_and_clear_round_trip(tmp_path):
    ctx = load_context(cwd=tmp_path, env={})
    saved = save_context(ctx, catalog="iot_light_5", model="rules", session=None)
    assert saved.catalog == "iot_light_5" and saved.model == "rules"
    assert load_context(cwd=tmp_path, env={}).catalog == "iot_light_5"

    cleared = clear_context(saved)
    assert (cleared.catalog, cleared.model, cleared.session, cleared.run) == ("", "", "", "")
    # runs_dir survives a --clear: it addresses the store, not a selection.
    assert str(cleared.runs_dir) == str(saved.runs_dir)


def test_require_names_the_flag_and_the_use_command():
    with pytest.raises(CtlError) as excinfo:
        Context().require("catalog")
    assert excinfo.value.status == 400
    assert excinfo.value.error == "missing_context"
    assert "--catalog" in excinfo.value.detail and "ganglion use" in excinfo.value.detail


def test_use_writes_the_context_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GANGLION_CATALOG", raising=False)
    assert main(["use", "--catalog", "iot_light_5", "--model", "rules", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["catalog"] == "iot_light_5"
    stored = json.loads((tmp_path / ".ganglion" / "context.json").read_text())
    assert stored == {"catalog": "iot_light_5", "model": "rules", "run": "", "runs_dir": DEFAULT_RUNS_DIR, "session": ""}


def test_use_show_does_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["use", "--catalog", "iot_light_5", "--show", "--json"]) == 0
    capsys.readouterr()
    assert not (tmp_path / ".ganglion" / "context.json").exists()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_width_counts_cjk_as_two_columns():
    assert width("abc") == 3
    assert width("거실 불 켜줘") == 2 * 5 + 2  # five wide glyphs + two spaces
    assert width(pad("거실", 8)) == 8


def test_table_aligns_mixed_width_rows():
    rows = [{"id": "t-1", "prompt": "거실 불 켜줘"}, {"id": "t-22", "prompt": "turn on"}]
    columns = (Column("id", "id"), Column("prompt", "prompt", max_width=10))
    lines = table(rows, columns).splitlines()
    assert len({width(line.rstrip()) for line in lines[1:2]}) == 1  # rule matches header width
    assert all(width(line) <= width(lines[1]) for line in lines)
    assert table([], columns) == "(none)"


def test_truncate_and_fmt():
    assert truncate("a  b\nc", 40) == "a b c"
    assert truncate("거실 불 켜줘", 6).endswith("…")
    assert width(truncate("거실 불 켜줘", 6)) <= 6
    assert (fmt(None), fmt(True), fmt(False), fmt(1.5), fmt(3)) == ("-", "yes", "no", "1.5", "3")
    assert fmt({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'


def test_plan_lines_reads_action_and_args():
    plan = {"calls": [{"action": "set_light", "args": {"state": "on", "room": "living"}}]}
    assert plan_lines(plan) == ["  1. set_light(room=living, state=on)"]
    assert "abstention" in plan_lines({"calls": []})[0]
    assert plan_lines(None) == ["  (no plan)"]


def test_kv_and_bar():
    assert kv([("a", 1), ("bbb", None)]) == "a    1\nbbb  -"
    assert bar(1, 2, columns=4) == "██░░"
    assert bar(0, 0) == ""


def test_output_modes(capsys):
    payload = {"rows": [{"a": 1}, {"a": 2}]}
    Output("json").emit(payload, lambda _p: None)
    assert json.loads(capsys.readouterr().out) == payload

    Output("jsonl").emit(payload, None, rows_key="rows")
    assert capsys.readouterr().out.splitlines() == ['{"a": 1}', '{"a": 2}']

    Output("human").emit(payload, lambda p: print(f"rows={len(p['rows'])}"))
    assert capsys.readouterr().out.strip() == "rows=2"


# ---------------------------------------------------------------------------
# error boundary + the one route
# ---------------------------------------------------------------------------


def test_health_reports_the_runs_dir(tmp_path, capsys):
    assert main(["health", "--runs", str(tmp_path / "nowhere"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["runs_dir"] == str(tmp_path / "nowhere")
    # A projection command creates nothing, not even the runs dir.
    assert not (tmp_path / "nowhere").exists()


def test_route_error_is_exit_1_with_the_routes_own_slug(tmp_path, capsys):
    """A `bfcl/*` id is not resolvable ([[analyzer_catalogs]]) — the route 404s."""
    code = main(["catalog", "show", "bfcl/simple_python", "--runs", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == [captured.err.strip()]  # exactly one line
    assert captured.err.startswith("error: unknown_catalog — ")


def test_a_noun_without_a_verb_exits_2(capsys):
    """Every noun registers its verbs `required=True`; a bare noun is a usage error."""
    for noun in ("catalog", "model", "run", "trace", "label", "patch", "session"):
        with pytest.raises(SystemExit) as excinfo:
            main([noun])
        assert excinfo.value.code == 2, noun
        capsys.readouterr()


def test_bridge_stays_unbuilt_for_a_local_command(tmp_path, monkeypatch, capsys):
    """`use` must not start a Writer thread — a leaked one hangs the process."""
    import threading

    monkeypatch.chdir(tmp_path)
    before = threading.active_count()
    assert main(["use", "--show", "--json"]) == 0
    capsys.readouterr()
    assert threading.active_count() == before
