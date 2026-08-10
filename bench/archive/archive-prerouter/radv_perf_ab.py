#!/usr/bin/env python3
"""
Is kyuz0's vulkan-radv-performance actually faster on OUR models?

Earlier I concluded backend choice was worth ~0% from kyuz0's published data —
but that dataset compares their fork against ROCm, never against a stock-Mesa
llama.cpp build like ours. This image differs from ours on TWO axes:

    ours : Mesa 26.0.3  + llama.cpp b10200 (stock)
    theirs: Mesa 26.1.5 + llama.cpp b10283 (Nathanw1014 strix-halo-vulkan fork,
            with Strix-Halo-targeted flash-attention / KV / matrix-MoE work)

Mesa is the dominant throughput factor on Strix Halo, and 26.0.3 -> 26.1.5 is a
real driver bump, so this is the first candidate all week that could plausibly
move the numbers. Both axes move together here; if it wins, decompose after.

Measured at OUR production ubatch per model (not their calibration), because
that is the decision we would actually be making. GPU probe already passed:
`RADV STRIX_HALO` present in --list-devices.
"""
import json
import subprocess
import sys
import time

HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
HF = "/root/.cache/huggingface/hub"

# (image, path to llama-bench inside it)
IMAGES = {
    "b10200-stock": ("ghcr.io/mostlygeek/llama-swap:v245-vulkan-b10200",
                     ["/app/llama", "bench"]),
    "radv-perf": ("kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance",
                  ["/usr/bin/llama-bench"]),
}

MODELS = {
    "gemma4-12b": dict(
        path=f"{HF}/models--unsloth--gemma-4-12B-it-qat-GGUF/snapshots/"
             f"980b060c40a8539ac159e0501a3e0f66a6365af3/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf",
        ctk="q8_0", ctv="q8_0", ub=2048),          # our live setting
    "qwen3.6-27b": dict(
        path=f"{HF}/models--unsloth--Qwen3.6-27B-MTP-GGUF/snapshots/"
             f"5cb35eb3dcbf52dbce5f87dbc64df6aaffadcace/Qwen3.6-27B-Q6_K.gguf",
        ctk="bf16", ctv="bf16", ub=256),           # our new live setting
    "qwen3.6-35b": dict(
        path=f"{HF}/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/"
             f"5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-Q8_0.gguf",
        ctk="q8_0", ctv="q8_0", ub=2048),
}

DOCKER = [
    "docker", "run", "--rm",
    "--device", "/dev/dri:/dev/dri", "--group-add", "video", "--group-add", "992",
    # RADV_PERFTEST=nogttspill removed 2026-08-05 (GTT flip: 512 MB carveout,
    # 124 GiB GTT). Blocking GTT placement now blocks all model memory.
    "--security-opt", "seccomp=unconfined",
    "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
]


def run(tag, model, series, depth):
    image, benchcmd = IMAGES[tag]
    m = MODELS[model]
    args = ["-m", m["path"], "-d", str(depth), "-b", "2048", "-ub", str(m["ub"]),
            "-ctk", m["ctk"], "-ctv", m["ctv"], "-fa", "on", "-ngl", "999",
            "-r", "3", "-o", "jsonl", "--no-warmup"]
    args += (["-p", "2048", "-n", "0"] if series == "prefill" else ["-p", "0", "-n", "64"])
    cmd = DOCKER + ["--entrypoint", benchcmd[0], image] + benchcmd[1:] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        return None, p.stderr.strip()[-200:]
    for line in p.stdout.splitlines():
        if line.strip().startswith("{") and '"avg_ts"' in line:
            r = json.loads(line)
            want_prompt = series == "prefill"
            if bool(r.get("n_prompt", 0)) == want_prompt:
                return (r["avg_ts"], r.get("stddev_ts", 0.0)), None
    return None, "no matching jsonl row"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    models = [only] if only else list(MODELS)
    print("# radv-performance vs our stock build")
    print("# ours   : Mesa 26.0.3 + llama.cpp b10200")
    print("# theirs : Mesa 26.1.5 + llama.cpp b10283 (strix-halo-vulkan fork)")
    print(f"# started {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")

    res = {}
    for model in models:
        ub = MODELS[model]["ub"]
        for series, depths in (("prefill", [0, 8192]), ("generation", [0])):
            for depth in depths:
                for tag in IMAGES:
                    t0 = time.time()
                    out, err = run(tag, model, series, depth)
                    el = time.time() - t0
                    if out:
                        res[(model, series, depth, tag)] = out[0]
                        print(f"  {model:<13} {series:<10} d={depth:<5} ub={ub:<5} "
                              f"{tag:<13} {out[0]:8.2f} t/s (±{out[1]:.2f}) [{el:.0f}s]")
                    else:
                        print(f"  {model:<13} {series:<10} d={depth:<5} ub={ub:<5} "
                              f"{tag:<13} FAILED: {err}")
                print()

    print("\n## summary — positive % means radv-perf is faster\n")
    print("| model | series | depth | b10200-stock | radv-perf | delta |")
    print("|---|---|---:|---:|---:|---:|")
    for model in models:
        for series, depths in (("prefill", [0, 8192]), ("generation", [0])):
            for depth in depths:
                a = res.get((model, series, depth, "b10200-stock"))
                b = res.get((model, series, depth, "radv-perf"))
                d = f"{(b-a)/a*100:+.1f}%" if (a and b) else "—"
                print(f"| {model} | {series} | {depth} | "
                      f"{f'{a:.1f}' if a else '—'} | {f'{b:.1f}' if b else '—'} | {d} |")

    with open("/home/dinesh-se/llama-stack/bench/radv-perf-ab.json", "w") as f:
        json.dump({f"{a}|{b}|{c}|{d}": v for (a, b, c, d), v in res.items()}, f, indent=2)
    print("\nsaved: bench/radv-perf-ab.json")


if __name__ == "__main__":
    main()
