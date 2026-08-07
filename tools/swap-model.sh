#!/usr/bin/env bash
# swap-model.sh — manually swap the resident heavy model on the llama.cpp router.
#
# The router (:9292) hosts gemma4-e4b (always resident, fast aux) plus ONE of
# the two heavy models — they CANNOT coexist (ornith 34.4 + ds4 98.4 + gemma
# 4.9 = 137 GiB > 120 GB VRAM cap).
#
#   ornith  -> daytime daily driver (complex tasks, planning, coding)
#   ds4     -> the OTHER model (general chat, web search, scrape)
#
# Usage: swap-model.sh {ds4|ornith|status}
set -euo pipefail
ROUTER="${ROUTER:-http://127.0.0.1:9292}"
ORNITH="ornith-1.0-35b"
DS4="deepseek-v4-flash"

_model_state() {  # $1=model-id -> "loaded"|"unloaded"|"error"|"unknown"
  local id="$1"
  curl -s --max-time 10 "$ROUTER/models" \
    | python3 -c 'import sys,json
d=json.load(sys.stdin)
try:
    print([m["status"]["value"] for m in d["data"] if m["id"]==__import__("sys").argv[1]][0])
except (IndexError, KeyError):
    print("unloaded")' "$id" 2>/dev/null || echo "unknown"
}

status() {
  echo "Resident models on $ROUTER:"
  curl -s --max-time 10 "$ROUTER/models" \
    | python3 -c 'import sys,json
d=json.load(sys.stdin)
for m in sorted(d["data"], key=lambda x: x["id"]):
    print("  {:22} {}".format(m["id"], m["status"]["value"]))' 2>/dev/null \
    || echo "  (could not query router)"
}

wait_loaded() {  # $1=id $2=label
  local id="$1" label="$2"
  echo "  waiting for $label ($id) to load..."
  local state=""
  for i in $(seq 1 240); do
    state="$(_model_state "$id")"
    if [ "$state" = "loaded" ]; then
      echo "  ✓ $label loaded."
      return 0
    fi
    if [ "$state" = "error" ]; then
      echo "  ✗ $label FAILED to load. Check router logs."; return 1
    fi
    sleep 5
  done
  echo "  ✗ TIMEOUT after $((240*5))s; $label state=$state"; return 1
}

unload() {  # $1=id $2=label
  local id="$1" label="$2"
  echo "  unloading $label ($id)..."
  curl -s --max-time 30 -X POST "$ROUTER/models/$id/unload" >/dev/null 2>&1 || true
  local state="loaded"
  for i in $(seq 1 60); do
    state="$(_model_state "$id")"
    [ "$state" != "loaded" ] && { echo "  ✓ $label unloaded."; return 0; }
    sleep 3
  done
  echo "  ⚠ $label may still be loaded (state=$state)"; return 0
}

case "${1:-status}" in
  ds4)
    echo "Swapping to deepseek-v4-flash (general chat / web / scrape)..."
    unload "$ORNITH" "ornith"
    curl -s --max-time 30 -X POST "$ROUTER/models/$DS4/load" >/dev/null 2>&1 || true
    wait_loaded "$DS4" "deepseek-v4-flash" || exit 1
    echo; status
    ;;
  ornith)
    echo "Swapping to ornith-1.0-35b (daytime daily driver: coding/planning)..."
    unload "$DS4" "deepseek-v4-flash"
    curl -s --max-time 30 -X POST "$ROUTER/models/$ORNITH/load" >/dev/null 2>&1 || true
    wait_loaded "$ORNITH" "ornith" || exit 1
    echo; status
    ;;
  status)
    status
    ;;
  *)
    echo "Usage: swap-model.sh {ds4|ornith|status}"
    echo "  ds4    -> load deepseek-v4-flash (evicts ornith)"
    echo "  ornith -> load ornith-1.0-35b (evicts ds4)"
    echo "  status -> show which heavy model is resident"
    exit 1
    ;;
esac
