"""``/api/*`` route table for the operator console.

Implements the route table of [[console_operator]]
(docs/tasks/console_operator.md). Two kinds of handler, and nothing else:

* **projection (P)** — reads ``manifest.json`` / ``traces.jsonl`` / the
  sidecars / ``events.jsonl`` and shapes JSON. Creates no files. The only
  exceptions, documented in the spec as *W†*, are ``GET /api/compare`` and
  ``GET /api/export/labels``: both persist an idempotent artifact and are
  therefore routed through the :class:`~ganglion.console.writer.Writer` like
  a POST.
* **write (W)** — calls exactly **one** primitive through the ``Writer`` and
  lets that primitive emit its declared ledger row.

No handler classifies, synthesises, attributes, compares, validates or calls
a model itself; every verdict comes from a primitive
([[analyzer_label_store]] ``resolve_gold`` for a trace's status,
[[analyzer_analyze]] for the histogram, [[analyzer_patch_decision]] for
precision, [[lm_request_serve]] for an inference).

Errata honoured here: **E7** — ``repeat_index`` is computed *inside* the
writer job at write time, so re-asking the deterministic ``rules`` model the
same question yields a second trace rather than colliding on the
content-addressed ``trace_id``; **E8** — a ``no_failure`` classification with
``confidence == 0.0`` is reported as ``"unclassified"``, never as a pass.

Public API:
    ApiError, ConsoleAPI, DEFAULT_LABELER.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ganglion import __version__
from ganglion.analyzer import ledger
from ganglion.analyzer.analyze import (
    UNCLASSIFIED,
    analyze_run,
    histogram,
    is_unclassified,
    read_classified,
    read_patches,
)
from ganglion.analyzer.catalogs import (
    CatalogNotResolvable,
    list_catalogs,
    register_compiled,
    resolve_catalog,
    source_tools_for,
)
from ganglion.analyzer.compare import compare_runs, safe_run_name
from ganglion.analyzer.corrections import Attribution
from ganglion.analyzer.decisions import (
    DECISIONS,
    STAGES,
    DecisionStore,
    PatchDecision,
    make_decision_id,
    precision_summary,
)
from ganglion.analyzer.labels import (
    VERDICTS,
    LabelRecord,
    LabelStore,
    export_sft,
    family_id_for,
    make_label_id,
    plan_from_dict,
    resolve_gold,
    write_exports,
    zone_for,
)
from ganglion.analyzer.manifest import (
    RunManifest,
    accelerator_stamp,
    list_runs,
    read_manifest,
    read_summary,
    run_dir,
    write_run_bundle,
)
from ganglion.analyzer.metrics import graded_score
from ganglion.analyzer.trace import Trace, TraceStore, now_iso
from ganglion.console.writer import Writer, WriterDeadError
from ganglion.contract.catalog import Catalog
from ganglion.contract.patch import PatchNotApplicableError, apply_patch
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.lm.prompts import SYSTEM_PROMPT_TEMPLATE
from ganglion.lm.registry import (
    Registry,
    ModelSpec,
    availability,
    load_registry,
    model_fingerprint,
    spec_to_row,
)
from ganglion.lm.serve import ServeRequest, Server

__all__ = ["ApiError", "ConsoleAPI", "DEFAULT_LABELER"]

DEFAULT_LABELER = "operator"

_RUN_VERBS = frozenset({"traces", "analyze", "patches", "events"})
_TWO_SEGMENT_CATALOG_PREFIXES = frozenset({"compiled", "bfcl"})
_BFCL_PREFIX = "bfcl/"
_PRODUCER = "console.api"
_LOG = logging.getLogger("ganglion.console.api")
_EVENTS_TAIL = 20
_DEFAULT_TRACE_LIMIT = 200
_HEALTH_TIMEOUT_S = 2.0


class ApiError(Exception):
    """One HTTP error response: ``{"error", "detail"}`` with a status code."""

    def __init__(self, status: int, error: str, detail: str = "") -> None:
        super().__init__(f"{status} {error}: {detail}" if detail else f"{status} {error}")
        self.status = status
        self.error = error
        self.detail = detail

    def payload(self) -> dict[str, str]:
        return {"error": self.error, "detail": self.detail}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _first(query: Mapping[str, Sequence[str]], name: str, default: str = "") -> str:
    values = query.get(name) or ()
    return str(values[0]) if values else default


def _flag(query: Mapping[str, Sequence[str]], name: str, default: bool = False) -> bool:
    raw = _first(query, name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int(value: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _body_map(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, Mapping):
        raise ApiError(400, "bad_request", "request body must be a JSON object")
    return dict(body)


def _require(body: Mapping[str, Any], *names: str) -> tuple[str, ...]:
    out: list[str] = []
    for name in names:
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ApiError(400, "bad_request", f"{name!r} is required")
        out.append(value.strip())
    return tuple(out)


def _split_catalog_and_run(parts: Sequence[str]) -> tuple[str, str]:
    """``("iot_light_5", "console/s-1")`` from the path parts after ``/api/runs/``.

    Errata E13's shard split: ``compiled`` / ``bfcl`` catalog ids take two
    segments, every other id one; the remainder joined by ``/`` is the run id.
    """
    if len(parts) < 2:
        raise ApiError(404, "not_found", "expected /api/runs/<catalog_id>/<run_id>")
    width = 2 if parts[0] in _TWO_SEGMENT_CATALOG_PREFIXES else 1
    if len(parts) < width + 1:
        raise ApiError(404, "not_found", "expected /api/runs/<catalog_id>/<run_id>")
    catalog_id = "/".join(parts[:width])
    run_id = "/".join(parts[width:])
    return catalog_id, run_id


def _classified_failure_type(row: Mapping[str, Any] | None) -> tuple[str | None, float | None]:
    """``(failure_type, confidence)`` with errata E8's unclassified rule applied."""
    if not row:
        return None, None
    confidence = float(row.get("confidence", 0.0) or 0.0)
    if is_unclassified(row):
        return UNCLASSIFIED, confidence
    return str(row.get("failure_type", "")) or None, confidence


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        fh = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping):
                out.append(dict(row))
    return out


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _first_attempt_payload(trace: Trace) -> Mapping[str, Any] | None:
    """The model's own first attempt as a mapping (patch preview / F⁰ input)."""
    if not trace.attempts:
        return None
    content = trace.attempts[0].get("content", "")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _parse_or_none(catalog: Catalog, payload: Mapping[str, Any], prompt: str) -> Any:
    """``catalog.parse_json_dsl`` or ``None`` — the patch-preview parse."""
    try:
        return catalog.parse_json_dsl(dict(payload), prompt=prompt)
    except (DSLValidationError, TypeError, ValueError, KeyError):
        return None


