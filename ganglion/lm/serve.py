"""Single-request serving surface ([[lm_request_serve]]).

One ``(model_id, catalog_id, prompt)`` in, one ``ServeResult`` out. This is
the adapter the operator console's chat page sits on and the only place
that computes the **F⁰ / Fᴷ** view of one inference: the plan the model
produced *alone* (post-correction hooks stripped via
``ganglion.contract.patch.strip_hooks``) next to the plan the full catalog
accepted. Unlike a ``ModelClient``, ``Server.serve`` never raises for a
model failure — raw text and repair attempts are preserved on the result
so a failed inference is still a recordable trace.

``serve()`` raises only for caller errors: unknown ``model_id`` (``KeyError``)
and whatever ``resolve_catalog`` raises for an unresolvable ``catalog_id``.
It persists nothing; the console owns the ``Trace`` write and ledger rows.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ganglion.analyzer.repair import RepairConfig, RepairExhaustedError
from ganglion.analyzer.trace import attempts_from_raw, raw_plan_from_attempts
from ganglion.contract.catalog import Catalog
from ganglion.contract.emitter import emit_tool_calls
from ganglion.contract.patch import ALL_HOOK_KINDS, strip_hooks
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.contract.types import ActionPlan
from ganglion.lm.client import ModelClient, ModelOutputError
from ganglion.lm.registry import Registry, build_client_from_spec

__all__ = ["ServeRequest", "ServeResult", "Server", "hooks_diff"]

_DEFAULT_PARSE_STRATEGY = "json_object"


@dataclass(frozen=True)
class ServeRequest:
    model_id: str
    catalog_id: str
    prompt: str
    repair: bool = False
    repair_max_attempts: int = 1
    session_id: str | None = None


@dataclass(frozen=True)
class ServeResult:
    """One served inference. ``plan is None ⇔ error is not None``.

    ``f0_plan`` / ``f0_error`` are populated whenever ``raw_plan`` decoded;
    ``hooks_diff`` lists what the hooks changed between F⁰ and ``plan``
    (``()`` when equal, or when F⁰ failed — ``f0_error`` then carries the
    rescue reason).
    """

    plan: dict[str, Any] | None
    tool_calls: list[dict[str, Any]]
    f0_plan: dict[str, Any] | None
    f0_error: str | None
    hooks_diff: tuple[str, ...]
    raw: Any
    attempts: tuple[dict[str, Any], ...]
    raw_plan: dict[str, Any] | None
    parse_strategy: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    model_id: str
    catalog_id: str
    catalog_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "tool_calls": list(self.tool_calls),
            "f0_plan": self.f0_plan,
            "f0_error": self.f0_error,
            "hooks_diff": list(self.hooks_diff),
            "raw": self.raw,
            "attempts": [dict(att) for att in self.attempts],
            "raw_plan": self.raw_plan,
            "parse_strategy": self.parse_strategy,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
            "model_id": self.model_id,
            "catalog_id": self.catalog_id,
            "catalog_fingerprint": self.catalog_fingerprint,
        }


# ---------------------------------------------------------------------------
# hooks diff
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _calls(plan: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not plan:
        return []
    calls = plan.get("calls")
    if not isinstance(calls, (list, tuple)):
        return []
    return [c for c in calls if isinstance(c, Mapping)]


def hooks_diff(f0_plan: Mapping[str, Any] | None, plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Human-readable lines describing what hooks changed between F⁰ and Fᴷ.

    Per call ``i`` (paired by position), comparing ``f0.args`` with
    ``plan.args``:

    - ``"<tool>.<arg>: <f0 value> → <plan value>"`` — value rewritten;
    - ``"<tool>.<arg>: (missing) → <value> (default)"`` — arg filled in;
    - ``"<tool>: dropped unknown arg '<name>'"`` — arg removed.

    ``()`` when either plan is missing or the two are equal. Call-count
    mismatches (a hook never adds or removes calls, but stay tolerant) are
    reported as ``"<tool>: call present only in F⁰|Fᴷ"``.
    """
    if f0_plan is None or plan is None or f0_plan == plan:
        return ()
    lines: list[str] = []
    f0_calls, k_calls = _calls(f0_plan), _calls(plan)
    for i in range(max(len(f0_calls), len(k_calls))):
        if i >= len(k_calls):
            lines.append(f"{f0_calls[i].get('action')}: call present only in F⁰")
            continue
        if i >= len(f0_calls):
            lines.append(f"{k_calls[i].get('action')}: call present only in Fᴷ")
            continue
        f0_call, k_call = f0_calls[i], k_calls[i]
        tool = str(k_call.get("action", f0_call.get("action", "?")))
        if f0_call.get("action") != k_call.get("action"):
            lines.append(f"{tool}: action {_fmt(f0_call.get('action'))} → {_fmt(k_call.get('action'))}")
        f0_args = f0_call.get("args") if isinstance(f0_call.get("args"), Mapping) else {}
        k_args = k_call.get("args") if isinstance(k_call.get("args"), Mapping) else {}
        for name in sorted(set(f0_args) - set(k_args)):
            lines.append(f"{tool}: dropped unknown arg '{name}'")
        for name in sorted(set(k_args) - set(f0_args)):
            lines.append(f"{tool}.{name}: (missing) → {_fmt(k_args[name])} (default)")
        for name in sorted(set(f0_args) & set(k_args)):
            if f0_args[name] != k_args[name]:
                lines.append(f"{tool}.{name}: {_fmt(f0_args[name])} → {_fmt(k_args[name])}")
    return tuple(lines)


