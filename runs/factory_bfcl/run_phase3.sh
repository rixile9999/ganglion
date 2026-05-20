#!/usr/bin/env bash
# Phase 3 BFCL: paraphrase + synth → SFT v1.5 → bootstrap → SFT v2 → DPO.
# Per-category, sequential. Total estimated wall ~3.5 hours.
set -euo pipefail

ROOT="/home/hyoseok/workspace/ganglion"
PHASE2="$ROOT/runs/factory_bfcl/phase2"
PHASE3="$ROOT/runs/factory_bfcl/phase3"
PY="/home/hyoseok/miniforge3/envs/constraints/bin/python"
mkdir -p "$PHASE3"

cd "$ROOT"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export TOKENIZERS_PARALLELISM=false
export HF_HUB_DISABLE_TELEMETRY=1
export GANGLION_TEACHER_MODEL=qwen3.6-plus

CATS="simple_python multiple parallel parallel_multiple irrelevance"

# ===== S3a paraphrase (DashScope teacher; API only, no GPU) =====
for cat in $CATS ; do
  out="$PHASE3/paraphrase/$cat"
  if [ -f "$out/paraphrased.jsonl" ]; then
    echo "[$(date +%H:%M:%S)] S3a skip $cat (exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3a paraphrase $cat" >&2
    $PY runs/factory_bfcl/teacher_augment.py --mode paraphrase --category "$cat" \
        -k 4 --out "$out" > "$PHASE3/paraphrase/$cat.log" 2>&1 \
      || echo "[FAIL] S3a $cat" >&2
  fi
done

# ===== S3b synth (DashScope teacher; API only, no GPU) =====
for cat in $CATS ; do
  out="$PHASE3/synth/$cat"
  if [ -f "$out/synth.jsonl" ]; then
    echo "[$(date +%H:%M:%S)] S3b skip $cat (exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3b synth $cat" >&2
    $PY runs/factory_bfcl/teacher_augment.py --mode synth --category "$cat" \
        -n 50 --out "$out" > "$PHASE3/synth/$cat.log" 2>&1 \
      || echo "[FAIL] S3b $cat" >&2
  fi
done

# ===== S3c SFT v1.5 (GPU; train on original ∪ paraphrase ∪ synth) =====
for cat in $CATS ; do
  out="$PHASE3/sft_v1_5/$cat"
  aux=("$PHASE3/paraphrase/$cat/paraphrased.jsonl" "$PHASE3/synth/$cat/synth.jsonl")
  if [ -f "$out/adapter/adapter_config.json" ]; then
    echo "[$(date +%H:%M:%S)] S3c skip $cat (adapter exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3c train v1.5 $cat" >&2
    $PY runs/factory_bfcl/bfcl_sft_v2.py --category "$cat" --out "$out" --aux "${aux[@]}" \
      > "$out.train.log" 2>&1 || { echo "[FAIL] S3c $cat" >&2 ; continue ; }
  fi
  echo "[$(date +%H:%M:%S)] S3c eval_holdout v1.5 $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split holdout \
      --adapter "$out/adapter" --out "$out/eval_holdout" \
    > "$out.eval_holdout.log" 2>&1 || echo "[FAIL] S3c eval_holdout $cat" >&2
  echo "[$(date +%H:%M:%S)] S3c eval_full v1.5 $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split full \
      --adapter "$out/adapter" --out "$out/eval_full" \
    > "$out.eval_full.log" 2>&1 || echo "[FAIL] S3c eval_full $cat" >&2
done

# ===== S3d bootstrap (sample N=4 from v1.5; keep ast_match=True) =====
for cat in $CATS ; do
  adapter="$PHASE3/sft_v1_5/$cat/adapter"
  out="$PHASE3/bootstrap/$cat"
  if [ ! -f "$adapter/adapter_config.json" ]; then
    echo "[skip] S3d $cat (no v1.5 adapter)" >&2
    continue
  fi
  mkdir -p "$out"
  if [ -f "$out/augmented_train.jsonl" ]; then
    echo "[$(date +%H:%M:%S)] S3d skip $cat (exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3d bootstrap $cat" >&2
    $PY runs/factory_bfcl/bfcl_bootstrap.py --category "$cat" --adapter "$adapter" \
        --out "$out/augmented_train.jsonl" --n-samples 4 --temperature 0.7 \
      > "$out.log" 2>&1 || echo "[FAIL] S3d $cat" >&2
  fi