def _status_for(trace: Trace, labels: Mapping[str, LabelRecord]) -> str:
    """``pass`` | ``fail`` | ``invalid`` | ``ungraded`` — gold decides, not the taxonomy.

    The same rule as ``ganglion.analyzer.compare._status``; kept here as the
    console's read-only projection of it ([[analyzer_label_store]] owns
    ``resolve_gold``, which is the only join).
    """
    gold, _origin = resolve_gold(trace, labels)
    if gold is None:
        return "ungraded"
    if trace.plan is None:
        return "invalid"
    predicted = plan_from_dict(trace.plan)
    return "pass" if predicted is not None and predicted == gold else "fail"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ConsoleAPI:
    """Route table + the console's long-lived handles.

    One instance per process: it owns the ``TraceStore`` / ``LabelStore`` /
    ``DecisionStore`` (all re-read shards whose ``(size, mtime)`` changed, so
    a CLI run in another process becomes visible), the model
    :class:`~ganglion.lm.serve.Server` and the single
    :class:`~ganglion.console.writer.Writer`.
    """

    def __init__(
        self,
        *,
        base_dir: Path | str = "runs/traces",
        web_dir: Path | str = "web",
        registry: Registry | None = None,
        models_path: Path | str | None = None,
        writer: Writer | None = None,
        labeler: str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.web_dir = Path(web_dir)
        self.registry = registry if registry is not None else load_registry(models_path)
        self.store = TraceStore(self.base_dir)
        self.labels = LabelStore(self.base_dir)
        self.decisions = DecisionStore(self.base_dir)
        self.serve_server = Server(self.registry, self.resolve_catalog)
        self.writer = writer if writer is not None else Writer().start()
        self._labeler = labeler
        self._count_lock = threading.Lock()
        #: ``"<path> <status>" → n`` (the ``console_request_count`` observation).
        self.request_count: dict[str, int] = {}

    # -- shared bits -------------------------------------------------------

    @property
    def labels_dir(self) -> Path:
        """``runs/labels/`` — the sibling of the runs dir (contract §0)."""
        return self.base_dir.parent / "labels"

    @property
    def labeler(self) -> str:
        """``$GANGLION_LABELER`` or ``operator``; read per request, not cached."""
        return self._labeler or os.environ.get("GANGLION_LABELER") or DEFAULT_LABELER

    def resolve_catalog(self, catalog_id: str) -> Catalog:
        """``catalog_id`` → ``Catalog`` ([[analyzer_catalogs]]); raises ``CatalogNotResolvable``."""
        return resolve_catalog(catalog_id, base_dir=self.base_dir)

    def _catalog_or_404(self, catalog_id: str) -> Catalog:
        try:
            return self.resolve_catalog(catalog_id)
        except CatalogNotResolvable as exc:
            raise ApiError(404, "unknown_catalog", str(exc)) from exc

    def _spec_or_404(self, model_id: str) -> ModelSpec:
        try:
            return self.registry.get(model_id)
        except KeyError as exc:
            raise ApiError(404, "unknown_model", str(exc)) from exc

    def _manifest_or_404(self, catalog_id: str, run_id: str) -> RunManifest:
        path = run_dir(self.base_dir, catalog_id, run_id) / "manifest.json"
        try:
            return read_manifest(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ApiError(404, "unknown_run", f"no run bundle at {path}") from exc

    def _submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return self.writer.submit(fn, *args, **kwargs)
        except WriterDeadError as exc:
            raise ApiError(503, "writer_unavailable", str(exc)) from exc

    def close(self) -> None:
        """Stop the writer thread (the server's shutdown path)."""
        self.writer.stop()

    # -- dispatch ----------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        query: Mapping[str, Sequence[str]] | None = None,
        body: Any = None,
    ) -> tuple[int, Any]:
        """Route one request; returns ``(status, payload)``. Never raises ``ApiError``."""
        query = query or {}
        try:
            status, payload = self._dispatch(method.upper(), path, query, body)
        except ApiError as exc:
            self._count(path, exc.status)
            return exc.status, exc.payload()
        except CatalogNotResolvable as exc:
            self._count(path, 404)
            return 404, {"error": "unknown_catalog", "detail": str(exc)}
        except WriterDeadError as exc:
            self._count(path, 503)
            return 503, {"error": "writer_unavailable", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — a primitive raised; 500 with its message
            # The response carries only the message; without this the traceback
            # of an internal error would exist nowhere at all (the request log
            # is quiet by default), leaving a 500 undiagnosable.
            _LOG.exception("%s %s failed", method.upper(), path)
            self._count(path, 500)
            return 500, {"error": "internal_error", "detail": f"{type(exc).__name__}: {exc}"}
        self._count(path, status)
        return status, payload

    def _count(self, path: str, status: int) -> None:
        key = f"{path} {status}"
        with self._count_lock:
            self.request_count[key] = self.request_count.get(key, 0) + 1

    def _dispatch(
        self,
        method: str,
        path: str,
        query: Mapping[str, Sequence[str]],
        body: Any,
    ) -> tuple[int, Any]:
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts or parts[0] != "api":
            raise ApiError(404, "not_found", path)
        parts = parts[1:]
        if not parts:
            raise ApiError(404, "not_found", path)
        head, rest = parts[0], parts[1:]

        if head == "health" and not rest:
            self._only(method, "GET")
            return 200, self._health()
        if head == "catalogs":
            return self._route_catalogs(method, rest, query, body)
        if head == "models":
            return self._route_models(method, rest, query, body)
        if head == "sessions" and not rest:
            self._only(method, "POST")
            return 200, self._create_session(_body_map(body))
        if head == "chat" and not rest:
            self._only(method, "POST")
            return 200, self._chat(_body_map(body))
        if head == "labels" and not rest:
            self._only(method, "POST")
            return 200, self._create_label(_body_map(body))
        if head == "runs":
            return self._route_runs(method, rest, query, body)
        if head == "patches":
            return self._route_patches(method, rest, body)
        if head == "compare" and not rest:
            self._only(method, "GET")
            return 200, self._compare(query)
        if head == "export" and rest == ["labels"]:
            self._only(method, "GET")
            return 200, self._export_labels(query)
        raise ApiError(404, "not_found", path)

    @staticmethod
    def _only(method: str, *allowed: str) -> None:
        if method not in allowed:
            raise ApiError(405, "method_not_allowed", f"{method}; allowed: {', '.join(allowed)}")

    # -- /api/health -------------------------------------------------------

    def _health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runs_dir": str(self.base_dir),
            "web_dir": str(self.web_dir),
            "registry_path": str(self.registry.path) if self.registry.path else None,
            "version": __version__,
        }

    # -- /api/catalogs -----------------------------------------------------

    def _route_catalogs(
        self,
        method: str,
        rest: Sequence[str],
        query: Mapping[str, Sequence[str]],
        body: Any,
    ) -> tuple[int, Any]:
        if not rest:
            self._only(method, "GET")
            return 200, {"catalogs": list_catalogs(self.base_dir)}
        if list(rest) == ["compile"]:
            self._only(method, "POST")
            return 200, self._compile_catalog(_body_map(body))
        self._only(method, "GET")
        return 200, self._catalog_detail("/".join(rest))

    def _catalog_detail(self, catalog_id: str) -> dict[str, Any]:
        catalog = self._catalog_or_404(catalog_id)
        try:
            from ganglion.lm.grammar import catalog_to_json_schema

            json_schema: dict[str, Any] | None = catalog_to_json_schema(catalog)
        except Exception:  # noqa: BLE001 — grammar module optional (xgrammar absent)
            json_schema = None
        dsl = catalog.render_json_dsl()
        return {
            "catalog_id": catalog_id,
            "fingerprint": catalog.fingerprint(),
            "source": "compiled" if catalog_id.startswith("compiled/") else "builtin",
            "describe": catalog.describe(),
            "json_dsl": dsl,
            "openai_tools": catalog.render_openai_tools(),
            "system_prompt": SYSTEM_PROMPT_TEMPLATE.format(dsl=dsl),
            "json_schema": json_schema,
            "source_tools": source_tools_for(catalog_id, self.base_dir),
        }

    def _compile_catalog(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "compiled_tools").strip() or "compiled_tools"
        tools = body.get("tools")
        if isinstance(tools, Mapping):
            tools = tools.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ApiError(400, "bad_request", "'tools' must be a non-empty list")
        allow_empty = bool(body.get("allow_empty_calls", False))

        def job() -> dict[str, Any]:
            catalog_id, mapper = register_compiled(
                self.base_dir, name, [dict(t) for t in tools], allow_empty_calls=allow_empty,
            )
            # The mapper is safe to report: `register_compiled` persists the tool
            # list with its key order intact (and recompiles from `catalog.json`
            # when the id already exists), so this catalog and the one every
            # other console surface resolves from disk carry the same
            # fingerprint across restarts. Regression test:
            # tests/test_analyzer_catalogs.py::test_fingerprint_is_stable_across_persistence.
            catalog = mapper.catalog
            ledger.emit(
                self.base_dir,
                "contract.catalog.compiled",
                {
                    "catalog_id": catalog_id,
                    "fingerprint": catalog.fingerprint(),
                    "n_tools": len(catalog.tools),
                    "label": name,
                },
                producer=_PRODUCER,
                correlation={"catalog_id": catalog_id},
                refs={"catalog": str(self.base_dir / catalog_id / "catalog.json")},
            )
            return {
                "catalog_id": catalog_id,
                "fingerprint": catalog.fingerprint(),
                "n_tools": len(catalog.tools),
            }

        try:
            return self._submit(job)
        except DSLValidationError as exc:
            raise ApiError(422, "invalid_tools", str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise ApiError(422, "invalid_tools", f"{type(exc).__name__}: {exc}") from exc

    # -- /api/models -------------------------------------------------------

    def _route_models(
        self,
        method: str,
        rest: Sequence[str],
        query: Mapping[str, Sequence[str]],
        body: Any,
    ) -> tuple[int, Any]:
        if not rest:
            self._only(method, "GET")
            return 200, self._models(_first(query, "catalog_id"))
        if len(rest) != 2:
            raise ApiError(404, "not_found", "/api/models/<model_id>/<health|load|unload>")
        model_id, verb = rest[0], rest[1]
        if verb == "health":
            self._only(method, "GET")
            return 200, self._model_health(model_id)
        if verb == "load":
            self._only(method, "POST")
            return 200, self._model_load(model_id)
        if verb == "unload":
            self._only(method, "POST")
            return 200, self._model_unload(model_id)
        raise ApiError(404, "not_found", f"unknown model verb {verb!r}")

    def _models(self, catalog_id: str) -> dict[str, Any]:
        active_fp: str | None = None
        if catalog_id:
            try:
                active_fp = self.resolve_catalog(catalog_id).fingerprint()
            except CatalogNotResolvable:
                active_fp = None
        rows: list[dict[str, Any]] = []
        for spec in self.registry.list(catalog_id or None):
            row = spec_to_row(spec)
            row["model_fingerprint"] = model_fingerprint(spec)
            trained_fp = (spec.trained_on or {}).get("catalog_fingerprint") if spec.trained_on else None
            if not trained_fp or active_fp is None:
                row["fingerprint_match"] = None
            else:
                row["fingerprint_match"] = bool(trained_fp == active_fp)
            rows.append(row)
        return {"models": rows}

    def _model_health(self, model_id: str) -> dict[str, Any]:
        spec = self._spec_or_404(model_id)
        if spec.kind == "rules":
            return {"status": "live", "detail": "offline rule-based stand-in"}
        if spec.kind == "local_hf":
            from ganglion.lm.local_hf import local_model_status

            return local_model_status(spec)
        ok, detail = availability(spec)
        if not ok:
            return {"status": "unknown", "detail": detail}
        from ganglion.lm.dashscope import DEFAULT_BASE_URL

        base_url = (spec.base_url or DEFAULT_BASE_URL).rstrip("/")
        url = f"{base_url}/models"
        request = urllib.request.Request(url, method="GET")
        api_key = os.environ.get(spec.api_key_env or "", "")
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(request, timeout=_HEALTH_TIMEOUT_S) as response:
                code = int(response.status)
            return {"status": "live" if 200 <= code < 300 else "down", "detail": f"GET {url} → {code}"}
        except urllib.error.HTTPError as exc:
            # 401/403 still proves the endpoint is up.
            alive = exc.code in (401, 403)
            return {
                "status": "live" if alive else "down",
                "detail": f"GET {url} → {exc.code} {exc.reason}",
            }
        except Exception as exc:  # noqa: BLE001 — DNS / refused / timeout are "down"
            return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}

    def _local_spec_or_404(self, model_id: str) -> ModelSpec:
        spec = self._spec_or_404(model_id)
        if spec.kind != "local_hf":
            raise ApiError(404, "not_local_model", f"{model_id!r} is kind {spec.kind!r}, not local_hf")
        return spec

    def _model_load(self, model_id: str) -> dict[str, Any]:
        spec = self._local_spec_or_404(model_id)
        from ganglion.lm.local_hf import load_local_model, local_model_status

        ok, detail = availability(spec)
        if not ok:
            raise ApiError(503, "model_unavailable", detail)

        def job() -> dict[str, Any]:
            started = time.perf_counter()
            load_local_model(spec)
            seconds = round(time.perf_counter() - started, 3)
            status = local_model_status(spec)
            return {
                "status": "loaded",
                "seconds": seconds,
                "device": status.get("device"),
                "vram_mb": status.get("vram_mb"),
            }

        return self._submit(job)

    def _model_unload(self, model_id: str) -> dict[str, Any]:
        spec = self._local_spec_or_404(model_id)
        from ganglion.lm.local_hf import unload_local_model

        unloaded = bool(self._submit(unload_local_model, spec))
        return {"status": "not_loaded", "unloaded": unloaded}

    # -- /api/sessions + /api/chat ----------------------------------------

    def _session_manifest(
        self,
        *,
        session_id: str,
        catalog_id: str,
        catalog: Catalog,
        spec: ModelSpec,
        decoding: Mapping[str, Any],
    ) -> RunManifest:
        return RunManifest(
            run_id=f"console/{session_id}",
            catalog_id=catalog_id,
            catalog_fingerprint=catalog.fingerprint(),
            model_id=spec.model_id,
            benchmark="chat",
            metric_kind="exact_match",
            client_kind="rules" if spec.kind == "rules" else spec.client,
            model_fingerprint=model_fingerprint(spec),
            served_model_version=spec.served_model or spec.base_model or "",
            dataset_path="",
            dataset_sha256="",
            n_cases=0,
            limit=None,
            decoding=dict(decoding),
            iteration=None,
            parent_run_id=None,
            base_model=spec.base_model,
            adapter_dir=spec.adapter_dir,
            git_head="",
            python=platform.python_version(),
            accelerator=accelerator_stamp(
                local=spec.kind == "local_hf", device=spec.device
            ),
            started_at=now_iso(),
            finished_at="",
            extra={"session_id": session_id, "producer": _PRODUCER},
        )

    def _create_session(self, body: Mapping[str, Any]) -> dict[str, Any]:
        catalog_id, model_id = _require(body, "catalog_id", "model_id")
        catalog = self._catalog_or_404(catalog_id)
        spec = self._spec_or_404(model_id)
        session_id = "s-{}-{}".format(
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2)
        )
        decoding = {
            "repair": bool(body.get("repair", False)),
            "repair_max_attempts": _int(str(body.get("repair_max_attempts", 1)), 1, minimum=1),
            "thinking": spec.client == "thinking",
            "repeat": 1,
            "grammar_mask": bool(spec.grammar_mask) if spec.kind == "local_hf" else False,
        }
        manifest = self._session_manifest(
            session_id=session_id,
            catalog_id=catalog_id,
            catalog=catalog,
            spec=spec,
            decoding=decoding,
        )
        describe = catalog.describe()

        def job() -> str:
            directory = Path(write_run_bundle(self.base_dir, manifest, None, None, producer=_PRODUCER))
            (directory / "describe.json").write_text(
                json.dumps(describe, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return str(directory / "manifest.json")

        manifest_path = self._submit(job)
        return {
            "session_id": session_id,
            "run_id": manifest.run_id,
            "manifest_path": manifest_path,
        }

    def _chat(self, body: Mapping[str, Any]) -> dict[str, Any]:
        session_id, catalog_id, model_id, prompt = _require(
            body, "session_id", "catalog_id", "model_id", "prompt"
        )
        self._catalog_or_404(catalog_id)
        spec = self._spec_or_404(model_id)
        run_id = f"console/{session_id}"
        # An unknown run is a 404 (composite `failure` clause): without this a
        # chat would append traces to a shard no `list_runs` scan can see.
        self._manifest_or_404(catalog_id, run_id)
        repair = bool(body.get("repair", False))
        repair_max_attempts = _int(str(body.get("repair_max_attempts", 1)), 1, minimum=1)
        request = ServeRequest(
            model_id=model_id,
            catalog_id=catalog_id,
            prompt=prompt,
            repair=repair,
            repair_max_attempts=repair_max_attempts,
            session_id=session_id,
        )
        case_id = "chat-" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]
        is_local = spec.kind == "local_hf"
        if is_local:
            ok, detail = availability(spec)
            if not ok:
                raise ApiError(503, "model_unavailable", detail)

        def job() -> dict[str, Any]:
            load_seconds: float | None = None
            if is_local:
                # Checked inside the writer so two first chats cannot both
                # claim to have loaded the model.
                from ganglion.lm.local_hf import load_local_model, local_model_status

                if local_model_status(spec).get("status") != "loaded":
                    started = time.perf_counter()
                    load_local_model(spec)
                    load_seconds = round(time.perf_counter() - started, 3)
            result = self.serve_server.serve(request)
            # E7: repeat_index is the number of traces already stored for
            # this (run_id, case_id), counted here inside the writer so a
            # repeated prompt to a deterministic model is a NEW trace.
            repeat_index = sum(
                1 for tr in self.store.iter(catalog_id, run_id) if tr.case_id == case_id
            )
            attempts = result.attempts
            trace = Trace(
                case_id=case_id,
                catalog_id=catalog_id,
                run_id=run_id,
                source="lm.invoke",
                prompt=prompt,
                raw_output=str(attempts[-1].get("content", "")) if attempts else "",
                parse_strategy=result.parse_strategy,
                latency_ms=float(result.latency_ms),
                input_tokens_total=int(result.input_tokens or 0),
                output_tokens_total=int(result.output_tokens or 0),
                model_id=result.model_id,
                timestamp=now_iso(),
                attempts=attempts,
                expected_plan=None,
                plan=result.plan,
                error_type=result.error,
                raw_plan=result.raw_plan,
                repeat_index=repeat_index,
            )
            self.store.append(trace)
            correlation = {
                "catalog_id": catalog_id,
                "run_id": run_id,
                "trace_id": trace.trace_id,
            }
            shard = str(self.store.shard_path(catalog_id, run_id))
            events = [
                ledger.emit(
                    self.base_dir,
                    "lm.inference.failed" if result.error else "lm.inference.completed",
                    {
                        "trace_id": trace.trace_id,
                        "case_id": case_id,
                        "model_id": result.model_id,
                        "session_id": session_id,
                        "latency_ms": round(float(result.latency_ms), 3),
                        "error": result.error or "",
                    },
                    producer=_PRODUCER,
                    correlation=correlation,
                    refs={"traces": shard},
                ),
                ledger.emit(
                    self.base_dir,
                    "analyzer.trace.recorded",
                    {
                        "trace_id": trace.trace_id,
                        "case_id": case_id,
                        "source": trace.source,
                        "repeat_index": repeat_index,
                    },
                    producer=_PRODUCER,
                    correlation=correlation,
                    refs={"traces": shard},
                ),
            ]
            payload = result.to_dict()
            payload.update(
                {
                    "trace_id": trace.trace_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "case_id": case_id,
                    "repeat_index": repeat_index,
                    "order_sensitive": True,
                    "event_ids": [event.event_id for event in events],
                }
            )
            if load_seconds is not None:
                payload["model_load_seconds"] = load_seconds
            return payload

        return self._submit(job)

    # -- /api/runs ---------------------------------------------------------

    def _route_runs(
        self,
        method: str,
        rest: Sequence[str],
        query: Mapping[str, Sequence[str]],
        body: Any,
    ) -> tuple[int, Any]:
        if not rest:
            self._only(method, "GET")
            return 200, self._runs(_first(query, "catalog_id"))
        parts = list(rest)
        trace_id = ""
        verb = ""
        if len(parts) >= 3 and parts[-2] == "traces":
            trace_id = parts[-1]
            verb = "trace"
            parts = parts[:-2]
        elif parts[-1] in _RUN_VERBS:
            verb = parts[-1]
            parts = parts[:-1]
        catalog_id, run_id = _split_catalog_and_run(parts)

        if verb == "":
            self._only(method, "GET")
            return 200, self._run_detail(catalog_id, run_id)
        if verb == "traces":
            self._only(method, "GET")
            return 200, self._run_traces(catalog_id, run_id, query)
        if verb == "trace":
            self._only(method, "GET")
            return 200, self._trace_detail(catalog_id, run_id, trace_id)
        if verb == "events":
            self._only(method, "GET")
            return 200, {
                "events": [event.to_dict() for event in ledger.read_events(self.base_dir, catalog_id, run_id)]
            }
        if verb == "analyze":
            self._only(method, "POST")
            return 200, self._analyze(catalog_id, run_id)
        if verb == "patches":
            self._only(method, "GET")
            return 200, self._patches(catalog_id, run_id)
        raise ApiError(404, "not_found", f"unknown run verb {verb!r}")

    def _compare_names(self, directory: Path) -> list[str]:
        return sorted(p.name[len("compare-"): -len(".json")] for p in directory.glob("compare-*.json"))

    def _runs(self, catalog_id: str) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for manifest in list_runs(self.base_dir, catalog_id or None):
            directory = run_dir(self.base_dir, manifest.catalog_id, manifest.run_id)
            row = manifest.to_dict()
            row["n_traces"] = sum(1 for _ in self.store.iter(manifest.catalog_id, manifest.run_id))
            row["n_labels"] = self.labels.count(manifest.catalog_id, manifest.run_id)
            row["summary"] = read_summary(self.base_dir, manifest.catalog_id, manifest.run_id)
            row["has_classified"] = (directory / "classified.jsonl").is_file()
            row["has_patches"] = (directory / "proposed_patches.jsonl").is_file()
            row["has_corrections"] = (directory / "corrections.summary.json").is_file()
            row["has_compare"] = self._compare_names(directory)
            rows.append(row)
        return {"runs": rows}

    def _run_detail(self, catalog_id: str, run_id: str) -> dict[str, Any]:
        manifest = self._manifest_or_404(catalog_id, run_id)
        directory = run_dir(self.base_dir, catalog_id, run_id)
        classified = read_classified(self.base_dir, catalog_id, run_id)
        patches = read_patches(self.base_dir, catalog_id, run_id)
        decisions = self.decisions.latest_by_patch(catalog_id, run_id)
        events = ledger.read_events(self.base_dir, catalog_id, run_id)
        return {
            "manifest": manifest.to_dict(),
            "summary": read_summary(self.base_dir, catalog_id, run_id),
            "histogram": histogram(classified),
            "corrections": _read_json(directory / "corrections.summary.json"),
            "precision": precision_summary(patches, decisions),
            "n_labels": self.labels.count(catalog_id, run_id),
            "n_traces": sum(1 for _ in self.store.iter(catalog_id, run_id)),
            "compares": self._compare_names(directory),
            "events_tail": [event.to_dict() for event in events[-_EVENTS_TAIL:]],
        }

    def _directions(self, catalog_id: str, run_id: str, parent: str) -> dict[str, str]:
        """``case_id → direction`` from ``compare-<safe(parent)>.json`` (``{}`` when absent)."""
        if not parent:
            return {}
        path = run_dir(self.base_dir, catalog_id, run_id) / f"compare-{safe_run_name(parent)}.json"
        payload = _read_json(path)
        if not payload:
            return {}
        out: dict[str, str] = {}
        for row in payload.get("per_case") or ():
            if isinstance(row, Mapping) and row.get("case_id"):
                out[str(row["case_id"])] = str(row.get("direction") or "")
        return out

    def _run_traces(
        self,
        catalog_id: str,
        run_id: str,
        query: Mapping[str, Sequence[str]],
    ) -> dict[str, Any]:
        manifest = self._manifest_or_404(catalog_id, run_id)
        wanted = _first(query, "filter", "all") or "all"
        if wanted not in {"all", "invalid", "wrong", "unlabelled", "changed"}:
            raise ApiError(400, "bad_request", f"unknown filter {wanted!r}")
        failure_filter = _first(query, "failure_type")
        limit = _int(_first(query, "limit"), _DEFAULT_TRACE_LIMIT, minimum=1, maximum=5000)
        offset = _int(_first(query, "offset"), 0, minimum=0)
        parent = _first(query, "parent") or (manifest.parent_run_id or "")
        directions = self._directions(catalog_id, run_id, parent)
        labels = self.labels.latest_by_trace(catalog_id, run_id)
        classified = read_classified(self.base_dir, catalog_id, run_id)

        rows: list[dict[str, Any]] = []
        counts = {"all": 0, "invalid": 0, "wrong": 0, "unlabelled": 0, "changed": 0}
        for trace in self.store.iter(catalog_id, run_id):
            label = labels.get(trace.trace_id)
            status = _status_for(trace, labels)
            failure_type, confidence = _classified_failure_type(classified.get(trace.trace_id))
            direction = directions.get(trace.case_id) or None
            row = {
                "trace_id": trace.trace_id,
                "case_id": trace.case_id,
                "prompt": trace.prompt,
                "plan": trace.plan,
                "raw_plan": trace.raw_plan,
                "expected_plan": trace.expected_plan,
                "error_type": trace.error_type,
                "status": status,
                "failure_type": failure_type,
                "failure_confidence": confidence,
                "label": label.to_dict() if label is not None else None,
                "direction": direction,
                "repeat_index": trace.repeat_index,
            }
            counts["all"] += 1
            if trace.plan is None:
                counts["invalid"] += 1
            if status == "fail":
                counts["wrong"] += 1
            if label is None:
                counts["unlabelled"] += 1
            if direction in {"fixed", "regressed"}:
                counts["changed"] += 1
            rows.append(row)

        def keep(row: Mapping[str, Any]) -> bool:
            if failure_filter and row.get("failure_type") != failure_filter:
                return False
            if wanted == "invalid":
                return row["plan"] is None
            if wanted == "wrong":
                return row["status"] == "fail"
            if wanted == "unlabelled":
                return row["label"] is None
            if wanted == "changed":
                return row["direction"] in {"fixed", "regressed"}
            return True

        selected = [row for row in rows if keep(row)]
        return {
            "traces": selected[offset: offset + limit],
            "total": len(selected),
            "counts": counts,
            "parent": parent or None,
            "limit": limit,
            "offset": offset,
        }

    def _attribution_for(self, catalog_id: str, run_id: str, trace_id: str) -> dict[str, Any] | None:
        path = run_dir(self.base_dir, catalog_id, run_id) / "corrections.jsonl"
        for row in _read_jsonl(path):
            if row.get("trace_id") == trace_id:
                try:
                    return Attribution.from_dict(row).to_dict()
                except (KeyError, TypeError, ValueError):
                    return dict(row)
        return None

    def _trace_detail(self, catalog_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        trace = self.store.by_id(trace_id, catalog_id=catalog_id, run_id=run_id)
        if trace is None:
            raise ApiError(404, "unknown_trace", f"{trace_id!r} is not in {catalog_id}/{run_id}")
        labels = self.labels.latest_by_trace(catalog_id, run_id)
        label = labels.get(trace_id)
        _gold, gold_origin = resolve_gold(trace, labels)
        classification = read_classified(self.base_dir, catalog_id, run_id).get(trace_id)
        return {
            "trace": trace.to_dict(),
            "classification": classification,
            "label": label.to_dict() if label is not None else None,
            "attribution": self._attribution_for(catalog_id, run_id, trace_id),
            "gold_origin": gold_origin,
            "status": _status_for(trace, labels),
        }

    def _analyze(self, catalog_id: str, run_id: str) -> dict[str, Any]:
        if catalog_id.startswith(_BFCL_PREFIX):
            raise ApiError(
                409,
                "not_analyzable",
                f"{catalog_id!r}: BFCL catalogs are per-case; runs are read-only in the console",
            )
        self._manifest_or_404(catalog_id, run_id)
        try:
            return self._submit(analyze_run, self.base_dir, catalog_id, run_id)
        except CatalogNotResolvable as exc:
            raise ApiError(409, "not_analyzable", str(exc)) from exc

    # -- patches + decisions ----------------------------------------------

    def _preview(
        self,
        catalog: Catalog,
        patch: Mapping[str, Any],
        traces: Sequence[Trace],
        labels: Mapping[str, LabelRecord],
    ) -> dict[str, Any]:
        """Re-parse each trace's first attempt under the patched catalog.

        In memory only ([[contract_patch_apply]]): counts how many graded
        cases the patch would rescue and how many it would regress.
        """
        try:
            patched = apply_patch(catalog, patch)
        except PatchNotApplicableError as exc:
            return {"rescues": 0, "regressions": 0, "applicable": False, "detail": str(exc)}
        rescues = regressions = considered = 0
        for trace in traces:
            gold, _origin = resolve_gold(trace, labels)
            if gold is None:
                continue
            payload = _first_attempt_payload(trace)
            if payload is None:
                continue
            considered += 1
            before = _parse_or_none(catalog, payload, trace.prompt)
            after = _parse_or_none(patched, payload, trace.prompt)
            before_ok = before is not None and before == gold
            after_ok = after is not None and after == gold
            if after_ok and not before_ok:
                rescues += 1
            elif before_ok and not after_ok:
                regressions += 1
        return {
            "rescues": rescues,
            "regressions": regressions,
            "applicable": True,
            "n_considered": considered,
        }

    def _retire_candidates(self, catalog_id: str, run_id: str) -> list[dict[str, Any]]:
        summary = _read_json(run_dir(self.base_dir, catalog_id, run_id) / "corrections.summary.json")
        if not summary:
            return []
        out: list[dict[str, Any]] = []
        by_hook = summary.get("by_hook") or {}
        for hook in sorted(by_hook):
            stats = by_hook[hook]
            if not isinstance(stats, Mapping) or stats.get("verdict") != "retire_candidate":
                continue
            row = dict(stats)
            row["hook"] = hook
            out.append(row)
        return out

    def _patches(self, catalog_id: str, run_id: str) -> dict[str, Any]:
        self._manifest_or_404(catalog_id, run_id)
        patches = read_patches(self.base_dir, catalog_id, run_id)
        decisions = self.decisions.latest_by_patch(catalog_id, run_id)
        catalog: Catalog | None = None
        traces: list[Trace] = []
        labels: Mapping[str, LabelRecord] = {}
        rows: list[dict[str, Any]] = []
        for patch in patches:
            row = dict(patch)
            stages = decisions.get(str(patch.get("patch_id", "")), {})
            row["decisions"] = {stage: dec.to_dict() for stage, dec in stages.items()}
            preview: dict[str, Any] | None = None
            if "blind" in stages:
                if catalog is None:
                    try:
                        catalog = self.resolve_catalog(catalog_id)
                    except CatalogNotResolvable:
                        catalog = None
                    if catalog is not None:
                        traces = list(self.store.iter(catalog_id, run_id))
                        labels = self.labels.latest_by_trace(catalog_id, run_id)
                if catalog is not None:
                    preview = self._preview(catalog, patch, traces, labels)
            row["preview"] = preview
            rows.append(row)
        return {
            "patches": rows,
            "precision": precision_summary(patches, decisions),
            "retire_candidates": self._retire_candidates(catalog_id, run_id),
        }

    def _route_patches(self, method: str, rest: Sequence[str], body: Any) -> tuple[int, Any]:
        if len(rest) != 2 or rest[1] not in {"decision", "ported"}:
            raise ApiError(404, "not_found", "/api/patches/<patch_id>/<decision|ported>")
        self._only(method, "POST")
        return 200, self._decide(rest[0], rest[1], _body_map(body))

    def _decide(self, patch_id: str, verb: str, body: Mapping[str, Any]) -> dict[str, Any]:
        catalog_id, run_id = _require(body, "catalog_id", "run_id")
        patches = read_patches(self.base_dir, catalog_id, run_id)
        match = next((p for p in patches if str(p.get("patch_id")) == patch_id), None)
        if match is None:
            raise ApiError(404, "unknown_patch", f"{patch_id!r} is not proposed by {catalog_id}/{run_id}")
        if verb == "ported":
            stage = decision = "ported"
            (commit_sha,) = _require(body, "commit_sha")
        else:
            stage = str(body.get("stage") or "blind")
            decision = str(body.get("decision") or "")
            commit_sha = str(body.get("commit_sha") or "") or None  # type: ignore[assignment]
            if stage not in STAGES:
                raise ApiError(400, "bad_request", f"stage {stage!r} not in {list(STAGES)}")
            if decision not in DECISIONS:
                raise ApiError(400, "bad_request", f"decision {decision!r} not in {list(DECISIONS)}")
        decided_at = now_iso()
        manifest = self._manifest_or_404(catalog_id, run_id)
        ttd = body.get("time_to_decide_ms")
        record = PatchDecision(
            decision_id=make_decision_id(patch_id, stage, decision, decided_at),
            patch_id=patch_id,
            catalog_id=catalog_id,
            run_id=run_id,
            catalog_fingerprint=manifest.catalog_fingerprint,
            stage=stage,
            decision=decision,
            reason=str(body.get("reason") or ""),
            decided_by=self.labeler,
            time_to_decide_ms=int(ttd) if isinstance(ttd, (int, float)) else None,
            commit_sha=commit_sha,
            decided_at=decided_at,
        )

        def job() -> dict[str, Any]:
            decision_id = self.decisions.append(record)
            event = self.decisions.last_event
            return {
                "decision_id": decision_id,
                "event_ids": [event.event_id] if event is not None else [],
            }

        try:
            return self._submit(job)
        except ValueError as exc:
            raise ApiError(422, "invalid_decision", str(exc)) from exc

    # -- labels ------------------------------------------------------------

    def _create_label(self, body: Mapping[str, Any]) -> dict[str, Any]:
        catalog_id, run_id, trace_id, verdict = _require(
            body, "catalog_id", "run_id", "trace_id", "verdict"
        )
        if verdict not in VERDICTS:
            raise ApiError(400, "bad_request", f"verdict {verdict!r} not in {sorted(VERDICTS)}")
        trace = self.store.by_id(trace_id, catalog_id=catalog_id, run_id=run_id)
        if trace is None:
            raise ApiError(404, "unknown_trace", f"{trace_id!r} is not in {catalog_id}/{run_id}")
        catalog = self._catalog_or_404(catalog_id)

        expected_plan = body.get("expected_plan")
        if expected_plan is not None and not isinstance(expected_plan, Mapping):
            raise ApiError(422, "invalid_expected_plan", "expected_plan must be an Action IR object")
        if verdict == "should_abstain":
            # [[analyzer_label_store]]: the gold for an abstention verdict *is*
            # the empty plan, whatever the catalog's `allow_empty_calls` says —
            # the operator is asserting the prompt is out of scope, not
            # proposing a call the catalog must accept. Gating this through
            # `parse_json_dsl` made the verdict unusable on all four builtin
            # tiers (every one has allow_empty_calls=False). Any *other* payload
            # under this verdict is still a mistake.
            if expected_plan not in (None, {}, {"calls": []}):
                raise ApiError(
                    422,
                    "invalid_expected_plan",
                    "verdict 'should_abstain' takes the empty plan {\"calls\": []}",
                )
            expected_plan = {"calls": []}
        elif expected_plan is not None:
            # E19: always the Catalog (never a CompiledToolMapper) and never auto-fixed.
            try:
                catalog.parse_json_dsl(dict(expected_plan), prompt=trace.prompt)
            except DSLValidationError as exc:
                raise ApiError(422, "invalid_expected_plan", str(exc)) from exc
            except (TypeError, ValueError, KeyError) as exc:
                raise ApiError(422, "invalid_expected_plan", f"{type(exc).__name__}: {exc}") from exc
        if verdict == "incorrect" and expected_plan is None:
            raise ApiError(422, "invalid_expected_plan", "verdict 'incorrect' requires an expected_plan")

        labeler = self.labeler
        origin = f"human:{labeler}"
        family_id = family_id_for(trace.prompt)
        zone = zone_for(family_id)
        session_id = str(body.get("session_id") or "").strip()
        batch_id = session_id or f"{labeler}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        manifest: RunManifest | None = None
        try:
            manifest = self._manifest_or_404(catalog_id, run_id)
        except ApiError:
            manifest = None
        ttl = body.get("time_to_label_ms")
        record = LabelRecord(
            label_id=make_label_id(
                trace_id,
                origin,
                verdict,
                dict(expected_plan) if expected_plan is not None else None,
                labeler,
                batch_id,
            ),
            trace_id=trace_id,
            case_id=trace.case_id,
            catalog_id=catalog_id,
            run_id=run_id,
            catalog_fingerprint=manifest.catalog_fingerprint if manifest else catalog.fingerprint(),
            model_id=trace.model_id,
            dataset_sha256=manifest.dataset_sha256 if manifest else "",
            origin=origin,
            verdict=verdict,
            expected_plan=dict(expected_plan) if expected_plan is not None else None,
            endorsed=body.get("endorsed"),
            saw_f0=bool(body.get("saw_f0", False)),
            failure_hint=body.get("failure_hint"),
            order_sensitive=bool(body.get("order_sensitive", True)),
            note=str(body.get("note") or ""),
            family_id=family_id,
            zone=zone,
            labeler=labeler,
            label_batch_id=batch_id,
            time_to_label_ms=int(ttl) if isinstance(ttl, (int, float)) else None,
            supersedes=body.get("supersedes"),
            created_at=now_iso(),
        )

        def job() -> dict[str, Any]:
            label_id = self.labels.append(record)
            event = self.labels.last_event
            return {
                "label_id": label_id,
                "event_ids": [event.event_id] if event is not None else [],
            }

        try:
            written = self._submit(job)
        except ValueError as exc:
            raise ApiError(422, "invalid_label", str(exc)) from exc

        gold, _origin = resolve_gold(trace, {trace_id: record})
        score = (
            round(graded_score(plan_from_dict(trace.plan), gold), 4) if gold is not None else None
        )
        return {
            "label_id": written["label_id"],
            "zone": zone,
            "family_id": family_id,
            "graded_score": score,
            "event_ids": written["event_ids"],
        }

    # -- compare + export --------------------------------------------------

    def _compare(self, query: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        catalog_id = _first(query, "catalog_id")
        run_a = _first(query, "run_a")
        run_b = _first(query, "run_b")
        if not catalog_id or not run_a or not run_b:
            raise ApiError(400, "bad_request", "catalog_id, run_a and run_b are required")
        # An unmanifested run is "unknown run_id" (404), not a comparison the
        # primitive refused (409) — compare_runs raises ValueError for both.
        self._manifest_or_404(catalog_id, run_a)
        self._manifest_or_404(catalog_id, run_b)
        allow_diff = _flag(query, "allow_diff")
        try:
            result = self._submit(
                compare_runs,
                self.base_dir,
                catalog_id,
                run_a,
                run_b,
                store=self.store,
                allow_diff=allow_diff,
            )
        except ValueError as exc:
            raise ApiError(409, "compare_refused", str(exc)) from exc
        return result.to_dict()

    def _export_labels(self, query: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        catalog_id = _first(query, "catalog_id")
        if not catalog_id:
            raise ApiError(400, "bad_request", "catalog_id is required")
        catalog = self._catalog_or_404(catalog_id)
        zones_raw = _first(query, "zones", "train")
        zones = tuple(z.strip() for z in zones_raw.split(",") if z.strip()) or ("train",)
        labels = list(self.labels.iter(catalog_id))
        traces_by_id = {tr.trace_id: tr for tr in self.store.iter(catalog_id)}
        sft_rows = export_sft(labels, traces_by_id, catalog=catalog, zones=zones)
        hard_rows = export_sft(labels, traces_by_id, catalog=catalog, zones=("dev",))
        zone_map = {label.family_id: label.zone for label in labels if label.family_id}
        paths = self._submit(
            write_exports,
            self.labels_dir,
            catalog_id,
            sft_rows,
            hard_rows,
            zones=zone_map,
        )
        return {
            "paths": paths,
            "n_sft": len(sft_rows),
            "n_hard": len(hard_rows),
            "zones": list(zones),
            "n_labels": len(labels),
        }
