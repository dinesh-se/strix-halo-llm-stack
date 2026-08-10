#!/usr/bin/env python3
"""TEST 3 — DS4 + gemma4 co-resident under llama-server router mode.

Reproduces the exact failure shape from 2026-08-06 morning: a ~23k-token
prefill against DS4 while a second model holds memory. That prefill OOM-killed
llama-server twice when the dspark sidecar took GTT to 108.1 GiB and left ~9 GiB
of host RAM. Here the sidecar is gone (DS4 ~98.4 GiB) but gemma4 (~8.7 GiB) is
co-resident instead, landing in the same ~107 GiB band.

Guard thresholds are the ones used successfully all night on 2026-08-06:
abort if GTT > 118 GiB or MemAvailable < 4 GiB.
"""
import json
import sys
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:10098"
GTT = "/sys/class/drm/card1/device/mem_info_gtt_used"
GTT_ABORT_GIB = 118.0
MEM_ABORT_GIB = 4.0

stop = threading.Event()
worst = {"gtt": 0.0, "mem": 999.0}
tripped = []


def gtt_gib():
    with open(GTT) as f:
        return int(f.read()) / 1024 ** 3


def mem_avail_gib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 ** 2
    return -1.0


def guard():
    """Sample memory every second; record extremes and any threshold breach."""
    while not stop.is_set():
        g, m = gtt_gib(), mem_avail_gib()
        worst["gtt"] = max(worst["gtt"], g)
        worst["mem"] = min(worst["mem"], m)
        if g > GTT_ABORT_GIB or m < MEM_ABORT_GIB:
            tripped.append(f"GUARD TRIPPED gtt={g:.1f} mem_avail={m:.1f}")
        time.sleep(1)


def post(path, payload, timeout=1800):
    req = urllib.request.Request(
        BASE + path, json.dumps(payload).encode(),
        {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def models():
    with urllib.request.urlopen(BASE + "/models", timeout=30) as r:
        return {m["id"]: m["status"]["value"] for m in json.load(r)["data"]}


# ~23k tokens of English prose, matching the prompt size that OOM-killed twice.
CHUNK = (
    "The unified memory architecture on Strix Halo exposes a single physical "
    "pool that both the CPU and the integrated GPU address, which means the "
    "usual discrete-GPU intuitions about VRAM budgeting do not transfer. "
    "Allocations made through GTT are backed by ordinary system pages, so "
    "pressure on the graphics translation table is indistinguishable, from the "
    "kernel's point of view, from pressure on anonymous process memory. "
)
# Calibrated 2026-08-06: 165 chunks measured 13,049 tokens (~79 tok/chunk), which
# UNDERSHOT the 23,412-token prefill that actually OOM-killed llama-server twice
# that morning. 300 chunks targets ~23.7k so the test reproduces the real
# failure condition rather than a smaller one.
PROMPT = CHUNK * 300


def main():
    print(f"start: gtt={gtt_gib():.1f} GiB  mem_avail={mem_avail_gib():.1f} GiB")
    print(f"models: {models()}")

    threading.Thread(target=guard, daemon=True).start()

    print("\n-- loading gemma4-12b alongside DS4 --")
    t0 = time.time()
    try:
        post("/models/load", {"model": "gemma4-12b"}, timeout=600)
    except Exception as e:
        print(f"  load call returned: {e} (may be async; polling /models)")
    for _ in range(120):
        st = models()
        if st.get("gemma4-12b") == "loaded":
            break
        time.sleep(5)
    print(f"  after {time.time() - t0:.0f}s: {models()}")
    print(f"  gtt={gtt_gib():.1f} GiB  mem_avail={mem_avail_gib():.1f} GiB")

    print(f"\n-- large prefill against DS4 ({len(PROMPT)} chars) --")
    t0 = time.time()
    d = post("/v1/chat/completions", {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": PROMPT +
                      "\n\nIn one sentence, what is this passage about?"}],
        "max_tokens": 256,
    })
    t = d.get("timings", {})
    print(f"  wall={time.time() - t0:.0f}s  prompt_n={t.get('prompt_n')}  "
          f"PP={t.get('prompt_per_second', 0):.2f} t/s  "
          f"TG={t.get('predicted_per_second', 0):.2f} t/s")

    stop.set()
    time.sleep(1.5)
    print(f"\npeak gtt={worst['gtt']:.1f} GiB   min mem_avail={worst['mem']:.1f} GiB")
    print(f"final models: {models()}")
    if tripped:
        print(f"RESULT: FAIL — {len(tripped)} guard samples breached")
        print("  " + tripped[0])
        return 1
    print("RESULT: PASS — no guard breach, both models resident, prefill completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
