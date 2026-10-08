"""The CLI reference site is generated, so the checked-in data must stay current.

`web/assets/cli.json` is produced by `tools/cli_docs/build.py` from the argparse
tree, the two task-doc route tables and the *measured* output of every example.
This file is the staleness gate ([[cli_operator]] observation
`ctl_payload_drift` / `ctl_commands_unreachable`), plus the structural
invariants the hand-written renderer `web/assets/cli.js` depends on.

`test_generated_file_is_current` re-runs the whole capture (~4 s: it seeds a
throwaway run store and executes every documented command). Everything else
reads the committed file and is instant.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from ganglion.ctl.main import build_parser

ROOT = Path(__file__).resolve().parents[1]
BUILD_PY = ROOT / "tools/cli_docs/build.py"
CLI_JSON = ROOT / "web/assets/cli.json"
CLI_HTML = ROOT / "web/cli.html"
CLI_JS = ROOT / "web/assets/cli.js"


def _load_builder():
    spec = importlib.util.spec_from_file_location("ganglion_cli_docs_build", BUILD_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


@pytest.fixture(scope="module")
def committed() -> dict:
    assert CLI_JSON.is_file(), f"{CLI_JSON} is missing — run python tools/cli_docs/build.py"
    return json.loads(CLI_JSON.read_text(encoding="utf-8"))


def _parser_leaf_ids() -> set[str]:
    def walk(parser, prefix=()):
        subs = [a for a in parser._actions if isinstance(getattr(a, "choices", None), dict)]
        if not subs:
            yield "-".join(prefix)
            return
        for action in subs:
            for name, child in action.choices.items():
                yield from walk(child, prefix + (name,))

    return set(walk(build_parser()))


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_generated_file_is_current(builder, committed):
    """A fresh build must equal the committed file, or the site lies.

    This also pins determinism: the build captures live command output, so a
    value that is not scrubbed (a wall clock, a temp path, `git_head`, a
    tie-broken listing order) surfaces here as a diff rather than as silently
    rotting documentation.

    Two of the captured blocks — `model list` and `model health` on a
    `local_hf` entry — report what *this machine* can run, which is the whole
    point of those examples. So the comparison is exact only when the committed
    file was generated with the same optional extras importable; otherwise it
    skips rather than cry wolf.
    """
    expected_env = (committed["_generated"].get("environment") or {}).get("fingerprint")
    actual_env = builder._environment()["fingerprint"]
    if expected_env != actual_env:
        pytest.skip(
            f"web/assets/cli.json was generated with extras [{expected_env}]; this "
            f"interpreter has [{actual_env}]. Run the project environment "
            f"(conda activate ganglion) to exercise the staleness gate."
        )
    fresh = builder.build(run=True)
    assert builder._comparable(fresh) == builder._comparable(committed), (
        "web/assets/cli.json is stale — run `python tools/cli_docs/build.py`"
    )


def test_captured_output_is_clock_and_order_stable(builder, committed):
    """The listing order must not depend on how fast the machine seeded.

    `list_runs` sorts by `(catalog_id, iteration, started_at, run_id)`, so two
    runs seeded inside one second tie-break on `run_id` and two that straddle a
    second boundary do not. `build.py` pins the seeded manifests' clocks to make
    the order fixed; this asserts the pinned stamps are what reached the page.
    """
    assert builder._PINNED_CLOCKS[0] < builder._PINNED_CLOCKS[1]
    run_list = [leaf for leaf in committed["leaves"] if leaf["id"] == "run-list"][0]
    stdout = run_list["examples"][0]["stdout"]
    parent = stdout.find("rules-seed")
    child = stdout.find("rules-degraded-seed")
    assert parent >= 0 and child >= 0
    assert parent < child, "a root run must list before the run that declares it as parent"


def test_no_accelerator_model_name_is_published(committed):
    """`availability()` names the GPU; a shareable page must not carry it."""
    blob = json.dumps(committed, ensure_ascii=False)
    assert "NVIDIA" not in blob and "GeForce" not in blob
    if "cuda available" in blob:
        assert "cuda available (<GPU>)" in blob


def test_build_reports_no_warnings(builder):
    """A warning means the parser and the task-doc route table have drifted."""
    spec = builder.build(run=False)
    assert spec["warnings"] == []


# ---------------------------------------------------------------------------
# structural invariants (cheap — read the committed file)
# ---------------------------------------------------------------------------


def test_every_parser_leaf_is_in_the_spec(committed):
    assert {leaf["id"] for leaf in committed["leaves"]} == _parser_leaf_ids()


def test_every_leaf_is_mapped_to_a_route_kind(committed):
    unmapped = [leaf["command"] for leaf in committed["leaves"] if leaf["kind"] == "?"]
    assert not unmapped, f"no cli_operator.md row for: {unmapped}"
    # `use` is the one command that calls no route.
    routeless = {leaf["id"] for leaf in committed["leaves"] if not leaf["routes"]}
    assert routeless == {"use"}, routeless


def test_every_leaf_has_a_measured_example(committed):
    """`ctl_commands_unreachable` must be 0 on the documentation side too."""
    bare = [leaf["command"] for leaf in committed["leaves"] if not leaf["examples"]]
    assert not bare, f"no example in tools/cli_docs/examples.py for: {bare}"
    assert {leaf["status"] for leaf in committed["leaves"]} == {"shipped"}


def test_examples_record_their_exit_code_and_output(committed):
    for leaf in committed["leaves"]:
        for example in leaf["examples"]:
            assert example["argv"][0] == "ganglion"
            assert isinstance(example["exit_code"], int)
            assert example["stdout"] or example["stderr"], example["argv"]
            # A non-zero example must show the operator what failed.
            if example["exit_code"]:
                assert example["stderr"].startswith("error: "), example["argv"]


def test_coverage_matrix_covers_every_console_route(committed):
    coverage = committed["coverage"]
    assert coverage["n_routes"] >= 22
    assert coverage["unmatched_cli_routes"] == []
    assert coverage["n_reached"] == coverage["n_routes"], coverage["web_only"]


def test_common_flags_are_listed_once_not_per_leaf(committed):
    """The renderer drops `common` flags from a leaf's table and links to the guide."""
    common = {opt for flag in committed["common_flags"] for opt in flag["options"]}
    assert {"--json", "--jsonl", "--runs", "--catalog"} <= common
    for leaf in committed["leaves"]:
        for flag in leaf["flags"]:
            expected = bool(flag["options"]) and all(opt in common for opt in flag["options"])
            assert flag["common"] is expected, (leaf["id"], flag["options"])


