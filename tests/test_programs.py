"""The workbench and CLI share v2 contracts and private streaming jobs."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import threading
import time

import pytest

from ganglion.console.api import ConsoleAPI
from ganglion.lm.registry import Registry, RULES_SPEC
from ganglion.programs.service import CHUNK_BYTES
from ganglion.programs.specs import builtins, fingerprint

CRYPTO = pytest.mark.skipif(importlib.util.find_spec("nacl") is None, reason="install ganglion[pii]")


@pytest.fixture
def api(tmp_path):
    app = ConsoleAPI(base_dir=tmp_path / "runs" / "traces", registry=Registry([RULES_SPEC]))
    yield app
    app.close()


def call(api, method, path, body=None, **query):
    status, result = api.handle(method, "/api/v2/" + path, {k: [str(v)] for k, v in query.items()}, body)
    assert 200 <= status < 300, (status, result)
    return result


def upload(api, data):
    row = call(api, "POST", "uploads", {})
    for start in range(0, len(data), CHUNK_BYTES):
        row = call(api, "POST", "uploads", {"upload_id": row["upload_id"], "offset": start,
                                             "data": base64.b64encode(data[start:start + CHUNK_BYTES]).decode()})
    return row["upload_id"]


def wait_job(api, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = call(api, "GET", "jobs/" + job_id)
        if job["status"] not in {"queued", "running", "cancelling"}:
            return job
        time.sleep(0.01)
    pytest.fail("job did not finish")


def download(api, job_id, name):
    chunks, offset = [], 0
    while True:
        row = call(api, "GET", f"jobs/{job_id}/artifacts/{name}", offset=offset)
        chunks.append(base64.b64decode(row["data"]))
        if row["done"]:
            return b"".join(chunks)
        offset += row["bytes"]


def legacy_pii_spec():
    """Keep legacy input validation covered independently of current builtins."""
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec["preprocessor"] = "utf8-windows"
    spec["input_schema"]["properties"].update({
        "window_chars": {"type": "integer", "default": 1024, "minimum": 64, "maximum": 8192},
        "overlap_chars": {"type": "integer", "default": 128, "minimum": 0, "maximum": 2048},
    })
    return spec


def test_spec_projection_is_read_only_and_describes_both_domains(api):
    assert not api.base_dir.parent.exists()
    specs = call(api, "GET", "specs")["specs"]
    assert {s["domain"] for s in specs} == {"pii-text", "tool-calling"}
    assert {s["contract"]["output"] for s in specs} == {"TextEditPlan", "ActionPlan"}
    assert all(len(s["fingerprint"]) == 64 for s in specs)
    call(api, "GET", "jobs")
    assert not api.base_dir.parent.exists()


def test_register_fingerprints_and_validates_data_only_spec(api):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec.update(id="custom-pii", title="Custom contract")
    registered = call(api, "POST", "specs", spec)
    assert registered["fingerprint"] == fingerprint(spec)
    assert call(api, "GET", "specs/custom-pii")["title"] == spec["title"]
    malicious = dict(spec, python_module="os")
    assert api.handle("POST", "/api/v2/specs", {}, malicious)[0] == 400


@pytest.mark.parametrize("field,value", [("model", []), ("input_schema", []), ("contract", "TextEditPlan"),
                                         ("input_schema", {"type": "object", "properties": {}, "required": 17})])
def test_malformed_nested_specs_are_client_errors(api, field, value):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec[field] = value
    status, result = api.handle("POST", "/api/v2/specs", {}, spec)
    assert status == 400, result


def test_invalid_upload_chunk_does_not_reserve_private_storage(api):
    status, _ = api.handle("POST", "/api/v2/uploads", {}, {"data": "%%%"})
    assert status == 400
    assert api._programs.uploads == {}
    assert not list((api.base_dir.parent / "programs" / "private").glob("*"))


def test_upload_offset_and_size_errors_preserve_original_bytes(api):
    row = call(api, "POST", "uploads", {"data": base64.b64encode(b"original").decode()})
    for body in ({"upload_id": row["upload_id"], "offset": 0, "data": base64.b64encode(b"bad").decode()},
                 {"upload_id": row["upload_id"], "offset": 8, "data": base64.b64encode(b"x" * (CHUNK_BYTES + 1)).decode()}):
        assert api.handle("POST", "/api/v2/uploads", {}, body)[0] == 400
    assert api._programs.uploads[row["upload_id"]]["path"].read_bytes() == b"original"
    call(api, "POST", f"uploads/{row['upload_id']}/discard", {})
    assert api._programs.uploads == {}


def test_tool_contract_uses_same_jobs_and_artifacts(api):
    started = call(api, "POST", "jobs", {"spec_id": "tool-planner", "inputs": {"prompt": "거실 불 켜줘"}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    assert job["result"]["domain"] == "tool-calling"
    assert job["result"]["plan"]["calls"]
    artifact = json.loads(download(api, job["job_id"], "plan.json"))
    assert artifact["plan"] == job["result"]["plan"]


@CRYPTO
def test_reversible_job_streams_and_keeps_source_and_keys_out_of_trace(api):
    original = ("이름: 김민수\r\n메일: secret.person@example.com\r\n전화: 010-1234-5678\r\n🙂 끝\n" * 4000).encode()
    keys = call(api, "POST", "keys", {})
    document = upload(api, original)
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": document, "public_key": keys["public_key"]}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    pseudonymous = download(api, job["job_id"], "document.txt")
    recovery = download(api, job["job_id"], "recovery.bin")
    trace = download(api, job["job_id"], "trace.jsonl")
    assert b"secret.person@example.com" not in pseudonymous + recovery + trace
    assert "김민수".encode() not in pseudonymous + recovery + trace
    assert job["result"]["processed_bytes"] == len(original)
    assert job["result"]["output_tokens"] == 0
    assert api._programs.uploads == {}
    assert not list((api._programs.root / "private").glob("*"))
    for path in (api._programs.root / "jobs" / job["job_id"]).rglob("*"):
        if path.is_file():
            assert keys["private_key"].encode() not in path.read_bytes()
    restore = call(api, "POST", "restore", {"document": upload(api, pseudonymous), "recovery": upload(api, recovery), "private_key": keys["private_key"]})
    assert download(api, restore["job_id"], "restored.txt") == original


def test_plan_only_job_needs_no_key_and_contains_no_source_text(api):
    source = "성명: 박지민\nemail: private@example.com\n".encode()
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": upload(api, source), "execute": False}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    assert job["result"]["mode"] == "plan"
    plan = download(api, job["job_id"], "plan.jsonl")
    assert b"private@example.com" not in plan
    assert all(row["source_span"][0] < row["source_span"][1] for row in map(json.loads, plan.splitlines()))


@CRYPTO
def test_wrong_restore_key_removes_uploads_and_publishes_no_partial_artifact(api):
    keys = call(api, "POST", "keys", {})
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": upload(api, b"email: person@example.com"), "public_key": keys["public_key"]}})
    job = wait_job(api, started["job_id"])
    doc, sidecar = (download(api, job["job_id"], name) for name in ("document.txt", "recovery.bin"))
    before = set((api._programs.root / "jobs").iterdir())
    wrong_keys = call(api, "POST", "keys", {})
    status, _ = api.handle("POST", "/api/v2/restore", {}, {"document": upload(api, doc), "recovery": upload(api, sidecar), "private_key": wrong_keys["private_key"]})
    assert status == 400
    assert set((api._programs.root / "jobs").iterdir()) == before
    assert not list((api._programs.root / "private").glob("*"))


def test_cancellation_removes_source_and_unpublished_output(api, monkeypatch):
    import ganglion.domains.pii.pipeline as pipeline
    entered, proceed = threading.Event(), threading.Event()

    def blocked(source, output, detector, **kwargs):
        (output / "partial.txt").write_bytes(b"partial")
        entered.set()
        assert proceed.wait(5)
        if kwargs["cancelled"]():
            raise pipeline.Cancelled()
        return {"artifacts": ["partial.txt"]}

    monkeypatch.setattr(pipeline, "run_document", blocked)
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": upload(api, b"private source"), "execute": False}})
    try:
        assert entered.wait(5)
        assert call(api, "POST", f"jobs/{started['job_id']}/cancel", {})["status"] == "cancelling"
    finally:
        proceed.set()
    job = wait_job(api, started["job_id"])
    assert job["status"] == "cancelled"
    assert not (api._programs.root / "jobs" / job["job_id"] / "artifacts").exists()
    assert not list((api._programs.root / "private").glob("*"))


def test_invalid_expected_spans_reject_text_in_feedback(api):
    started = call(api, "POST", "jobs", {"spec_id": "tool-planner", "inputs": {"prompt": "거실 불 켜줘"}})
    job = wait_job(api, started["job_id"])
    status, _ = api.handle("POST", f"/api/v2/jobs/{job['job_id']}/feedback", {}, {"verdict": "missed_pii", "expected_spans": [{"start": 0, "end": 3, "type": "PERSON", "text": "secret"}]})
    assert status == 400
    assert not (api._programs.root / "jobs" / job["job_id"] / "feedback.jsonl").exists()


def test_artifact_routes_reject_traversal_and_invalid_offset(api):
    started = call(api, "POST", "jobs", {"spec_id": "tool-planner", "inputs": {"prompt": "거실 불 켜줘"}})
    job = wait_job(api, started["job_id"])
    for name, offset in (("spec.json", 0), ("plan.json", -1), ("plan.json", 10**9)):
        assert api.handle("GET", f"/api/v2/jobs/{job['job_id']}/artifacts/{name}", {"offset": [str(offset)]})[0] == 400


@CRYPTO
def test_cli_runs_and_restores_across_independent_api_lifetimes(tmp_path, monkeypatch, capsys):
    from ganglion.ctl.main import main

    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.delenv("GANGLION_CONSOLE_URL", raising=False)
    runs = tmp_path / "runs" / "traces"
    public, private = tmp_path / "public.json", tmp_path / "private.json"
    source, restored = tmp_path / "source.txt", tmp_path / "restored.txt"
    source.write_bytes("이름: 이서연\r\nemail: test.secret@example.com\r\n전화: 010-2345-6789\n".encode())
    flags = ["--runs", str(runs), "--json"]
    assert main(["program", "keygen", "--private", str(private), "--public", str(public), *flags]) == 0
    key_result = json.loads(capsys.readouterr().out)
    assert key_result["private_key_file"] == str(private)
    assert private.stat().st_mode & 0o777 == 0o600
    assert "private_key" not in json.loads(public.read_text())
    output_dir = tmp_path / "output"
    assert main(["program", "run", "pii-rules", "--file", str(source), "--public-key", str(public), "--output-dir", str(output_dir), *flags]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["status"] == "complete"
    assert main(["program", "job", job["job_id"], *flags]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    assert main(["program", "restore", str(output_dir / "document.txt"), str(output_dir / "recovery.bin"), "--key", str(private), "--output", str(restored), *flags]) == 0
    capsys.readouterr()
    assert restored.read_bytes() == source.read_bytes()
    assert main(["program", "restore", str(output_dir / "document.txt"), str(output_dir / "recovery.bin"), "--key", str(private), "--output", str(restored), *flags]) != 0
    assert source.read_bytes() == restored.read_bytes()


def test_cli_generic_tool_inputs_and_plan_artifacts(tmp_path, monkeypatch, capsys):
    from ganglion.ctl.main import main

    monkeypatch.delenv("GANGLION_CONSOLE_URL", raising=False)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"prompt": "거실 불 켜줘", "catalog_id": "iot_light_5", "model_id": "rules"}))
    output = tmp_path / "tool-output"
    assert main(["program", "run", "tool-planner", "--inputs", str(inputs), "--output-dir", str(output), "--runs", str(tmp_path / "runs" / "traces"), "--json"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert json.loads((output / "plan.json").read_text())["plan"] == job["result"]["plan"]


@pytest.mark.parametrize("domain,name,kind", [("pii-rules", "document", "boolean"), ("pii-rules", "execute", "string"),
                                             ("pii-rules", "public_key", "integer"), ("pii-rules", "window_chars", "string"),
                                             ("pii-rules", "overlap_chars", "boolean"), ("tool-planner", "prompt", "integer"),
                                             ("tool-planner", "catalog_id", "boolean"), ("tool-planner", "model_id", "integer")])
def test_registered_domain_fields_keep_their_semantic_types(api, domain, name, kind):
    spec = copy.deepcopy(builtins()[domain])
    spec["input_schema"]["properties"][name] = {"type": kind}
    assert api.handle("POST", "/api/v2/specs", {}, spec)[0] == 400


@pytest.mark.parametrize("field,changes", [("window_chars", {"minimum": "64"}), ("window_chars", {"maximum": True}),
                                          ("window_chars", {"minimum": 8193, "maximum": 8192}),
                                          ("window_chars", {"default": "1024"}), ("execute", {"default": 1}),
                                          ("public_key", {"maxLength": 0}), ("public_key", {"enum": []}),
                                          ("public_key", {"enum": [17]}), ("public_key", {"enum": ["x"], "default": "y"}),
                                          ("window_chars", {"default": 9000}), ("public_key", {"default": "x" * 101})])
def test_registered_schema_rejects_invalid_bounds_defaults_and_enums(api, field, changes):
    spec = legacy_pii_spec()
    spec["input_schema"]["properties"][field].update(changes)
    assert api.handle("POST", "/api/v2/specs", {}, spec)[0] == 400


def test_registered_pii_contract_cannot_change_coordinates_or_native_backbone(api):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec["contract"]["coordinate"] = "codepoint"
    assert api.handle("POST", "/api/v2/specs", {}, spec)[0] == 400
    native = copy.deepcopy(builtins()["pii-qwen"])
    native["model"]["base_model"] = "arbitrary/model"
    assert api.handle("POST", "/api/v2/specs", {}, native)[0] == 400


def test_optional_executor_absence_allows_core_plan_and_refuses_execution(api):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec.update(id="pii-core-plan", executor=None)
    call(api, "POST", "specs", spec)
    document = upload(api, b"email: private@example.com")
    rejected = api.handle("POST", "/api/v2/jobs", {}, {"spec_id": spec["id"], "inputs": {"document": document, "execute": True}})
    assert rejected[0] == 400
    assert document in api._programs.uploads
    started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {"document": document, "execute": False}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete"
    assert job["result"]["mode"] == "plan"
    assert set(job["result"]["artifacts"]) == {"plan.jsonl", "trace.jsonl"}


def test_optional_preprocessor_absence_accepts_bounded_input_and_refuses_long_document(api):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec.update(id="pii-bounded-core", preprocessor=None, executor=None)
    call(api, "POST", "specs", spec)
    for text, expected in (("email: core@example.com", "complete"), ("가" * 1025, "failed")):
        document = upload(api, text.encode())
        started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {"document": document, "execute": False}})
        job = wait_job(api, started["job_id"])
        assert job["status"] == expected, job
        if expected == "complete":
            assert job["result"]["units"] == 1
        else:
            assert not (api._programs.root / "jobs" / job["job_id"] / "artifacts").exists()


def test_read_only_startup_retains_orphans_until_first_mutation_cleans_them(api):
    root = api.base_dir.parent / "programs"
    private = root / "private"
    private.mkdir(parents=True)
    orphan = private / ("a" * 32)
    orphan.write_bytes(b"private orphan from interrupted process")
    call(api, "GET", "specs")
    call(api, "GET", "jobs")
    assert orphan.read_bytes() == b"private orphan from interrupted process"
    row = call(api, "POST", "uploads", {})
    assert not orphan.exists()
    assert api._programs.uploads[row["upload_id"]]["path"].exists()


def test_runtime_ownership_prevents_second_service_from_consuming_live_uploads(api):
    document = upload(api, b"private pending document")
    live_path = api._programs.uploads[document]["path"]
    successor = ConsoleAPI(base_dir=api.base_dir, registry=Registry([RULES_SPEC]))
    try:
        call(successor, "GET", "specs")
        assert live_path.read_bytes() == b"private pending document"
        status, _ = successor.handle("POST", "/api/v2/uploads", {}, {})
        assert status == 400
        assert live_path.read_bytes() == b"private pending document"
        api.close()
        row = call(successor, "POST", "uploads", {})
        assert row["upload_id"] in successor._programs.uploads
        assert not live_path.exists()
    finally:
        successor.close()


def test_worker_directory_failure_terminates_job_and_removes_consumed_source(api, monkeypatch):
    document = upload(api, b"private consumed document")
    source = api._programs.uploads[document]["path"]
    original = api._programs._directory

    def fail_artifacts(path):
        if path.name == "artifacts":
            raise PermissionError("injected output directory failure")
        return original(path)

    monkeypatch.setattr(api._programs, "_directory", fail_artifacts)
    started = call(api, "POST", "jobs", {"spec_id": "pii-rules", "inputs": {"document": document, "execute": False}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "PermissionError"
    deadline = time.monotonic() + 1
    while source.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not source.exists()
    assert document not in api._programs.uploads


def test_cli_download_preserves_preexisting_dangling_symlink(tmp_path):
    from ganglion.ctl.commands.programs import _download
    from ganglion.ctl.errors import CtlError

    destination = tmp_path / "existing-symlink"
    destination.symlink_to(tmp_path / "missing-target")

    class FakeAPI:
        def get(self, *args, **kwargs):
            return {"data": base64.b64encode(b"artifact").decode(), "done": True, "total_bytes": 8}

    with pytest.raises((FileExistsError, CtlError)):
        _download(FakeAPI(), "a" * 32, "plan.json", destination)
    assert destination.is_symlink(), "a failed exclusive open must not delete an existing user's path"


def test_first_mutation_reclaims_interrupted_artifacts_but_preserves_completed_jobs(api):
    root = api.base_dir.parent / "programs"
    for index, status in enumerate(("running", "complete")):
        job_id = f"{index:032x}"
        directory = root / "jobs" / job_id
        (directory / "artifacts").mkdir(parents=True)
        (directory / "artifacts" / "document.txt").write_bytes(b"artifact")
        (directory / "job.json").write_text(json.dumps({"job_id": job_id, "status": status}))
    call(api, "GET", "jobs")
    assert (root / "jobs" / ("0" * 32) / "artifacts").is_dir()
    call(api, "POST", "uploads", {})
    assert not (root / "jobs" / ("0" * 32) / "artifacts").exists()
    assert json.loads((root / "jobs" / ("0" * 32) / "job.json").read_text())["status"] == "interrupted"
    assert (root / "jobs" / f"{1:032x}" / "artifacts" / "document.txt").read_bytes() == b"artifact"


@CRYPTO
def test_restore_directory_failure_cleans_both_consumed_uploads(api, monkeypatch):
    keys = call(api, "POST", "keys", {})
    ids = [upload(api, b"input document"), upload(api, b"recovery sidecar")]
    paths = [api._programs.uploads[upload_id]["path"] for upload_id in ids]
    original = api._programs._directory

    def fail_artifacts(path):
        if path.name == "artifacts":
            raise PermissionError("injected restore output directory failure")
        return original(path)

    monkeypatch.setattr(api._programs, "_directory", fail_artifacts)
    status, _ = api.handle("POST", "/api/v2/restore", {}, {"document": ids[0], "recovery": ids[1], "private_key": keys["private_key"]})
    assert status == 400
    assert all(not path.exists() for path in paths)
    assert api._programs.uploads == {}


@pytest.mark.parametrize("base_model,status", [("Qwen/Qwen3.5-0.8B", "complete"), ("different/backbone", "failed")])
def test_native_job_pins_checkpoint_bytes_and_rejects_backbone_mismatch(api, tmp_path, monkeypatch, base_model, status):
    import hashlib
    import ganglion.domains.pii.native as native
    from ganglion.domains.pii.rules import RulesDetector

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    metadata = json.dumps({"base_model": base_model}).encode()
    head_bytes = b"head-checkpoint-version-one"
    (checkpoint / "model.json").write_bytes(metadata)
    (checkpoint / "heads.safetensors").write_bytes(head_bytes)
    monkeypatch.setenv("GANGLION_PII_CHECKPOINT", str(checkpoint))

    class FakeNative(RulesDetector):
        backend = "qwen_native"

        def __init__(self, path):
            self.metadata = json.loads((path / "model.json").read_text())

    monkeypatch.setattr(native, "NativeDetector", FakeNative)
    started = call(api, "POST", "jobs", {"spec_id": "pii-qwen", "inputs": {"document": upload(api, b"email: private@example.com"), "execute": False}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == status, job
    if status == "complete":
        expected = "native-" + hashlib.sha256(metadata + head_bytes).hexdigest()
        assert job["result"]["model_fingerprint"] == expected
        assert job["model_fingerprint"] == expected
        saved = json.loads((api._programs.root / "jobs" / job["job_id"] / "job.json").read_text())
        assert saved["result"]["model_fingerprint"] == expected


@pytest.mark.parametrize("changed", ["model.json", "heads.safetensors", "adapter/adapter_model.safetensors"])
def test_fresh_native_runtime_fingerprint_changes_with_metadata_heads_or_adapter(tmp_path, monkeypatch, changed):
    from ganglion.programs.service import ProgramService

    checkpoint = tmp_path / "checkpoint"
    (checkpoint / "adapter").mkdir(parents=True)
    (checkpoint / "model.json").write_text(json.dumps({"base_model": "Qwen/Qwen3.5-0.8B", "base_model_revision": "revision-one"}))
    (checkpoint / "heads.safetensors").write_bytes(b"heads-one")
    (checkpoint / "adapter" / "adapter_model.safetensors").write_bytes(b"adapter-one")
    monkeypatch.setenv("GANGLION_PII_CHECKPOINT", str(checkpoint))
    first = ProgramService(tmp_path / "runtime-one", None)
    second = ProgramService(tmp_path / "runtime-two", None)
    try:
        before = first._model_fingerprint()
        (checkpoint / changed).write_bytes(b"different checkpoint bytes")
        assert first._model_fingerprint() == before, "the active loaded runtime remains pinned"
        assert second._model_fingerprint() != before, "a fresh runtime must identify the changed checkpoint"
        assert not first.root.exists() and not second.root.exists(), "fingerprinting does not mutate runtime storage"
    finally:
        first.close()
        second.close()


def test_remote_http_bridge_uses_running_console_for_legacy_and_v2_routes(api, tmp_path, monkeypatch):
    from ganglion.console.server import create_server
    from ganglion.ctl.bridge import ApiBridge
    from ganglion.ctl.errors import CtlError

    server = create_server(api=api, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GANGLION_CONSOLE_URL", server.url)
    local_runs = tmp_path / "should-not-be-created" / "traces"
    bridge = ApiBridge(local_runs)
    try:
        assert bridge.get("/api/health")["ok"] is True
        assert bridge.get("/api/v2/specs/pii-rules")["domain"] == "pii-text"
        source = "성명: 김민수\nemail: remote.private@example.com".encode()
        document = bridge.post("/api/v2/uploads", {"data": base64.b64encode(source).decode()})["upload_id"]
        started = bridge.post("/api/v2/jobs", {"spec_id": "pii-rules", "inputs": {"document": document, "execute": False}})
        job = wait_job(api, started["job_id"])
        assert job["status"] == "complete"
        assert bridge.get(f"/api/v2/jobs/{job['job_id']}")["spec_fingerprint"] == job["spec_fingerprint"]
        artifact = bridge.get(f"/api/v2/jobs/{job['job_id']}/artifacts/plan.jsonl", offset=0)
        assert b"remote.private@example.com" not in base64.b64decode(artifact["data"])
        assert bridge.get(f"/api/v2/jobs/{job['job_id']}/artifacts/plan.jsonl", offset=artifact["total_bytes"])["bytes"] == 0
        with pytest.raises(CtlError) as error:
            bridge.get(f"/api/v2/jobs/{job['job_id']}/artifacts/plan.jsonl", offset=-1)
        assert error.value.status == 400
        tool = bridge.post("/api/v2/jobs", {"spec_id": "tool-planner", "inputs": {"prompt": "거실 불 켜줘"}})
        assert wait_job(api, tool["job_id"])["result"]["plan"]["calls"]
        assert not bridge.started
        assert not local_runs.parent.exists()
        bridge.close()
        assert bridge.get("/api/health")["ok"] is True, "closing an HTTP bridge must not stop the shared console"
    finally:
        bridge.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_remote_http_bridge_cancels_the_same_server_owned_job(api, monkeypatch):
    import ganglion.domains.pii.pipeline as pipeline
    from ganglion.console.server import create_server
    from ganglion.ctl.bridge import ApiBridge

    entered, proceed = threading.Event(), threading.Event()

    def blocked(source, output, detector, **kwargs):
        entered.set()
        assert proceed.wait(5)
        if kwargs["cancelled"]():
            raise pipeline.Cancelled()
        return {"artifacts": []}

    monkeypatch.setattr(pipeline, "run_document", blocked)
    server = create_server(api=api, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GANGLION_CONSOLE_URL", server.url)
    bridge = ApiBridge()
    try:
        document = bridge.post("/api/v2/uploads", {"data": base64.b64encode(b"private source").decode()})["upload_id"]
        started = bridge.post("/api/v2/jobs", {"spec_id": "pii-rules", "inputs": {"document": document, "execute": False}})
        assert entered.wait(5)
        cancelled = bridge.post(f"/api/v2/jobs/{started['job_id']}/cancel")
        assert cancelled["status"] == "cancelling"
        proceed.set()
        assert wait_job(api, started["job_id"])["status"] == "cancelled"
        assert not list((api._programs.root / "private").glob("*"))
    finally:
        proceed.set()
        bridge.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("endpoint", ["http://example.com", "http://127.0.0.1.example.com", "https://127.0.0.1", "http://127.0.0.1/api", "http://user@127.0.0.1"])
def test_remote_http_bridge_rejects_non_loopback_or_ambiguous_urls_without_network(monkeypatch, tmp_path, endpoint):
    from ganglion.ctl.bridge import ApiBridge
    from ganglion.ctl.errors import CtlError
    import urllib.request

    def forbidden_network(*args, **kwargs):
        pytest.fail("an invalid console endpoint must be rejected before any network call")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    monkeypatch.setenv("GANGLION_CONSOLE_URL", endpoint)
    bridge = ApiBridge(tmp_path / "local" / "traces")
    try:
        with pytest.raises(CtlError) as error:
            bridge.get("/api/health")
        assert error.value.status == 400
        assert error.value.error == "invalid_endpoint"
        assert not bridge.started
        assert not (tmp_path / "local").exists()
    finally:
        bridge.close()


def preprocessing_spec(*, strategy="semantic", max_chars=256, overlap_chars=64, max_tokens=128):
    spec = copy.deepcopy(builtins()["pii-rules"])
    spec.update(id="configured-pii", preprocessor={"adapter": "utf8-windows", "version": 1, "config": {
        "strategy": strategy, "max_chars": max_chars, "overlap_chars": overlap_chars, "max_tokens": max_tokens}})
    return spec


@pytest.mark.parametrize("field,value", [("strategy", "arbitrary"), ("strategy", []), ("max_chars", True),
                                         ("max_chars", "256"), ("max_chars", 0), ("overlap_chars", True),
                                         ("overlap_chars", -1), ("overlap_chars", 128), ("max_tokens", True),
                                         ("max_tokens", "128"), ("max_tokens", 0)])
def test_preprocessor_descriptor_rejects_invalid_strategy_budgets_and_overlap(api, field, value):
    spec = preprocessing_spec()
    spec["preprocessor"]["config"][field] = value
    status, _ = api.handle("POST", "/api/v2/specs", {}, spec)
    assert status == 400


@pytest.mark.parametrize("changes", [{"adapter": "arbitrary"}, {"version": True}, {"version": 0}, {"version": 2},
                                     {"config": []}, {"config": {"strategy": "semantic"}}, {"python_module": "os"}])
def test_preprocessor_descriptor_rejects_unknown_adapter_version_and_incomplete_config(api, changes):
    spec = preprocessing_spec()
    spec["preprocessor"].update(changes)
    assert api.handle("POST", "/api/v2/specs", {}, spec)[0] == 400


def test_builtin_preprocessing_budgets_are_specs_and_cannot_be_request_overrides(api):
    for name, strategy, max_chars, overlap, max_tokens in (("pii-rules", "fixed", 1024, 128, 1024), ("pii-qwen", "semantic", 256, 64, 128)):
        spec = call(api, "GET", "specs/" + name)
        descriptor = spec["preprocessor"]
        assert descriptor["adapter"] == "utf8-windows"
        assert descriptor["version"] == 1
        assert descriptor["config"] == {"strategy": strategy, "max_chars": max_chars, "overlap_chars": overlap, "max_tokens": max_tokens}
        assert not {"window_chars", "overlap_chars"} & set(spec["input_schema"]["properties"])
    document = upload(api, b"email: private@example.com")
    for override in ({"window_chars": 8192}, {"overlap_chars": 0}, {"max_tokens": 4096}):
        body = {"spec_id": "pii-rules", "inputs": {"document": document, "execute": False, **override}}
        assert api.handle("POST", "/api/v2/jobs", {}, body)[0] == 400
        assert document in api._programs.uploads, "invalid overrides must not consume the input document"


@pytest.mark.parametrize("field", ["window_chars", "overlap_chars"])
def test_descriptor_budget_cannot_be_reintroduced_as_registered_input(api, field):
    spec = preprocessing_spec()
    spec["input_schema"]["properties"][field] = {"type": "integer"}
    assert api.handle("POST", "/api/v2/specs", {}, spec)[0] == 400


def test_registered_preprocessing_token_budget_controls_real_runner_windows(api, monkeypatch):
    import ganglion.domains.pii.rules as rules
    original = rules.RulesDetector

    class OneCharacterPerToken(original):
        def token_count(self, text):
            return len(text)

    monkeypatch.setattr(rules, "RulesDetector", OneCharacterPerToken)
    spec = preprocessing_spec(strategy="fixed", max_chars=128, overlap_chars=16, max_tokens=48)
    call(api, "POST", "specs", spec)
    text = "개인정보가 없는 문장을 작은 모델 입력으로 차례대로 처리합니다.\n" * 30
    started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {"document": upload(api, text.encode()), "execute": False}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    trace = list(map(json.loads, download(api, job["job_id"], "trace.jsonl").splitlines()))
    assert len(trace) > 1
    assert all(0 < row["characters"] <= 48 for row in trace)
    assert any(row["characters"] == 48 for row in trace)
    assert job["result"]["processed_bytes"] == len(text.encode())


def test_legacy_string_preprocessor_retains_registered_window_input_behavior(api):
    spec = legacy_pii_spec()
    spec["id"] = "legacy-pii"
    call(api, "POST", "specs", spec)
    text = "개인정보가 없는 오래된 프로그램의 입력 문서입니다.\n" * 12
    started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {
        "document": upload(api, text.encode()), "execute": False, "window_chars": 96, "overlap_chars": 24}})
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    trace = list(map(json.loads, download(api, job["job_id"], "trace.jsonl").splitlines()))
    assert len(trace) > 1
    assert trace[0]["characters"] == 96
    assert trace[1]["source_character_start"] == 72
    assert all(row["characters"] <= 96 for row in trace)


def test_registered_spec_update_does_not_change_a_running_jobs_preprocessing(api, monkeypatch):
    import ganglion.domains.pii.rules as rules
    entered, proceed = threading.Event(), threading.Event()

    class BlockingRules(rules.RulesDetector):
        def detect(self, text):
            if not entered.is_set():
                entered.set()
                assert proceed.wait(5)
            return []

    monkeypatch.setattr(rules, "RulesDetector", BlockingRules)
    spec = preprocessing_spec(strategy="fixed", max_chars=128, overlap_chars=16, max_tokens=1024)
    first = call(api, "POST", "specs", spec)
    text = ("입력 문서의 분할 정책은 실행을 시작한 계약에 고정되어야 합니다.\n" * 20).encode()
    started = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {"document": upload(api, text), "execute": False}})
    try:
        assert entered.wait(5)
        updated = copy.deepcopy(spec)
        updated["preprocessor"]["config"].update(max_chars=64, overlap_chars=8)
        second = call(api, "POST", "specs", updated)
        assert first["fingerprint"] != second["fingerprint"]
    finally:
        proceed.set()
    job = wait_job(api, started["job_id"])
    assert job["status"] == "complete", job
    assert job["spec_fingerprint"] == first["fingerprint"]
    trace = list(map(json.loads, download(api, job["job_id"], "trace.jsonl").splitlines()))
    assert trace[0]["characters"] == 128
    saved = json.loads((api._programs.root / "jobs" / job["job_id"] / "spec.json").read_text())
    assert saved["preprocessor"] == spec["preprocessor"]
    successor = call(api, "POST", "jobs", {"spec_id": spec["id"], "inputs": {"document": upload(api, text), "execute": False}})
    next_job = wait_job(api, successor["job_id"])
    assert next_job["status"] == "complete"
    assert next_job["spec_fingerprint"] == second["fingerprint"]
    next_trace = list(map(json.loads, download(api, next_job["job_id"], "trace.jsonl").splitlines()))
    assert next_trace[0]["characters"] == 64
