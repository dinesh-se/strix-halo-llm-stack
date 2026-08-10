#!/usr/bin/env python3
"""
Server-level config A/B -- the test that actually matches the user's complaint.

Rationale for the pivot: the kernel-level rollback A/B showed b9853 and b10200
are IDENTICAL on gemma4 (three points, <0.2% apart, one pair at +-0.5 stddev).
So the image is not what changed under the user on 2026-08-01. What else landed
that same Friday was the SERVING config: `--parallel` pinned on all three models
(-np had been auto=4), plus -b/-ub 2048 on gemma4. Those are what this tests.

Two questions, both measured through a real llama-server with the live flags:

  Q1 (27b):    does -ub 1024 -> 256 hold up end-to-end, not just in llama-bench?
  Q2 (gemma4): does -sps 0.10 -> 0.5 remove the measured 5.1% slot-destroying
               assignments, and what does that cost/save in re-prefill?

The headline metric for Q2 is PREFILL AMPLIFICATION: tokens the server actually
prefilled divided by tokens we submitted. 1.0 is perfect reuse. The 35b was
measured at ~45x before -sps 0.5 was applied to it on 2026-08-02.
"""
import json
import subprocess
import sys
import time
import urllib.request

HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
HF = "/root/.cache/huggingface/hub"
IMAGE = "ghcr.io/mostlygeek/llama-swap:v245-vulkan-b10200"
PORT = 9401

GEMMA = ("-hf", "unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL")
QWEN27 = ("-hf", "unsloth/Qwen3.6-27B-MTP-GGUF:Q6_K")

# Live flags, copied from config/llama-swap.yaml, minus the log redirect and
# with -c trimmed to 65536 so a throwaway server starts fast and leaves VRAM
# for the resident 35b. Slot behaviour under test is unaffected by n_ctx.
BASE_GEMMA = [
    "--jinja", "-ngl", "999", "-c", "65536", "-fa", "on",
    "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
    "--no-mmproj", "--no-webui", "-b", "2048", "-ub", "2048",
    "--temp", "1.0", "--top-p", "0.95", "--top-k", "64",
    "--parallel", "2", "--kv-unified",
    "--spec-type", "draft-mtp", "--spec-draft-n-max", "4",
    "--reasoning-budget", "8192", "-cram", "0", "--metrics",
]
BASE_QWEN = [
    "--jinja", "-ngl", "999", "-c", "65536", "-fa", "on",
    "--cache-type-k", "bf16", "--cache-type-v", "bf16",
    "--cache-reuse", "256", "--no-mmproj", "--no-webui",
    "-b", "2048",
    "--parallel", "2", "--kv-unified",
    "--spec-type", "draft-mtp", "--spec-draft-n-max", "2",
    "--reasoning-budget", "8192", "-cram", "0", "--metrics",
]


def start_server(model_args, extra):
    name = f"bench-srv-{int(time.time())}"
    cmd = [
        "docker", "run", "-d", "--rm", "--name", name,
        "--device", "/dev/dri:/dev/dri",
        "--group-add", "video", "--group-add", "992",
        "--security-opt", "seccomp=unconfined",
    # RADV_PERFTEST=nogttspill removed 2026-08-05 (GTT flip: 512 MB carveout,
    # 124 GiB GTT). Blocking GTT placement now blocks all model memory.
        "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
        "-p", f"{PORT}:{PORT}",
        "--entrypoint", "/app/llama-server", IMAGE,
        "--host", "0.0.0.0", "--port", str(PORT),
        *model_args, *extra,
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return name


def wait_ready(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(5)
    return False


def metrics():
    out = {}
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/metrics", timeout=10) as r:
            for line in r.read().decode().splitlines():
                if line.startswith("llamacpp:"):
                    k, _, v = line.partition(" ")
                    try:
                        out[k] = float(v)
                    except ValueError:
                        pass
    except Exception:
        pass
    return out


def chat(prompt, max_tokens=16):
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        f"http://localhost:{PORT}/v1/chat/completions",
        data=body, headers={"content-type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    return d, time.time() - t0


def mixed_traffic():
    """Interleave one long conversation with short aux calls.

    This is the shape that breaks slot affinity: a short prompt scores a low
    f_sim against the shared chat-template preamble and, at -sps 0.10, is still
    accepted onto the slot holding the long prefix -- destroying it. f_sim is
    normalised by the INCOMING prompt length, not the slot's.
    """
    long_ctx = ("Here is a source file to review.\n\n" +
                "\n".join(f"def function_{i}(x): return x * {i}  # helper {i}"
                          for i in range(900)))
    seq = []
    for turn in range(6):
        seq.append(("long", long_ctx + f"\n\nQuestion {turn}: summarise briefly."))
        seq.append(("short", f"ok {turn}"))
    return seq


def run_case(label, model_args, base, extra):
    print(f"\n### {label}")
    name = None
    try:
        name = start_server(model_args, base + extra)
        if not wait_ready():
            print("  SERVER FAILED TO BECOME READY")
            logs = subprocess.run(["docker", "logs", "--tail", "15", name],
                                  capture_output=True, text=True)
            print("  " + (logs.stdout + logs.stderr)[-600:].replace("\n", "\n  "))
            return None
        m0 = metrics()
        submitted = 0
        t_total = 0.0
        for kind, prompt in mixed_traffic():
            d, el = chat(prompt)
            t_total += el
            submitted += d.get("usage", {}).get("prompt_tokens", 0)
        m1 = metrics()
        prefilled = m1.get("llamacpp:prompt_tokens_total", 0) - m0.get("llamacpp:prompt_tokens_total", 0)
        amp = prefilled / submitted if submitted else 0
        print(f"  submitted prompt tokens : {submitted:,}")
        print(f"  actually prefilled      : {int(prefilled):,}")
        print(f"  AMPLIFICATION           : {amp:.2f}x   (1.00 = perfect reuse)")
        print(f"  wall time               : {t_total:.1f}s")
        return dict(submitted=submitted, prefilled=prefilled, amp=amp, wall=t_total)
    finally:
        if name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            time.sleep(5)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "gemma4"
    print(f"# server config A/B -- {which}")
    print(f"# image {IMAGE}")
    res = {}
    if which == "gemma4":
        # Q2: does -sps 0.5 remove the slot destruction we measured in the logs?
        res["sps_default_0.10"] = run_case(
            "gemma4  -sps 0.10 (current, llama.cpp default)", GEMMA, BASE_GEMMA, [])
        res["sps_0.5"] = run_case(
            "gemma4  -sps 0.5 (proposed)", GEMMA, BASE_GEMMA, ["-sps", "0.5"])
    else:
        # Q1: does the llama-bench ubatch win survive the real serving path?
        res["ub_1024"] = run_case(
            "27b  -ub 1024 (current)", QWEN27, BASE_QWEN, ["-ub", "1024"])
        res["ub_256"] = run_case(
            "27b  -ub 256 (proposed)", QWEN27, BASE_QWEN, ["-ub", "256"])

    print("\n## summary")
    for k, v in res.items():
        if v:
            print(f"  {k:<28} amp {v['amp']:.2f}x   wall {v['wall']:.1f}s")
    with open(f"/home/dinesh-se/llama-stack/bench/server-ab-{which}.json", "w") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
