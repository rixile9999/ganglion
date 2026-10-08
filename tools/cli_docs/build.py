#!/usr/bin/env python3
"""Generate the `ganglion` CLI reference site data from the CLI itself.

Nothing on the site is written by hand. Three sources of truth are walked and
joined into ``web/assets/cli.json``, which ``web/cli.html`` renders:

1. ``ganglion.ctl.main.build_parser()`` — every noun, verb, positional, flag,
   ``help=``, ``choices`` and default. New commands appear here for free.
2. ``docs/tasks/cli_operator.md`` §Scope — the command → route → kind table.
3. ``docs/tasks/console_operator.md`` — the full console route table, joined
   against (2) to produce the coverage matrix (what is still browser-only).

plus ``tools/cli_docs/examples.py``, whose steps are **executed** against a
throwaway seeded runs dir so every output block on the site is measured. Output
is scrubbed (see :class:`Scrubber`) into something byte-stable, which is what
makes ``--check`` a real staleness gate.

Usage::

    python tools/cli_docs/build.py            # regenerate web/assets/cli.json
    python tools/cli_docs/build.py --check    # exit 1 if the committed file is stale
    python tools/cli_docs/build.py --no-run   # skip example capture (parser only)
    python tools/cli_docs/build.py --standalone out.html   # one self-contained file

Run from anywhere. ``--check`` changes no repository file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "web/assets/cli.json"
PAGE = ROOT / "web/cli.html"
STYLE = ROOT / "web/assets/style.css"
SCRIPT = ROOT / "web/assets/cli.js"
CLI_DOC = ROOT / "docs/tasks/cli_operator.md"
CONSOLE_DOC = ROOT / "docs/tasks/console_operator.md"

sys.path.insert(0, str(ROOT))


def _load_sibling(name: str):
    """Import ``<this dir>/<name>.py`` by path, under a namespaced module name.

    A bare ``import examples`` resolves to the repository's ``examples/``
    namespace package instead — and once pytest has put that in
    ``sys.modules`` (``pythonpath = ["."]``), even a ``sys.path`` insert here
    cannot win. Loading by file path has no name to collide on.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ganglion_cli_docs_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover — unreadable sibling
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


examples_module = _load_sibling("examples")

from ganglion import __version__  # noqa: E402
from ganglion.ctl.context import _ENV as CONTEXT_ENV  # noqa: E402
from ganglion.ctl.main import build_parser, common_parser  # noqa: E402

WARNING = "GENERATED FILE — do not edit. Regenerate: python tools/cli_docs/build.py"

#: Environment variables the CLI reads that are not context fields.
_EXTRA_ENV = (
    ("GANGLION_MODELS", "registry yaml path; the registry's own resolution, shared with --models",
     "ganglion.lm.registry.load_registry"),
    ("GANGLION_LABELER", "author recorded on every label and patch decision (default: operator)",
     "ganglion.console.api.ConsoleAPI.labeler"),
    ("GANGLION_CONSOLE_URL", "loopback HTTP console URL, e.g. http://127.0.0.1:8766; when set, use the running server instead of creating an in-process API",
     "ganglion.ctl.bridge.ApiBridge"),
    ("GANGLION_PII_CHECKPOINT", "native PII checkpoint directory (default: runs/pii/qwen-0.8b-v2); read by the process that owns ProgramService",
     "ganglion.programs.service.ProgramService"),
    ("GANGLION_PII_CANDIDATE_CHECKPOINT", "candidate PII checkpoint directory (default: runs/pii/qwen-0.8b-candidates-v1); read by the process that owns ProgramService",
     "ganglion.programs.service.ProgramService"),
    ("GANGLION_PII_CANDIDATE_FALLBACK_CHECKPOINT", "optional fallback directory; must match the candidate checkpoint's fingerprinted source encoder",
     "ganglion.programs.service.ProgramService"),
)

_ENV_WHAT = {
    "catalog": "default catalog_id, as --catalog would set it",
    "model": "default registry model_id, as --model would set it",
    "session": "default chat session_id",
    "run": "default run_id",
    "runs_dir": "runs-bundle root, as --runs would set it (default: runs/traces)",
}

