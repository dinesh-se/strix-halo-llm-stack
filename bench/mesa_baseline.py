#!/usr/bin/env python3
"""Standardized PP / TG baseline for Mesa/RADV before-after comparison.

Mirrors llama-bench's PP512 + TG128 measurement using llama-server's native
/completion endpoint timings field. cache_prompt=false forces a real prefill
on every run so PP rates reflect the actual compute path.

Output is stable Markdown so two runs can be diffed.
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "http://127.0.0.1:9292"
# 2026-08-06: was ["qwen3.6-35b", "qwen3.6-27b", "gemma4-12b"] — all three were
# retired with llama-swap. BASE :9292 is unchanged: the llama-server router took
# over that port. NOTE the router will AUTO-LOAD a named model, so never add an
# id here that you do not want pulled into memory.
# (Earlier history: was ["qwen3.6-35b", "granite-4.1-8b"]; granite was dropped
# 2026-05-19 and this script was un-runnable until 2026-08-01.)
MODELS = ["deepseek-v4-flash", "gemma4-e4b"]
LOG_DIR = os.environ.get("LLAMA_LOG_DIR",
                        os.path.expanduser("~/llama-stack/logs"))
ITERS = 4  # 1 warmup (discarded) + 3 measured
N_PREDICT = 128

PROMPT = (
    "The Vulkan API is a low-overhead, cross-platform interface for "
    "high-performance graphics and compute workloads, originally derived "
    "from AMD's Mantle API and standardized by the Khronos Group. "
) * 24  # ~3.6 KB, well past 512 tokens for typical English tokenizers


def log_offset(model: str) -> int:
    """Byte offset of a model's stderr log, so we can read only what this run adds."""
    try:
        with open(f"{LOG_DIR}/{model}.stderr.log", "rb") as f:
            f.seek(0, 2)
            return f.tell()
    except OSError:
        return -1


def draft_acceptance(model: str, since: int) -> dict | None:
    """MTP draft acceptance over the bench window only.

    llama-server prints one `draft acceptance = X` per finished task. Reading
    from `since` scopes it to this run rather than the whole log's history.
    The standing floor for "investigate" is a median below 0.70.
    """
    if since < 0:
        return None
    try:
        with open(f"{LOG_DIR}/{model}.stderr.log", "rb") as f:
            f.seek(since)
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    vals = [float(x) for x in re.findall(r"draft acceptance = ([0-9.]+)", chunk)]
    if not vals:
        return None
    return {
        "n": len(vals),
        "median": statistics.median(vals),
        "mean": statistics.mean(vals),
        "min": min(vals),
        "max": max(vals),
    }


def call(model: str, n_predict: int) -> dict:
    body = json.dumps({
        "prompt": PROMPT,
        "n_predict": n_predict,
        "cache_prompt": False,
        "temperature": 0,
        # 2026-08-01: REQUIRED, not optional. Without it qwen3.6-35b emits EOS
        # on its very first token for this prompt (predicted_n=1, content='',
        # stop=eos, reproducible 3/3), so predicted_ms is ~0 and llama.cpp
        # reports its divide-by-zero sentinel TG=1000000 t/s. The 27b and
        # gemma4 happen not to, which is exactly how a bogus number sneaks
        # into a comparison table unnoticed. Pinning the generation length is
        # also what llama-bench does for TG128 — this is now measuring the
        # same thing it claims to mirror.
        "ignore_eos": True,
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
    off = log_offset(model)
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
    mtp = draft_acceptance(model, off)
    if mtp:
        print(
            f"  MTP draft acceptance: median={mtp['median']:.3f} "
            f"mean={mtp['mean']:.3f} range={mtp['min']:.3f}-{mtp['max']:.3f} "
            f"(n={mtp['n']})",
            flush=True,
        )
    return {
        "model": model,
        "mtp": mtp,
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


def report(now: str, info: dict, results: list) -> str:
    """Build the Markdown report once, so stdout and the file cannot drift apart."""
    out = [f"# Mesa/RADV baseline — {now}", "", "## System"]
    out += [f"- **{k}**: {v}" for k, v in info.items()]
    out += [
        f"- **n_predict**: {N_PREDICT}",
        f"- **iters**: {ITERS - 1} measured (+1 warmup discarded)",
        "- **cache_prompt**: false (fresh prefill each run)",
        "",
        "## Summary",
        "",
        "| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        out.append(
            f"| {r['model']} | {r['prompt_n']} | "
            f"{r['pp_median']:.2f} | {r['pp_min']:.2f}–{r['pp_max']:.2f} | "
            f"{r['tg_median']:.2f} | {r['tg_min']:.2f}–{r['tg_max']:.2f} |"
        )

    # MTP is the second gate on any llama.cpp bump: the standing floor for
    # "investigate" is a median below 0.70.
    out += [
        "",
        "## MTP draft acceptance (this run only)",
        "",
        "| Model | median | mean | min–max | n tasks |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in results:
        m = r.get("mtp")
        if m:
            out.append(
                f"| {r['model']} | {m['median']:.3f} | {m['mean']:.3f} | "
                f"{m['min']:.3f}–{m['max']:.3f} | {m['n']} |"
            )
        else:
            out.append(f"| {r['model']} | — | — | — | 0 (no samples) |")
    return "\n".join(out) + "\n"


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    info = sysinfo()

    print(f"# Mesa/RADV baseline — {now}", flush=True)
    results = [run_model(m) for m in MODELS]

    text = report(now, info, results)
    print("\n" + text)

    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
        print(f"# wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
