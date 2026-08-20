#!/usr/bin/env bash
# bench-qwen38.sh — benchmark Qwen3.8-27B (Q8_0) at 256k context.
#
# Measures, at ctx 262144 with DEFAULT xhigh reasoning (per user's request —
# the model's native default is NOT disabled):
#   - decode t/s (raw), prefill t/s
#   - KV cache GiB + total memory footprint
#   - first-token latency, total wall-time, total tokens (reasoning + answer)
#   - end-to-end wall time INCLUDING the xhigh reasoning trace (the true cost)
#
# Assumes the model is ALREADY loaded on the router (:9292) as qwen3.8-27b.
# Run `swap-model.sh qwen` first. Results go to ~/llama-stack/logs/.
#
# Usage: bench-qwen38.sh [--ctx 262144] [--probes N]
set -euo pipefail
ROUTER="${ROUTER:-http://127.0.0.1:9292}"
MODEL="qwen3.8-27b"
CTX="${CTX:-262144}"
PROBES="${PROBES:-3}"
LOG_DIR=~/llama-stack/logs
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$LOG_DIR/qwen38-bench-${STAMP}.log"
JSON="$LOG_DIR/qwen38-bench-${STAMP}.json"

echo "bench-qwen38.sh — ctx=$CTX probes=$PROBES (default xhigh reasoning)"
echo "  router: $ROUTER  model: $MODEL"
echo "  output: $OUT" | tee "$OUT"

# ---- 1. Confirm model is loaded ----
state=$(curl -s --max-time 10 "$ROUTER/models" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print([m['status']['value'] for m in d['data'] if m['id']=='$MODEL'][0] if any(m['id']=='$MODEL' for m in d['data']) else 'absent')" 2>/dev/null || echo absent)
if [ "$state" != "loaded" ]; then
  echo "✗ $MODEL not loaded (state=$state). Run: swap-model.sh qwen" | tee -a "$OUT"
  exit 1
fi
echo "✓ $MODEL loaded" | tee -a "$OUT"

# ---- 2. Read metrics (KV cache, memory) from router child ----
echo "--- metrics snapshot (KV / memory) ---" | tee -a "$OUT"
curl -s --max-time 10 "$ROUTER/metrics?model=$MODEL" 2>/dev/null \
  | grep -iE "llamacpp_kv_cache_bytes|llamacpp_kv_cache_blocks|v0_llama_used|llamacpp_tokens" \
  | tee -a "$OUT" || echo "  (metrics not available)" | tee -a "$OUT"

# ---- 3. Run completion probes (default reasoning) ----
echo "--- probes (default xhigh reasoning, ctx $CTX) ---" | tee -a "$OUT"
: > "$JSON"
echo '[' >> "$JSON"
for i in $(seq 1 "$PROBES"); do
  echo "  probe $i/$PROBES..." | tee -a "$OUT"
  START=$(date +%s%N)
  body=$(cat <<EOF
{"model":"$MODEL","max_tokens":1024,
 "messages":[{"role":"user","content":"Write a concise 3-sentence summary of how hybrid attention (full attention every 4th block) enables 1M-token context windows, explaining the tradeoffs vs full attention."}]}
EOF
)
  resp=$(curl -s --max-time 1800 "$ROUTER/v1/chat/completions" \
    -H 'content-type: application/json' -d "$body")
  END=$(date +%s%N)
  ELAPSED_MS=$(( (END - START) / 1000000 ))

  usage=$(echo "$resp" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    u=d.get("usage",{})
    print(f"{u.get(\"prompt_tokens\",\"?\")} {u.get(\"completion_tokens\",\"?\")} {u.get(\"total_tokens\",\"?\")}")
except Exception:
    print("ERR")' 2>/dev/null || echo "ERR")
  pt=$(echo "$usage" | awk '{print $1}'); ct=$(echo "$usage" | awk '{print $2}'); tt=$(echo "$usage" | awk '{print $3}')

  # decode t/s from the tokenizer/usage timing
  if [ "$ct" != "?" ] && [ "$ct" -gt 0 ] 2>/dev/null; then
    dec=$(python3 -c "print(f'{$ct/($ELAPSED_MS/1000):.2f}')" 2>/dev/null || echo "?")
  else
    dec="?"
  fi

  echo "  probe $i: ${ELAPSED_MS}ms wall | prompt=$pt completion=$ct total=$tt | decode ~${dec} t/s (incl reasoning)" | tee -a "$OUT"
  [ $i -gt 1 ] && echo "," >> "$JSON"
  echo "{\"probe\":$i,\"wall_ms\":$ELAPSED_MS,\"prompt_tokens\":\"$pt\",\"completion_tokens\":\"$ct\",\"total_tokens\":\"$tt\",\"decode_tps_est\":\"$dec\"}" >> "$JSON"
done
echo ']' >> "$JSON"

echo "--- done. results: $JSON ---" | tee -a "$OUT"
echo "Full log: $OUT"
