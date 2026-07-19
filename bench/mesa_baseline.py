#!/usr/bin/env python3
"""Standardized PP / TG baseline for Mesa/RADV before-after comparison.

Mirrors llama-bench's PP512 + TG128 measurement using llama-server's native
/completion endpoint timings field. cache_prompt=false forces a real prefill
on every run so PP rates reflect the actual compute path.

Output is stable Markdown so two runs can be diffed.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "http://127.0.0.1:9292"
MODELS = ["orchestrator", "coder"]
ITERS = 4  # 1 warmup (discarded) + 3 measured
N_PREDICT = 128

PROMPT = (
    "The Vulkan API is a low-overhead, cross-platform interface for "
    "high-performance graphics and compute workloads, originally derived "
    "from AMD's Mantle API and standardized by the Khronos Group. "
) * 24  # ~3.6 KB, well past 512 tokens for typical English tokenizers


def call(model: str, n_predict: int) -> dict:
    body = json.dumps({
        "prompt": PROMPT,
        "n_predict": n_predict,
        "cache_prompt": False,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/upstream/{model}/completion",
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def run_model(model: str) -> dict:
    print(f"\n## {model}", flush=True)
    pp_rates, tg_rates, prompt_ns = [], [], []
    for i in range(ITERS):
        label = "warmup" if i == 0 else f"run {i}"
        t0 = time.perf_counter()
        r = call(model, N_PREDICT)
        t = r["timings"]
        elapsed = time.perf_counter() - t0
        print(
            f"  {label}: prompt_n={t['prompt_n']} "
            f"PP={t['prompt_per_second']:.2f} t/s  "
            f"TG={t['predicted_per_second']:.2f} t/s  "
            f"({elapsed:.2f}s wall)",
            flush=True,
        )
        if i > 0:
            pp_rates.append(t["prompt_per_second"])
            tg_rates.append(t["predicted_per_second"])
            prompt_ns.append(t["prompt_n"])
    return {
        "model": model,
        "prompt_n": prompt_ns[0],
        "pp_median": statistics.median(pp_rates),
        "pp_min": min(pp_rates),
        "pp_max": max(pp_rates),
        "tg_median": statistics.median(tg_rates),
        "tg_min": min(tg_rates),
        "tg_max": max(tg_rates),
        "n_predict": N_PREDICT,
        "iters": len(pp_rates),
    }


def sysinfo() -> dict:
    def sh(cmd: str) -> str:
        try:
            return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            return ""

    mesa = sh("dpkg -l mesa-vulkan-drivers 2>/dev/null | awk '/^ii/{print $3}'")
    kernel = sh("uname -r")
    llama_cpp = sh("docker exec llama-swap /app/llama-server --version 2>&1 | head -1")
    swap = sh("docker exec llama-swap /app/llama-swap --version 2>&1 | head -1")
    radv_perftest = sh(
        "docker inspect llama-swap --format '{{range .Config.Env}}{{println .}}{{end}}' "
        "| grep RADV_PERFTEST || true"
    )
    gpu = sh("vulkaninfo --summary 2>/dev/null | awk -F= '/deviceName/{print $2; exit}'").strip()
    return {
        "mesa": mesa,
        "kernel": kernel,
        "llama_cpp": llama_cpp,
        "llama_swap": swap,
        "radv_perftest": radv_perftest,
        "gpu": gpu,
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    info = sysinfo()

    print(f"# Mesa/RADV baseline — {now}")
    print()
    print("## System")
    for k, v in info.items():
        print(f"- **{k}**: {v}")
    print(f"- **n_predict**: {N_PREDICT}")
    print(f"- **iters**: {ITERS - 1} measured (+1 warmup discarded)")
    print(f"- **cache_prompt**: false (fresh prefill each run)")

    results = [run_model(m) for m in MODELS]

    print("\n## Summary")
    print()
    print("| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |")
    print("|---|---:|---:|---:|---:|---:|")
    for r in results:
        print(
            f"| {r['model']} | {r['prompt_n']} | "
            f"{r['pp_median']:.2f} | {r['pp_min']:.2f}–{r['pp_max']:.2f} | "
            f"{r['tg_median']:.2f} | {r['tg_min']:.2f}–{r['tg_max']:.2f} |"
        )

    if out_path:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print(f"# Mesa/RADV baseline — {now}")
            print()
            print("## System")
            for k, v in info.items():
                print(f"- **{k}**: {v}")
            print(f"- **n_predict**: {N_PREDICT}")
            print(f"- **iters**: {ITERS - 1} measured (+1 warmup discarded)")
            print(f"- **cache_prompt**: false (fresh prefill each run)")
            print("\n## Summary\n")
            print("| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |")
            print("|---|---:|---:|---:|---:|---:|")
            for r in results:
                print(
                    f"| {r['model']} | {r['prompt_n']} | "
                    f"{r['pp_median']:.2f} | {r['pp_min']:.2f}–{r['pp_max']:.2f} | "
                    f"{r['tg_median']:.2f} | {r['tg_min']:.2f}–{r['tg_max']:.2f} |"
                )
        with open(out_path, "w") as f:
            f.write(buf.getvalue())
        print(f"\n# wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
