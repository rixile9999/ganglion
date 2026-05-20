#!/usr/bin/env bash
# Phase 2 BFCL on Qwen3-0.6B (local GPU):
#  - S1b   : grammar masking ablation (mask off vs on) on untuned 0.6B for each category, full 100 cases
#  - S2a   : per-category LoRA SFT, train on 80, eval on 20-case holdout
#  - S2a-full: per-category adapter evaluated on the FULL 100 cases (for direct comparison with Phase 1)
set -euo pipefail

ROOT="/home/hyoseok/workspace/ganglion"
OUT="$ROOT/runs/factory_bfcl/phase2"
PY="/home/hyoseok/miniforge3/envs/constraints/bin/python"
SFT="$ROOT/runs/factory_bfcl/bfcl_sft.py"
EVAL="$ROOT/runs/factory_bfcl/bfcl_eval.py"

cd "$ROOT"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export TOKENIZERS_PARALLELISM=false
export TRANSFORMERS_NO_ADVISORY_WARNINGS=1
export HF_HUB_DISABLE_TELEMETRY=1

CATS="simple_python multiple parallel parallel_multiple irrelevance"

# --- S1b: grammar masking ablation on untuned base (full 100 each, mask off + mask on)
for cat in $CATS ; do
  echo "[$(date +%H:%M:%S)] S1b mask_off $cat" >&2
  $PY "$EVAL" --category "$cat" --split full \
      --out "$OUT/grammar/${cat}_mask_off" \
    > "$OUT/grammar/${cat}_mask_off.log" 2>&1 \
    || echo "[FAIL] S1b mask_off $cat" >&2

  echo "[$(date +%H:%M:%S)] S1b mask_on  $cat" >&2
  $PY "$EVAL" --category "$cat" --split full --grammar-mask \
      --out "$OUT/grammar/${cat}_mask_on" \
    > "$OUT/grammar/${cat}_mask_on.log" 2>&1 \
    || echo "[FAIL] S1b mask_on $cat" >&2
done

# --- S2a: per-category SFT
for cat in $CATS ; do
  ADAPTER_OUT="$OUT/sft/$cat"
  if [ -f "$ADAPTER_OUT/adapter/adapter_config.json" ]; then
    echo "[$(date +%H:%M:%S)] S2a SKIP $cat (adapter exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S2a train $cat" >&2
    $PY "$SFT" --category "$cat" --out "$ADAPTER_OUT" \
      > "$ADAPTER_OUT.train.log" 2>&1 \
      || { echo "[FAIL] S2a train $cat" >&2 ; continue ; }
  fi

  echo "[$(date +%H:%M:%S)] S2a eval-holdout $cat" >&2
  $PY "$EVAL" --category "$cat" --split holdout \
      --adapter "$ADAPTER_OUT/adapter" \
      --out "$ADAPTER_OUT/eval_holdout" \
    > "$ADAPTER_OUT.eval_holdout.log" 2>&1 \
    || echo "[FAIL] S2a eval_holdout $cat" >&2

  echo "[$(date +%H:%M:%S)] S2a eval-full    $cat" >&2
  $PY "$EVAL" --category "$cat" --split full \
      --adapter "$ADAPTER_OUT/adapter" \
      --out "$ADAPTER_OUT/eval_full" \
    > "$ADAPTER_OUT.eval_full.log" 2>&1 \
    || echo "[FAIL] S2a eval_full $cat" >&2
done

echo "[$(date +%H:%M:%S)] phase2 complete" >&2
