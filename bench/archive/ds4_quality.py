#!/usr/bin/env python3
"""DS4 quality: IQ3_XXS vs IQ2_XXS on real work (Phase 6).

The 08-04 evaluation shelved DS4 partly because "IQ2_XXS is 2.06 bpw on a 284B
model, so quality at that bit depth is a real open question (not evaluated)".
This evaluates it — the whole point of tonight's carveout flip was to make the
higher quant reachable, so "is it actually better?" is the payoff question.

⚠️ Reasoning effort is the confound. DS4 exposes only no-think/high/max, and a
model emitting 3x the tokens *feels* worse-value at identical t/s. Both arms
run byte-identical request bodies at temperature 0 with the same token cap, so
whatever reasoning behaviour occurs, it occurs on both sides.

⚠️ No sidecar on either arm. Speculative decoding is a speed feature, and
leaving it out keeps quant the only variable.

Outputs are written to a file for human reading; only lengths and timings come
back to the console.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

IMAGE = "kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance"
BINARY = "/usr/bin/llama-server"
HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
MODELS = {
    "iq3": ("/root/.cache/huggingface/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
            "/snapshots/1290dcca3f84612f646fb546fb9e8433c1b339b0/UD-IQ3_XXS"
            "/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00001-of-00004.gguf"),
    "iq2": ("/root/.cache/huggingface/hub/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
            "/snapshots/57326b941c4603e24d1a5e71c22520c66e086eb8/UD-IQ2_XXS"
            "/DeepSeek-V4-Flash-0731-UD-IQ2_XXS-00001-of-00003.gguf"),
}
PORT = 10098
NAME = "ds4-quality"
OUT = Path("/home/dinesh-se/llama-stack/bench")
MAX_TOKENS = 2048

TASKS = {
    "coding": (
        "Write a Python function `merge_intervals(intervals)` that takes a list "
        "of [start, end] pairs, merges all overlapping intervals, and returns "
        "the merged list sorted by start. Handle the empty list, single "
        "interval, intervals that touch exactly at a boundary, and fully "
        "nested intervals. Include the reasoning for your boundary-condition "
        "choices, then the final code."
    ),
    "reasoning": (
        "A machine has 122 GiB of system RAM. A GPU shares that RAM through a "
        "unified memory architecture, drawing from a 124 GiB GTT pool rather "
        "than a dedicated carveout. You want to run a 97 GiB model plus a 10 "
        "GiB speculative-decoding draft model simultaneously, and the KV cache "
        "for a 16k context costs roughly 2 GiB. Work out whether this fits, "
        "what the failure mode looks like if it does not, and which single "
        "measurement you would take first to find out. Be specific about which "
        "numbers are load-bearing in your reasoning."
    ),
}


def kill():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def start(quant):
    kill()
    args = [
        "docker", "run", "-d", "--rm", "--name", NAME,
        "--device", "/dev/dri:/dev/dri",
        "--group-add", "video", "--group-add", "992",
        "--security-opt", "seccomp=unconfined",
        "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
        "-p", f"{PORT}:{PORT}",
        "--entrypoint", BINARY, IMAGE,
        "-m", MODELS[quant],
        "--port", str(PORT), "--host", "0.0.0.0",
        "-c", "16384", "-ngl", "999", "-fa", "on",
        "-b", "2048", "-ub", "1024",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--jinja", "--no-webui",
    ]
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()[-300:]


def wait_ready(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        alive = subprocess.run(["docker", "ps", "-q", "-f", f"name={NAME}"],
                               capture_output=True, text=True).stdout.strip()
        if not alive:
            return False, "container exited during load"
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                if r.status == 200:
                    return True, None
        except Exception:
            pass
        time.sleep(5)
    return False, f"not ready after {timeout}s"


def ask(prompt):
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS, "temperature": 0.0, "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
    msg = d["choices"][0]["message"]
    return {
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content") or "",
        "finish_reason": d["choices"][0].get("finish_reason"),
        "usage": d.get("usage", {}),
        "wall_s": round(time.time() - t0, 1),
    }


def main():
    quants = sys.argv[1:] or ["iq3", "iq2"]
    all_out = {}
    summary = []
    for q in quants:
        ok, err = start(q)
        if not ok:
            summary.append({"quant": q, "error": f"start: {err}"})
            continue
        ready, err = wait_ready()
        if not ready:
            tail = subprocess.run(["docker", "logs", "--tail", "10", NAME],
                                  capture_output=True, text=True)
            summary.append({"quant": q, "error": err,
                            "tail": (tail.stdout + tail.stderr)[-300:]})
            kill()
            continue
        all_out[q] = {}
        for name, prompt in TASKS.items():
            try:
                r = ask(prompt)
            except Exception as e:
                r = {"content": "", "reasoning": "", "error": str(e),
                     "usage": {}, "wall_s": -1, "finish_reason": "error"}
            all_out[q][name] = r
            u = r.get("usage", {})
            summary.append({
                "quant": q, "task": name,
                "completion_tokens": u.get("completion_tokens"),
                "reasoning_chars": len(r.get("reasoning", "")),
                "content_chars": len(r.get("content", "")),
                "finish_reason": r.get("finish_reason"),
                "wall_s": r.get("wall_s"),
            })
            print(json.dumps(summary[-1]), flush=True)
        kill()
        time.sleep(5)

    # MERGE, don't clobber. Running this once per quant (which is the normal
    # way to use it, since each arm needs its own 97 GiB load) previously had
    # the second invocation overwrite the first arm's outputs entirely.
    out_f = OUT / "ds4-quality-outputs.json"
    sum_f = OUT / "ds4-quality-summary.json"
    prev = json.loads(out_f.read_text()) if out_f.exists() else {}
    prev.update(all_out)
    out_f.write_text(json.dumps(prev, indent=2))
    prev_s = json.loads(sum_f.read_text()) if sum_f.exists() else []
    sum_f.write_text(json.dumps(prev_s + summary, indent=2))
    print("WROTE ds4-quality-outputs.json + ds4-quality-summary.json", flush=True)


if __name__ == "__main__":
    main()
