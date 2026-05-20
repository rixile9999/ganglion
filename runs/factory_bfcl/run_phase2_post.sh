#!/usr/bin/env bash
# Run post-correction on each category's SFT-full eval, then aggregate.
set -euo pipefail
ROOT="/home/hyoseok/workspace/ganglion"
OUT="$ROOT/runs/factory_bfcl/phase2/post_corr"
PY="/home/hyoseok/miniforge3/envs/constraints/bin/python"
mkdir -p "$OUT"

cd "$ROOT"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"

for cat in simple_python multiple parallel parallel_multiple irrelevance ; do
  CASES="$ROOT/runs/factory_bfcl/phase2/sft/$cat/eval_full/cases.jsonl"
  if [ ! -f "$CASES" ]; then
    echo "[skip] $cat (no SFT eval_full)" >&2
    continue
  fi
  $PY "$ROOT/runs/factory_bfcl/post_correction.py" \
    --category "$cat" --cases "$CASES" --out "$OUT/$cat" \
    > "$OUT/$cat.log" 2>&1 || echo "[FAIL] post_corr $cat" >&2
done

$PY "$ROOT/runs/factory_bfcl/aggregate.py"
