"""Offline seed data for the console ([[console_operator]] §Scope, errata E8).

``python -m ganglion.console seed`` fills a runs dir with **two** runs so
every page has real data without an API key and without a GPU:

1. ``rules-seed`` — the registry ``rules`` model over
   ``examples/iot_light/dataset.jsonl`` (+ ``adversarial_cases.jsonl`` when
   present), through the same code path as ``cli --trace-store``
   (``ganglion.cli.persist_iot_run``).
2. ``rules-degraded-seed`` — :class:`DegradedRulesClient`, a console-owned
   wrapper over ``RuleBasedJSONDSLClient``. Measured fact behind errata E8:
   the plain rules client is valid on 500/500 dataset rows, so
   ``proposed_patches.jsonl`` would be empty, ``precision_summary`` all zeros
   and the Rules page would have nothing to decide on. The wrapper produces
   real ``missing_required_arg`` and ``unknown_arg`` failures instead.

Both runs are ``iteration 0``; ``analyze_run`` runs on each, and (by default)
``compare_runs`` joins them so the Traces page's ``changed`` filter and the
Loops page's transition matrix have data too.

The degradation is deliberately *model-shaped* — the things a small model
actually does on this dataset:

* **drops ``state``** from ``set_light`` when neither ``brightness`` nor
  ``color_temp`` is present (the tier's ``defaults_when_missing`` predicate
  needs one of those, so validation genuinely fails) → ``MISSING_REQUIRED_ARG``;
* on the remaining ``set_light`` calls (the ones carrying ``brightness`` or
  ``color_temp``), **echoes the prompt's trailing token back as an extra arg
  ``id``** — the ``#N`` counter when the prompt has one, which is the leak
  ``iot_light.py`` documents — *and* writes the state as ``"turn_on"`` /
  ``"turn_off"`` → ``UNKNOWN_ARG``. Every ``iot_light_5`` tool sets
  ``strip_unknown_args=True``, so an echoed arg on an otherwise valid call is
  silently stripped and nothing fails; pairing it with a non-canonical
  ``state`` makes the call fail, keeps the echoed arg visible in ``raw_plan``,
  and the taxonomy's priority order (``unknown_arg`` before
  ``value_out_of_enum``) files it under ``UNKNOWN_ARG``.

Public API:
    DegradedRulesClient, DEGRADED_SPEC, RULES_SEED_RUN_ID,
    DEGRADED_SEED_RUN_ID, seed_runs.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ganglion.analyzer.analyze import analyze_run
from ganglion.analyzer.compare import compare_runs
from ganglion.analyzer.repair import RepairConfig
from ganglion.benchmarks.iot.dataset import (
    ADVERSARIAL_DATASET,
    EvalCase,
    default_dataset_for,
    load_dataset,
)
from ganglion.benchmarks.iot.runner import run_iot
from ganglion.contract.builtins import get_catalog
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.lm.client import ModelOutputError, ModelResult
from ganglion.lm.registry import ModelSpec
from ganglion.lm.rules import RuleBasedJSONDSLClient

__all__ = [
    "DEGRADED_SEED_RUN_ID",
    "DEGRADED_SPEC",
    "RULES_SEED_RUN_ID",
    "RULES_SPEC",
    "DegradedRulesClient",
    "seed_runs",
]

RULES_SEED_RUN_ID = "rules-seed"
DEGRADED_SEED_RUN_ID = "rules-degraded-seed"

#: `#12` at the end of a prompt — the echoed-token failure mode.
_TRAILING_HASH_RE = re.compile(r"#(\d+)\s*$")

RULES_SPEC = ModelSpec(
    model_id="rules",
    kind="rules",
    client="json-dsl",
    provider="none",
    catalog_ids=("iot_light_5",),
    notes="offline regex stand-in (single call)",
)

DEGRADED_SPEC = ModelSpec(
    model_id="rules-degraded",
    kind="rules",
    client="json-dsl",
    provider="none",
    catalog_ids=("iot_light_5",),
    notes="console seed: degraded rules stand-in (errata E8)",
)


class DegradedRulesClient:
    """``RuleBasedJSONDSLClient`` with two injected small-model failure modes.

    Implements ``ModelClient``. Never returns an unvalidated plan: a payload
    the catalog rejects is raised as ``ModelOutputError`` carrying ``raw`` and
    ``attempts``, exactly like the real clients (errata E9), so
    ``traces_from_results`` can rebuild ``raw_plan`` from it.
    """

    def __init__(self, catalog: Any = None) -> None:
        self.catalog = catalog if catalog is not None else get_catalog("iot_light_5")
        self._base = RuleBasedJSONDSLClient()

    # -- degradation -------------------------------------------------------

    @staticmethod
    def _echoed_token(prompt: str) -> str:
        """The prompt's trailing token — the ``#N`` counter when there is one."""
        match = _TRAILING_HASH_RE.search(prompt)
        if match:
            return match.group(1)
        tokens = prompt.strip().split()
        return tokens[-1] if tokens else "0"

    @classmethod
    def _degrade(cls, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        """Apply the two failure modes to the rules client's payload."""
        out: list[dict[str, Any]] = []
        for call in payload.get("calls", []):
            call = dict(call)
            args = dict(call.get("args", {}) or {})
            if call.get("action") == "set_light":
                if "brightness" not in args and "color_temp" not in args:
                    # The tier's `defaults_when_missing` predicate needs one
                    # of those two, so `state` really is missing.
                    args.pop("state", None)
                elif isinstance(args.get("state"), str):
                    # Echoed trailing token + a non-canonical state value: the
                    # echo alone would be stripped by `strip_unknown_args`, so
                    # pairing it with a bad state keeps it visible in raw_plan.
                    args["id"] = cls._echoed_token(prompt)
                    args["state"] = f"turn_{args['state']}"
            out.append({**call, "args": args})
        return {**payload, "calls": out}

    # -- ModelClient -------------------------------------------------------

    def invoke(self, user_prompt: str) -> ModelResult:
        started = time.perf_counter()
        payload = self._degrade(self._base._to_payload(user_prompt), user_prompt)
        try:
            plan = self.catalog.parse_json_dsl(payload, prompt=user_prompt)
        except DSLValidationError as exc:
            from ganglion.analyzer.trace import attempts_from_raw

            raise ModelOutputError(
                str(exc), raw=payload, attempts=attempts_from_raw(payload)
            ) from exc
        return ModelResult(
            plan=plan,
            raw=payload,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=None,
            output_tokens=None,
        )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _cases(catalog: Any, catalog_id: str, limit: int | None) -> tuple[list[EvalCase], Path]:
    """Dataset rows + the adversarial file when it exists; ``limit`` applies per file."""
    dataset_path = default_dataset_for(catalog_id)
    cases = load_dataset(dataset_path, limit=limit, catalog=catalog)
    if ADVERSARIAL_DATASET.is_file() and catalog_id != "home_assistant_4":
        cases.extend(load_dataset(ADVERSARIAL_DATASET, limit=limit, catalog=catalog))
    return cases, dataset_path


def _run_one(
    base_dir: Path,
    *,
    client: Any,
    spec: ModelSpec,
    run_id: str,
    catalog: Any,
    catalog_id: str,
    cases: list[EvalCase],
    dataset_path: Path,
    limit: int | None,
    parent_run: str | None,
) -> dict[str, Any]:
    from ganglion.cli import persist_iot_run  # lazy: cli imports the whole benchmark surface

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = run_iot(client, cases)
    run_directory = persist_iot_run(
        base_dir,
        results=results,
        cases=cases,
        catalog=catalog,
        catalog_id=catalog_id,
        spec=spec,
        run_id=run_id,
        dataset_path=dataset_path,
        limit=limit,
        repeat=1,
        repair=RepairConfig(enabled=False),
        iteration=0,
        parent_run=parent_run,
        started_at=started,
    )
    return {"run_id": run_id, "run_dir": str(run_directory), "n_cases": len(cases)}


def seed_runs(
    base_dir: Path | str,
    *,
    catalog_id: str = "iot_light_5",
    limit: int | None = 100,
    analyze: bool = True,
    compare: bool = True,
) -> dict[str, Any]:
    """Write ``rules-seed`` + ``rules-degraded-seed`` under ``base_dir``.

    Returns ``{"catalog_id", "runs": [{run_id, run_dir, n_cases, analyze}],
    "compare": {...} | None}``. Callers that need serialisation (the console)
    submit this whole function to the ``Writer``.
    """
    base = Path(base_dir)
    catalog = get_catalog(catalog_id)
    cases, dataset_path = _cases(catalog, catalog_id, limit)

    runs: list[dict[str, Any]] = [
        _run_one(
            base,
            client=RuleBasedJSONDSLClient(),
            spec=RULES_SPEC,
            run_id=RULES_SEED_RUN_ID,
            catalog=catalog,
            catalog_id=catalog_id,
            cases=cases,
            dataset_path=dataset_path,
            limit=limit,
            parent_run=None,
        ),
        _run_one(
            base,
            client=DegradedRulesClient(catalog),
            spec=DEGRADED_SPEC,
            run_id=DEGRADED_SEED_RUN_ID,
            catalog=catalog,
            catalog_id=catalog_id,
            cases=cases,
            dataset_path=dataset_path,
            limit=limit,
            parent_run=RULES_SEED_RUN_ID,
        ),
    ]

    if analyze:
        for run in runs:
            run["analyze"] = analyze_run(base, catalog_id, run["run_id"])

    comparison: dict[str, Any] | None = None
    if compare:
        # Same dataset + decoding on both sides, so this never refuses; the
        # compare file is what the `changed` trace filter reads.
        comparison = compare_runs(
            base,
            catalog_id,
            RULES_SEED_RUN_ID,
            DEGRADED_SEED_RUN_ID,
            bootstrap=200,
        ).to_dict()

    return {"catalog_id": catalog_id, "runs": runs, "compare": comparison}
