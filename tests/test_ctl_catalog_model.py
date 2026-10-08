"""[[cli_operator]] Phase 2: the ``catalog`` and ``model`` nouns.

Offline throughout — the ``rules`` model, a seeded ``tmp_path`` runs dir, no
``DASHSCOPE_API_KEY`` and no socket. Two invariants are asserted for every
leaf, because they are the two observations of the spec that catch drift:

* ``ctl_payload_drift`` — a leaf's ``--json`` stdout is the route payload
  byte for byte, so a script binds to ``/api/*`` and not to this package.
* ``p_command_writes`` — a projection command leaves the runs dir's
  ``(path, size)`` set untouched, not even creating a directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from ganglion.ctl.main import build_parser, main
from ganglion.ctl.render import width

_REPO_ROOT = Path(__file__).resolve().parents[1]

_ENV_VARS = (
    "GANGLION_CATALOG",
    "GANGLION_MODEL",
    "GANGLION_SESSION",
    "GANGLION_RUN",
    "GANGLION_RUNS",
    "GANGLION_LABELER",
    "DASHSCOPE_API_KEY",
)

#: Every leaf of the two nouns this module owns — the ``ctl_commands_unreachable``
#: denominator. A leaf added here without a test below is the metric going bad.
_LEAVES = {
    ("catalog", "list"),
    ("catalog", "show"),
    ("catalog", "compile"),
    ("model", "list"),
    ("model", "health"),
    ("model", "load"),
    ("model", "unload"),
}

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["c", "f"]}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": "N-day forecast.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["city", "days"],
            },
        },
    },
]

#: Korean names, args, enum values and description — the CJK width path through
#: every cell of the two tables these nouns render.
_KOREAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "거실조명",
            "description": "거실 조명을 켜거나 끕니다 (한 칸에 들어가지 않는 긴 설명)",
            "parameters": {
                "type": "object",
                "properties": {
                    "방": {"type": "string", "enum": ["거실", "주방", "침실"]},
                    "밝기": {"type": "integer"},
                },
                "required": ["방"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_temp",
            "description": "Set the temperature.",
            "parameters": {"type": "object", "properties": {"c": {"type": "number"}}, "required": ["c"]},
        },
    },
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _leaves(parser, prefix=()):
    """Yield ``(argv_prefix, parser)`` for every parser that carries a handler."""
    actions = [a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict)]
    if not actions:
        yield prefix, parser
        return
    for action in actions:
        for name, child in action.choices.items():
            yield from _leaves(child, prefix + (name,))


@contextlib.contextmanager
def _console(runs_dir: Path) -> Iterator[Any]:
    """A ``ConsoleAPI`` shaped exactly like ``ApiBridge`` builds one.

    The one import of a non-``ctl`` package in this file: the payloads the CLI
    must reproduce verbatim can only come from the route table itself.
    """
    from ganglion.console.api import ConsoleAPI
    from ganglion.console.server import default_web_dir

    api = ConsoleAPI(base_dir=runs_dir, web_dir=default_web_dir(), models_path=None, labeler=None)
    try:
        yield api
    finally:
        api.close()


def _route(runs_dir: Path, method: str, path: str, *, query: dict[str, str] | None = None,
           body: Any = None) -> Any:
    """The route's payload; the status must be 2xx (error paths assert on stderr)."""
    with _console(runs_dir) as api:
        status, payload = api.handle(method, path, {k: [v] for k, v in (query or {}).items()}, body)
    assert 200 <= status < 300, (status, payload)
    return payload


def _json_out(capsys) -> tuple[str, Any]:
    """``(stdout, parsed)`` of a ``--json`` invocation; stderr must be empty."""
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out, json.loads(captured.out)


def _assert_verbatim(out: str, payload: Any) -> None:
    """``--json`` is ``Output.json``'s exact bytes over the route payload."""
    assert out == json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def _snapshot(root: Path) -> set[tuple[str, int, int]]:
    """``(relative path, size, mtime_ns)`` under ``root`` (size ``-1`` = directory).

    ``mtime_ns`` is in the tuple because a rewrite of identical bytes is still a
    write: without it a ``P`` command could truncate-and-restore unnoticed.
    """
    if not root.exists():
        return set()
    out: set[tuple[str, int, int]] = set()
    for path in root.rglob("*"):
        stat = path.stat()
        out.add((str(path.relative_to(root)), stat.st_size if path.is_file() else -1, stat.st_mtime_ns))
    return out


