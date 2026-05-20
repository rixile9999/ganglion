"""Markdown report renderer over the canonical summary JSON shape.

Implements the "markdown report renderer" surface from
[[analyzer_metrics]] (docs/tasks/analyzer_metrics.md). Reads the dict produced
by :func:`ganglion.analyzer.metrics.summarize` (and the BFCL flavour produced by
:func:`ganglion.benchmarks.bfcl.runner.summarize_bfcl`) and renders a
human-readable ``report.md`` body.

Every numeric in the rendered markdown can be stamped with an HTML comment
``<!-- src:<source_path>#/<json/pointer> -->`` so the future
[[report_freshness]] cross-checker can verify prose against the underlying
``summary.json``. Stamping is opt-in via the ``source_path`` argument; when
omitted, the renderer produces clean markdown suitable for ad-hoc viewing.

Pure functions: :func:`render_markdown` and :func:`render_summary_table` perform
no I/O. :func:`write_report` is the only entry point that touches disk.

Out of scope (per spec):
    - Aggregation logic (lives in :mod:`ganglion.analyzer.metrics`).
    - HTML / JSON-Schema outputs.
    - Stamp verification (legacy ``report_freshness`` concept).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["render_markdown", "render_summary_table", "write_report"]


_MISSING = "—"


def _fmt(value: Any, spec: str) -> str:
    """Format ``value`` with ``spec`` (a `format()` mini-language fragment).

    Returns ``"—"`` for missing / unformattable values so the renderer never
    crashes on a sparse summary dict.
    """
    if value is None:
        return _MISSING
    try:
        return format(float(value), spec) if spec else str(int(value))
    except (TypeError, ValueError):
        return _MISSING


def _fmt_rate(value: Any) -> str:
    return _fmt(value, ".3f")


def _fmt_latency(value: Any) -> str:
    return _fmt(value, ".2f")


def _fmt_int(value: Any) -> str:
    return _fmt(value, "")


def _stamp(source_path: str | None, pointer: str) -> str:
    if source_path is None:
        return ""
    return f" <!-- src:{source_path}#/{pointer.lstrip('/')} -->"


# ---------------------------------------------------------------------------
# Section renderers (pure)
# ---------------------------------------------------------------------------


def _row(label: str, value: str, stamp: str = "") -> str:
    return f"| {label} | {value}{stamp} |"


def render_summary_table(summary: dict[str, Any]) -> str:
    """Render the top-level metrics table only.

    Useful for embedding the headline rates inside a larger document without
    pulling in the full per-section breakdown.
    """
    return _render_summary_table(summary, source_path=None)


def _render_summary_table(
    summary: dict[str, Any], *, source_path: str | None
) -> str:
    # ``ast_match_rate`` is BFCL-only; ``exact_match_rate`` / ``action_match_rate``
    # are IoT-only. Skipping the absent field is what keeps both summary
    # flavours round-tripping through the same renderer.
    rows: list[tuple[str, str, str]] = [
        ("total", _fmt_int(summary.get("total")), "total"),
    ]
    for key in ("ast_match_rate", "exact_match_rate", "action_match_rate"):
        if key in summary:
            rows.append((key, _fmt_rate(summary.get(key)), key))
    rows.append(
        ("syntax_valid_rate", _fmt_rate(summary.get("syntax_valid_rate")), "syntax_valid_rate")
    )

    lines: list[str] = ["| Metric | Value |", "|---|---|"]
    for label, value, pointer in rows:
        lines.append(_row(label, value, _stamp(source_path, pointer)))
    return "\n".join(lines)


def _render_latency(summary: dict[str, Any], *, source_path: str | None) -> str:
    lines = [
        "## Latency",
        "",
        "| Percentile | ms |",
        "|---|---|",
        _row(
            "mean",
            _fmt_latency(summary.get("latency_ms_mean")),
            _stamp(source_path, "latency_ms_mean"),
        ),
        _row(
            "p50",
            _fmt_latency(summary.get("latency_ms_p50")),
            _stamp(source_path, "latency_ms_p50"),
        ),
        _row(
            "p95",
            _fmt_latency(summary.get("latency_ms_p95")),
            _stamp(source_path, "latency_ms_p95"),
        ),
    ]
    return "\n".join(lines)


def _render_tokens(summary: dict[str, Any], *, source_path: str | None) -> str:
    lines = [
        "## Token totals",
        "",
        "| Stream | Tokens |",
        "|---|---|",
        _row(
            "input",
            _fmt_int(summary.get("input_tokens_total")),
            _stamp(source_path, "input_tokens_total"),
        ),
        _row(
            "output",
            _fmt_int(summary.get("output_tokens_total")),
            _stamp(source_path, "output_tokens_total"),
        ),
    ]
    return "\n".join(lines)


def _render_parse_strategies(
    summary: dict[str, Any], *, source_path: str | None
) -> str | None:
    counts = summary.get("parse_strategy_counts")
    if not counts or not isinstance(counts, dict):
        return None
    canonical = ("strict", "fenced", "embedded", "failed")
    extras = sorted(k for k in counts if k not in canonical)
    ordered_keys = [k for k in canonical if k in counts] + extras

    lines = ["## Parse strategies", "", "| Strategy | Count |", "|---|---|"]
    for key in ordered_keys:
        lines.append(
            _row(
                key,
                _fmt_int(counts.get(key)),
                _stamp(source_path, f"parse_strategy_counts/{key}"),
            )
        )
    return "\n".join(lines)


def _render_repair(summary: dict[str, Any], *, source_path: str | None) -> str | None:
    attempts = summary.get("repair_attempts_total")
    successes = summary.get("repair_successes_total")
    if attempts is None and successes is None:
        return None
    lines = [
        "## Repair",
        "",
        "| Metric | Value |",
        "|---|---|",
        _row(
            "attempts_total",
            _fmt_int(attempts),
            _stamp(source_path, "repair_attempts_total"),
        ),
        _row(
            "successes_total",
            _fmt_int(successes),
            _stamp(source_path, "repair_successes_total"),
        ),
    ]
    return "\n".join(lines)


def _render_by_category(
    summary: dict[str, Any], *, source_path: str | None
) -> str | None:
    by_category = summary.get("by_category")
    if not by_category or not isinstance(by_category, dict):
        return None
    lines = [
        "## By category",
        "",
        "| Category | Total | AST match | Syntax valid |",
        "|---|---|---|---|",
    ]
    for cat in sorted(by_category.keys()):
        stats = by_category.get(cat) or {}
        if not isinstance(stats, dict):
            continue
        cells = [
            cat,
            _fmt_int(stats.get("total")) + _stamp(source_path, f"by_category/{cat}/total"),
            _fmt_rate(stats.get("ast_match_rate"))
            + _stamp(source_path, f"by_category/{cat}/ast_match_rate"),
            _fmt_rate(stats.get("syntax_valid_rate"))
            + _stamp(source_path, f"by_category/{cat}/syntax_valid_rate"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_error_types(
    summary: dict[str, Any], *, source_path: str | None
) -> str | None:
    error_types = summary.get("error_type_counts")
    if not error_types or not isinstance(error_types, dict):
        return None
    lines = [
        "## Error types",
        "",
        "| Error type | Count |",
        "|---|---|",
    ]
    for key in sorted(error_types.keys()):
        lines.append(
            _row(
                key,
                _fmt_int(error_types.get(key)),
                _stamp(source_path, f"error_type_counts/{key}"),
            )
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_markdown(
    summary: dict[str, Any],
    *,
    title: str = "Run summary",
    source_path: str | None = None,
) -> str:
    """Render a full markdown report from a canonical summary dict.

    Parameters
    ----------
    summary:
        The dict produced by :func:`ganglion.analyzer.metrics.summarize` or its
        BFCL counterpart. Missing optional fields render as em-dashes; missing
        whole sections (e.g. ``parse_strategy_counts`` for BFCL runs) are
        skipped entirely.
    title:
        Heading of the report; defaults to ``"Run summary"``.
    source_path:
        When provided (e.g. ``"runs/traces/iot_light_5/<run_id>/summary.json"``),
        every numeric is followed by an HTML stamp comment pointing to the
        corresponding JSON pointer. Omit to produce un-stamped markdown.
    """
    blocks: list[str | None] = [
        _render_summary_table(summary, source_path=source_path),
        _render_latency(summary, source_path=source_path),
        _render_tokens(summary, source_path=source_path),
        _render_parse_strategies(summary, source_path=source_path),
        _render_repair(summary, source_path=source_path),
        _render_by_category(summary, source_path=source_path),
        _render_error_types(summary, source_path=source_path),
    ]

    sections: list[str] = [f"# {title}"]
    for block in blocks:
        if block is None:
            continue
        sections.extend(["", block])
    return "\n".join(sections) + "\n"


def write_report(
    summary: dict[str, Any],
    output_path: Path,
    *,
    title: str = "Run summary",
    source_path: str | None = None,
) -> Path:
    """Render and persist a markdown report; returns the written path.

    Creates parent directories as needed. Overwrites an existing file at
    ``output_path``. Stamp comments are emitted only when ``source_path`` is
    provided — by convention this should be the path the consumer would use to
    reach the corresponding ``summary.json`` (typically a path relative to the
    repo root).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = render_markdown(summary, title=title, source_path=source_path)
    output_path.write_text(body, encoding="utf-8")
    return output_path
