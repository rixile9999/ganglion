"""Generic ApplicationSpec commands over the same v2 routes as the workbench."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import time

from ganglion.ctl.errors import CtlError


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise CtlError(400, "invalid_file", str(exc)) from exc


def _upload(api, path):
    from ganglion.programs.service import CHUNK_BYTES
    row = api.post("/api/v2/uploads", {})
    try:
        with Path(path).open("rb") as source:
            while block := source.read(CHUNK_BYTES):
                row = api.post("/api/v2/uploads", {"upload_id": row["upload_id"], "offset": row["bytes"], "data": base64.b64encode(block).decode()})
        return row["upload_id"]
    except Exception:
        api.post(f"/api/v2/uploads/{row['upload_id']}/discard")
        raise


def _download(api, job_id, name, destination):
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise CtlError(409, "file_exists", str(target))
    # Fetch the first chunk before creating a destination on a bad request.
    row = api.get(f"/api/v2/jobs/{job_id}/artifacts/{name}", offset=0)
    created = False
    try:
        with target.open("xb") as file:
            created = True
            os.chmod(target, 0o600)
            while True:
                file.write(base64.b64decode(row["data"], validate=True))
                if row["done"]:
                    break
                row = api.get(f"/api/v2/jobs/{job_id}/artifacts/{name}", offset=row["offset"] + row["bytes"])
        return {"job_id": job_id, "artifact": name, "path": str(target), "bytes": row["total_bytes"]}
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise


def _emit(deps, payload):
    return deps.out.emit(payload, lambda body: deps.out.write(json.dumps(body, ensure_ascii=False, indent=2)),
                         rows_key="specs" if "specs" in payload else "jobs" if "jobs" in payload else None)


def command(args, deps):
    try:
        verb = args.verb
        if verb == "list":
            payload = deps.api.get("/api/v2/specs")
        elif verb == "show":
            payload = deps.api.get(f"/api/v2/specs/{args.spec_id}")
        elif verb == "register":
            payload = deps.api.post("/api/v2/specs", _read(args.file))
        elif verb == "jobs":
            payload = deps.api.get("/api/v2/jobs")
        elif verb == "job":
            payload = deps.api.get(f"/api/v2/jobs/{args.job_id}")
        elif verb == "cancel":
            payload = deps.api.post(f"/api/v2/jobs/{args.job_id}/cancel")
        elif verb == "feedback":
            payload = deps.api.post(f"/api/v2/jobs/{args.job_id}/feedback", _read(args.file))
        elif verb == "artifact":
            payload = _download(deps.api, args.job_id, args.name, args.output)
        elif verb == "keygen":
            private = Path(args.private)
            public = Path(args.public)
            if private.resolve() == public.resolve() or private.exists() or public.exists():
                raise CtlError(409, "file_exists", "key files must be distinct new paths")
            keys = deps.api.post("/api/v2/keys")
            created = []
            try:
                for path, content in ((private, keys), (public, {"format": keys["format"], "public_key": keys["public_key"]})):
                    with path.open("x") as target:
                        created.append(path)
                        os.chmod(path, 0o600)
                        json.dump(content, target, indent=2)
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            payload = {"private_key_file": str(private), "public_key_file": str(public)}
        elif verb == "restore":
            if Path(args.output).exists():
                raise CtlError(409, "file_exists", str(args.output))
            uploads = []
            try:
                for path in (args.document, args.recovery):
                    uploads.append(_upload(deps.api, path))
                job = deps.api.post("/api/v2/restore", {"document": uploads[0], "recovery": uploads[1], "private_key": _read(args.key)["private_key"]})
                payload = _download(deps.api, job["job_id"], "restored.txt", args.output)
            finally:
                for upload in uploads:
                    deps.api.post(f"/api/v2/uploads/{upload}/discard")
        elif verb == "run":
            inputs = _read(args.inputs) if args.inputs else {}
            upload = None
            try:
                if args.file:
                    upload = _upload(deps.api, args.file)
                    inputs["document"] = upload
                if args.public_key:
                    inputs["public_key"] = _read(args.public_key)["public_key"]
                if args.plan:
                    inputs["execute"] = False
                job = deps.api.post("/api/v2/jobs", {"spec_id": args.spec_id, "inputs": inputs})
                while job["status"] in {"queued", "running", "cancelling"}:
                    time.sleep(0.15)
                    job = deps.api.get(f"/api/v2/jobs/{job['job_id']}")
                if args.output_dir and job["status"] == "complete":
                    directory = Path(args.output_dir)
                    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
                    for name in job["result"]["artifacts"]:
                        _download(deps.api, job["job_id"], name, directory / name)
                _emit(deps, job)
                return 0 if job["status"] == "complete" else 1
            finally:
                if upload:
                    deps.api.post(f"/api/v2/uploads/{upload}/discard")
        return _emit(deps, payload)
    except (OSError, ValueError, KeyError) as exc:
        raise CtlError(400, "invalid_program_input", str(exc)) from exc


def add_parser(sub, common):
    noun = sub.add_parser("program", help="spec-driven programs, jobs and artifacts")
    verbs = noun.add_subparsers(dest="verb", required=True)
    descriptions = {"list": "list application specs", "show": "show input/output schemas", "register": "validate and register a data-only spec",
                    "run": "run a spec and wait for completion", "jobs": "list jobs", "job": "show a job", "cancel": "cancel a job",
                    "artifact": "download an artifact without overwriting", "keygen": "create recovery key files", "restore": "restore exact document bytes",
                    "feedback": "record independent feedback"}
    for verb, description in descriptions.items():
        leaf = verbs.add_parser(verb, parents=[common], help=description)
        leaf.set_defaults(_handler=command)
        if verb in {"show", "run"}:
            leaf.add_argument("spec_id")
        if verb in {"job", "cancel", "artifact", "feedback"}:
            leaf.add_argument("job_id")
        if verb in {"register", "feedback"}:
            leaf.add_argument("file", help="JSON file")
        if verb == "run":
            leaf.add_argument("--inputs", help="JSON object matching the spec input schema")
            leaf.add_argument("--file", help="stream a document file into the document input")
            leaf.add_argument("--public-key", help="public key JSON for the optional executor")
            leaf.add_argument("--plan", action="store_true", help="request core plan without execution")
            leaf.add_argument("--output-dir", help="new directory to download all artifacts")
        if verb == "artifact":
            leaf.add_argument("name")
            leaf.add_argument("--output", required=True)
        if verb == "keygen":
            leaf.add_argument("--private", required=True)
            leaf.add_argument("--public", required=True)
        if verb == "restore":
            leaf.add_argument("document")
            leaf.add_argument("recovery")
            leaf.add_argument("--key", required=True)
            leaf.add_argument("--output", required=True)
