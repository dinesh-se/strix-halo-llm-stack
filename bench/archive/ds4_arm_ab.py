#!/usr/bin/env python3
"""DS4 Flash 0731 — backend A/B (radv vs ROCm) under the GTT memory model.

Written 2026-08-05 for the overnight GTT-flip run. Mirrors the 2026-08-04
measurement EXACTLY so the numbers tie back to it:
  -p 512 -n 64, -b 2048 -ub 512, type_k/v q8_0, -fa 1, -ngl 999, 2 reps.
That run produced 142.06 PP / 13.21 TG on v247-vulkan-b10257 with a 96 GiB
VRAM carveout. Anything materially different here is the memory model, not
the workload.

Three binary paths are in play — this is a real trap:
  ours (mostlygeek)              /app/llama-bench
  kyuz0 vulkan-radv-performance  /usr/bin/llama-bench
  kyuz0 rocm-7.14                /usr/local/bin/llama-bench

⚠️ HSA_OVERRIDE_GFX_VERSION is exported in the user's shell rc files. It is
NOT forwarded here on purpose: the driver reports gfx1151 natively, so it is
a no-op, and forwarding it could mask a genuine detection failure.

⚠️ GTT is now the memory pool, not the overflow path. The guard aborts on GTT
approaching total, NOT on GTT being non-zero (which is what the 08-04 guard
watched, when any GTT use meant a spill).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"

# ⚠️ TWO CACHE LAYOUTS COEXIST under hf-cache, and the difference is one path
# segment that silently breaks model loading:
#   IQ2_XXS (pulled 08-04, HF_HOME semantics)  → .../huggingface/hub/models--…
#   IQ3_XXS + sidecar (pulled 08-05 with       → .../huggingface/models--…
#     `hf download --cache-dir hf-cache`)         (NO `hub/` segment)
# hf-cache is bind-mounted at /root/.cache/huggingface, so these are the
# in-container paths.
MODELS = {
    "iq2": (
        "/root/.cache/huggingface/hub/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
        "/snapshots/57326b941c4603e24d1a5e71c22520c66e086eb8/UD-IQ2_XXS"
        "/DeepSeek-V4-Flash-0731-UD-IQ2_XXS-00001-of-00003.gguf"
    ),
    "iq3": (
        "/root/.cache/huggingface/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
        "/snapshots/1290dcca3f84612f646fb546fb9e8433c1b339b0/UD-IQ3_XXS"
        "/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00001-of-00004.gguf"
    ),
}
QUANT = os.environ.get("DS4_QUANT", "iq2")
MODEL = MODELS[QUANT]
UBATCH = os.environ.get("DS4_UBATCH", "512")
TAG = os.environ.get("DS4_TAG", f"c0-{QUANT}-ub{UBATCH}")
OUT_DIR = Path("/home/dinesh-se/llama-stack/bench")

GTT_TOTAL_F = "/sys/class/drm/card1/device/mem_info_gtt_total"
GTT_USED_F = "/sys/class/drm/card1/device/mem_info_gtt_used"
GTT_ABORT_GIB = 118.0   # ~6 GiB below the 124 GiB pool
RAM_ABORT_GIB = 4.0

ARMS = {
    "radv": {
        "image": "kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance",
        "binary": "/usr/bin/llama-bench",
        "docker_extra": [],
    },
    "rocm": {
        "image": "kyuz0/amd-strix-halo-toolboxes:rocm-7.14_20260805T174643",
        "binary": "/usr/local/bin/llama-bench",
        # ROCm needs the compute node and the render group; /dev/dri alone
        # is enough for Vulkan but not for HIP.
        "docker_extra": ["--device", "/dev/kfd", "--group-add", "render"],
    },
}

BENCH_ARGS = [
    "-m", MODEL,
    "-p", "512", "-n", "64",
    "-b", "2048", "-ub", UBATCH,
    "-ngl", "999",
    "-fa", "1",
    "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
    "-r", "2",
    "-o", "json",
]


def gib(path: str) -> float:
    try:
        return int(Path(path).read_text().strip()) / 1073741824
    except OSError:
        return 0.0


def mem_available_gib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1048576
    return 0.0


class Guard(threading.Thread):
    """Kills the container if GTT or host RAM heads for the wall.

    Silence is not success: this also records the peak so a run that merely
    came close is still visible afterwards.
    """

    def __init__(self, container: str):
        super().__init__(daemon=True)
        self.container = container
        self.stop_flag = threading.Event()
        self.peak_gtt = 0.0
        self.min_ram = 999.0
        self.tripped = None

    def run(self):
        while not self.stop_flag.wait(3):
            g = gib(GTT_USED_F)
            r = mem_available_gib()
            self.peak_gtt = max(self.peak_gtt, g)
            self.min_ram = min(self.min_ram, r)
            if g > GTT_ABORT_GIB or r < RAM_ABORT_GIB:
                self.tripped = f"GTT {g:.1f} GiB / MemAvailable {r:.1f} GiB"
                subprocess.run(["docker", "kill", self.container],
                               capture_output=True)
                return


def run_arm(name: str, cfg: dict) -> dict:
    container = f"ds4-bench-{name}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    cmd = [
        "docker", "run", "--rm", "--name", container,
        "--device", "/dev/dri:/dev/dri",
        "--group-add", "video", "--group-add", "992",
        "--security-opt", "seccomp=unconfined",
        *cfg["docker_extra"],
        "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
        "--entrypoint", cfg["binary"],
        cfg["image"],
        *BENCH_ARGS,
    ]

    guard = Guard(container)
    guard.start()
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    guard.stop_flag.set()
    guard.join(timeout=10)
    elapsed = time.time() - t0

    raw = OUT_DIR / f"ds4-{TAG}-{name}.txt"
    raw.write_text(proc.stdout + "\n===== STDERR =====\n" + proc.stderr)

    result = {
        "arm": name,
        "image": cfg["image"],
        "returncode": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "peak_gtt_gib": round(guard.peak_gtt, 1),
        "min_ram_avail_gib": round(guard.min_ram, 1),
        "guard_tripped": guard.tripped,
        "pp": None, "pp_sd": None, "tg": None, "tg_sd": None,
        "build": None, "gpu": None, "error": None,
    }

    # llama-bench -o json emits a pretty-printed JSON ARRAY of objects, one per
    # benchmark. Parse the document as a whole — an earlier regex here matched
    # objects individually with a `(?=\s*(?:\{|$))` lookahead, which the comma
    # between array elements defeats, silently yielding zero results on a run
    # that had in fact succeeded. Only scalars are lifted out; raw output must
    # never reach the transcript.
    objs = []
    try:
        doc = json.loads(proc.stdout)
        objs = doc if isinstance(doc, list) else [doc]
    except json.JSONDecodeError:
        # Fallback: flat objects, no nesting (llama-bench emits none).
        for m in re.finditer(r"\{[^{}]*\}", proc.stdout, re.S):
            try:
                objs.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue
    for o in objs:
        result["build"] = o.get("build_number", result["build"])
        result["gpu"] = o.get("gpu_info", result["gpu"])
        if o.get("n_prompt"):
            result["pp"] = round(o["avg_ts"], 2)
            result["pp_sd"] = round(o.get("stddev_ts", 0), 2)
        elif o.get("n_gen"):
            result["tg"] = round(o["avg_ts"], 2)
            result["tg_sd"] = round(o.get("stddev_ts", 0), 2)

    if result["pp"] is None and result["tg"] is None:
        tail = (proc.stderr or proc.stdout)[-600:]
        result["error"] = tail.replace("\n", " | ")[-600:]

    return result


def main():
    arms = sys.argv[1:] or list(ARMS)
    print(f"quant={QUANT} ubatch={UBATCH} tag={TAG}", flush=True)
    print(f"GTT total {gib(GTT_TOTAL_F):.1f} GiB, "
          f"used {gib(GTT_USED_F):.1f} GiB, "
          f"MemAvailable {mem_available_gib():.1f} GiB at start", flush=True)

    results = []
    for name in arms:
        print(f"--- arm {name} starting ---", flush=True)
        try:
            r = run_arm(name, ARMS[name])
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", f"ds4-bench-{name}"],
                           capture_output=True)
            r = {"arm": name, "error": "timeout after 3600s"}
        results.append(r)
        print(json.dumps(r), flush=True)
        time.sleep(10)  # let GTT drain before the next arm

    (OUT_DIR / f"ds4-{TAG}-results.json").write_text(json.dumps(results, indent=2))
    print(f"WROTE ds4-{TAG}-results.json", flush=True)


if __name__ == "__main__":
    main()
