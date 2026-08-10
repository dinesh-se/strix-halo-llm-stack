#!/usr/bin/env python3
"""
Depth-aware ubatch calibration, kyuz0 methodology, run against OUR image.

Why not llama-cockpit: cockpit's calibrate-ubatch runs inside a kyuz0 toolbox
container, so it reports the optimum for THAT build. ubatch optima are
build- and driver-specific -- the whole gemma4 regression came from a Vulkan
submission-threshold change between two llama.cpp builds (#25240). We need the
optimum for the binary we actually serve with, so we exec `llama bench` inside
the running llama-swap container.

Prefill series: with `d` tokens already in KV, time appending a 2048-token
chunk. That is the number that matters for us -- our workload is prefill
dominated (27b: 22 min of prefill for 2,847 output tokens in a 94 min window).

Usage:
  python3 ubatch_curve.py gemma4-12b
  python3 ubatch_curve.py qwen3.6-27b
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

HF = "/root/.cache/huggingface/hub"

# (gguf path in container, cache-type, batch) -- mirrors the live llama-swap yaml
MODELS = {
    "gemma4-12b": dict(
        model=f"{HF}/models--unsloth--gemma-4-12B-it-qat-GGUF/snapshots/"
              f"980b060c40a8539ac159e0501a3e0f66a6365af3/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf",
        ctk="q8_0", ctv="q8_0", batch=2048,
    ),
    "qwen3.6-27b": dict(
        model=f"{HF}/models--unsloth--Qwen3.6-27B-MTP-GGUF/snapshots/"
              f"5cb35eb3dcbf52dbce5f87dbc64df6aaffadcace/Qwen3.6-27B-Q6_K.gguf",
        ctk="bf16", ctv="bf16", batch=2048,
    ),
    "qwen3.6-35b": dict(
        model=f"{HF}/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/"
              f"5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-Q8_0.gguf",
        ctk="q8_0", ctv="q8_0", batch=2048,
    ),
}

UBATCHES = [256, 512, 1024, 2048]
DEPTHS = [0, 8192, 32768]
CHUNK = 2048       # prompt tokens appended at each depth
REPS = 3


def gpu_busy():
    try:
        with open("/sys/class/drm/card1/device/gpu_busy_percent") as f:
            return int(f.read().strip())
    except OSError:
        return -1


def run_bench(cfg, ub, depth):
    """One llama-bench prefill point. Returns (t/s mean, stddev) or None."""
    cmd = [
        "docker", "exec", "llama-swap", "/app/llama", "bench",
        "-m", cfg["model"],
        "-p", str(CHUNK), "-n", "0", "-d", str(depth),
        "-b", str(cfg["batch"]), "-ub", str(ub),
        "-ctk", cfg["ctk"], "-ctv", cfg["ctv"],
        "-fa", "on", "-ngl", "999",
        "-r", str(REPS), "-o", "jsonl", "--no-warmup",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        print(f"    FAILED rc={proc.returncode}: {proc.stderr.strip()[-300:]}")
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        rec = json.loads(line)
        if rec.get("n_prompt", 0) > 0:
            return rec["avg_ts"], rec.get("stddev_ts", 0.0)
    return None


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "gemma4-12b"
    cfg = MODELS[name]

    busy = gpu_busy()
    print(f"# ubatch curve: {name}")
    print(f"# started {datetime.now(timezone.utc).isoformat()}  gpu_busy={busy}%")
    if busy > 25:
        print(f"# WARNING: GPU is {busy}% busy -- results will be polluted by "
              f"concurrent serving. Contention shows up as a low outlier.")
    print()

    results = {}
    for ub in UBATCHES:
        for depth in DEPTHS:
            t0 = time.time()
            out = run_bench(cfg, ub, depth)
            el = time.time() - t0
            if out:
                results[(ub, depth)] = out
                print(f"  ub={ub:<5} d={depth:<6} {out[0]:8.2f} t/s "
                      f"(±{out[1]:.2f})  [{el:.0f}s, busy={gpu_busy()}%]")
            else:
                print(f"  ub={ub:<5} d={depth:<6} FAILED  [{el:.0f}s]")

    print(f"\n## {name} — prefill t/s ({CHUNK}-token chunk appended at depth)\n")
    hdr = "| ubatch | " + " | ".join(f"d={d}" for d in DEPTHS) + " |"
    print(hdr)
    print("|---" * (len(DEPTHS) + 1) + "|")
    for ub in UBATCHES:
        cells = []
        for d in DEPTHS:
            r = results.get((ub, d))
            cells.append(f"{r[0]:.1f}" if r else "—")
        print(f"| {ub} | " + " | ".join(cells) + " |")

    # best ubatch by mean across depths
    scored = []
    for ub in UBATCHES:
        vals = [results[(ub, d)][0] for d in DEPTHS if (ub, d) in results]
        if vals:
            scored.append((sum(vals) / len(vals), ub))
    if scored:
        scored.sort(reverse=True)
        print(f"\nBest depth-averaged ubatch: **{scored[0][1]}** "
              f"({scored[0][0]:.1f} t/s avg)")
        for avg, ub in scored:
            delta = (avg - scored[0][0]) / scored[0][0] * 100
            print(f"  ub={ub:<5} {avg:7.1f} t/s  {delta:+.1f}%")


if __name__ == "__main__":
    main()
