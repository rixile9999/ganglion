#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

LIMIT=50
REPEAT=5
TIER=iot_light_5
OUT="runs/m3"
mkdir -p "$OUT"

for path in qwen qwen-native; do
  label="${path}_${TIER}_x${REPEAT}"
  echo "=== M3 starting: $label ==="
  python -m ganglion.eval.runner \
    --llm "$path" --tier "$TIER" --limit "$LIMIT" --repeat "$REPEAT" \
    > "$OUT/${label}.json"
  echo "=== M3 done: $label ==="
done
echo "=== M3 ALL COMPLETE ==="
