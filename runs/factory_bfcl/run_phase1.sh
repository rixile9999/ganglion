#!/usr/bin/env bash
# Phase 1 BFCL on Qwen3-0.6B (DashScope).
#  - M1' baseline: untuned 0.6B, DSL across 5 categories + native across 4 (no irrelevance native)
#  - M4' repair : DSL + --repair --repair-max-attempts 1 across 5 categories
# Per-category: 100 cases (matches existing M1'~M5' reports).
set -euo pipefail

ROOT="/home/hyoseok/workspace/ganglion"
OUT="$ROOT/runs/factory_bfcl/phase1"
PY="/home/hyoseok/miniforge3/envs/constraints/bin/python"
export GANGLION_MODEL=qwen3-0.6b

run_dsl () {
  local cat=$1 mode=$2 extra=${3:-}
  local dest="$OUT/$mode"
  mkdir -p "$dest"
  echo "[$(date +%H:%M:%S)] start $mode/$cat $extra" >&2
  $PY -m ganglion.eval.runner --llm qwen --bfcl "$cat" --bfcl-per-category 100 \
       --bfcl-output "$dest/${cat}_dsl_cases.jsonl" $extra \
       > "$dest/${cat}_dsl_summary.json" 2> "$dest/${cat}_dsl.log" \
    || echo "[FAIL] $mode dsl $cat" >&2
  echo "[$(date +%H:%M:%S)] done  $mode/$cat dsl" >&2
}

run_native () {
  local cat=$1
  local dest="$OUT/baseline"
  mkdir -p "$dest"
  echo "[$(date +%H:%M:%S)] start native/$cat" >&2
  $PY -m ganglion.eval.runner --llm qwen-native --bfcl "$cat" --bfcl-per-category 100 \
       --bfcl-output "$dest/${cat}_native_cases.jsonl" \
       > "$dest/${cat}_native_summary.json" 2> "$dest/${cat}_native.log" \
    || echo "[FAIL] native $cat" >&2
  echo "[$(date +%H:%M:%S)] done  native/$cat" >&2
}

# M1' baseline DSL: 5 categories (irrelevance needs allow-empty-calls)
run_dsl simple_python      baseline
run_dsl multiple           baseline
run_dsl parallel           baseline
run_dsl parallel_multiple  baseline
run_dsl irrelevance        baseline "--bfcl-allow-empty-calls"

# M1' baseline native: 4 callable categories (irrelevance not meaningful via native)
run_native simple_python
run_native multiple
run_native parallel
run_native parallel_multiple

# M4' repair DSL: same 5 categories with --repair
run_dsl simple_python      repair "--repair --repair-max-attempts 1"
run_dsl multiple           repair "--repair --repair-max-attempts 1"
run_dsl parallel           repair "--repair --repair-max-attempts 1"
run_dsl parallel_multiple  repair "--repair --repair-max-attempts 1"
run_dsl irrelevance        repair "--repair --repair-max-attempts 1 --bfcl-allow-empty-calls"

echo "[$(date +%H:%M:%S)] phase1 complete" >&2
