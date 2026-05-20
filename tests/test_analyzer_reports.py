"""Tests for analyzer.reports — markdown report renderer (M4-F).

Covers the "markdown report renderer" surface from
docs/tasks/analyzer_metrics.md:
    - IoT summary renders all expected section headers + metric byte values.
    - BFCL summary renders BFCL-only sections (by_category, error_type_counts).
    - write_report persists to disk and returns the path.
    - Stamp markers appear iff ``source_path`` is provided.
    - Missing optional fields are gracefully rendered (no crash).
"""

from __future__ import annotations

from pathlib import Path

from ganglion.analyzer.reports import (
    render_markdown,
    render_summary_table,
    write_report,
)


def _iot_summary() -> dict:
    return {
        "total": 25,
        "runs_per_case": 1,
        "syntax_valid_rate": 0.96,
        "exact_match_rate": 0.92,
        "action_match_rate": 0.96,
        "latency_ms_mean": 123.45,
        "latency_ms_p50": 100.0,
        "latency_ms_p95": 180.5,
        "latency_ms_stddev": 21.3,
        "input_tokens_total": 5400,
        "output_tokens_total": 1230,
        "parse_strategy_counts": {"strict": 24, "fenced": 1, "embedded": 0},
        "repair_attempts_total": 3,
        "repair_successes_total": 2,
    }


def _bfcl_summary() -> dict:
    return {
        "total": 50,
        "ast_match_rate": 0.84,
        "syntax_valid_rate": 0.98,
        "latency_ms_mean": 540.12,
        "latency_ms_p50": 510.0,
        "latency_ms_p95": 720.0,
        "latency_ms_stddev": 80.4,
        "input_tokens_total": 12000,
        "output_tokens_total": 3400,
        "by_category": {
            "simple_python": {
                "total": 20,
                "ast_match_rate": 0.95,
                "syntax_valid_rate": 1.0,
            },
            "multiple": {
                "total": 15,
                "ast_match_rate": 0.80,
                "syntax_valid_rate": 1.0,
            },
            "parallel": {
                "total": 15,
                "ast_match_rate": 0.73,
                "syntax_valid_rate": 0.93,
            },
        },
        "error_type_counts": {"wrong_value": 4, "missing_call": 2},
    }


def test_render_markdown_iot_has_all_sections() -> None:
    summary = _iot_summary()
    out = render_markdown(summary, title="IoT run")

    assert "# IoT run" in out
    # Top metrics table.
    assert "| total | 25 |" in out
    assert "exact_match_rate" in out
    assert "0.920" in out  # exact_match_rate formatted as .3f
    assert "action_match_rate" in out
    assert "syntax_valid_rate" in out

    # Latency section.
    assert "## Latency" in out
    assert "123.45" in out
    assert "180.50" in out

    # Tokens.
    assert "## Token totals" in out
    assert "5400" in out
    assert "1230" in out

    # Parse strategies.
    assert "## Parse strategies" in out
    assert "| strict | 24 |" in out
    assert "| fenced | 1 |" in out

    # Repair.
    assert "## Repair" in out
    assert "| attempts_total | 3 |" in out
    assert "| successes_total | 2 |" in out


def test_render_markdown_iot_omits_bfcl_only_sections() -> None:
    out = render_markdown(_iot_summary())
    assert "## By category" not in out
    assert "## Error types" not in out
    # ast_match_rate row should not appear when absent.
    assert "ast_match_rate" not in out


def test_render_markdown_bfcl_includes_bfcl_specific_sections() -> None:
    summary = _bfcl_summary()
    out = render_markdown(summary, title="BFCL run")

    assert "# BFCL run" in out
    # BFCL surfaces ast_match_rate in the headline table.
    assert "ast_match_rate" in out
    assert "0.840" in out

    # By category section with each category present.
    assert "## By category" in out
    assert "simple_python" in out
    assert "multiple" in out
    assert "parallel" in out

    # Error types histogram.
    assert "## Error types" in out
    assert "| wrong_value | 4 |" in out
    assert "| missing_call | 2 |" in out

    # BFCL summaries lack parse_strategy_counts / repair → those sections skip.
    assert "## Parse strategies" not in out
    assert "## Repair" not in out

    # IoT-only fields absent — no exact/action match rows.
    assert "exact_match_rate" not in out
    assert "action_match_rate" not in out


def test_render_summary_table_returns_just_top_table() -> None:
    table = render_summary_table(_iot_summary())
    assert table.startswith("| Metric | Value |")
    assert "total" in table
    assert "exact_match_rate" in table
    # No section headers in the standalone table helper.
    assert "## Latency" not in table
    assert "## Token totals" not in table


def test_stamp_markers_present_when_source_path_given() -> None:
    out = render_markdown(
        _iot_summary(),
        source_path="runs/traces/iot_light_5/r1/summary.json",
    )
    assert (
        "<!-- src:runs/traces/iot_light_5/r1/summary.json#/exact_match_rate -->"
        in out
    )
    assert (
        "<!-- src:runs/traces/iot_light_5/r1/summary.json#/total -->" in out
    )
    assert (
        "<!-- src:runs/traces/iot_light_5/r1/summary.json#/latency_ms_p95 -->"
        in out
    )
    assert (
        "<!-- src:runs/traces/iot_light_5/r1/summary.json#/parse_strategy_counts/strict -->"
        in out
    )


def test_stamp_markers_absent_by_default() -> None:
    out = render_markdown(_iot_summary())
    assert "<!-- src:" not in out


def test_stamp_markers_for_bfcl_breakdowns() -> None:
    out = render_markdown(
        _bfcl_summary(),
        source_path="runs/traces/bfcl/simple_python/r1/summary.json",
    )
    assert (
        "<!-- src:runs/traces/bfcl/simple_python/r1/summary.json#/by_category/simple_python/ast_match_rate -->"
        in out
    )
    assert (
        "<!-- src:runs/traces/bfcl/simple_python/r1/summary.json#/error_type_counts/wrong_value -->"
        in out
    )


def test_missing_optional_fields_render_em_dash() -> None:
    sparse = {
        "total": 5,
        "syntax_valid_rate": 1.0,
        "exact_match_rate": 1.0,
        "action_match_rate": 1.0,
    }
    out = render_markdown(sparse)
    # No crash. Latency missing renders em-dash.
    assert "—" in out
    assert "## Latency" in out
    # Optional sections absent.
    assert "## Parse strategies" not in out
    assert "## Repair" not in out


def test_write_report_persists_and_returns_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.md"
    summary = _iot_summary()

    returned = write_report(summary, target, title="Run X")

    assert returned == target
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert body.startswith("# Run X")
    assert "## Latency" in body
    # No stamps when source_path omitted.
    assert "<!-- src:" not in body


def test_write_report_with_source_path_stamps(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    write_report(
        _iot_summary(),
        target,
        source_path="runs/traces/iot_light_5/r1/summary.json",
    )
    body = target.read_text(encoding="utf-8")
    assert (
        "<!-- src:runs/traces/iot_light_5/r1/summary.json#/total -->" in body
    )