#: Sourced from cli_operator.md §Contract.out; small and stable enough to state here.
_EXIT_CODES = (
    (0, "success"),
    (1, "a non-2xx route response, a missing context field, or (ask only) a recorded model failure"),
    (2, "argparse usage error — an unknown flag, a missing verb, --json with --jsonl"),
    (130, "KeyboardInterrupt"),
)


# ---------------------------------------------------------------------------
# 1. the argparse tree
# ---------------------------------------------------------------------------


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _choice_help(action: argparse._SubParsersAction) -> dict[str, str]:
    """``name -> help=`` — argparse stores it on the parent, not the child."""
    return {pseudo.dest: (pseudo.help or "") for pseudo in action._choices_actions}


def _action_kind(action: argparse.Action) -> str:
    return {
        "_StoreTrueAction": "flag",
        "_StoreFalseAction": "flag",
        "_StoreConstAction": "const",
        "_CountAction": "count",
        "_AppendAction": "append",
        "_HelpAction": "help",
        "_VersionAction": "version",
        "_StoreAction": "value",
    }.get(type(action).__name__, type(action).__name__)


def _jsonable_default(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _describe_action(action: argparse.Action, common: frozenset[str]) -> dict[str, Any]:
    return {
        "options": list(action.option_strings),
        "dest": action.dest,
        "metavar": action.metavar,
        "nargs": action.nargs if isinstance(action.nargs, (int, str)) else None,
        "help": action.help or "",
        "choices": list(action.choices) if action.choices else None,
        "default": _jsonable_default(action.default),
        "kind": _action_kind(action),
        "required": bool(action.required),
        "common": bool(action.option_strings) and bool(common) and all(
            opt in common for opt in action.option_strings
        ),
    }


def _exclusive_groups(parser: argparse.ArgumentParser) -> list[list[str]]:
    out: list[list[str]] = []
    for group in parser._mutually_exclusive_groups:
        dests = [a.dest for a in group._group_actions]
        if len(dests) > 1:
            out.append(dests)
    return out


def _walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser, str]]:
    """Yield ``(path, parser, help)`` for every leaf (a parser with no subparsers)."""
    action = _subparsers(parser)
    if action is None:
        yield prefix, parser, ""
        return
    helps = _choice_help(action)
    for name, child in action.choices.items():
        grand = _subparsers(child)
        if grand is None:
            yield prefix + (name,), child, helps.get(name, "")
        else:
            for path, leaf, _ in _walk(child, prefix + (name,)):
                yield path, leaf, _choice_help(grand).get(path[-1], "")


def extract_parser() -> dict[str, Any]:
    """The whole CLI surface as JSON-able data."""
    root = build_parser()
    common = common_parser()
    common_opts = frozenset(opt for a in common._actions for opt in a.option_strings)
    common_flags = [
        _describe_action(a, frozenset()) for a in common._actions if a.option_strings
    ]

    top = _subparsers(root)
    assert top is not None, "the root parser must have subcommands"
    top_help = _choice_help(top)

    leaves: list[dict[str, Any]] = []
    for path, leaf, help_text in _walk(root):
        positionals, flags = [], []
        for action in leaf._actions:
            if _action_kind(action) == "help":
                continue
            described = _describe_action(action, common_opts)
            (flags if action.option_strings else positionals).append(described)
        leaves.append({
            "id": "-".join(path),
            "path": list(path),
            "command": "ganglion " + " ".join(path),
            "noun": path[0],
            "verb": " ".join(path[1:]),
            "help": help_text or top_help.get(path[0], ""),
            "description": (leaf.description or "").strip(),
            "usage": " ".join(leaf.format_usage().replace("usage: ", "").split()),
            "positionals": positionals,
            "flags": flags,
            "exclusive_groups": _exclusive_groups(leaf),
        })

    nouns: list[dict[str, Any]] = []
    for name in top.choices:
        members = [leaf["id"] for leaf in leaves if leaf["path"][0] == name]
        nouns.append({"name": name, "help": top_help.get(name, ""), "leaves": members})

    return {
        "prog": root.prog,
        "description": (root.description or "").strip(),
        "epilog": (root.epilog or "").rstrip(),
        "common_flags": common_flags,
        "nouns": nouns,
        "leaves": leaves,
    }


