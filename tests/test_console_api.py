"""[[console_operator]] — the ``/api/*`` surface over a tmp runs dir.

Drives a real :class:`~ganglion.console.server.ConsoleHTTPServer` bound to
``127.0.0.1:0`` with ``urllib.request``, exactly as a browser would. Covers
the composite's ``success`` clause: health, catalogs (list / detail /
compile), sessions + chat, labels (accept + 422), the seeded runs (list,
detail, trace filters, analyze, patches, decision, compare, export, events),
errata **E7** (a repeated prompt is a second trace via ``repeat_index``) and
the "GET creates no files" invariant for every projection route.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ganglion.analyzer.analyze import UNCLASSIFIED
from ganglion.analyzer.catalogs import resolve_catalog
from ganglion.console.api import ConsoleAPI
from ganglion.console.seed import DEGRADED_SEED_RUN_ID, RULES_SEED_RUN_ID, seed_runs
from ganglion.console.server import create_server
from ganglion.console.writer import Writer, WriterDeadError
from ganglion.lm.registry import RULES_SPEC, ModelSpec, Registry

CATALOG_ID = "iot_light_5"
KOREAN_PROMPT = "거실 불 켜줘"

TWO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                },
                "required": ["to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inbox",
            "description": "List the inbox.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def request(server, method: str, path: str, body=None) -> tuple[int, object]:
    """One HTTP call → ``(status, parsed json)``; HTTP errors are returned, not raised."""
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return int(exc.code), json.loads(raw) if raw else None


def get(server, path: str) -> tuple[int, object]:
    return request(server, "GET", path)


def post(server, path: str, body=None) -> tuple[int, object]:
    return request(server, "POST", path, body)


def raw_get(server, path: str) -> tuple[int, bytes, str]:
    """Static fetch → ``(status, body bytes, content type)``."""
    url = f"http://127.0.0.1:{server.port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return int(response.status), response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), exc.headers.get("Content-Type", "")


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """``{relative path: (size, mtime_ns)}`` for every file under ``root``."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def make_server(base_dir: Path, web_dir: Path, registry: Registry):
    api = ConsoleAPI(base_dir=base_dir, web_dir=web_dir, registry=registry)
    server = create_server(api=api, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(scope="module")
def web_dir(tmp_path_factory) -> Path:
    """A minimal static tree (the real ``web/`` belongs to the web tasks)."""
    root = tmp_path_factory.mktemp("web")
    (root / "index.html").write_text("<h1>console</h1>", encoding="utf-8")
    (root / "chat.html").write_text("<h1>chat</h1>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "api.js").write_text("window.GanglionAPI = {};\n", encoding="utf-8")
    (root / "secret.key").write_text("not-served-outside", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> tuple[Path, dict]:
    """``seed_runs`` on a tmp runs dir — the same helper the ``seed`` command calls."""
    base = tmp_path_factory.mktemp("console") / "runs" / "traces"
    result = seed_runs(base, catalog_id=CATALOG_ID, limit=20)
    return base, result


@pytest.fixture(scope="module")
def server(seeded, web_dir):
    base, _result = seeded
    srv, thread = make_server(base, web_dir, Registry([RULES_SPEC]))
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def session(server) -> dict:
    status, payload = post(
        server, "/api/sessions", {"catalog_id": CATALOG_ID, "model_id": "rules"}
    )
    assert status == 200, payload
    return payload


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


def test_writer_blocks_and_reraises():
    with Writer() as writer:
        assert writer.submit(lambda a, b: a + b, 2, 3) == 5

        def boom():
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            writer.submit(boom)
        assert writer.stats()["jobs"] == 2
    assert not writer.alive
    with pytest.raises(WriterDeadError):
        writer.submit(lambda: None)


# ---------------------------------------------------------------------------
# health, catalogs, models
# ---------------------------------------------------------------------------


def test_health(server, seeded):
    base, _ = seeded
    status, payload = get(server, "/api/health")
    assert status == 200
    assert payload["ok"] is True
    assert payload["runs_dir"] == str(base)
    assert payload["registry_path"] is None
    assert payload["version"]


def test_catalogs_list_and_detail(server):
    status, payload = get(server, "/api/catalogs")
    assert status == 200
    ids = {row["catalog_id"] for row in payload["catalogs"]}
    assert {"iot_light_5", "home_iot_20", "smart_home_50", "home_assistant_4"} <= ids

    status, detail = get(server, f"/api/catalogs/{CATALOG_ID}")
    assert status == 200
    assert detail["catalog_id"] == CATALOG_ID
    assert detail["fingerprint"].startswith("cf-")
    assert detail["source"] == "builtin"
    assert detail["source_tools"] is None
    assert detail["describe"]["name"] == CATALOG_ID
    assert "set_light" in detail["json_dsl"]
    assert detail["json_dsl"] in detail["system_prompt"]
    assert any(t["function"]["name"] == "set_light" for t in detail["openai_tools"])

    status, payload = get(server, "/api/catalogs/nope_9")
    assert status == 404
    assert payload["error"] == "unknown_catalog"


def test_compile_catalog(server, seeded):
    base, _ = seeded
    status, payload = post(
        server,
        "/api/catalogs/compile",
        {"name": "mailbox", "tools": TWO_TOOLS, "allow_empty_calls": True},
    )
    assert status == 200, payload
    catalog_id = payload["catalog_id"]
    assert catalog_id.startswith("compiled/")
    assert payload["n_tools"] == 2

    # E5: the compiled catalog's own name is its id, so patches stamp it.
    assert resolve_catalog(catalog_id, base_dir=base).name == catalog_id

    status, detail = get(server, f"/api/catalogs/{catalog_id}")
    assert status == 200
    assert detail["source"] == "compiled"
    assert [t["function"]["name"] for t in detail["source_tools"]] == ["send_email", "list_inbox"]

    # Idempotent: same input → same sha12, one directory.
    status, again = post(
        server,
        "/api/catalogs/compile",
        {"name": "mailbox", "tools": TWO_TOOLS, "allow_empty_calls": True},
    )
    assert again["catalog_id"] == catalog_id
    assert (base / catalog_id / "catalog.json").is_file()

    status, payload = post(server, "/api/catalogs/compile", {"name": "x", "tools": []})
    assert status == 400


def test_models_and_health(server):
    status, payload = get(server, f"/api/models?catalog_id={CATALOG_ID}")
    assert status == 200
    rows = payload["models"]
    assert [row["model_id"] for row in rows] == ["rules"]
    assert rows[0]["available"] is True
    assert rows[0]["model_fingerprint"] == "mf-rules"
    assert rows[0]["fingerprint_match"] is None

    status, payload = get(server, "/api/models/rules/health")
    assert status == 200
    assert payload["status"] == "live"

    status, payload = get(server, "/api/models/ghost/health")
    assert status == 404
    assert payload["error"] == "unknown_model"

    # Load / unload are local_hf-only.
    for verb in ("load", "unload"):
        status, payload = post(server, f"/api/models/rules/{verb}")
        assert status == 404
        assert payload["error"] == "not_local_model"


# ---------------------------------------------------------------------------
# sessions, chat, labels
# ---------------------------------------------------------------------------


def test_session_creates_manifest(server, seeded, session):
    base, _ = seeded
    assert session["run_id"] == f"console/{session['session_id']}"
    manifest = json.loads(Path(session["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["benchmark"] == "chat"
    assert manifest["model_id"] == "rules"
    assert manifest["client_kind"] == "rules"
    assert (Path(session["manifest_path"]).parent / "describe.json").is_file()

    status, payload = post(server, "/api/sessions", {"catalog_id": "bfcl/simple_python", "model_id": "rules"})
    assert status == 404
    status, payload = post(server, "/api/sessions", {"catalog_id": CATALOG_ID, "model_id": "ghost"})
    assert status == 404
    status, payload = post(server, "/api/sessions", {"catalog_id": CATALOG_ID})
    assert status == 400


def test_chat_stores_a_trace(server, seeded, session):
    base, _ = seeded
    status, payload = post(
        server,
        "/api/chat",
        {
            "session_id": session["session_id"],
            "catalog_id": CATALOG_ID,
            "model_id": "rules",
            "prompt": KOREAN_PROMPT,
        },
    )
    assert status == 200, payload
    assert payload["error"] is None
    assert payload["plan"]["calls"][0]["action"] == "set_light"
    assert payload["plan"]["calls"][0]["args"]["room"] == "living"
    assert payload["tool_calls"][0]["name"] == "set_light"
    assert payload["trace_id"].startswith("tr-")
    assert payload["run_id"] == session["run_id"]
    assert payload["order_sensitive"] is True
    assert len(payload["event_ids"]) == 2
    assert "model_load_seconds" not in payload

    shard = base / CATALOG_ID / session["run_id"] / "traces.jsonl"
    rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines() if line.strip()]
    stored = [row for row in rows if row["trace_id"] == payload["trace_id"]]
    assert len(stored) == 1
    assert stored[0]["prompt"] == KOREAN_PROMPT
    assert stored[0]["source"] == "lm.invoke"
    assert stored[0]["expected_plan"] is None

    events = get(server, f"/api/runs/{CATALOG_ID}/{session['run_id']}/events")[1]["events"]
    names = [event["name"] for event in events]
    assert "analyzer.run.recorded" in names
    assert "lm.inference.completed" in names
    assert "analyzer.trace.recorded" in names

    status, payload = post(
        server,
        "/api/chat",
        {"session_id": session["session_id"], "catalog_id": CATALOG_ID, "model_id": "ghost", "prompt": "x"},
    )
    assert status == 404

    # An unknown session is an unknown run: no orphan shard is created.
    status, payload = post(
        server,
        "/api/chat",
        {"session_id": "s-nope", "catalog_id": CATALOG_ID, "model_id": "rules", "prompt": "x"},
    )
    assert status == 404
    assert payload["error"] == "unknown_run"
    assert not (base / CATALOG_ID / "console" / "s-nope").exists()


def test_chat_repeat_and_concurrency(server, seeded):
    """Errata E7: 8 distinct concurrent prompts + a repeat → 9 traces, repeat_index 1."""
    base, _ = seeded
    status, session = post(server, "/api/sessions", {"catalog_id": CATALOG_ID, "model_id": "rules"})
    assert status == 200
    prompts = [f"{room} 불 켜줘" for room in ("거실", "침실", "주방", "복도", "서재")]
    prompts += ["조명 장치 목록 보여줘", "주방 조명 상태 확인해줘", "영화 모드로 바꿔줘"]
    assert len(prompts) == 8
    payloads = prompts + [prompts[0]]  # the repeat

    def fire(prompt: str):
        return post(
            server,
            "/api/chat",
            {
                "session_id": session["session_id"],
                "catalog_id": CATALOG_ID,
                "model_id": "rules",
                "prompt": prompt,
            },
        )

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(fire, payloads))
    assert all(status == 200 for status, _ in results), results

    shard = base / CATALOG_ID / session["run_id"] / "traces.jsonl"
    lines = [line for line in shard.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]  # no interleaved / truncated lines
    assert len(rows) == 9
    assert len({row["trace_id"] for row in rows}) == 9
    assert len({row["case_id"] for row in rows}) == 8

    repeated_case = [row for row in rows if row["prompt"] == prompts[0]]
    assert sorted(row["repeat_index"] for row in repeated_case) == [0, 1]
    assert len({row["trace_id"] for row in repeated_case}) == 2


def test_labels_accept_and_reject(server, seeded, session):
    base, _ = seeded
    status, chat = post(
        server,
        "/api/chat",
        {
            "session_id": session["session_id"],
            "catalog_id": CATALOG_ID,
            "model_id": "rules",
            "prompt": "침실 불 꺼줘",
        },
    )
    assert status == 200
    trace_id = chat["trace_id"]

    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "correct",
            "saw_f0": True,
            "time_to_label_ms": 1200,
        },
    )
    assert status == 200, payload
    assert payload["label_id"].startswith("lb-")
    assert payload["family_id"].startswith("fam-")
    assert payload["zone"] in {"train", "dev", "release"}
    assert payload["graded_score"] == 1.0
    assert len(payload["event_ids"]) == 1

    labels_path = base / CATALOG_ID / session["run_id"] / "labels.jsonl"
    assert labels_path.is_file()

    # 422: expected_plan must validate against the catalog, never auto-fixed.
    # (brightness has no prompt-correction hook, so this really fails.)
    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "incorrect",
            "expected_plan": {
                "calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "off", "brightness": 150}}]
            },
        },
    )
    assert status == 422
    assert payload["error"] == "invalid_expected_plan"

    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "incorrect",
            "expected_plan": {"calls": [{"action": "teleport", "args": {}}]},
        },
    )
    assert status == 422

    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "incorrect",
        },
    )
    assert status == 422

    status, payload = post(
        server,
        "/api/labels",
        {"catalog_id": CATALOG_ID, "run_id": session["run_id"], "trace_id": "tr-nope", "verdict": "correct"},
    )
    assert status == 404

    # A real correction round-trips and becomes the gold.
    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "incorrect",
            "expected_plan": {
                "calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "off", "brightness": 10}}]
            },
            "note": "brightness was dropped",
        },
    )
    assert status == 200, payload
    assert 0.0 <= payload["graded_score"] < 1.0

    status, detail = get(server, f"/api/runs/{CATALOG_ID}/{session['run_id']}/traces/{trace_id}")
    assert status == 200
    assert detail["label"]["verdict"] == "incorrect"
    assert detail["gold_origin"].startswith("human:")
    assert detail["status"] == "fail"