# ---------------------------------------------------------------------------
# no absolute paths / clocks leaked into the published data
# ---------------------------------------------------------------------------


def test_captured_output_is_portable(committed):
    """Nothing machine-specific may reach the published site."""
    blob = json.dumps(committed, ensure_ascii=False)
    assert str(ROOT) not in blob, "an absolute repo path leaked into the captured output"
    assert not re.search(r"/home/[a-z]", blob), "a home directory leaked into the captured output"
    assert "<REPO>" in blob and "<TMP>" in blob, "the path scrubber did not run"


def test_scrubber_normalises_the_volatile_fields(builder, tmp_path):
    scrub = builder.Scrubber(tmp_path, ROOT)
    raw = (
        f"runs_dir  {tmp_path}/runs\n"
        f"web_dir   {ROOT}/web\n"
        "latency_ms          0.3471\n"
        "latency_ms_p95      0.04\n"
        '  git_head             072115f2cfcafa1…\n'
        "  python               3.12.8\n"
        "started_at  2026-09-22T06:38:22Z\n"
        "session s-20260922-063831-ace2 → console/s-20260922-063831-ace2\n"
        "trace tr-176e24c678963e3f then tr-176e24c678963e3f again\n"
        "keep cf-133e0e6b97ea mf-rules rs-iot_light_5-8fd687f88424 compiled/8b1f0c2d4e6a\n"
    )
    out = scrub(raw)
    assert "<TMP>/runs" in out and "<REPO>/web" in out
    assert "0.3471" not in out and "0.04" not in out
    assert "<COMMIT>" in out and "<PYTHON>" in out and "3.12.8" not in out
    assert "2026-09-22T06:38:22Z" not in out and "2026-01-01T00:00:00Z" in out
    # One session id, aliased once and consistently on both sides of the arrow.
    assert len(set(re.findall(r"s-\d{8}-\d{6}-\d{4}", out))) == 1
    # The same trace id maps to the same alias, so examples still correlate.
    aliases = re.findall(r"\btr-\d{16}\b", out)
    assert len(aliases) == 2 and aliases[0] == aliases[1]
    # Content-addressed values are reproducible and must survive verbatim.
    for keep in ("cf-133e0e6b97ea", "mf-rules", "rs-iot_light_5-8fd687f88424", "compiled/8b1f0c2d4e6a"):
        assert keep in out


