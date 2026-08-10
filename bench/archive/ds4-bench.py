#!/usr/bin/env python3
"""DS4 realistic benchmark — replicate handoff protocol (prefill/decode on ~19K prompt)."""
import json, time, urllib.request, sys

URL = "http://127.0.0.1:10097/completion"
REF = "/home/dinesh-se/observability/stack/llama-watchdog/watchdog.py"

# --- build prompt: real code + commentary, padded to ~19K tokens ---
with open(REF) as f:
    code = f.read()

commentary = (
    "\n\nTASK: Review the attached llama-watchdog script for correctness and reliability. "
    "Focus on: (1) restart/backoff logic and its handling of transient failures, "
    "(2) whether the health-check and process-supervision paths correctly distinguish "
    "crash-loops from slow startups, (3) any race conditions between the watchdog and "
    "the monitored service's own signal handling. Give concrete line-level findings "
    "and propose minimal patches for the three highest-priority issues.\n\n"
)

prompt = code + commentary

# pad to ~19K tokens (~3.9 chars/token for this code-heavy mix)
TARGET_TOKENS = 19000
pad_units = max(0, (TARGET_TOKENS * 4) - len(prompt))
filler = (
    "The watchdog must remain robust under partial failures. "
    "Consideration of exit codes, signal propagation, and supervisor state "
    "transitions is essential for production reliability. "
) * (pad_units // len("The watchdog must remain robust under partial failures. "
    "Consideration of exit codes, signal propagation, and supervisor state "
    "transitions is essential for production reliability. "))
prompt = prompt + "\n" + filler

# --- send request ---
def run(tag, cache_prompt):
    body = json.dumps({
        "prompt": prompt,
        "n_predict": 128,
        "temperature": 0,
        "cache_prompt": cache_prompt,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read())
    wall = time.time() - t0
    tt = data.get("timings", {})
    print(f"[{tag}] wall={wall:.2f}s  prompt_n={tt.get('prompt_n')} "
          f"predicted_n={tt.get('predicted_n')}  prefill={tt.get('prompt_per_second'):.2f} tok/s  "
          f"decode={tt.get('predicted_per_second'):.2f} tok/s  "
          f"prompt_ms={tt.get('prompt_ms'):.0f}  pred_ms={tt.get('predicted_ms'):.0f}")
    return data

# --- KV-cache-reuse test: same prompt again, cache_prompt=True ---
# first call populates the reusable cache; second call should skip prefill
run("pass-1 (cache_prompt=False)", False)
run("pass-2 (cache_prompt=True, SAME prompt)", True)