def test_should_abstain_is_labellable_on_a_builtin_tier(server, seeded, session):
    """[[analyzer_label_store]] defines `{"calls": []}` as the gold for an
    abstention verdict. Every builtin tier has `allow_empty_calls=False`, so
    routing that gold through `parse_json_dsl` made the verdict unusable
    everywhere — the operator got `'calls' must not be empty` and the button
    was dead. The verdict is now its own gate: the empty plan (or no
    `expected_plan` at all) is accepted, anything else under that verdict is
    still 422.
    """
    status, chat = post(
        server,
        "/api/chat",
        {
            "session_id": session["session_id"],
            "catalog_id": CATALOG_ID,
            "model_id": "rules",
            "prompt": "주방 불 켜줘",
        },
    )
    assert status == 200
    trace_id = chat["trace_id"]

    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "should_abstain",
            "expected_plan": {"calls": []},
            "saw_f0": True,
        },
    )
    assert status == 200, payload
    assert payload["label_id"].startswith("lb-")
    # The model DID call something, so the abstention gold grades it as wrong.
    assert payload["graded_score"] < 1.0

    status, detail = get(server, f"/api/runs/{CATALOG_ID}/{session['run_id']}/traces/{trace_id}")
    assert status == 200
    assert detail["label"]["verdict"] == "should_abstain"
    assert detail["label"]["expected_plan"] == {"calls": []}
    assert detail["status"] == "fail"

    # No expected_plan at all is the same assertion.
    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "should_abstain",
        },
    )
    assert status == 200, payload

    # A non-empty plan under `should_abstain` is a mistake, not a gold.
    status, payload = post(
        server,
        "/api/labels",
        {
            "catalog_id": CATALOG_ID,
            "run_id": session["run_id"],
            "trace_id": trace_id,
            "verdict": "should_abstain",
            "expected_plan": {
                "calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on"}}]
            },
        },
    )
    assert status == 422, payload
    assert payload["error"] == "invalid_expected_plan"