done

# ===== S3e SFT v2 (train on original ∪ paraphrase ∪ synth ∪ bootstrap-pass) =====
for cat in $CATS ; do
  out="$PHASE3/sft_v2/$cat"
  aux=("$PHASE3/paraphrase/$cat/paraphrased.jsonl"
       "$PHASE3/synth/$cat/synth.jsonl"
       "$PHASE3/bootstrap/$cat/augmented_train.jsonl")
  if [ -f "$out/adapter/adapter_config.json" ]; then
    echo "[$(date +%H:%M:%S)] S3e skip $cat (adapter exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3e train v2 $cat" >&2
    $PY runs/factory_bfcl/bfcl_sft_v2.py --category "$cat" --out "$out" --aux "${aux[@]}" \
      > "$out.train.log" 2>&1 || { echo "[FAIL] S3e $cat" >&2 ; continue ; }
  fi
  echo "[$(date +%H:%M:%S)] S3e eval_holdout v2 $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split holdout \
      --adapter "$out/adapter" --out "$out/eval_holdout" \
    > "$out.eval_holdout.log" 2>&1 || echo "[FAIL] S3e eval_holdout $cat" >&2
  echo "[$(date +%H:%M:%S)] S3e eval_full v2 $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split full \
      --adapter "$out/adapter" --out "$out/eval_full" \
    > "$out.eval_full.log" 2>&1 || echo "[FAIL] S3e eval_full $cat" >&2
done

# ===== S3f DPO pairs =====
for cat in $CATS ; do
  v2_adapter="$PHASE3/sft_v2/$cat/adapter"
  out_pairs="$PHASE3/dpo_pairs/$cat/pairs.jsonl"
  mkdir -p "$(dirname "$out_pairs")"
  if [ ! -f "$v2_adapter/adapter_config.json" ]; then
    echo "[skip] S3f $cat (no v2 adapter)" >&2
    continue
  fi
  if [ -f "$out_pairs" ]; then
    echo "[$(date +%H:%M:%S)] S3f skip $cat (pairs exist)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3f pairs $cat" >&2
    $PY runs/factory_bfcl/bfcl_dpo.py pairs --category "$cat" --adapter "$v2_adapter" \
        --out "$out_pairs" --n-samples 4 --temperature 0.7 \
      > "$PHASE3/dpo_pairs/$cat.log" 2>&1 || echo "[FAIL] S3f $cat" >&2
  fi
done

# ===== S3g DPO train + eval =====
for cat in $CATS ; do
  v2_adapter="$PHASE3/sft_v2/$cat/adapter"
  pairs="$PHASE3/dpo_pairs/$cat/pairs.jsonl"
  out="$PHASE3/dpo/$cat"
  if [ ! -f "$pairs" ] || [ ! -s "$pairs" ]; then
    echo "[skip] S3g $cat (no pairs)" >&2
    continue
  fi
  mkdir -p "$out"
  if [ -f "$out/adapter/adapter_config.json" ]; then
    echo "[$(date +%H:%M:%S)] S3g skip $cat (adapter exists)" >&2
  else
    echo "[$(date +%H:%M:%S)] S3g dpo $cat" >&2
    $PY runs/factory_bfcl/bfcl_dpo.py train --category "$cat" --pairs "$pairs" \
        --adapter "$v2_adapter" --out "$out" --epochs 1 --beta 0.1 \
      > "$out.train.log" 2>&1 || { echo "[FAIL] S3g $cat" >&2 ; continue ; }
  fi
  echo "[$(date +%H:%M:%S)] S3g eval_holdout dpo $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split holdout \
      --adapter "$out/adapter" --out "$out/eval_holdout" \
    > "$out.eval_holdout.log" 2>&1 || echo "[FAIL] S3g eval_holdout $cat" >&2
  echo "[$(date +%H:%M:%S)] S3g eval_full dpo $cat" >&2
  $PY runs/factory_bfcl/bfcl_eval.py --category "$cat" --split full \
      --adapter "$out/adapter" --out "$out/eval_full" \
    > "$out.eval_full.log" 2>&1 || echo "[FAIL] S3g eval_full $cat" >&2
done

echo "[$(date +%H:%M:%S)] phase3 complete" >&2