def _parse_strategy_from_raw(raw: Any, failed: bool) -> str:
    if isinstance(raw, Mapping):
        strategy = raw.get("parse_strategy")
        if isinstance(strategy, str) and strategy:
            return strategy
    if failed:
        return "failed"
    return _DEFAULT_PARSE_STRATEGY


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class Server:
    """Registry-backed single-request server with a per-key client cache.

    Clients are cached by ``(model_id, catalog_id, repair, repair_max_attempts)``
    for the server's lifetime (one ``build_client_from_spec`` per key); the
    resolved ``Catalog`` and its hook-stripped F⁰ twin are cached per
    ``catalog_id``. Thread-safe: a lock guards the caches; ``invoke`` itself
    runs outside the lock.
    """

    def __init__(self, registry: Registry, resolve_catalog: Callable[[str], Catalog]) -> None:
        self.registry = registry
        self.resolve_catalog = resolve_catalog
        self._lock = threading.Lock()
        self._clients: dict[tuple[str, str, bool, int], ModelClient] = {}
        self._catalogs: dict[str, Catalog] = {}
        self._f0_catalogs: dict[str, Catalog] = {}
        self.build_count: int = 0  # observability: client_build_count

    # -- caches ------------------------------------------------------------

    def _catalog(self, catalog_id: str) -> Catalog:
        with self._lock:
            cached = self._catalogs.get(catalog_id)
        if cached is not None:
            return cached
        catalog = self.resolve_catalog(catalog_id)  # may raise CatalogNotResolvable
        with self._lock:
            self._catalogs.setdefault(catalog_id, catalog)
            return self._catalogs[catalog_id]

    def _f0_catalog(self, catalog_id: str, catalog: Catalog) -> Catalog:
        with self._lock:
            cached = self._f0_catalogs.get(catalog_id)
            if cached is None:
                cached = strip_hooks(catalog, ALL_HOOK_KINDS)
                self._f0_catalogs[catalog_id] = cached
            return cached

    def _client(self, req: ServeRequest, catalog: Catalog) -> ModelClient:
        key = (req.model_id, req.catalog_id, bool(req.repair), int(req.repair_max_attempts))
        with self._lock:
            client = self._clients.get(key)
        if client is not None:
            return client
        spec = self.registry.get(req.model_id)  # KeyError → propagate
        built = build_client_from_spec(
            spec,
            catalog,
            repair=RepairConfig(enabled=bool(req.repair), max_attempts=max(1, int(req.repair_max_attempts))),
        )
        with self._lock:
            client = self._clients.setdefault(key, built)
            if client is built:
                self.build_count += 1
        return client

    def invalidate(self, model_id: str | None = None) -> None:
        """Drop cached clients (all, or those of one ``model_id``)."""
        with self._lock:
            for key in [k for k in self._clients if model_id is None or k[0] == model_id]:
                del self._clients[key]

    # -- serve -------------------------------------------------------------

    def serve(self, req: ServeRequest) -> ServeResult:
        """Serve one prompt; never raises for model failures.

        ``ModelOutputError`` / ``RepairExhaustedError`` → ``error=str(exc)``,
        ``raw=exc.raw``, attempts preserved; any other ``Exception`` →
        ``error="<ExcClass>: <msg>"``, ``raw=None``. F⁰ is still attempted on
        ``raw_plan`` in every case so the operator sees what the model said.
        """
        spec = self.registry.get(req.model_id)  # KeyError → propagate (404 upstream)
        catalog = self._catalog(req.catalog_id)
        client = self._client(req, catalog)

        plan_obj: ActionPlan | None = None
        raw: Any = None
        error: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        started = time.perf_counter()
        try:
            result = client.invoke(req.prompt)
            plan_obj = result.plan
            raw = result.raw
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            latency_ms = float(result.latency_ms)
        except (ModelOutputError, RepairExhaustedError) as exc:
            raw = exc.raw
            error = str(exc)
            # A generation that happened but did not validate is still billable.
            input_tokens = getattr(exc, "input_tokens", None)
            output_tokens = getattr(exc, "output_tokens", None)
            latency_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:  # noqa: BLE001 — transport/auth/local-load failures become results
            raw = None
            error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000

        attempts = attempts_from_raw(raw)
        raw_plan = raw_plan_from_attempts(attempts)

        f0_plan: dict[str, Any] | None = None
        f0_error: str | None = None
        if raw_plan is not None:
            try:
                f0_plan = (
                    self._f0_catalog(req.catalog_id, catalog)
                    .parse_json_dsl(raw_plan, prompt=req.prompt)
                    .to_jsonable()
                )
            except DSLValidationError as exc:
                f0_error = str(exc)
            except (TypeError, ValueError, KeyError) as exc:
                # Garbage raw_plan shapes (non-mapping args, etc.) are a
                # model failure, not a contract bug.
                f0_error = f"{type(exc).__name__}: {exc}"

        plan = plan_obj.to_jsonable() if plan_obj is not None else None
        # E19: pass the ActionPlan object — a dict would re-parse and re-apply hooks.
        tool_calls = emit_tool_calls(plan_obj, catalog) if plan_obj is not None else []
        diff = hooks_diff(f0_plan, plan) if (f0_plan is not None and plan is not None) else ()

        return ServeResult(
            plan=plan,
            tool_calls=tool_calls,
            f0_plan=f0_plan,
            f0_error=f0_error,
            hooks_diff=diff,
            raw=raw,
            attempts=attempts,
            raw_plan=raw_plan,
            parse_strategy=_parse_strategy_from_raw(raw, failed=error is not None),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error,
            model_id=spec.model_id,
            catalog_id=req.catalog_id,
            catalog_fingerprint=catalog.fingerprint(),
        )
