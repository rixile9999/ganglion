"""Shared UI/CLI jobs with bounded uploads and privacy-safe PII traces."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from .specs import builtins, fingerprint, validate, validate_inputs, preprocessing

CHUNK_BYTES = 192 * 1024
ANALYSIS_SPAN_LIMIT = 10000


class ProgramService:
    def __init__(self, root: Path, tool_server):
        self.root, self.tool_server = root, tool_server
        self.checkpoint = Path(os.environ.get("GANGLION_PII_CHECKPOINT", "runs/pii/qwen-0.8b-v2"))
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ganglion-program")
        self.active, self.uploads = {}, {}
        self.detector = None
        self.model_lock = threading.Lock()
        self.owner_file = None
        self.model_stamp = None

    def close(self):
        self.pool.shutdown(wait=True)
        for upload in self.uploads.values():
            upload["path"].unlink(missing_ok=True)
        if self.owner_file is not None:
            self.owner_file.close()
            self.owner_file = None

    def _claim(self):
        """Single writer runtime per root; reclaim private inputs after a crash."""
        import fcntl
        with self.lock:
            if self.owner_file is not None:
                return
            self._directory(self.root)
            owner = (self.root / ".owner.lock").open("a")
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                owner.close()
                raise ValueError("program runtime is already active; connect CLI with GANGLION_CONSOLE_URL") from None
            self.owner_file = owner
            # Nobody else owns this root; these files belonged to an interrupted process.
            for path in (self.root / "private").glob("*"):
                if path.is_file():
                    path.unlink()
            for path in (self.root / "jobs").glob("*/job.json"):
                job = json.loads(path.read_text())
                if job["status"] in {"queued", "running", "cancelling"}:
                    shutil.rmtree(path.parent / "artifacts", ignore_errors=True)
                    job.update(status="interrupted", error={"code": "interrupted", "detail": "Previous runtime stopped before completion."})
                    self._save(job)

    def _model_fingerprint(self):
        if self.model_stamp is not None:
            return self.model_stamp
        digest = hashlib.sha256()
        for name in ("model.json", "heads.safetensors"):
            with (self.checkpoint / name).open("rb") as source:
                while block := source.read(65536):
                    digest.update(block)
        for path in sorted((self.checkpoint / "adapter").rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(self.checkpoint)).encode())
                with path.open("rb") as source:
                    while block := source.read(65536):
                        digest.update(block)
        self.model_stamp = "native-" + digest.hexdigest()
        return self.model_stamp

    def _directory(self, path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)

    def _specs(self):
        specs = builtins()
        for path in sorted((self.root / "specs").glob("*.json")):
            spec = validate(json.loads(path.read_text()))
            specs[spec["id"]] = spec
        return specs

    def specs(self):
        return {"specs": [self._describe(s) for s in self._specs().values()]}

    def _describe(self, spec):
        result = copy.deepcopy(spec)
        result["fingerprint"] = fingerprint(spec)
        result["available"] = spec["model"]["backend"] != "qwen_native" or all((self.checkpoint / name).is_file() for name in ("model.json", "heads.safetensors"))
        result["availability_detail"] = "ready" if result["available"] else "native checkpoint not trained; see PII setup"
        return result

    def spec(self, spec_id):
        if spec_id not in self._specs():
            raise ValueError("unknown application spec")
        return self._describe(self._specs()[spec_id])

    def register(self, spec):
        validate(spec)
        # HTTP clients can declare data contracts, never Python modules or model paths.
        allowed = {"version", "id", "title", "domain", "description", "model", "contract", "preprocessor", "executor", "input_schema", "result_schema"}
        if set(spec) - allowed or set(spec["model"]) - {"backend", "base_model"}:
            raise ValueError("spec contains unsupported execution settings")
        self._claim()
        path = self.root / "specs" / (spec["id"] + ".json")
        self._directory(path.parent)
        with self.lock:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
            temp.replace(path)
        return self._describe(spec)

    def upload(self, body):
        data = base64.b64decode(body.get("data", ""), validate=True)
        offset = body.get("offset", 0)
        if len(data) > CHUNK_BYTES or type(offset) is not int or offset < 0:
            raise ValueError("invalid upload chunk or offset")
        if not body.get("upload_id") and offset != 0:
            raise ValueError("new upload offset must be zero")
        self._claim()
        with self.lock:
            if not body.get("upload_id"):
                if len(self.uploads) >= 16:
                    raise ValueError("too many pending uploads")
                upload_id = uuid.uuid4().hex
                directory = self.root / "private"
                self._directory(directory)
                path = directory / upload_id
                path.touch(mode=0o600, exist_ok=False)
                self.uploads[upload_id] = {"path": path, "size": 0}
            else:
                upload_id = body["upload_id"]
                if upload_id not in self.uploads:
                    raise ValueError("unknown or consumed upload")
            upload = self.uploads[upload_id]
            if offset != upload["size"]:
                raise ValueError("invalid upload chunk or offset")
            with upload["path"].open("ab") as target:
                target.write(data)
            upload["size"] += len(data)
            return {"upload_id": upload_id, "bytes": upload["size"]}

    def discard(self, upload_id):
        self._claim()
        with self.lock:
            if upload_id in self.uploads:
                self.uploads.pop(upload_id)["path"].unlink(missing_ok=True)
        return {"discarded": True}

    def _consume(self, upload_id):
        if upload_id not in self.uploads:
            raise ValueError("unknown or consumed document upload")
        return self.uploads.pop(upload_id)["path"]

    def start(self, body):
        spec = self.spec(body.get("spec_id"))
        if not spec["available"]:
            raise ValueError("native checkpoint is unavailable")
        inputs = validate_inputs(spec["input_schema"], body.get("inputs", {}))
        self._claim()
        source = None
        with self.lock:
            if sum(j["status"] in {"queued", "running"} for j in self.active.values()) >= 4:
                raise ValueError("job queue is full")
            if spec["domain"] == "pii-text":
                from ganglion.adapters.recovery import decode_key
                if inputs.get("execute", True):
                    if spec.get("executor") != "reversible-text":
                        raise ValueError("execution requires the reversible-text adapter")
                    decode_key(inputs.get("public_key", ""))
                if not isinstance(spec.get("preprocessor"), dict) and inputs.get("overlap_chars", 128) >= inputs.get("window_chars", 1024) // 2:
                    raise ValueError("overlap must be smaller than half the window")
                source = self._consume(inputs["document"])
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "spec_id": spec["id"], "spec_fingerprint": spec["fingerprint"],
                   "result_schema": spec["result_schema"],
                   "status": "queued", "created_at": time.time(), "progress": {}, "result": None}
            self.active[job_id] = job
            directory = self.root / "jobs" / job_id
            try:
                self._directory(directory)
                # Pin the application contract; do not persist input text or key material.
                (directory / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
                self._save(job)
                self.pool.submit(self._run, job, spec, inputs, source)
            except Exception:
                self.active.pop(job_id, None)
                shutil.rmtree(directory, ignore_errors=True)
                if source:
                    source.unlink(missing_ok=True)
                raise
            return copy.deepcopy(job)

    def _save(self, job):
        path = self.root / "jobs" / job["job_id"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        temp.replace(path)

    def _run(self, job, spec, inputs, source):
        from ganglion.domains.pii.pipeline import run_document, Cancelled
        output = self.root / "jobs" / job["job_id"] / "artifacts"
        try:
            self._directory(output)
            with self.lock:
                if job["status"] == "cancelling":
                    raise Cancelled()
                job["status"] = "running"
                self._save(job)
            if spec["domain"] == "pii-text":
                if spec["model"]["backend"] == "rules":
                    from ganglion.domains.pii.rules import RulesDetector
                    detector = RulesDetector()
                else:
                    with self.model_lock:
                        if self.detector is None:
                            from ganglion.domains.pii.native import NativeDetector
                            self._model_fingerprint()
                            self.detector = NativeDetector(self.checkpoint)
                            if self.detector.metadata["base_model"] != spec["model"].get("base_model", "Qwen/Qwen3.5-0.8B"):
                                self.detector = None
                                raise ValueError("checkpoint backbone does not match application spec")
                    detector = self.detector

                def progress(values):
                    with self.lock:
                        job["progress"] = values
                config = preprocessing(spec) or {"strategy": "fixed", "max_chars": 1024, "overlap_chars": 128, "max_tokens": 1024}
                if not isinstance(spec.get("preprocessor"), dict):
                    config["max_chars"] = inputs.get("window_chars", config["max_chars"])
                    config["overlap_chars"] = inputs.get("overlap_chars", config["overlap_chars"])
                if hasattr(detector, "metadata") and config["max_tokens"] > detector.metadata.get("max_tokens", 1024):
                    raise ValueError("preprocessor token budget exceeds checkpoint capability")
                result = run_document(source, output, detector, public_key=inputs.get("public_key"),
                                      document_id=job["job_id"], execute=inputs.get("execute", True),
                                      preprocess=spec.get("preprocessor") is not None,
                                      strategy=config["strategy"], max_tokens=config["max_tokens"],
                                      window_chars=config["max_chars"], overlap_chars=config["overlap_chars"],
                                      progress=progress, cancelled=lambda: job["status"] == "cancelling")
                if spec["model"]["backend"] == "qwen_native":
                    result["model_fingerprint"] = self._model_fingerprint()
                else:
                    from ganglion.domains.pii import rules
                    result["model_fingerprint"] = "rules-" + hashlib.sha256(Path(rules.__file__).read_bytes()).hexdigest()
            else:
                from ganglion.lm.serve import ServeRequest
                served = self.tool_server.serve(ServeRequest(prompt=inputs["prompt"], catalog_id=inputs["catalog_id"], model_id=inputs["model_id"]))
                if served.error:
                    raise ValueError("tool model returned an invalid plan")
                result = {"domain": spec["domain"], "plan": served.plan, "raw_plan": served.f0_plan,
                          "corrections": list(served.hooks_diff), "latency_ms": served.latency_ms,
                          "input_tokens": served.input_tokens, "output_tokens": served.output_tokens,
                          "artifacts": ["plan.json"], "mode": "plan"}
                from ganglion.lm.registry import model_fingerprint
                result["model_fingerprint"] = model_fingerprint(self.tool_server.registry.get(inputs["model_id"]))
                result["catalog_fingerprint"] = served.catalog_fingerprint
                (output / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            with self.lock:
                if job["status"] == "cancelling":
                    raise Cancelled()
                job["model_fingerprint"] = result["model_fingerprint"]
                job.update(status="complete", result=result, finished_at=time.time())
        except Exception as exc:
            shutil.rmtree(output, ignore_errors=True)
            with self.lock:
                job.update(status="cancelled" if isinstance(exc, Cancelled) else "failed",
                           error={"code": type(exc).__name__, "detail": "Execution failed; no partial artifacts published."}, finished_at=time.time())
        finally:
            if source:
                source.unlink(missing_ok=True)
            inputs.clear()
            with self.lock:
                self._save(job)
                self.active.pop(job["job_id"], None)

    def job(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", str(job_id)):
            raise ValueError("invalid job id")
        with self.lock:
            if job_id in self.active:
                return copy.deepcopy(self.active[job_id])
            path = self.root / "jobs" / job_id / "job.json"
            if not path.is_file():
                raise ValueError("unknown job")
            job = json.loads(path.read_text())
            if job["status"] in {"queued", "running", "cancelling"}:
                job.update(status="interrupted", error={"code": "interrupted", "detail": "Server stopped before completion."})
            return job

    def jobs(self):
        paths = sorted((self.root / "jobs").glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
        return {"jobs": [self.job(p.parent.name) for p in paths]}

    def cancel(self, job_id):
        self._claim()
        with self.lock:
            job = self.job(job_id)
            if job_id in self.active and job["status"] in {"queued", "running"}:
                self.active[job_id]["status"] = "cancelling"
                self._save(self.active[job_id])
            return self.job(job_id)

    def artifact(self, job_id, name, offset=0):
        job = self.job(job_id)
        if job["status"] != "complete" or name not in job["result"]["artifacts"]:
            raise ValueError("artifact is unavailable")
        path = self.root / "jobs" / job_id / "artifacts" / name
        if offset < 0 or offset > path.stat().st_size:
            raise ValueError("invalid artifact offset")
        with path.open("rb") as source:
            source.seek(offset)
            data = source.read(CHUNK_BYTES)
        return {"name": name, "offset": offset, "bytes": len(data), "total_bytes": path.stat().st_size,
                "data": base64.b64encode(data).decode(), "done": offset + len(data) == path.stat().st_size}

    def _analyze_pii_feedback(self, job, gold, gold_complete):
        """Compare coordinates only; source text and recovery keys are absent.

        Window overlap duplicates are consolidated before document-level
        analysis. Attribution includes Interpreter and window reconciliation.
        Older traces without byte offsets still permit final-stage analysis.
        """
        from ganglion.analyzer.domain_analysis import analyze_predictions
        directory = self.root / "jobs" / job["job_id"] / "artifacts"
        final, raw = [], set()
        with (directory / "plan.jsonl").open() as source:
            for line in source:
                edit = json.loads(line)
                final.append({"start": edit["source_span"][0], "end": edit["source_span"][1], "type": edit["type"]})
                if len(final) > ANALYSIS_SPAN_LIMIT:
                    return {"status": "unavailable", "reason": "analysis_span_limit", "limit": ANALYSIS_SPAN_LIMIT}
        with (directory / "trace.jsonl").open() as source:
            for line in source:
                unit = json.loads(line)
                if "raw_byte_spans" not in unit:
                    raw = None
                    break
                raw.update((s["start"], s["end"], s["type"]) for s in unit["raw_byte_spans"])
                if len(raw) > ANALYSIS_SPAN_LIMIT:
                    return {"status": "unavailable", "reason": "analysis_span_limit", "limit": ANALYSIS_SPAN_LIMIT}
        raw_spans = None if raw is None else [{"start": s, "end": e, "type": t} for s, e, t in sorted(raw)]
        result = analyze_predictions("pii-text", raw_spans, final, gold, coordinate="utf8-byte",
                                     gold_complete=gold_complete, document_length=job["result"]["processed_bytes"])
        result["attribution_scope"] = "interpreter_and_window_reconciliation"
        return result

    def feedback(self, job_id, body):
        job = self.job(job_id)
        if job["status"] != "complete" or body.get("verdict") not in {"correct", "missed_pii", "overmasked", "invalid"}:
            raise ValueError("feedback requires a complete job and a supported verdict")
        spans = body.get("expected_spans", [])
        if not isinstance(spans, list) or len(spans) > 10000:
            raise ValueError("invalid expected spans")
        gold_complete = body.get("gold_complete", False)
        if type(gold_complete) is not bool or gold_complete and "expected_spans" not in body:
            raise ValueError("gold_complete requires an explicit expected_spans list and a boolean value")
        for s in spans:
            from ganglion.domains.pii.types import ENTITY_TYPES
            if not isinstance(s, dict) or set(s) != {"start", "end", "type"} or type(s["start"]) is not int or type(s["end"]) is not int or not 0 <= s["start"] < s["end"] <= job["result"].get("processed_bytes", 2**63) or s["type"] not in ENTITY_TYPES:
                raise ValueError("expected spans must contain byte coordinates and type only")
        row = {"job_id": job_id, "spec_fingerprint": job["spec_fingerprint"], "verdict": body["verdict"],
               "expected_spans": spans if "expected_spans" in body else None,
               "gold_complete": gold_complete, "coordinate": "utf8-byte", "created_at": time.time(),
               "status": "awaiting_independent_validation"}
        if job["result"].get("domain") == "pii-text":
            row["analysis"] = self._analyze_pii_feedback(job, row["expected_spans"], gold_complete)
        else:
            row["analysis"] = {"status": "unavailable", "reason": "feedback_analyzer_not_installed"}
        self._claim()
        with self.lock, (self.root / "jobs" / job_id / "feedback.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        return row

    def restore(self, body):
        from ganglion.adapters.recovery import restore_document, decode_key
        decode_key(body.get("private_key", ""))
        with self.lock:
            ids = (body.get("document"), body.get("recovery"))
            if len(set(ids)) != 2 or any(i not in self.uploads for i in ids):
                raise ValueError("two distinct uploads are required")
            document, recovery = (self._consume(i) for i in ids)
        job_id = uuid.uuid4().hex
        directory = self.root / "jobs" / job_id
        output = directory / "artifacts"
        try:
            self._directory(output)
            result = restore_document(document, recovery, body["private_key"], output / "restored.txt")
            result.pop("source_sha256", None)
            job = {"job_id": job_id, "spec_id": "restore", "spec_fingerprint": "", "created_at": time.time(),
                   "status": "complete", "result": {"domain": "restore", "artifacts": ["restored.txt"], **result}}
            self._save(job)
            return job
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise ValueError("restoration failed: wrong key, changed document or invalid recovery file") from None
        finally:
            document.unlink(missing_ok=True)
            recovery.unlink(missing_ok=True)

    def route(self, method, parts, query, body):
        from ganglion.console.api import ApiError
        body = {} if body is None else body
        try:
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            if method == "GET" and parts == ["specs"]:
                return 200, self.specs()
            if method == "GET" and len(parts) == 2 and parts[0] == "specs":
                return 200, self.spec(parts[1])
            if method == "POST" and parts == ["specs"]:
                return 200, self.register(body)
            if method == "POST" and parts == ["uploads"]:
                return 200, self.upload(body)
            if method == "POST" and len(parts) == 3 and parts[0] == "uploads" and parts[2] == "discard":
                return 200, self.discard(parts[1])
            if method == "POST" and parts == ["keys"]:
                from ganglion.adapters.recovery import generate_keypair
                return 200, generate_keypair()
            if method == "POST" and parts == ["restore"]:
                return 200, self.restore(body)
            if method == "POST" and parts == ["jobs"]:
                return 202, self.start(body)
            if method == "GET" and parts == ["jobs"]:
                return 200, self.jobs()
            if len(parts) >= 2 and parts[0] == "jobs":
                if method == "GET" and len(parts) == 2:
                    return 200, self.job(parts[1])
                if method == "POST" and parts[2:] == ["cancel"]:
                    return 200, self.cancel(parts[1])
                if method == "POST" and parts[2:] == ["feedback"]:
                    return 200, self.feedback(parts[1], body)
                if method == "GET" and len(parts) == 4 and parts[2] == "artifacts":
                    return 200, self.artifact(parts[1], parts[3], int((query.get("offset") or [0])[0]))
        except (ValueError, TypeError, KeyError) as exc:
            raise ApiError(400, "invalid_program_request", str(exc)) from None
        raise ApiError(404, "not_found", "unknown v2 route")
