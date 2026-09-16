"""Top-level Ganglion CLI dispatch.

Exposes `python -m ganglion.cli --model … --tier …` (IoT benchmark) and
`python -m ganglion.cli --model … --bfcl …` (BFCL benchmark). The legacy
`--llm <flavour>` flag keeps working and maps onto registry ids
(`ganglion.lm.registry.LLM_TO_MODEL_ID`).

This module is the only place that knows about every concrete client and
benchmark adapter at once; the underlying runners (`benchmarks/iot/runner.py`,
`benchmarks/bfcl/runner.py`) stay benchmark-specific and import-clean.

Persistence ([[benchmark_iot]] / [[benchmark_bfcl]] `--trace-store`): after a
run, every invocation becomes a `Trace` in `TraceStore(dir)` and a run bundle
(`manifest.json` + `summary.json` + `report.md`, [[analyzer_run_manifest]])
plus two ledger rows ([[analyzer_event_ledger]]) are written under
`<dir>/<catalog_id>/<run_id>/`. `persist_iot_run` is the reusable code path
the console `seed` command shares.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ganglion.analyzer.metrics import CaseResult, summarize
from ganglion.analyzer.repair import RepairConfig
from ganglion.benchmarks.bfcl.loader import (
    CATEGORIES as BFCL_CATEGORIES,
    SAMPLE_ROOT as BFCL_SAMPLE_ROOT,
    load_category,
)
from ganglion.benchmarks.bfcl.runner import (
    BFCLCaseResult,
    run_bfcl,
    summarize_bfcl,
    traces_from_bfcl_results,
)
from ganglion.benchmarks.iot.dataset import (
    ADVERSARIAL_DATASET,
    DEFAULT_DATASET,
    EvalCase,
    default_dataset_for,
    load_dataset,
)
from ganglion.benchmarks.iot.runner import run_iot, traces_from_results
from ganglion.contract.builtins import get_catalog
from ganglion.contract.catalog import Catalog
from ganglion.lm.client import ModelClient
from ganglion.lm.registry import (
    LLM_TO_MODEL_ID,
    ModelSpec,
    Registry,
    build_client_from_spec,
    load_registry,
    model_fingerprint,
)

__all__ = [
    "build_client",
    "default_run_id",
    "main",
    "persist_bfcl_run",
    "persist_iot_run",
    "run_eval",
]


BFCL_CALLABLE_CATEGORIES = ("simple_python", "multiple", "parallel", "parallel_multiple")
LLM_CHOICES = tuple(LLM_TO_MODEL_ID)
_PRODUCER = "cli.trace_store"


def build_client(
    name: str,
    catalog: Catalog,
    *,
    repair: RepairConfig | None = None,
    registry: Registry | None = None,
) -> ModelClient:
    """Thin `--llm`-name (or registry id) wrapper over `build_client_from_spec`.

    `name` is a legacy flavour (`rules | qwen | qwen-text | qwen-thinking |
    qwen-native`) mapped through `LLM_TO_MODEL_ID`, or a registry id used
    verbatim. Unknown ids raise `ValueError` naming the known ones.
    """
    reg = registry or load_registry()
    model_id = LLM_TO_MODEL_ID.get(name, name)
    try:
        spec = reg.get(model_id)
    except KeyError as exc:
        raise ValueError(f"unknown llm/model {name!r}: {exc.args[0]}") from None
    return build_client_from_spec(spec, catalog, repair=repair)


def run_eval(
    client: ModelClient,
    dataset_path: Path,
    limit: int | None,
    *,
    repeat: int = 1,
) -> list[CaseResult]:
    """Legacy entry point: load dataset from disk then run the IoT loop.

    Kept for backward compatibility with `tests/test_eval_runner.py`. New
    code should call `ganglion.benchmarks.iot.runner.run_iot` directly on a
    pre-loaded `Sequence[EvalCase]`.
    """
    cases = load_dataset(dataset_path, limit=limit)
    return run_iot(client, cases, repeat=repeat)


# ---------------------------------------------------------------------------
# Small provenance helpers (kept local so the only coupling to
# ganglion.analyzer.manifest is RunManifest + write_run_bundle)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path | str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def default_run_id(model_id: str, when: datetime | None = None) -> str:
    """`f"{model_id}-{YYYYmmdd-HHMMSS}"` with `@`, `/`, `:` and spaces → `-`."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    safe = "".join("-" if ch in "@/:\\ " else ch for ch in model_id)
    return f"{safe}-{stamp}"