# ---------------------------------------------------------------------------
# seeded runs
# ---------------------------------------------------------------------------


def test_seed_produced_two_runs(seeded):
    base, result = seeded
    run_ids = [run["run_id"] for run in result["runs"]]
    assert run_ids == [RULES_SEED_RUN_ID, DEGRADED_SEED_RUN_ID]
    degraded = result["runs"][1]["analyze"]
    assert degraded["histogram"]["missing_required_arg"] > 0
    assert degraded["histogram"]["unknown_arg"] > 0
    operations = {
        json.loads(line)["operation"]
        for line in (base / CATALOG_ID / DEGRADED_SEED_RUN_ID / "proposed_patches.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    assert {"set_default", "enable_strip_unknown_args"} <= operations


def test_runs_list_and_detail(server):
    status, payload = get(server, f"/api/runs?catalog_id={CATALOG_ID}")
    assert status == 200
    by_id = {row["run_id"]: row for row in payload["runs"]}
    assert RULES_SEED_RUN_ID in by_id and DEGRADED_SEED_RUN_ID in by_id
    seed_row = by_id[RULES_SEED_RUN_ID]
    assert seed_row["benchmark"] == "iot"
    assert seed_row["n_traces"] > 0
    assert seed_row["has_classified"] is True
    assert seed_row["summary"] is not None

    status, detail = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}")
    assert status == 200
    assert detail["manifest"]["run_id"] == DEGRADED_SEED_RUN_ID
    assert detail["histogram"]["missing_required_arg"] > 0
    assert UNCLASSIFIED in detail["histogram"]
    assert detail["precision"]["n_proposed"] >= 2
    assert detail["corrections"]["n"] > 0
    assert RULES_SEED_RUN_ID in detail["compares"]
    assert detail["events_tail"]

    status, payload = get(server, f"/api/runs/{CATALOG_ID}/nope")
    assert status == 404


def test_trace_filters(server):
    base_path = f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces"
    status, payload = get(server, base_path)
    assert status == 200
    counts = payload["counts"]
    assert counts["all"] == payload["total"]
    assert counts["invalid"] > 0
    assert counts["unlabelled"] == counts["all"]
    assert counts["changed"] > 0  # the seed compare against rules-seed

    row = payload["traces"][0]
    assert set(row) >= {
        "trace_id", "case_id", "prompt", "plan", "raw_plan", "expected_plan",
        "error_type", "status", "failure_type", "failure_confidence", "label", "direction",
    }

    status, invalid = get(server, f"{base_path}?filter=invalid")
    assert status == 200
    assert invalid["total"] == counts["invalid"]
    assert all(trace["plan"] is None for trace in invalid["traces"])
    assert all(trace["status"] == "invalid" for trace in invalid["traces"])

    status, changed = get(server, f"{base_path}?filter=changed")
    assert changed["total"] == counts["changed"]
    assert all(trace["direction"] in {"fixed", "regressed"} for trace in changed["traces"])

    status, typed = get(server, f"{base_path}?failure_type=missing_required_arg&limit=3")
    assert status == 200
    assert typed["total"] > 0
    assert len(typed["traces"]) <= 3
    assert all(trace["failure_type"] == "missing_required_arg" for trace in typed["traces"])

    status, paged = get(server, f"{base_path}?limit=2&offset=1")
    assert len(paged["traces"]) == 2
    assert paged["traces"][0]["trace_id"] == payload["traces"][1]["trace_id"]

    status, payload = get(server, f"{base_path}?filter=bogus")
    assert status == 400

    # No `no_failure` with zero confidence leaks through as a pass (errata E8).
    status, all_rows = get(server, f"{base_path}?limit=1000")
    for trace in all_rows["traces"]:
        if trace["failure_type"] == "no_failure":
            assert trace["failure_confidence"] != 0.0


def test_trace_detail(server):
    status, listing = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces?filter=invalid&limit=1")
    trace_id = listing["traces"][0]["trace_id"]
    status, detail = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces/{trace_id}")
    assert status == 200
    assert detail["trace"]["trace_id"] == trace_id
    assert detail["trace"]["plan"] is None
    assert detail["trace"]["raw_plan"] is not None
    assert detail["classification"]["failure_type"] in {"missing_required_arg", "unknown_arg"}
    assert detail["label"] is None
    assert detail["attribution"]["trace_id"] == trace_id
    assert detail["gold_origin"] == "dataset"

    status, payload = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces/tr-ghost")
    assert status == 404


def test_analyze_patches_and_decision(server, seeded):
    base, _ = seeded
    status, payload = post(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/analyze")
    assert status == 200, payload
    assert payload["n_traces"] > 0
    assert payload["n_patches"] >= 2
    assert payload["histogram"]["unknown_arg"] > 0

    status, payload = post(server, f"/api/runs/{CATALOG_ID}/nope/analyze")
    assert status == 404
    status, payload = post(server, "/api/runs/bfcl/simple_python/case-1/analyze")
    assert status in (404, 409)

    status, patches = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/patches")
    assert status == 200
    assert patches["precision"]["n_proposed"] == len(patches["patches"])
    strip = next(p for p in patches["patches"] if p["operation"] == "enable_strip_unknown_args")
    assert strip["preview"] is None  # no blind decision yet
    assert strip["decisions"] == {}

    patch_id = strip["patch_id"]
    status, decision = post(
        server,
        f"/api/patches/{patch_id}/decision",
        {
            "catalog_id": CATALOG_ID,
            "run_id": DEGRADED_SEED_RUN_ID,
            "stage": "blind",
            "decision": "accept",
            "reason": "id is not a real arg",
            "time_to_decide_ms": 4200,
        },
    )
    assert status == 200, decision
    assert decision["decision_id"].startswith("pd-")
    assert len(decision["event_ids"]) == 1

    status, patches = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/patches")
    strip = next(p for p in patches["patches"] if p["patch_id"] == patch_id)
    assert strip["decisions"]["blind"]["decision"] == "accept"
    assert strip["preview"]["applicable"] is True
    assert strip["preview"]["rescues"] >= 0
    assert patches["precision"]["accepted_blind"] == 1

    status, payload = post(
        server,
        f"/api/patches/{patch_id}/ported",
        {"catalog_id": CATALOG_ID, "run_id": DEGRADED_SEED_RUN_ID, "commit_sha": "deadbeef"},
    )
    assert status == 200, payload

    status, payload = post(
        server,
        f"/api/patches/{patch_id}/decision",
        {"catalog_id": CATALOG_ID, "run_id": DEGRADED_SEED_RUN_ID, "stage": "blind", "decision": "yolo"},
    )
    assert status == 400
    status, payload = post(
        server,
        "/api/patches/rs-ghost/decision",
        {"catalog_id": CATALOG_ID, "run_id": DEGRADED_SEED_RUN_ID, "stage": "blind", "decision": "accept"},
    )
    assert status == 404


def test_compare_and_export(server, seeded):
    base, _ = seeded
    status, payload = get(
        server,
        f"/api/compare?catalog_id={CATALOG_ID}&run_a={RULES_SEED_RUN_ID}&run_b={DEGRADED_SEED_RUN_ID}",
    )
    assert status == 200, payload
    assert payload["n"] > 0
    assert payload["counts"]["regressed"] > 0
    assert payload["em_delta"] < 0

    # A run against itself: same manifest → accepted, zero delta.
    status, same = get(
        server,
        f"/api/compare?catalog_id={CATALOG_ID}&run_a={RULES_SEED_RUN_ID}&run_b={RULES_SEED_RUN_ID}",
    )
    assert status == 200
    assert same["em_delta"] == 0.0
    assert same["counts"]["fixed"] == 0 and same["counts"]["regressed"] == 0

    status, payload = get(server, f"/api/compare?catalog_id={CATALOG_ID}&run_a={RULES_SEED_RUN_ID}")
    assert status == 400
    # An unmanifested run is an unknown run_id (404 per console_operator.md's
    # failure clause), not a refusal by compare_runs (409 is for a manifest
    # mismatch without allow_diff, exercised below).
    status, payload = get(
        server, f"/api/compare?catalog_id={CATALOG_ID}&run_a=ghost&run_b={RULES_SEED_RUN_ID}"
    )
    assert status == 404, payload
    assert payload["error"] == "unknown_run"

    status, payload = get(server, f"/api/export/labels?catalog_id={CATALOG_ID}&zones=train,dev")
    assert status == 200, payload
    assert payload["n_sft"] >= 0
    for name in ("human_sft", "hard_pool", "zones"):
        assert Path(payload["paths"][name]).is_file()
    assert Path(payload["paths"]["human_sft"]).parent == base.parent / "labels" / CATALOG_ID

    status, payload = get(server, "/api/export/labels")
    assert status == 400


def test_events_route(server):
    status, payload = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/events")
    assert status == 200
    names = {event["name"] for event in payload["events"]}
    assert {"analyzer.run.recorded", "analyzer.failure.classified", "analyzer.rule.proposed"} <= names
    assert all(event["event_id"].startswith("ev-") for event in payload["events"])


# ---------------------------------------------------------------------------
# invariants
# ---------------------------------------------------------------------------


def test_projection_routes_create_no_files(server, seeded, session):
    """Every *P* route is a pure projection; only the two W† GETs may write."""
    base, _ = seeded
    listing = get(server, f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces?limit=1")[1]
    trace_id = listing["traces"][0]["trace_id"]
    before = snapshot(base)
    paths = [
        "/api/health",
        "/api/catalogs",
        f"/api/catalogs/{CATALOG_ID}",
        f"/api/models?catalog_id={CATALOG_ID}",
        "/api/models/rules/health",
        "/api/runs",
        f"/api/runs?catalog_id={CATALOG_ID}",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces?filter=wrong",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces?filter=unlabelled",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/traces/{trace_id}",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/patches",
        f"/api/runs/{CATALOG_ID}/{DEGRADED_SEED_RUN_ID}/events",
        f"/api/runs/{CATALOG_ID}/{session['run_id']}",
        f"/api/runs/{CATALOG_ID}/{session['run_id']}/traces",
    ]
    for path in paths:
        status, _payload = get(server, path)
        assert status == 200, path
    assert snapshot(base) == before


def test_static_files_and_traversal(server, web_dir):
    status, body, content_type = raw_get(server, "/")
    assert status == 200
    assert b"console" in body
    assert content_type.startswith("text/html")

    status, body, content_type = raw_get(server, "/chat.html")
    assert status == 200 and content_type.startswith("text/html")

    status, body, content_type = raw_get(server, "/assets/api.js")
    assert status == 200
    assert b"GanglionAPI" in body
    assert content_type.startswith("text/javascript")

    for path in ("/../conftest.py", "/assets/../../secret.key", "/%2e%2e/setup.py", "/assets"):
        status, _body, _ct = raw_get(server, path)
        assert status == 404, path

    status, _body, _ct = raw_get(server, "/nope.html")
    assert status == 404


def test_bad_requests(server):
    status, payload = get(server, "/api/nope")
    assert status == 404
    status, payload = post(server, "/api/sessions", "not-a-mapping")
    assert status == 400
    url = f"http://127.0.0.1:{server.port}/api/sessions"
    req = urllib.request.Request(url, data=b"{oops", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 400


# ---------------------------------------------------------------------------
# local HF models (§3b) — no GPU, no torch call: the lifecycle is stubbed
# ---------------------------------------------------------------------------


LOCAL_SPEC = ModelSpec(
    model_id="qwen3-0.6b@local",
    kind="local_hf",
    client="json-dsl",
    provider="local",
    base_model="Qwen/Qwen3-0.6B",
    catalog_ids=("*",),
)


@pytest.fixture
def local_server(tmp_path, web_dir, monkeypatch):
    local_hf = pytest.importorskip("ganglion.lm.local_hf")
    loaded: dict[str, bool] = {"on": False}

    def fake_load(spec):
        loaded["on"] = True
        return object(), object()

    def fake_unload(spec):
        was = loaded["on"]
        loaded["on"] = False
        return was

    def fake_status(spec):
        return {
            "status": "loaded" if loaded["on"] else "not_loaded",
            "detail": "stub",
            "device": "cpu",
            "vram_mb": None,
        }

    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (True, "stub"))
    monkeypatch.setattr(local_hf, "load_local_model", fake_load)
    monkeypatch.setattr(local_hf, "unload_local_model", fake_unload)
    monkeypatch.setattr(local_hf, "local_model_status", fake_status)
    monkeypatch.setattr(
        local_hf,
        "generate_dsl",
        lambda *a, **k: '{"calls":[{"action":"set_light","args":{"room":"living","state":"on"}}]}',
    )
    base = tmp_path / "traces"
    srv, thread = make_server(base, web_dir, Registry([RULES_SPEC, LOCAL_SPEC]))
    yield srv, loaded
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def test_local_model_lifecycle(local_server):
    server, loaded = local_server
    model_id = LOCAL_SPEC.model_id

    status, payload = get(server, f"/api/models/{model_id}/health")
    assert status == 200
    assert payload["status"] == "not_loaded"

    status, payload = post(server, f"/api/models/{model_id}/load")
    assert status == 200, payload
    assert payload["status"] == "loaded"
    assert payload["seconds"] >= 0.0
    assert loaded["on"] is True
    assert get(server, f"/api/models/{model_id}/health")[1]["status"] == "loaded"

    status, payload = post(server, f"/api/models/{model_id}/unload")
    assert status == 200
    assert payload["status"] == "not_loaded"
    status, payload = post(server, f"/api/models/{model_id}/unload")
    assert status == 200  # idempotent
    assert payload["unloaded"] is False


def test_local_model_chat_reports_load_seconds(local_server):
    server, loaded = local_server
    model_id = LOCAL_SPEC.model_id
    status, session = post(server, "/api/sessions", {"catalog_id": CATALOG_ID, "model_id": model_id})
    assert status == 200, session
    body = {
        "session_id": session["session_id"],
        "catalog_id": CATALOG_ID,
        "model_id": model_id,
        "prompt": KOREAN_PROMPT,
    }
    status, first = post(server, "/api/chat", body)
    assert status == 200, first
    assert "model_load_seconds" in first
    assert first["plan"]["calls"][0]["action"] == "set_light"

    status, second = post(server, "/api/chat", body)
    assert status == 200
    assert "model_load_seconds" not in second
    assert second["repeat_index"] == 1


def test_local_model_unavailable_is_503(local_server, monkeypatch):
    server, _loaded = local_server
    import ganglion.lm.local_hf as local_hf

    monkeypatch.setattr(local_hf, "local_hf_available", lambda: (False, "torch not importable"))
    model_id = LOCAL_SPEC.model_id

    status, payload = post(server, f"/api/models/{model_id}/load")
    assert status == 503
    assert "torch" in payload["detail"]

    status, session = post(server, "/api/sessions", {"catalog_id": CATALOG_ID, "model_id": model_id})
    assert status == 200
    status, payload = post(
        server,
        "/api/chat",
        {
            "session_id": session["session_id"],
            "catalog_id": CATALOG_ID,
            "model_id": model_id,
            "prompt": KOREAN_PROMPT,
        },
    )
    assert status == 503
