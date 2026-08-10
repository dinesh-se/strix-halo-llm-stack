#!/usr/bin/env python3
"""
Rollback A/B: is Friday's pin (b10200) actually worse than what preceded it?

User reports perf + device-lost regressions since 2026-08-01, which is exactly
the b9853 -> b10200 bump. Two levels, because they answer different questions:

  KERNEL  (`llama bench`, no server, no MTP drafter, no slots)
          -> did raw prefill/decode regress between builds?
  SERVER  (real llama-server + live flags + MTP + --parallel 2, over HTTP)
          -> did the SERVING path regress? This is where tonight's gemma4
             finding says the real regression lives: -ub 512 costs only 6%
             at kernel level but was recorded as -45% through the server.

Runs each image as a throwaway container, so the live llama-swap stack is
never touched.
"""
import json
import subprocess
import sys
import time
import urllib.request

HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
HF = "/root/.cache/huggingface/hub"

IMAGES = {
    "b9853":  "ghcr.io/mostlygeek/llama-swap:v234-vulkan-b9853",
    "b10121": "llama-swap:b10121",
    "b10200": "ghcr.io/mostlygeek/llama-swap:v245-vulkan-b10200",
}

GEMMA = (f"{HF}/models--unsloth--gemma-4-12B-it-qat-GGUF/snapshots/"
         f"980b060c40a8539ac159e0501a3e0f66a6365af3/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf")
QWEN27 = (f"{HF}/models--unsloth--Qwen3.6-27B-MTP-GGUF/snapshots/"
          f"5cb35eb3dcbf52dbce5f87dbc64df6aaffadcace/Qwen3.6-27B-Q6_K.gguf")

DOCKER_BASE = [
    "docker", "run", "--rm",
    "--device", "/dev/dri:/dev/dri",
    "--group-add", "video", "--group-add", "992",
    "--security-opt", "seccomp=unconfined",
    # RADV_PERFTEST=nogttspill removed 2026-08-05 (GTT flip: 512 MB carveout,
    # 124 GiB GTT). Blocking GTT placement now blocks all model memory.
    "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
]


def kernel_point(image, model, ub, depth, ctk, ctv, reps=3):
    cmd = DOCKER_BASE + [
        "--entrypoint", "/app/llama", image, "bench",
        "-m", model, "-p", "2048", "-n", "0", "-d", str(depth),
        "-b", "2048", "-ub", str(ub),
        "-ctk", ctk, "-ctv", ctv, "-fa", "on", "-ngl", "999",
        "-r", str(reps), "-o", "jsonl", "--no-warmup",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=2400)
    if p.returncode != 0:
        return None, p.stderr.strip()[-200:]
    for line in p.stdout.splitlines():
        if line.strip().startswith("{"):
            r = json.loads(line)
            if r.get("n_prompt", 0) > 0:
                return (r["avg_ts"], r.get("stddev_ts", 0.0)), None
    return None, "no jsonl row"


def image_present(image):
    p = subprocess.run(["docker", "image", "inspect", image],
                       capture_output=True, text=True)
    return p.returncode == 0


def main():
    print(f"# rollback A/B  started {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print("# KERNEL level (llama bench: no server, no MTP, no slots)\n")

    # b10121 may still be pulling behind the DeepSeek download -- skip whatever
    # is not on disk yet rather than burning a slot on a guaranteed failure.
    for tag in list(IMAGES):
        if not image_present(IMAGES[tag]):
            print(f"# SKIP {tag} ({IMAGES[tag]}) -- not pulled yet")
            del IMAGES[tag]
    print()

    # gemma4 is the model with the recorded -45% regression at -ub 512.
    # If b10200 regressed the KERNEL, ub=512 should be much worse on b10200
    # than on b9853. Tonight's data says it will not be -- this confirms it
    # across builds rather than just across ubatch.
    matrix = [
        ("gemma4",  GEMMA,  "q8_0", "q8_0", [(512, 0), (2048, 0), (512, 8192)]),
        ("qwen27b", QWEN27, "bf16", "bf16", [(256, 0), (1024, 0)]),
    ]

    results = {}
    for label, model, ctk, ctv, points in matrix:
        for ub, depth in points:
            for tag, image in IMAGES.items():
                key = (label, ub, depth, tag)
                t0 = time.time()
                out, err = kernel_point(image, model, ub, depth, ctk, ctv)
                el = time.time() - t0
                if out:
                    results[key] = out[0]
                    print(f"  {label:<8} ub={ub:<5} d={depth:<6} {tag:<7} "
                          f"{out[0]:8.2f} t/s (±{out[1]:.2f})  [{el:.0f}s]")
                else:
                    print(f"  {label:<8} ub={ub:<5} d={depth:<6} {tag:<7} FAILED: {err}")
            print()

    print("\n## Kernel-level summary (t/s, and % vs b9853)\n")
    print("| model | ub | depth | b9853 | b10121 | b10200 | b10200 vs b9853 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for label, _, _, _, points in matrix:
        for ub, depth in points:
            base = results.get((label, ub, depth, "b9853"))
            cells = []
            for tag in ("b9853", "b10121", "b10200"):
                v = results.get((label, ub, depth, tag))
                cells.append(f"{v:.1f}" if v else "—")
            new = results.get((label, ub, depth, "b10200"))
            delta = f"{(new-base)/base*100:+.1f}%" if (base and new) else "—"
            print(f"| {label} | {ub} | {depth} | " + " | ".join(cells) + f" | {delta} |")

    with open("/home/dinesh-se/llama-stack/bench/rollback-ab-kernel.json", "w") as f:
        json.dump({f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v for k, v in results.items()}, f, indent=2)
    print("\nsaved: bench/rollback-ab-kernel.json")


if __name__ == "__main__":
    main()
