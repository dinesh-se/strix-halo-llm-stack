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
#
# 🔴 ROUTER LIFECYCLE ROUTES ARE BODY-FORM ONLY. MEASURED 2026-08-07:
#     POST /models/{id}/unload  -> 404 File Not Found      (what this script used
#     POST /models/{id}/load    -> 404 File Not Found       to send — a silent no-op)
#     POST /models/unload  {"model": id}  -> 200 | 400 "model is not running"
#     POST /models/load    {"model": id}  -> 200 | 400 "model is already running"
#                                          | 404 for an unknown id
# The old path form was swallowed by `|| true`, so `swap-model.sh ds4` NEVER
# evicted anything. On 2026-08-07 a Hermes /model switch autoloaded DS4 on top of
# a resident ornith and the kernel OOM-killed ornith 90s later. Every failure
# path below is now FATAL for exactly that reason: never load a second heavy
# model after an eviction we could not confirm.
set -euo pipefail
ROUTER="${ROUTER:-http://127.0.0.1:9292}"
ORNITH="ornith-1.0-35b"
DS4="deepseek-v4-flash"

# Sets REPLY_CODE / REPLY_BODY. Never fails the script itself — callers decide.
REPLY_CODE=""
REPLY_BODY=""
_post_model() {  # $1=load|unload  $2=model-id
  local out
  out="$(curl -s -w $'\n%{http_code}' --max-time 30 \
           -X POST "$ROUTER/models/$1" \
           -H 'content-type: application/json' \
           -d "{\"model\":\"$2\"}" 2>/dev/null || true)"
  REPLY_CODE="${out##*$'\n'}"
  REPLY_BODY="${out%$'\n'*}"
}

# -> "<value> <failed>", e.g. "loaded 0" / "unloaded 1" / "unknown 0".
# ⚠️ `status.failed` is STICKY from the LAST load attempt, not a liveness signal:
# ornith-1.0-35b still reports failed=true exit_code=1 from the 2026-08-07 OOM
# kill while sitting perfectly happily at value=unloaded. Only `value` describes
# what is running now — see wait_loaded() for the one place `failed` is used.
_model_status() {  # $1=model-id
  local id="$1"
  curl -s --max-time 10 "$ROUTER/models" \
    | python3 -c 'import sys,json
d=json.load(sys.stdin)
try:
    st = [m["status"] for m in d["data"] if m["id"]==sys.argv[1]][0]
except (IndexError, KeyError):
    # NOT "unloaded": an id the router has never heard of is a typo, and
    # reporting it as safely-evicted would let the OTHER heavy model load on
    # top of a still-resident one. That is precisely the 2026-08-07 OOM.
    print("absent 0"); raise SystemExit
print(st.get("value", "unknown"), 1 if st.get("failed") else 0)' "$id" 2>/dev/null \
    || echo "unknown 0"
}

_model_state() {  # $1=model-id -> "loaded"|"loading"|"unloaded"|"absent"|"unknown"
  local st; st="$(_model_status "$1")"; echo "${st%% *}"
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
  local st state="" failed="0" saw_loading=0
  for i in $(seq 1 240); do
    st="$(_model_status "$id")"; state="${st%% *}"; failed="${st##* }"
    [ "$state" = "loading" ] && saw_loading=1
    if [ "$state" = "loaded" ]; then
      echo "  ✓ $label loaded."
      return 0
    fi
    # Fail fast on a load that died (OOM mid-load is the realistic case), but
    # only once OUR load has actually been seen to start — otherwise the sticky
    # `failed` from a PREVIOUS attempt would abort us on the first iteration.
    if [ "$saw_loading" = "1" ] && [ "$state" = "unloaded" ] && [ "$failed" = "1" ]; then
      echo "  ✗ $label FAILED to load (router reports failed). Check:"
      echo "    journalctl --user -u llama-router  /  journalctl -k | grep -i oom"
      return 1
    fi
    sleep 5
  done
  echo "  ✗ TIMEOUT after $((240*5))s; $label state=$state"; return 1
}

unload() {  # $1=id $2=label — returns non-zero unless the model is CONFIRMED gone
  local id="$1" label="$2" state
  state="$(_model_state "$id")"
  case "$state" in
    loaded|loading) ;;
    unloaded) echo "  ✓ $label already unloaded."; return 0 ;;
    absent) echo "  ✗ '$id' is not a model the router knows — typo in this script?"
            echo "    Cannot confirm eviction, so refusing to load anything on top of it."
            return 1 ;;
    *) echo "  ✗ cannot read $label state (got '$state') — refusing to proceed."
       return 1 ;;
  esac

  echo "  unloading $label ($id)..."
  _post_model unload "$id"
  case "$REPLY_CODE" in
    200|202|204) ;;
    400)
      # Benign race: something else unloaded it between our read and our POST.
      case "$REPLY_BODY" in
        *"not running"*) echo "  ✓ $label was already unloaded."; return 0 ;;
        *) echo "  ✗ unload of $label rejected: HTTP 400 $REPLY_BODY"; return 1 ;;
      esac
      ;;
    *) echo "  ✗ unload of $label failed: HTTP ${REPLY_CODE:-none} $REPLY_BODY"
       return 1 ;;
  esac

  # The router's unload is ASYNC — it returns before the child has exited. This
  # wait is what the watchdog's recover() was missing on 2026-08-07, where an
  # immediate reload got 400 "model is already running" and recovery "FAILED".
  # Require an explicit "unloaded"; "unknown" (router unreachable) must not read
  # as success.
  for i in $(seq 1 60); do
    state="$(_model_state "$id")"
    [ "$state" = "unloaded" ] && { echo "  ✓ $label unloaded."; return 0; }
    sleep 3
  done
  echo "  ✗ $label STILL LOADED after 180s (state=$state) — refusing to load a"
  echo "    second heavy model on top of it. Check: journalctl --user -u llama-router"
  return 1
}

load() {  # $1=id $2=label
  local id="$1" label="$2"
  echo "  loading $label ($id)..."
  _post_model load "$id"
  case "$REPLY_CODE" in
    200|202|204) return 0 ;;
    400)
      case "$REPLY_BODY" in
        *"already running"*) echo "  ✓ $label was already loaded."; return 0 ;;
        *) echo "  ✗ load of $label rejected: HTTP 400 $REPLY_BODY"; return 1 ;;
      esac
      ;;
    404) echo "  ✗ load of $label failed: HTTP 404 — is '$id' defined in models.ini?"
         return 1 ;;
    *) echo "  ✗ load of $label failed: HTTP ${REPLY_CODE:-none} $REPLY_BODY"
       return 1 ;;
  esac
}

case "${1:-status}" in
  ds4)
    echo "Swapping to deepseek-v4-flash (general chat / web / scrape)..."
    unload "$ORNITH" "ornith" || exit 1
    load "$DS4" "deepseek-v4-flash" || exit 1
    wait_loaded "$DS4" "deepseek-v4-flash" || exit 1
    echo; status
    ;;
  ornith)
    echo "Swapping to ornith-1.0-35b (daytime daily driver: coding/planning)..."
    unload "$DS4" "deepseek-v4-flash" || exit 1
    load "$ORNITH" "ornith" || exit 1
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