# ---------------------------------------------------------------------------
# 2 + 3. the two markdown route tables
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(r"`(GET|POST|PUT|DELETE|PATCH)\s+([^`]+?)`")
_COMMAND_RE = re.compile(r"`ganglion ([^`]+)`")
_VERB_TOKEN = re.compile(r"[a-z][a-z0-9-]*\Z")


def _cells(line: str) -> list[str]:
    """Markdown table cells, honouring ``\\|`` inside a cell."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    body = stripped.strip("|").replace(r"\|", "\x00")
    return [cell.replace("\x00", "|").strip() for cell in body.split("|")]


def _normalise_route(path: str) -> str:
    """``/api/runs/{cat}/{run}?x=1`` → ``/api/runs/{}/{}`` so two docs can be joined."""
    path = path.split("?", 1)[0].strip()
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def _routes_in(cell: str) -> list[dict[str, str]]:
    out, seen = [], set()
    for method, raw in _ROUTE_RE.findall(cell):
        key = (method, _normalise_route(raw))
        if key in seen:
            continue
        seen.add(key)
        out.append({"method": method, "path": raw.split("?", 1)[0].strip(), "key": f"{method} {key[1]}"})
    return out


def _command_path(cell: str) -> tuple[str, ...] | None:
    match = _COMMAND_RE.search(cell)
    if not match:
        return None
    path: list[str] = []
    for token in match.group(1).split():
        if _VERB_TOKEN.match(token):
            path.append(token)
        else:
            break
    return tuple(path) or None


def parse_command_table(text: str) -> dict[str, dict[str, Any]]:
    """``leaf id -> {routes, kind}`` from cli_operator.md's command table."""
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        cells = _cells(line)
        if len(cells) < 3:
            continue
        path = _command_path(cells[0])
        if path is None:
            continue
        kind = cells[2].strip() or "?"
        if kind.lower() in {"kind", "---"}:
            continue
        out["-".join(path)] = {"routes": _routes_in(cells[1]), "kind": kind}
    return out


def parse_console_routes(text: str) -> list[dict[str, str]]:
    """Every route console_operator.md declares, in document order."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        cells = _cells(line)
        if len(cells) < 2:
            continue
        routes = _routes_in(cells[0])
        if not routes:
            continue
        route = routes[0]
        if route["key"] in seen:
            continue
        seen.add(route["key"])
        kind = cells[1].strip()
        out.append({**route, "kind": kind if len(kind) <= 3 else "?"})
    return out


def build_coverage(leaves: Sequence[Mapping[str, Any]], console_routes: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Console route → the CLI leaves that reach it. Empty list = still browser-only."""
    reached: dict[str, list[str]] = {route["key"]: [] for route in console_routes}
    unknown: list[str] = []
    for leaf in leaves:
        for route in leaf.get("routes") or ():
            if route["key"] in reached:
                reached[route["key"]].append(leaf["id"])
            else:
                unknown.append(f'{leaf["id"]} → {route["key"]}')
    rows = [
        {**route, "reached_by": reached[route["key"]]}
        for route in console_routes
    ]
    return {
        "routes": rows,
        "n_routes": len(rows),
        "n_reached": sum(1 for row in rows if row["reached_by"]),
        "web_only": [row["key"] for row in rows if not row["reached_by"]],
        "unmatched_cli_routes": sorted(set(unknown)),
    }


# ---------------------------------------------------------------------------
# 4. deterministic example capture
# ---------------------------------------------------------------------------

#: Keys whose value is a wall-clock measurement — never reproducible.
#: `latency_ms\w*` covers the aggregate forms (`_mean`, `_p50`, `_p95`, `_stddev`)
#: that `run show`'s summary block prints; missing one of those was the first
#: thing `--check` caught.
_VOLATILE_KEY = r"latency_ms\w*|elapsed\w*|vram_mb|model_load_seconds|seconds|created_at|finished_at"


