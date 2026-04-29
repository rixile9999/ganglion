#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

LIMIT=50
TIER=iot_light_5
OUT="runs/m4"
mkdir -p "$OUT"

# Repair off (baseline)
echo "=== M4 starting: qwen_repair_off ==="
python -m ganglion.eval.runner \
  --llm qwen --tier "$TIER" --limit "$LIMIT" \
  > "$OUT/qwen_repair_off.json"
echo "=== M4 done: qwen_repair_off ==="

# Repair on
echo "=== M4 starting: qwen_repair_on ==="
python -m ganglion.eval.runner \
  --llm qwen --tier "$TIER" --limit "$LIMIT" \
  --repair --repair-max-attempts 1 \
  > "$OUT/qwen_repair_on.json"
echo "=== M4 done: qwen_repair_on ==="

echo "=== M4 ALL COMPLETE ==="
