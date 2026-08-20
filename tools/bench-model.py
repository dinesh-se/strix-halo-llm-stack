"""Like-for-like DS4 throughput bench vs the 2026-08-12 baseline (268.98 PP / 19.48 TG @ pp2053).

Uses the CHILD /completion endpoint with id_slot pinned to the last slot, so it
cannot LRU-steal a cached prefix from a conversation slot (watchdog gotcha #5).
Greedy sampling so runs are comparable.
"""
import json, sys, time, urllib.request

PORT = sys.argv[1]
TARGET_PP = int(sys.argv[2]) if len(sys.argv) > 2 else 2053
TRIALS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
N_PREDICT = 128
# pin to the HIGHEST slot (the watchdog's probe slot) so we never destroy a
# conversation's cached prefix -- works for any --parallel value.
import urllib.request as _u
SLOT = max(x["id"] for x in __import__("json").load(_u.urlopen(f"http://127.0.0.1:{PORT}/slots", timeout=30)))

def post(path, payload, timeout=600):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def tokenize(text):
    return post("/tokenize", {"content": text})["tokens"]

# Build a prompt of almost exactly TARGET_PP tokens out of non-repetitive prose,
# so ngram/cache tricks cannot inflate the number.
seed = ("The quantisation format determines how many bytes must be read from "
        "memory for every token generated, which on a unified-memory machine is "
        "the dominant cost at batch size one. Prefill by contrast is compute "
        "bound and scales with the ubatch size. ")
words = (seed * 400).split()
lo, hi = 1, len(words)
while lo < hi:
    mid = (lo + hi) // 2
    if len(tokenize(" ".join(words[:mid]))) < TARGET_PP:
        lo = mid + 1
    else:
        hi = mid
prompt = " ".join(words[:lo])
actual_pp = len(tokenize(prompt))
print(f"prompt built: {actual_pp} tokens (target {TARGET_PP})\n")

rows = []
for i in range(TRIALS):
    # unique prefix per trial -> forces a real prefill, no cache replay
    p = f"[trial {i} {'x'*i}] " + prompt
    t0 = time.time()
    r = post("/completion", {
        "prompt": p, "n_predict": N_PREDICT, "temperature": 0, "top_k": 1,
        "cache_prompt": False, "id_slot": SLOT,
        # force the full N_PREDICT tokens -- without this a greedy run on a
        # repetitive prompt emits EOS after ~5 tokens and the decode figure is noise
        "ignore_eos": True,
    })
    wall = time.time() - t0
    tm = r["timings"]
    rows.append((tm["prompt_n"], tm["prompt_per_second"], tm["predicted_n"],
                 tm["predicted_per_second"], wall))
    print(f"  trial {i+1}: PP {tm['prompt_n']:>5} tok @ {tm['prompt_per_second']:7.2f} t/s   "
          f"TG {tm['predicted_n']:>4} tok @ {tm['predicted_per_second']:6.2f} t/s   wall {wall:5.1f}s")

pp = sorted(r[1] for r in rows)[len(rows)//2]
tg = sorted(r[3] for r in rows)[len(rows)//2]
print(f"\nMEDIAN: prefill {pp:.2f} t/s | decode {tg:.2f} t/s")
print(f"BASELINE 2026-08-12 @pp2053: prefill 268.98 | decode 19.48")
print(f"DELTA:  prefill {pp/268.98*100:5.1f}% of baseline | decode {tg/19.48*100:5.1f}% of baseline")