def _client_kind(spec: ModelSpec) -> str:
    return "rules" if spec.kind == "rules" else spec.client


def _adapter_sha256(spec: ModelSpec) -> str | None:
    if not spec.adapter_dir:
        return None
    cfg = Path(spec.adapter_dir) / "adapter_config.json"
    return _sha256_file(cfg) or None


def _decoding(spec: ModelSpec, repair: RepairConfig, repeat: int, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "repair": bool(repair.enabled),
        "repair_max_attempts": int(repair.max_attempts),
        "thinking": spec.client == "thinking",
        "repeat": int(repeat),
        "grammar_mask": bool(spec.grammar_mask) if spec.kind == "local_hf" else False,
    }
    payload.update(extra)
    return payload


def _manifest_fields(
    *,
    run_id: str,
    catalog_id: str,
    catalog_fingerprint: str,
    spec: ModelSpec,
    benchmark: str,
    metric_kind: str,
    dataset_path: str,
    n_cases: int,
    limit: int | None,
    decoding: Mapping[str, Any],
    iteration: int | None,
    parent_run_id: str | None,
    started_at: str,
    finished_at: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keyword payload for `ganglion.analyzer.manifest.RunManifest(**fields)`."""
    from ganglion.analyzer.manifest import accelerator_stamp

    return {
        "run_id": run_id,
        "catalog_id": catalog_id,
        "catalog_fingerprint": catalog_fingerprint,
        "model_id": spec.model_id,
        "benchmark": benchmark,
        "metric_kind": metric_kind,
        "client_kind": _client_kind(spec),
        "model_fingerprint": model_fingerprint(spec),
        "served_model_version": spec.served_model or spec.base_model or "",
        "dataset_path": dataset_path,
        "dataset_sha256": _sha256_file(dataset_path),
        "n_cases": int(n_cases),
        "limit": limit,
        "decoding": dict(decoding),
        "iteration": iteration,
        "parent_run_id": parent_run_id,
        "base_model": spec.base_model,
        "adapter_dir": spec.adapter_dir,
        "adapter_sha256": _adapter_sha256(spec),
        "train_provenance": dict(spec.trained_on) if spec.trained_on else None,
        "labels_used": None,
        "git_head": _git_head(),
        "python": platform.python_version(),
        "accelerator": accelerator_stamp(local=spec.kind == "local_hf", device=spec.device),
        "started_at": started_at,
        "finished_at": finished_at,
        "extra": dict(extra or {}),
    }


def _persist_run(
    base_dir: Path,
    *,
    traces: Sequence[Any],
    catalog_id: str,
    run_id: str,
    manifest_fields: Mapping[str, Any],
    summary: dict[str, Any] | None,
    report_md: str | None,
    describe: Mapping[str, Any] | None = None,
) -> Path:
    """Traces → `TraceStore`; then run bundle + ledger rows; returns the run dir.

    `ganglion.analyzer.manifest` / `ganglion.analyzer.ledger` are imported
    lazily here (the trace-store branch is the only consumer). Should either
    be unavailable the traces are still written and a stderr note says what
    was skipped — never a silent partial bundle.
    """
    from ganglion.analyzer.trace import TraceStore

    base_dir = Path(base_dir)
    store = TraceStore(base_dir)
    for trace in traces:
        store.append(trace)
    run_dir = store.shard_path(catalog_id, run_id).parent
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        from ganglion.analyzer import manifest as manifest_mod
    except ImportError as exc:  # parallel-development guard; see docstring
        print(f"[trace-store] run bundle not written ({exc}); traces at {run_dir}", file=sys.stderr)
        return run_dir
    manifest = manifest_mod.RunManifest(**manifest_fields)
    run_dir = Path(manifest_mod.write_run_bundle(base_dir, manifest, summary, report_md))
    if describe is not None:
        (run_dir / "describe.json").write_text(
            json.dumps(describe, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    try:
        from ganglion.analyzer.ledger import emit
    except ImportError as exc:
        print(f"[trace-store] ledger rows not written ({exc})", file=sys.stderr)
        return run_dir
    # `write_run_bundle` already emitted `analyzer.run.recorded` for this
    # manifest (its docstring: "producers that call this need not emit it
    # again"); the CLI adds only the metrics row.
    summary_path = str(run_dir / "summary.json")
    emit(
        base_dir,
        "analyzer.metrics.summarized",
        {
            "summary_path": summary_path,
            "n_traces": len(traces),
            "model_id": manifest_fields.get("model_id", ""),
            "benchmark": manifest_fields.get("benchmark", ""),
        },
        producer=_PRODUCER,
        correlation={"catalog_id": catalog_id, "run_id": run_id},
        refs={"summary": summary_path},
    )
    return run_dir


def persist_iot_run(
    base_dir: Path | str,
    *,
    results: list[CaseResult],
    cases: Sequence[EvalCase],
    catalog: Catalog,
    catalog_id: str,
    spec: ModelSpec,
    run_id: str,
    dataset_path: Path | str,
    limit: int | None,
    repeat: int,
    repair: RepairConfig,
    iteration: int | None = None,
    parent_run: str | None = None,
    started_at: str | None = None,
    summary: dict[str, Any] | None = None,
) -> Path:
    """The `--trace-store` code path for one IoT run (shared with the console `seed`).

    `run_iot` results → `traces_from_results` → `TraceStore` → run bundle
    (`RunManifest(benchmark="iot", metric_kind="exact_match", …)`,
    `summary` = `summarize(results)` + `tier`/`model_id`/char counts when not
    supplied, `report.md` from `render_markdown`, `describe.json`) → two
    ledger rows. Returns the run dir.
    """
    from ganglion.analyzer.reports import render_markdown

    finished_at = _now_iso()
    started_at = started_at or finished_at
    if summary is None:
        summary = summarize(results)
        summary["tier"] = catalog_id
        summary["model_id"] = spec.model_id
        summary["dsl_catalog_chars"] = len(catalog.render_json_dsl())
        summary["openai_tools_chars"] = len(json.dumps(catalog.render_openai_tools()))
    traces = traces_from_results(
        results, catalog_id=catalog_id, run_id=run_id, model_id=spec.model_id
    )
    fields = _manifest_fields(
        run_id=run_id,
        catalog_id=catalog_id,
        catalog_fingerprint=catalog.fingerprint(),
        spec=spec,
        benchmark="iot",
        metric_kind="exact_match",
        dataset_path=str(dataset_path),
        n_cases=len(cases),
        limit=limit,
        decoding=_decoding(spec, repair, repeat),
        iteration=iteration,
        parent_run_id=parent_run,
        started_at=started_at,
        finished_at=finished_at,
    )
    source_path = str(Path(base_dir) / catalog_id / run_id / "summary.json")
    report_md = render_markdown(summary, title=f"Run summary — {run_id}", source_path=source_path)
    return _persist_run(
        Path(base_dir),
        traces=traces,
        catalog_id=catalog_id,
        run_id=run_id,
        manifest_fields=fields,
        summary=summary,
        report_md=report_md,
        describe=catalog.describe(),
    )


def persist_bfcl_run(
    base_dir: Path | str,
    *,
    results: list[BFCLCaseResult],
    category: str,
    spec: ModelSpec,
    run_id: str,
    limit: int | None,
    repeat: int,
    repair: RepairConfig,
    allow_empty_calls: bool,
    iteration: int | None = None,
    parent_run: str | None = None,
    started_at: str | None = None,
    summary_extra: Mapping[str, Any] | None = None,
) -> Path:
    """`--trace-store` for ONE BFCL category (`catalog_id = bfcl/<category>`).

    `catalog_fingerprint=""` (per-case catalogs), `benchmark="bfcl"`,
    `metric_kind="ast_match"`, `report_md=None`, `expected_plan=None` on every
    trace (errata E6). `results` must already be filtered to `category`.
    """
    catalog_id = f"bfcl/{category}"
    finished_at = _now_iso()
    started_at = started_at or finished_at
    summary = summarize_bfcl(results)
    summary["model_id"] = spec.model_id
    summary["bfcl_category"] = category
    summary["bfcl_allow_empty_calls"] = allow_empty_calls
    if summary_extra:
        summary.update(dict(summary_extra))
    traces = traces_from_bfcl_results(
        results, category=category, run_id=run_id, model_id=spec.model_id
    )
    dataset_path = BFCL_SAMPLE_ROOT / f"{category}.jsonl"
    fields = _manifest_fields(
        run_id=run_id,
        catalog_id=catalog_id,
        catalog_fingerprint="",
        spec=spec,
        benchmark="bfcl",
        metric_kind="ast_match",
        dataset_path=str(dataset_path),
        n_cases=len(results),
        limit=limit,
        decoding=_decoding(spec, repair, repeat, allow_empty_calls=bool(allow_empty_calls)),
        iteration=iteration,
        parent_run_id=parent_run,
        started_at=started_at,
        finished_at=finished_at,
        extra={"category": category},
    )
    return _persist_run(
        Path(base_dir),
        traces=traces,
        catalog_id=catalog_id,
        run_id=run_id,
        manifest_fields=fields,
        summary=summary,
        report_md=None,
    )


# ---------------------------------------------------------------------------
# argparse + dispatch
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument(
        "--llm",
        choices=list(LLM_CHOICES),
        default=None,
        help=(
            "Legacy model flavour; maps onto a registry id "
            "(rules → rules, qwen → qwen3.6-plus@dashscope, …). "
            "Mutually exclusive with --model."
        ),
    )
    model_group.add_argument(
        "--model",
        default=None,
        help="Registry model_id from configs/models.yaml (see --models).",
    )
    parser.add_argument(
        "--models",
        type=Path,
        default=None,
        help="Registry YAML path (default: $GANGLION_MODELS or configs/models.yaml).",
    )
    parser.add_argument(
        "--tier",
        default="iot_light_5",
        help=(
            "Catalog tier: iot_light_5 | home_iot_20 | smart_home_50 | "
            "home_assistant_4 (Home Assistant Assist projection; uses its own "
            "derived dataset unless --dataset is given)."
        ),
    )
    parser.add_argument(
        "--bfcl",
        default=None,
        help=(
            "Run BFCL v4 sample instead of the IoT dataset. "
            "Use a category name (simple_python | multiple | parallel | "
            "parallel_multiple | irrelevance), 'callable' for the four "
            "non-irrelevance categories, or 'all' for all five."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="JSONL dataset path. Use examples/iot_light/adversarial_cases.jsonl for adversarial-only cases.",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Use merged dataset: main + adversarial cases (M4).",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each case N times for latency stats (M3).",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Enable validator-error repair loop (json-dsl path only, M4).",
    )
    parser.add_argument(
        "--repair-max-attempts",
        type=int,
        default=1,
        help="Max repair retry attempts after the initial call.",
    )
    parser.add_argument(
        "--bfcl-per-category",
        type=int,
        default=None,
        help="Take the first N cases from each BFCL category before merging.",
    )
    parser.add_argument(
        "--bfcl-skip-per-category",
        type=int,
        default=0,
        help="Skip the first N cases from each BFCL category (use with --bfcl-per-category to take a slice).",
    )
    parser.add_argument(
        "--bfcl-output",
        type=Path,
        default=None,
        help="Write per-case BFCL records as JSONL to this path.",
    )
    parser.add_argument(
        "--bfcl-allow-empty-calls",
        action="store_true",
        help=(
            "Allow the BFCL DSL path to emit {\"calls\":[]} when no listed "
            "tool is needed (M5 abstention/no-call support)."
        ),
    )
    parser.add_argument(
        "--trace-store",
        type=Path,
        default=None,
        help=(
            "Runs dir (e.g. runs/traces). When given, every invocation is written "
            "as a Trace and a run bundle (manifest.json, summary.json, report.md) "
            "is produced under <dir>/<catalog_id>/<run_id>/."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run name inside the trace store (default: <model_id>-<YYYYmmdd-HHMMSS>).",
    )
    parser.add_argument(
        "--iteration",
        type=int,
        default=None,
        help="Loop iteration number recorded in the manifest.",
    )
    parser.add_argument(
        "--parent-run",
        default=None,
        help="run_id of the parent run (for compare / loop pages).",
    )
    return parser


def _resolve_model_id(args: argparse.Namespace) -> str:
    """E18: both absent → `rules`; `--llm` maps via `LLM_TO_MODEL_ID`."""
    if args.model:
        return str(args.model)
    if args.llm:
        return LLM_TO_MODEL_ID[args.llm]
    return "rules"


def _resolve_spec(args: argparse.Namespace) -> tuple[Registry, ModelSpec]:
    registry = load_registry(args.models)
    model_id = _resolve_model_id(args)
    try:
        spec = registry.get(model_id)
    except KeyError as exc:
        raise SystemExit(f"{exc.args[0]} (registry: {registry.path or 'built-in'})") from None
    return registry, spec


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repair = RepairConfig(
        enabled=args.repair,
        max_attempts=max(1, args.repair_max_attempts),
    )
    _registry, spec = _resolve_spec(args)
    if args.run_id is None:
        args.run_id = default_run_id(spec.model_id)

    if args.bfcl is not None:
        _run_bfcl_path(args, repair, spec)
        return

    _run_iot_path(args, repair, spec)


def _run_iot_path(args: argparse.Namespace, repair: RepairConfig, spec: ModelSpec) -> None:
    catalog = get_catalog(args.tier)
    client = build_client_from_spec(spec, catalog, repair=repair)

    dataset_path = args.dataset
    if dataset_path == DEFAULT_DATASET:
        dataset_path = default_dataset_for(args.tier)
    if args.adversarial:
        main_cases = load_dataset(args.dataset, limit=None)
        adv_cases = load_dataset(ADVERSARIAL_DATASET, limit=None)
        merged_path = Path("examples/iot_light/merged_dataset.jsonl")
        with merged_path.open("w", encoding="utf-8") as f:
            for case in main_cases + adv_cases:
                row = {
                    "id": case.id,
                    "prompt": case.prompt,
                    "expected": {
                        "calls": [
                            {"action": c.action, "args": c.args}
                            for c in case.expected.calls
                        ]
                    },
                }
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        dataset_path = merged_path
        print(
            f"Using merged dataset: {dataset_path} "
            f"({len(main_cases)} + {len(adv_cases)} cases)"
        )

    cases = load_dataset(dataset_path, limit=args.limit, catalog=catalog)
    started_at = _now_iso()
    results = run_iot(client, cases, repeat=args.repeat)
    summary = summarize(results)
    summary["tier"] = args.tier
    summary["llm"] = args.llm or spec.model_id
    summary["model_id"] = spec.model_id
    summary["dsl_catalog_chars"] = len(catalog.render_json_dsl())
    summary["openai_tools_chars"] = len(json.dumps(catalog.render_openai_tools()))

    if args.trace_store is not None:
        run_dir = persist_iot_run(
            args.trace_store,
            results=results,
            cases=cases,
            catalog=catalog,
            catalog_id=args.tier,
            spec=spec,
            run_id=args.run_id,
            dataset_path=dataset_path,
            limit=args.limit,
            repeat=args.repeat,
            repair=repair,
            iteration=args.iteration,
            parent_run=args.parent_run,
            started_at=started_at,
            summary=summary,
        )
        print(f"[trace-store] run written to {run_dir}", file=sys.stderr)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _run_bfcl_path(args: argparse.Namespace, repair: RepairConfig, spec: ModelSpec) -> None:
    if spec.kind == "rules":
        raise SystemExit(
            "--llm rules / --model rules has no BFCL adapter; use an openai_compat or local_hf model."
        )
    categories = _resolve_bfcl_categories(args.bfcl)
    cases = []
    for category in categories:
        cat_cases = load_category(category)
        if args.bfcl_skip_per_category:
            cat_cases = cat_cases[args.bfcl_skip_per_category:]
        if args.bfcl_per_category is not None:
            cat_cases = cat_cases[: args.bfcl_per_category]
        cases.extend(cat_cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    def factory(catalog: Catalog) -> ModelClient:
        return build_client_from_spec(spec, catalog, repair=repair)

    started_at = _now_iso()
    results = run_bfcl(
        factory,
        cases,
        repeat=args.repeat,
        allow_empty_calls=args.bfcl_allow_empty_calls,
    )
    if args.bfcl_output is not None:
        _write_bfcl_per_case(results, args.bfcl_output)
    summary = summarize_bfcl(results)
    summary["llm"] = args.llm or spec.model_id
    summary["model_id"] = spec.model_id
    summary["bfcl_categories"] = list(categories)
    summary["bfcl_per_category"] = args.bfcl_per_category
    summary["bfcl_skip_per_category"] = args.bfcl_skip_per_category
    summary["bfcl_allow_empty_calls"] = args.bfcl_allow_empty_calls

    if args.trace_store is not None:
        for category in categories:
            res_c = [r for r in results if r.case.category == category]
            if not res_c:
                continue
            run_dir = persist_bfcl_run(
                args.trace_store,
                results=res_c,
                category=category,
                spec=spec,
                run_id=args.run_id,
                limit=args.bfcl_per_category,
                repeat=args.repeat,
                repair=repair,
                allow_empty_calls=args.bfcl_allow_empty_calls,
                iteration=args.iteration,
                parent_run=args.parent_run,
                started_at=started_at,
                summary_extra={
                    "llm": args.llm or spec.model_id,
                    "bfcl_skip_per_category": args.bfcl_skip_per_category,
                },
            )
            print(f"[trace-store] run written to {run_dir}", file=sys.stderr)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _write_bfcl_per_case(results, path: Path) -> None:
    """Persist per-case BFCL outcomes for post-hoc analysis (Phase E/G)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            run = r.runs[0] if r.runs else None
            row = {
                "id": r.case.id,
                "category": r.case.category,
                "tool_count": len(r.case.tools),
                "expects_call": r.case.expects_call,
                "ast_valid": r.grade.valid,
                "grade_error_type": r.grade.error_type,
                "syntax_valid": run is not None and run.plan is not None,
                "error": run.error if run else None,
                "latency_ms": run.latency_ms if run else None,
                "input_tokens": run.input_tokens if run else None,
                "output_tokens": run.output_tokens if run else None,
                "dsl_chars": r.dsl_chars,
                "native_chars": r.native_chars,
                "predicted": r.predicted.to_jsonable() if r.predicted is not None else None,
            }
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _resolve_bfcl_categories(arg: str) -> tuple[str, ...]:
    if arg == "all":
        return BFCL_CATEGORIES
    if arg == "callable":
        return BFCL_CALLABLE_CATEGORIES
    if arg in BFCL_CATEGORIES:
        return (arg,)
    raise SystemExit(
        f"unknown --bfcl value: {arg!r}. "
        f"Choose one of: {', '.join(BFCL_CATEGORIES)}, callable, all."
    )


if __name__ == "__main__":
    main()