# ---------------------------------------------------------------------------
# the page's contract with the data
# ---------------------------------------------------------------------------


def test_page_assets_reference_each_other():
    html = CLI_HTML.read_text(encoding="utf-8")
    assert "./assets/cli.js" in html and "./assets/style.css" in html
    assert "./assets/cli.json" in CLI_JS.read_text(encoding="utf-8")


def test_renderer_only_reads_keys_the_generator_writes(committed):
    """Static check that `cli.js` and `build.py` have not drifted apart."""
    js = CLI_JS.read_text(encoding="utf-8")
    checks = (
        ("SPEC", committed, r"\bSPEC\.(\w+)"),
        ("leaf", committed["leaves"][0], r"\bleaf\.(\w+)"),
        ("coverage", committed["coverage"], r"\bcov\.(\w+)"),
        ("_generated", committed["_generated"], r"\bmeta\.(\w+)"),
    )
    for label, payload, pattern in checks:
        used = set(re.findall(pattern, js))
        missing = sorted(key for key in used if key not in payload)
        assert not missing, f"cli.js reads {label}.{missing} which build.py does not write"


def test_standalone_build_is_one_offline_file(builder, committed, tmp_path):
    """`--standalone` must need no server, no sibling asset and no network."""
    html = builder.standalone_html(committed)
    out = tmp_path / "ganglion-cli.html"
    out.write_text(html, encoding="utf-8")

    # Nothing left to load from beside the file.
    assert '<link rel="stylesheet" href="./assets/' not in html
    assert '<script src="./assets/' not in html
    # The three inlined pieces are all there.
    assert "window.__GANGLION_CLI__=" in html
    assert "--mono:" in html, "style.css was not inlined"
    assert "GANGLION CLI REFERENCE" in html, "cli.js was not inlined"
    # `<` inside the embedded JSON must be escaped, or one `</script` in the
    # captured output would terminate the block and blank the page.
    assert "\\u003c" in html
    assert html.count("</script>") == 2
    # Ten dead console links are replaced by a provenance label. The *rules*
    # for .opbar__nav survive, of course — style.css is inlined whole.
    assert '<nav class="opbar__nav">' not in html
    assert "standalone snapshot" in html
    assert './catalog.html' not in html
    # The content actually survived the round trip.
    assert "거실 불 켜줘" in html and "F\u2070" in html
    assert out.stat().st_size > 100_000


def test_standalone_template_regexes_still_match(builder):
    """A restructured `web/cli.html` must fail loudly, not emit a broken file."""
    page = builder.PAGE.read_text(encoding="utf-8")
    for name in ("_NAV_RE", "_LINK_RE", "_SCRIPT_RE"):
        assert getattr(builder, name).search(page), f"{name} no longer matches web/cli.html"


def test_generated_file_carries_the_do_not_edit_stamp(committed):
    meta = committed["_generated"]
    assert "do not edit" in meta["warning"].lower()
    assert meta["by"] == "tools/cli_docs/build.py"
    assert meta["examples_captured"] is True
