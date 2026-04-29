#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

LIMIT=50
OUT="runs/m2"
mkdir -p "$OUT"

for tier in iot_light_5 home_iot_20 smart_home_50; do
  for path in qwen qwen-native; do
    label="${path}_${tier}"
    echo "=== M2 starting: $label ==="
    python -m ganglion.eval.runner \
      --llm "$path" --tier "$tier" --limit "$LIMIT" \
      > "$OUT/${label}.json"
    echo "=== M2 done: $label ==="
  done
done
echo "=== M2 ALL COMPLETE ==="
