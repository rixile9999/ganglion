"""Aggregate runs/*/*.json into compact tables for the report."""
from __future__ import annotations

import json
from pathlib import Path


RUNS = Path("runs")


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fmt(value, default="-"):
    if value is None:
        return default
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_m2() -> None:
    print("\n=== M2 scaling (50 cases per tier) ===")
    headers = ("tier", "path", "exact", "input_tok", "output_tok", "p50_ms", "p95_ms", "dsl_chars", "native_chars")
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for tier in ("iot_light_5", "home_iot_20", "smart_home_50"):
        for path in ("qwen", "qwen-native"):
            file = RUNS / "m2" / f"{path}_{tier}.json"
            if not file.exists():
                print(f"{tier} | {path} | MISSING")
                continue
            d = load(file)
            print(" | ".join([
                tier, path,
                fmt(d.get("exact_match_rate")),
                fmt(d.get("input_tokens_total")),
                fmt(d.get("output_tokens_total")),
                fmt(d.get("latency_ms_p50")),
                fmt(d.get("latency_ms_p95")),
                fmt(d.get("dsl_catalog_chars")),
                fmt(d.get("openai_tools_chars")),
            ]))


def render_m3() -> None:
    print("\n=== M3 repeat=5 (iot_light_5, 50 cases) ===")
    headers = ("path", "exact", "mean_ms", "p50_ms", "p95_ms", "stddev_ms", "input_tok_total", "output_tok_total")
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for path in ("qwen", "qwen-native"):
        file = RUNS / "m3" / f"{path}_iot_light_5_x5.json"
        if not file.exists():
            print(f"{path} | MISSING")
            continue
        d = load(file)
        print(" | ".join([
            path,
            fmt(d.get("exact_match_rate")),
            fmt(d.get("latency_ms_mean")),
            fmt(d.get("latency_ms_p50")),
            fmt(d.get("latency_ms_p95")),
            fmt(d.get("latency_ms_stddev")),
            fmt(d.get("input_tokens_total")),
            fmt(d.get("output_tokens_total")),
        ]))


def render_m4() -> None:
    print("\n=== M4 repair ablation (iot_light_5, 50 cases) ===")
    headers = ("variant", "exact", "valid", "input_tok", "output_tok", "repair_attempts", "repair_successes")
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for variant in ("qwen_repair_off", "qwen_repair_on"):
        file = RUNS / "m4" / f"{variant}.json"
        if not file.exists():
            print(f"{variant} | MISSING")
            continue
        d = load(file)
        print(" | ".join([
            variant,
            fmt(d.get("exact_match_rate")),
            fmt(d.get("syntax_valid_rate")),
            fmt(d.get("input_tokens_total")),
            fmt(d.get("output_tokens_total")),
            fmt(d.get("repair_attempts_total")),
            fmt(d.get("repair_successes_total")),
        ]))


if __name__ == "__main__":
    render_m2()
    render_m3()
    render_m4()