def _assert_table_aligns(text: str) -> None:
    """No row of a header / rule / rows block is wider than its rule.

    The tables here carry Korean cells, which ``len()`` counts as one column
    and a terminal draws as two; this is the assertion that the command layers
    its layout on ``render.table`` rather than on ``str.ljust``.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    rules = [i for i, line in enumerate(lines) if set(line) <= {"-", " "} and "-" in line]
    assert rules, f"no table in:\n{text}"
    for index in rules:
        columns = width(lines[index])
        for row in [lines[index - 1], *lines[index + 1:]]:
            assert width(row) <= columns, f"{row!r} exceeds the {columns}-column rule"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> Path:
    """Two offline runs under ``iot_light_5``; read-only for every test here."""
    from ganglion.console.seed import seed_runs

    base = tmp_path_factory.mktemp("seeded-runs")
    # `seed_runs` resolves examples/iot_light/dataset.jsonl relative to the cwd.
    with contextlib.chdir(_REPO_ROOT):
        seed_runs(str(base), catalog_id="iot_light_5", limit=20)
    return base


@pytest.fixture()
def isolated(tmp_path, monkeypatch) -> Path:
    """No inherited context: ``GANGLION_*`` cleared, cwd a scratch dir.

    Both matter. An inherited ``GANGLION_CATALOG`` would silently change the
    ``?catalog_id=`` a command sends, and a ``.ganglion/context.json`` in the
    checkout would do the same from disk.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# parser surface
# ---------------------------------------------------------------------------


def test_the_two_nouns_register_exactly_the_documented_leaves():
    found = {argv for argv, _leaf in _leaves(build_parser()) if argv and argv[0] in {"catalog", "model"}}
    assert found == _LEAVES


def test_a_noun_without_a_verb_is_a_usage_error():
    for noun in ("catalog", "model"):
        with pytest.raises(SystemExit) as excinfo:
            main([noun])
        assert excinfo.value.code == 2


def test_no_projection_command_creates_the_runs_dir(isolated, capsys):
    """``p_command_writes`` counts directories too: a GET-backed leaf never mkdirs.

    Every other purity assertion below runs against a runs dir that already
    exists, so this is the only place the "including no directory" half of
    [[cli_operator]] §Contract.out is exercised for these two nouns.
    """
    absent = isolated / "never"
    for argv in (
        ["catalog", "list"],
        ["catalog", "show", "iot_light_5"],
        ["model", "list"],
        ["model", "health", "rules"],
    ):
        assert main([*argv, "--runs", str(absent), "--json"]) == 0
        capsys.readouterr()
        assert not absent.exists(), f"`{' '.join(argv)}` created {absent}"


# ---------------------------------------------------------------------------
# catalog list
# ---------------------------------------------------------------------------