class Scrubber:
    """Make captured output byte-stable without flattening what matters.

    Content-addressed values stay verbatim — catalog fingerprints (``cf-``),
    model fingerprints (``mf-``), compiled catalog ids (``compiled/<sha12>``)
    and rule-synthesis patch ids (``rs-``) are all functions of their inputs, so
    a reader can reproduce them. Only genuinely per-run values are normalised:
    absolute paths, wall-clock timestamps, latencies, and the ids that hash a
    session's timestamp (``tr-`` / ``lb-`` / ``pd-`` / ``ev-`` / ``s-``).

    Volatile ids are aliased by **order of first appearance**, so a value shown
    by one example still matches the one the next example consumes.
    """

    _ID_RE = re.compile(r"\b(tr|lb|pd|ev)-[0-9a-f]{12,}\b")
    _V2_ID_RE = re.compile(r"\b[0-9a-f]{32}\b")
    _SESSION_RE = re.compile(r"\bs-\d{8}-\d{6}-[0-9a-f]{4}\b")
    _TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
    _NUM_KV_RE = re.compile(rf"\b({_VOLATILE_KEY})(\s+)(?=[-\d.])[-\d.eE+]+")
    _NUM_JSON_RE = re.compile(rf'("(?:{_VOLATILE_KEY})":\s*)(?=[-\d.])[-\d.eE+]+')
    _HEAD_KV_RE = re.compile(r"^(\s*git_head\s+)\S+", re.MULTILINE)
    _HEAD_JSON_RE = re.compile(r'("git_head":\s*)"[^"]*"')
    _PYTHON_RE = re.compile(r"^(\s*python\s+)\d+\.\d+(?:\.\d+)?", re.MULTILINE)
    #: `availability()` names the GPU; a published page should not carry it.
    _GPU_RE = re.compile(r"(cuda available )\([^)]*\)")

    def __init__(self, tmp_root: Path, repo_root: Path) -> None:
        # Longest first: a nested path must not be half-replaced.
        self._paths = sorted(
            [(str(tmp_root), "<TMP>"), (str(repo_root), "<REPO>")],
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self._tmp_prefix_re = re.compile(
            re.escape(str(tmp_root.parent)) + r"/" + re.escape(tmp_root.name.split("-")[0])
            + r"[-\w]*"
        )
        self._alias: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def _alias_for(self, real: str, prefix: str, width: int) -> str:
        if real not in self._alias:
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            self._alias[real] = f"{prefix}-{self._counts[prefix]:0{width}d}"
        return self._alias[real]

    def __call__(self, text: str) -> str:
        for real, placeholder in self._paths:
            text = text.replace(real, placeholder)
        text = self._tmp_prefix_re.sub("<TMP>", text)
        text = self._SESSION_RE.sub(lambda m: self._alias_for(m.group(0), "s-20260101-000000", 4), text)
        text = self._ID_RE.sub(lambda m: self._alias_for(m.group(0), m.group(1), 16), text)
        text = self._V2_ID_RE.sub(lambda m: self._alias_for(m.group(0), "job", 4), text)
        text = self._TS_RE.sub("2026-01-01T00:00:00Z", text)
        text = self._NUM_KV_RE.sub(r"\g<1>0.1234", text)
        text = self._NUM_JSON_RE.sub(r"\g<1>0.1234", text)
        # Environment stamps a run manifest carries. `git_head` moves with every
        # commit and `python` with every interpreter, so leaving either in would
        # make `--check` fail on an unrelated change — the gate would then be
        # noise instead of a signal.
        text = self._HEAD_KV_RE.sub(r"\g<1><COMMIT>", text)
        text = self._HEAD_JSON_RE.sub(r'\g<1>"<COMMIT>"', text)
        text = self._PYTHON_RE.sub(r"\g<1><PYTHON>", text)
        text = self._GPU_RE.sub(r"\g<1>(<GPU>)", text)
        return text


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GANGLION_", "DASHSCOPE_", "RLM_"))}
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return env


#: Fixed stamps handed to the seeded runs, parents before children.
_PINNED_CLOCKS = ("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z")


def _pin_clocks(runs_dir: Path) -> None:
    """Give the seeded runs fixed, ordered ``started_at`` / ``finished_at``.

    `list_runs` sorts by ``(catalog_id, iteration, started_at, run_id)``, which
    is deterministic — but *whether the two seed runs land in the same wall-clock
    second* is not. When they tie, `run_id` orders them (degraded first); when
    seeding crosses a second boundary, `started_at` does (parent first). So
    `run list`'s captured table flips depending on how fast the machine seeded,
    which is a latent flake on any machine, not an environment difference.

    Pinning the clocks after seeding removes the tie entirely and fixes the
    listing order at parent-then-child, which is also the order that reads
    correctly in the documentation.
    """
    manifests = sorted(runs_dir.rglob(_MANIFEST_NAME))
    ordered = sorted(
        manifests,
        key=lambda path: _manifest_order(json.loads(path.read_text(encoding="utf-8"))),
    )
    for index, path in enumerate(ordered):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = _PINNED_CLOCKS[min(index, len(_PINNED_CLOCKS) - 1)]
        payload["started_at"] = stamp
        payload["finished_at"] = stamp
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


_MANIFEST_NAME = "manifest.json"


def _manifest_order(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """``(iteration, has a parent, run_id)`` — a root run sorts before its child."""
    iteration = payload.get("iteration")
    return (
        iteration if isinstance(iteration, int) else -1,
        bool(payload.get("parent_run_id")),
        str(payload.get("run_id", "")),
    )


def _seed(runs_dir: Path, env: Mapping[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "ganglion.console", "seed", "--runs", str(runs_dir), "--limit", "30"],
        cwd=ROOT, env=dict(env), check=True, capture_output=True, text=True,
    )


def run_examples(leaves: Sequence[MutableLeaf], *, keep: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Execute every step of ``examples.STEPS``; return ``leaf id -> [block]``."""
    by_leaf: dict[str, list[dict[str, Any]]] = {}
    env = _clean_env()
    tmp_root = Path(tempfile.mkdtemp(prefix="ganglion-cli-docs-"))
    try:
        runs_dir, work = tmp_root / "runs", tmp_root / "work"
        work.mkdir()
        # Capture a declared fixture, independent of a developer's trained
        # checkpoint. The examples use rules rather than initiating GPU work.
        env["GANGLION_PII_CHECKPOINT"] = str(tmp_root / "untrained-native")
        env["GANGLION_PII_CANDIDATE_CHECKPOINT"] = str(tmp_root / "untrained-candidate")
        _seed(runs_dir, env)
        _pin_clocks(runs_dir)
        scrub = Scrubber(tmp_root, ROOT)
        context: dict[str, Any] = {}

        for index, step in enumerate(examples_module.STEPS):
            for name, contents in step.files.items():
                path = work / f"{name}.json"
                path.write_text(contents, encoding="utf-8")
                context[name] = path.name
            argv = [str(token).format(**context) for token in step.argv]
            full = [*argv, "--runs", str(runs_dir)]
            if step.json_mode:
                full.append("--json")
            proc = subprocess.run(
                [sys.executable, "-m", "ganglion.ctl", *full],
                cwd=work, env=env, capture_output=True, text=True,
            )
            if step.capture is not None:
                try:
                    step.capture(context, json.loads(proc.stdout))
                except (ValueError, KeyError, IndexError) as exc:
                    raise SystemExit(
                        f"step {index} ({' '.join(argv)}) could not capture: {exc}\n"
                        f"stdout: {proc.stdout[:400]}\nstderr: {proc.stderr[:400]}"
                    ) from exc
            if not step.show:
                continue
            by_leaf.setdefault(step.leaf, []).append({
                "argv": [scrub(token) for token in ["ganglion", *argv]] + (["--json"] if step.json_mode else []),
                "note": step.note,
                "exit_code": proc.returncode,
                "stdout": scrub(proc.stdout).rstrip("\n"),
                "stderr": scrub(proc.stderr).rstrip("\n"),
            })
        if keep is not None:
            shutil.copytree(tmp_root, keep, dirs_exist_ok=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return by_leaf


MutableLeaf = dict  # readability alias for the mutable leaf dicts above


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


#: Optional extras whose presence changes what `model list` / `model health`
#: report. The captured output therefore describes one environment, and the
#: gate in tests/test_cli_docs.py only applies where that environment matches.
_OPTIONAL_EXTRAS = ("openai", "torch", "transformers", "peft", "trl", "xgrammar", "nacl")


def _environment() -> dict[str, Any]:
    import importlib.util

    present = [
        name for name in _OPTIONAL_EXTRAS
        if importlib.util.find_spec(name) is not None
    ]
    return {"extras": present, "fingerprint": "+".join(present) or "none"}


def _commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def build(*, run: bool = True) -> dict[str, Any]:
    spec = extract_parser()
    mapping = parse_command_table(CLI_DOC.read_text(encoding="utf-8"))
    console_routes = parse_console_routes(CONSOLE_DOC.read_text(encoding="utf-8"))

    warnings: list[str] = []
    for leaf in spec["leaves"]:
        entry = mapping.get(leaf["id"])
        if entry is None:
            leaf["routes"], leaf["kind"] = [], "?"
            warnings.append(f'{leaf["command"]} has no row in cli_operator.md §Scope')
        else:
            leaf["routes"], leaf["kind"] = entry["routes"], entry["kind"]
    for leaf_id in sorted(set(mapping) - {leaf["id"] for leaf in spec["leaves"]}):
        warnings.append(f"cli_operator.md documents `ganglion {leaf_id.replace('-', ' ')}`, "
                        "which the parser does not register")

    examples = run_examples(spec["leaves"]) if run else {}
    for leaf in spec["leaves"]:
        blocks = examples.get(" ".join(leaf["path"]), [])
        leaf["examples"] = blocks
        leaf["status"] = _status_of(blocks)
    for orphan in sorted(set(examples) - {" ".join(l["path"]) for l in spec["leaves"]}):
        warnings.append(f"examples.py files a block under `{orphan}`, which is not a leaf")

    coverage = build_coverage(spec["leaves"], console_routes)
    warnings.extend(f"cli_operator.md route not in console_operator.md: {row}"
                    for row in coverage["unmatched_cli_routes"])

    spec["coverage"] = coverage
    spec["environment"] = [
        {"name": CONTEXT_ENV[field], "what": _ENV_WHAT[field], "read_by": "ganglion.ctl.context"}
        for field in ("catalog", "model", "session", "run", "runs_dir")
    ] + [{"name": n, "what": w, "read_by": r} for n, w, r in _EXTRA_ENV]
    spec["exit_codes"] = [{"code": code, "meaning": meaning} for code, meaning in _EXIT_CODES]
    spec["warnings"] = warnings
    spec["_generated"] = {
        "warning": WARNING,
        "by": "tools/cli_docs/build.py",
        "ganglion_version": __version__,
        "commit": _commit(),
        "n_leaves": len(spec["leaves"]),
        "n_examples": sum(len(leaf["examples"]) for leaf in spec["leaves"]),
        "examples_captured": run,
        "environment": _environment(),
    }
    return spec


def _status_of(blocks: Sequence[Mapping[str, Any]]) -> str:
    """``shipped`` unless every block reports the Phase-0 stub's 501."""
    if not blocks:
        return "unexercised"
    if all("not_implemented" in (block.get("stderr") or "") for block in blocks):
        return "stub"
    return "shipped"


_NAV_RE = re.compile(r'<nav class="opbar__nav">.*?</nav>', re.DOTALL)
_LINK_RE = re.compile(r'\s*<link rel="stylesheet" href="\./assets/style\.css">')
_SCRIPT_RE = re.compile(r'\s*<script src="\./assets/cli\.js"></script>')


def standalone_html(spec: Mapping[str, Any]) -> str:
    """`web/cli.html` with the stylesheet, renderer and data inlined.

    One file, no server, opens over ``file://`` — for handing the reference to
    someone who cannot reach the console. The renderer is the *same*
    ``cli.js``: it boots from ``window.__GANGLION_CLI__`` when the global is
    present and fetches otherwise, so there is no second implementation to
    keep in step.

    The only thing lost is the console navigation, which would be ten dead
    links next to a lone file; it is replaced by a one-line provenance label.
    """
    page = PAGE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8").replace("</script", "<\\/script")
    # `<` must not appear raw inside a <script>: the captured output is full of
    # `<TMP>` / `<trace_id>` placeholders, and one `</script` would end the block.
    data = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    meta = spec.get("_generated", {})
    label = (
        '<div class="opbar__brand-sub" style="padding:0 20px">standalone snapshot'
        f' · ganglion {esc_html(str(meta.get("ganglion_version", "")))}'
        + (f' · commit {esc_html(str(meta["commit"]))}' if meta.get("commit") else "")
        + " · regenerate with <b>python tools/cli_docs/build.py --standalone</b></div>"
    )

    # Every replacement is passed as a function: `re.sub` would otherwise parse
    # backslash escapes in the replacement text, and the embedded JSON is full
    # of `\u003c`.
    def literal(text: str):
        return lambda _match: text

    page, n_nav = _NAV_RE.subn(literal(label), page, count=1)
    page, n_link = _LINK_RE.subn(literal(f"\n<style>\n{style}\n</style>"), page, count=1)
    page, n_script = _SCRIPT_RE.subn(
        literal(f"\n<script>window.__GANGLION_CLI__={data};</script>\n<script>\n{script}\n</script>"),
        page,
        count=1,
    )
    if not (n_nav and n_link and n_script):
        raise SystemExit(
            "web/cli.html no longer matches the standalone template "
            f"(nav={n_nav}, style={n_link}, script={n_script}) — update the "
            "regexes in tools/cli_docs/build.py"
        )
    return page


def esc_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _dumps(spec: Mapping[str, Any]) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _comparable(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Everything but ``_generated.commit``, which moves with every commit."""
    out = json.loads(json.dumps(spec, ensure_ascii=False))
    out.get("_generated", {}).pop("commit", None)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed file; change nothing")
    parser.add_argument("--no-run", action="store_true",
                        help="skip example capture (parser + routes only)")
    parser.add_argument("--standalone", type=Path, default=None, metavar="FILE",
                        help="also write one self-contained HTML file (opens over file://)")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    spec = build(run=not args.no_run)
    for warning in spec["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    if args.check:
        if not args.out.is_file():
            print(f"stale: {_rel(args.out)} does not exist", file=sys.stderr)
            return 1
        current = json.loads(args.out.read_text(encoding="utf-8"))
        if _comparable(current) != _comparable(spec):
            theirs = (current.get("_generated", {}).get("environment") or {}).get("fingerprint")
            mine = spec["_generated"]["environment"]["fingerprint"]
            if theirs != mine:
                # `model list` / `model health` report what this machine can
                # run, so a different set of optional extras is a legitimate
                # difference, not staleness.
                print(f"environment mismatch: {_rel(args.out)} was generated with "
                      f"extras [{theirs}] but this interpreter has [{mine}] — "
                      "regenerate from the project environment (conda activate ganglion)",
                      file=sys.stderr)
                return 1
            print(f"stale: {_rel(args.out)} differs from a fresh build — "
                  "run `python tools/cli_docs/build.py`", file=sys.stderr)
            return 1
        print(f"current: {_rel(args.out)} "
              f"({spec['_generated']['n_leaves']} leaves, {spec['_generated']['n_examples']} examples)")
        return 0

    if args.standalone is not None:
        if not spec["_generated"]["examples_captured"]:
            print("warning: --standalone with --no-run produces a page with no output blocks",
                  file=sys.stderr)
        args.standalone.parent.mkdir(parents=True, exist_ok=True)
        args.standalone.write_text(standalone_html(spec), encoding="utf-8")
        size = args.standalone.stat().st_size
        print(f"wrote {_rel(args.standalone)}: {size // 1024} KiB, self-contained")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_dumps(spec), encoding="utf-8")
    print(f"wrote {_rel(args.out)}: "
          f"{spec['_generated']['n_leaves']} leaves, {spec['_generated']['n_examples']} examples, "
          f"{spec['coverage']['n_reached']}/{spec['coverage']['n_routes']} routes reached"
          + (f", {len(spec['warnings'])} warning(s)" if spec["warnings"] else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover — script entry point
    sys.exit(main())
