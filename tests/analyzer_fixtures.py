"""Shared fixtures for the ``tests/test_analyzer_*.py`` files (§4 of the console
contract). Not a test module (no ``test_`` prefix); imported bare thanks to
pytest's prepend import mode (``tests/`` has no ``__init__``).

Every trace here is produced by the *real* pipeline: a client
(``RuleBasedJSONDSLClient`` or the degraded wrapper) → ``run_iot`` →
``traces_from_results`` → ``TraceStore`` under ``tmp_path``, plus a run
bundle so ``list_runs`` / ``compare_runs`` see a manifest. Nothing is
hand-written except the degraded client's failure shapes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ganglion.analyzer.manifest import RunManifest, write_run_bundle
from ganglion.analyzer.metrics import CaseResult, summarize
from ganglion.analyzer.trace import Trace, TraceStore, attempts_from_raw, now_iso
from ganglion.benchmarks.iot.dataset import DEFAULT_DATASET, EvalCase, load_dataset
from ganglion.benchmarks.iot.runner import run_iot, traces_from_results
from ganglion.contract.builtins import get_catalog
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.lm.client import ModelResult
from ganglion.lm.rules import RuleBasedJSONDSLClient

CATALOG_ID = "iot_light_5"
CATALOG = get_catalog(CATALOG_ID)
DATASET_PATH = Path(__file__).resolve().parents[1] / DEFAULT_DATASET

try:  # landed by agent "lm"; fall back to a local shape with the same attributes
    from ganglion.lm.client import ModelOutputError as _OutputError
except ImportError:  # pragma: no cover - only before the lm section lands

    class _OutputError(DSLValidationError):  # type: ignore[no-redef]
        def __init__(self, message: str, *, raw: Any = None, attempts: tuple = ()) -> None:
            super().__init__(message)
            self.raw = raw
            self.attempts = attempts


_HASH_RE = re.compile(r"#(\d+)\s*$")


def load_cases(n: int, *, offset: int = 0) -> list[EvalCase]:
    """First ``n`` dataset rows after ``offset`` (deterministic, checked-in data)."""
    cases = load_dataset(DATASET_PATH, limit=offset + n)
    return cases[offset:offset + n]


class DegradedRulesClient:
    """``RuleBasedJSONDSLClient`` with the console-seed degradations (errata E8):

    * ``drop_state`` — remove ``state`` from ``set_light`` when neither
      ``brightness`` nor ``color_temp`` is present (the catalog default
      cannot rescue it → ``MISSING_REQUIRED_ARG``);
    * ``echo_id`` — echo a trailing ``#N`` token as an extra arg ``id``
      (``strip_unknown_args`` rescues it on every ``iot_light_5`` tool);
    * on validation failure raise ``ModelOutputError`` with ``raw`` /
      ``attempts`` so the runner preserves the payload.
    """

    def __init__(self, *, drop_state: bool = True, echo_id: bool = True) -> None:
        self._inner = RuleBasedJSONDSLClient()
        self.drop_state = drop_state
        self.echo_id = echo_id

    def _payload(self, prompt: str) -> dict[str, Any]:
        payload = json.loads(json.dumps(self._inner._to_payload(prompt)))
        for call in payload["calls"]:
            args = call.setdefault("args", {})
            if (
                self.drop_state
                and call["action"] == "set_light"
                and "brightness" not in args
                and "color_temp" not in args
            ):
                args.pop("state", None)
            if self.echo_id:
                match = _HASH_RE.search(prompt)
                if match:
                    args["id"] = match.group(1)
        return payload

    def invoke(self, prompt: str) -> ModelResult:
        payload = self._payload(prompt)
        try:
            plan = CATALOG.parse_json_dsl(payload, prompt=prompt)
        except DSLValidationError as exc:
            raise _OutputError(str(exc), raw=payload, attempts=attempts_from_raw(payload)) from exc
        return ModelResult(plan=plan, raw=payload, latency_ms=0.5, input_tokens=None, output_tokens=None)


def seed_run(
    base_dir: Path,
    run_id: str,
    cases: list[EvalCase],
    *,
    client: Any | None = None,
    model_id: str = "rules",
    iteration: int | None = None,
    parent_run_id: str | None = None,
    decoding: dict[str, Any] | None = None,
    dataset_sha256: str = "sha-fixture",
    started_at: str | None = None,
) -> tuple[list[Trace], RunManifest, list[CaseResult]]:
    """Run ``client`` over ``cases`` and persist traces + run bundle under ``base_dir``."""
    client = client or RuleBasedJSONDSLClient()
    results = run_iot(client, cases)
    traces = traces_from_results(results, catalog_id=CATALOG_ID, run_id=run_id, model_id=model_id)
    store = TraceStore(base_dir)
    for trace in traces:
        store.append(trace)
    manifest = RunManifest(
        run_id=run_id,
        catalog_id=CATALOG_ID,
        catalog_fingerprint=CATALOG.fingerprint(),
        model_id=model_id,
        benchmark="iot",
        client_kind="rules",
        dataset_path=str(DATASET_PATH),
        dataset_sha256=dataset_sha256,
        n_cases=len(cases),
        decoding=decoding
        or {"repair": False, "repair_max_attempts": 0, "thinking": False, "repeat": 1, "grammar_mask": False},
        iteration=iteration,
        parent_run_id=parent_run_id,
        started_at=started_at or now_iso(),
        finished_at=now_iso(),
    )
    write_run_bundle(base_dir, manifest, summarize(results), "# fixture run\n")
    return traces, manifest, results


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