def test_catalog_list_is_the_route_payload_and_writes_nothing(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/catalogs")
    before = _snapshot(seeded)

    assert main(["catalog", "list", "--runs", str(seeded), "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    assert payload == expected
    assert {row["catalog_id"] for row in payload["catalogs"]} >= {"iot_light_5", "home_assistant_4"}
    assert _snapshot(seeded) == before


def test_catalog_list_human_marks_the_context_catalog(isolated, seeded, capsys):
    assert main(["catalog", "list", "--runs", str(seeded), "--catalog", "home_iot_20"]) == 0
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if line.startswith("*  ")]
    assert len(marked) == 1 and "home_iot_20" in marked[0]
    assert lines[-1] == "*=context catalog"

    # Without a context catalog the marker column is absent, not blank.
    assert main(["catalog", "list", "--runs", str(seeded)]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("catalog_id")


def test_catalog_list_jsonl_emits_one_object_per_row(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/catalogs")
    assert main(["catalog", "list", "--runs", str(seeded), "--jsonl"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == expected["catalogs"]


# ---------------------------------------------------------------------------
# catalog show
# ---------------------------------------------------------------------------


def test_catalog_show_is_the_route_payload_and_writes_nothing(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/catalogs/iot_light_5")
    before = _snapshot(seeded)

    assert main(["catalog", "show", "iot_light_5", "--runs", str(seeded), "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    assert set(payload) == {
        "catalog_id", "fingerprint", "source", "describe", "json_dsl",
        "openai_tools", "system_prompt", "json_schema", "source_tools",
    }
    assert _snapshot(seeded) == before


def test_catalog_show_falls_back_to_the_context_catalog(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/catalogs/home_assistant_4")
    assert main(["catalog", "show", "--catalog", "home_assistant_4", "--runs", str(seeded), "--json"]) == 0
    _, payload = _json_out(capsys)
    assert payload == expected

    code = main(["catalog", "show", "--runs", str(seeded), "--json"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: missing_context")
    assert "ganglion use --catalog" in captured.err


def test_catalog_show_part_selects_only_the_human_rendering(isolated, seeded, capsys):
    payload = _route(seeded, "GET", "/api/catalogs/iot_light_5")
    argv = ["catalog", "show", "iot_light_5", "--runs", str(seeded)]

    # Raw parts are printed as-is; `print` adds the one trailing newline.
    assert main([*argv, "--part", "dsl"]) == 0
    assert capsys.readouterr().out == payload["json_dsl"] + "\n"

    assert main([*argv, "--part", "prompt"]) == 0
    assert capsys.readouterr().out == payload["system_prompt"] + "\n"

    assert main([*argv, "--part", "describe"]) == 0
    assert json.loads(capsys.readouterr().out) == payload["describe"]

    assert main([*argv, "--part", "tools"]) == 0
    assert json.loads(capsys.readouterr().out) == payload["openai_tools"]

    assert main([*argv, "--part", "schema"]) == 0
    assert json.loads(capsys.readouterr().out) == payload["json_schema"]

    # A builtin tier is declared in code, so it has no source schema.
    assert payload["source_tools"] is None
    assert main([*argv, "--part", "source"]) == 0
    assert capsys.readouterr().out.startswith("(no source_tools")

    assert main([*argv, "--part", "all"]) == 0
    rendered = capsys.readouterr().out
    assert payload["fingerprint"] in rendered
    assert "set_light" in rendered and "room:enum" in rendered
    assert payload["json_dsl"] in rendered

    # --part is a human selector: the machine mode prints the whole payload.
    assert main([*argv, "--part", "dsl", "--json"]) == 0
    with_part, _ = _json_out(capsys)
    assert main([*argv, "--json"]) == 0
    whole, _ = _json_out(capsys)
    assert with_part == whole


def test_catalog_show_jsonl_emits_the_tool_schema_rows(isolated, seeded, capsys):
    # The detail payload's only row-like member is the OpenAI tool list, so that
    # is what --jsonl streams; --json above stays the whole-payload contract.
    expected = _route(seeded, "GET", "/api/catalogs/iot_light_5")
    assert main(["catalog", "show", "iot_light_5", "--runs", str(seeded), "--jsonl"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == expected["openai_tools"]


def test_catalog_show_surfaces_the_routes_own_404(isolated, seeded, capsys):
    # BFCL catalogs are per-case; the route refuses to resolve one by id and the
    # CLI must not turn that into a silent success.
    code = main(["catalog", "show", "bfcl/simple_python", "--runs", str(seeded)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: unknown_catalog")

    code = main(["catalog", "show", "nope", "--runs", str(seeded), "--json"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: unknown_catalog")


# ---------------------------------------------------------------------------
# catalog compile (W)
# ---------------------------------------------------------------------------


def _tools_file(directory: Path, name: str = "weather_tools.json", document: Any = None) -> Path:
    path = directory / name
    path.write_text(json.dumps(_TOOLS if document is None else document), encoding="utf-8")
    return path


def test_catalog_compile_persists_a_catalog_and_is_idempotent(isolated, capsys):
    runs = isolated / "runs"
    tools = _tools_file(isolated)

    assert main(["catalog", "compile", str(tools), "--runs", str(runs), "--json"]) == 0
    out, payload = _json_out(capsys)
    assert set(payload) == {"catalog_id", "fingerprint", "n_tools"}
    assert payload["n_tools"] == 2
    catalog_id = payload["catalog_id"]
    assert catalog_id.startswith("compiled/")
    assert (runs / catalog_id / "catalog.json").is_file()

    # Same tools → same content address, and the payload is the route's own.
    _assert_verbatim(out, _route(runs, "POST", "/api/catalogs/compile",
                                 body={"name": "weather_tools", "tools": _TOOLS}))

    # --name defaults to the file stem, which the list route reports as `label`.
    listed = _route(runs, "GET", "/api/catalogs")
    row = next(r for r in listed["catalogs"] if r["catalog_id"] == catalog_id)
    assert row["label"] == "weather_tools"
    assert row["allow_empty_calls"] is False

    # The compiled id is a two-segment path and must survive interpolation.
    assert main(["catalog", "show", catalog_id, "--runs", str(runs), "--part", "source"]) == 0
    assert json.loads(capsys.readouterr().out) == _TOOLS


def test_catalog_compile_allow_empty_calls_moves_the_fingerprint(isolated, capsys):
    runs = isolated / "runs"
    tools = _tools_file(isolated)

    assert main(["catalog", "compile", str(tools), "--runs", str(runs), "--json"]) == 0
    _, strict = _json_out(capsys)
    assert main(["catalog", "compile", str(tools), "--runs", str(runs),
                 "--name", "abstaining", "--allow-empty-calls", "--json"]) == 0
    _, abstaining = _json_out(capsys)

    assert strict["catalog_id"] != abstaining["catalog_id"]
    assert strict["fingerprint"] != abstaining["fingerprint"]


def test_catalog_compile_reads_stdin(isolated, monkeypatch, capsys):
    runs = isolated / "runs"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tools": _TOOLS})))

    assert main(["catalog", "compile", "-", "--runs", str(runs), "--json"]) == 0
    _, payload = _json_out(capsys)
    assert payload["n_tools"] == 2

    # The wrapper form goes through untouched, so it addresses the same catalog
    # as the bare list, and the stdin label is the route's own default.
    row = next(r for r in _route(runs, "GET", "/api/catalogs")["catalogs"]
               if r["catalog_id"] == payload["catalog_id"])
    assert row["label"] == "compiled_tools"


def test_catalog_compile_bad_input_never_reaches_the_route(isolated, monkeypatch, capsys):
    runs = isolated / "runs"

    missing = isolated / "absent.json"
    code = main(["catalog", "compile", str(missing), "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: bad_input")

    broken = isolated / "broken.json"
    broken.write_text('{"tools":', encoding="utf-8")
    code = main(["catalog", "compile", str(broken), "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: bad_input")

    # Unreadable is not only "absent": a non-UTF-8 byte must be bad_input too,
    # not a UnicodeDecodeError traceback on the stderr reserved for `error: …`.
    binary = isolated / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    code = main(["catalog", "compile", str(binary), "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: bad_input") and "\n" not in captured.err.rstrip()

    # `-` with fd 0 closed: `sys.stdin` is None, which is an input problem and
    # not an AttributeError.
    monkeypatch.setattr(sys, "stdin", None)
    code = main(["catalog", "compile", "-", "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: bad_input")

    # bad_input is raised before the seam, so nothing was written at all.
    assert _snapshot(runs) == set()


def test_catalog_compile_lets_the_route_reject_the_tool_list(isolated, capsys):
    runs = isolated / "runs"
    rejected = _tools_file(isolated, "bad_tools.json", document=[{"nope": 1}])

    code = main(["catalog", "compile", str(rejected), "--runs", str(runs)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    # The compiler's own 422, not reworded by the CLI.
    assert captured.err.startswith("error: invalid_tools")


def test_catalog_tables_align_korean_cells(isolated, capsys):
    runs = isolated / "runs"
    tools = _tools_file(isolated, "ko_tools.json", document=_KOREAN_TOOLS)

    assert main(["catalog", "compile", str(tools), "--runs", str(runs),
                 "--name", "한글카탈로그", "--json"]) == 0
    catalog_id = json.loads(capsys.readouterr().out)["catalog_id"]

    # The tool table: Korean tool name, Korean arg names, truncated Korean
    # description. Everything after the `json_dsl` heading is the catalog's own
    # text, not a table.
    assert main(["catalog", "show", catalog_id, "--runs", str(runs)]) == 0
    rendered = capsys.readouterr().out
    assert "거실조명" in rendered and "방:enum" in rendered
    _assert_table_aligns(rendered.split("json_dsl")[0])

    # The listing's `label` column, where `--name` lands.
    assert main(["catalog", "list", "--runs", str(runs)]) == 0
    listed = capsys.readouterr().out
    assert "한글카탈로그" in listed
    _assert_table_aligns(listed)


# ---------------------------------------------------------------------------
# model list
# ---------------------------------------------------------------------------


def test_model_list_without_a_context_catalog_lists_everything(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/models")
    before = _snapshot(seeded)

    assert main(["model", "list", "--runs", str(seeded), "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    rules = next(row for row in payload["models"] if row["model_id"] == "rules")
    assert rules["kind"] == "rules" and rules["client"] == "json-dsl"
    assert rules["model_fingerprint"] == "mf-rules"
    # No trained_on and no catalog to compare against: not applicable, not a mismatch.
    assert rules["fingerprint_match"] is None
    assert _snapshot(seeded) == before


def test_model_list_sends_the_context_catalog_as_the_query(isolated, seeded, capsys):
    # `rules` declares the three scaling tiers, so it drops out of a
    # home_assistant_4 listing — proof the query reached the registry.
    expected = _route(seeded, "GET", "/api/models", query={"catalog_id": "home_assistant_4"})
    assert main(["model", "list", "--runs", str(seeded), "--catalog", "home_assistant_4", "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    ids = [row["model_id"] for row in payload["models"]]
    assert "rules" not in ids and ids


def test_model_list_human_shows_the_registry_columns(isolated, seeded, capsys):
    assert main(["model", "list", "--runs", str(seeded), "--model", "rules"]) == 0
    lines = capsys.readouterr().out.splitlines()
    header = lines[0].split()
    assert header[:6] == ["model_id", "kind", "client", "provider", "avail", "match"]
    marked = [line for line in lines if line.startswith("*")]
    assert len(marked) == 1 and marked[0].split()[1] == "rules"
    # fingerprint_match null renders as the NONE glyph, never as "no".
    assert marked[0].split()[6] == "-"


def test_model_jsonl_streams_rows_and_falls_back_to_the_one_payload(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/models")
    assert main(["model", "list", "--runs", str(seeded), "--jsonl"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == expected["models"]

    # `health` has no list in its payload: --jsonl prints the single object,
    # which is `Output._rows`' documented fallback.
    assert main(["model", "health", "rules", "--runs", str(seeded), "--jsonl"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line) for line in lines] == [_route(seeded, "GET", "/api/models/rules/health")]


# ---------------------------------------------------------------------------
# model health / load / unload
# ---------------------------------------------------------------------------


def test_model_health_rules_answers_live_offline(isolated, seeded, capsys):
    expected = _route(seeded, "GET", "/api/models/rules/health")
    before = _snapshot(seeded)

    assert main(["model", "health", "rules", "--runs", str(seeded), "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    assert payload["status"] == "live"
    assert _snapshot(seeded) == before

    assert main(["model", "health", "rules", "--runs", str(seeded)]) == 0
    rendered = capsys.readouterr().out
    assert "model_id" in rendered and "live" in rendered


def test_model_health_falls_back_to_the_context_model(isolated, seeded, capsys):
    assert main(["model", "health", "--model", "rules", "--runs", str(seeded), "--json"]) == 0
    _, payload = _json_out(capsys)
    assert payload["status"] == "live"

    code = main(["model", "health", "--runs", str(seeded)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: missing_context")
    assert "ganglion use --model" in captured.err

    code = main(["model", "health", "nope", "--runs", str(seeded)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("error: unknown_model")


def test_model_load_and_unload_refuse_a_non_local_model(isolated, seeded, capsys):
    for verb in ("load", "unload"):
        code = main(["model", verb, "rules", "--runs", str(seeded)])
        captured = capsys.readouterr()
        assert code == 1 and captured.out == ""
        assert captured.err.startswith("error: not_local_model")

    code = main(["model", "load", "--runs", str(seeded)])
    captured = capsys.readouterr()
    assert code == 1 and captured.err.startswith("error: missing_context")


def test_model_unload_of_a_local_model_that_was_never_loaded(isolated, seeded, capsys):
    # Reaches the writer and the lm primitive without importing torch: the
    # process-wide cache is simply empty, which is a 200, not an error.
    expected = _route(seeded, "POST", "/api/models/qwen3-0.6b@local/unload")
    before = _snapshot(seeded)

    assert main(["model", "unload", "qwen3-0.6b@local", "--runs", str(seeded), "--json"]) == 0
    out, payload = _json_out(capsys)

    _assert_verbatim(out, expected)
    assert payload == {"status": "not_loaded", "unloaded": False}
    assert _snapshot(seeded) == before
