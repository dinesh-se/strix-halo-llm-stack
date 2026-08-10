#!/usr/bin/env python3
"""
Where did gemma4's prefill actually go?

The recorded story (llama-swap.yaml, since corrected) was:
    b9853  -ub 512  : 677 t/s
    b10200 -ub 512  : 369 t/s   <- "-45%, caused by llama.cpp #25240"
    b10200 -ub 2048 : 456 t/s
measured through llama-server on a ~986-token prompt.

2026-08-04 kernel A/B killed that explanation: `llama bench` on the SAME two
images at the SAME settings is identical (ub=512: 690.19 vs 690.26 t/s;
ub=2048: 732.94 vs 734.01; ub=512 d=8192: 541.60 vs 540.87 at +-0.5 stddev).
So the regression is not in compute. It is in something llama-server does that
llama-bench does not.

llama-bench runs: no HTTP, no slots, no MTP drafter, no prompt cache.
The live server runs all four. This script isolates them one at a time on the
SAME image, measuring prefill the way the original claim did — via the
`prompt_ms` / `prompt_n` that llama-server reports on a real completion.

Factors:
  ubatch    512 vs 2048        (the flag the old story blamed)
  MTP       on vs off          (drafter also has to eat the prompt)
  slots     --parallel 2 --kv-unified  vs  -np auto
If the 45% swing is real, one of these cells will show it. If no cell does,
the original 677/369 pair was confounded — most likely by the --parallel
pinning that landed the same day as the image bump.
"""
import itertools
import json
import subprocess
import sys
import time
import urllib.request

HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
PORT = 9402
IMAGES = {
    "b10200": "ghcr.io/mostlygeek/llama-swap:v245-vulkan-b10200",
    "b9853": "ghcr.io/mostlygeek/llama-swap:v234-vulkan-b9853",
}

# ~986 tokens, matching the prompt size the original measurement used.
PROMPT = ("Summarise the following notes.\n\n" +
          "\n".join(f"- item {i}: the quick brown fox jumps over the lazy dog "
                    f"near the riverbank at dawn" for i in range(120)))


def start(image, extra):
    name = f"g4hunt-{int(time.time()*1000)%10**9}"
    cmd = [
        "docker", "run", "-d", "--rm", "--name", name,
        "--device", "/dev/dri:/dev/dri", "--group-add", "video",
        "--group-add", "992", "--security-opt", "seccomp=unconfined",
    # RADV_PERFTEST=nogttspill removed 2026-08-05 (GTT flip: 512 MB carveout,
    # 124 GiB GTT). Blocking GTT placement now blocks all model memory.
        "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
        "-p", f"{PORT}:{PORT}",
        "--entrypoint", "/app/llama-server", image,
        "--host", "0.0.0.0", "--port", str(PORT),
        "-hf", "unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL",
        "--jinja", "-ngl", "999", "-c", "32768", "-fa", "on",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--no-mmproj", "--no-webui", "-cram", "0", "--metrics",
        *extra,
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return name


def ready(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(4)
    return False


def prefill_ts(reps=3):
    """Prefill t/s as llama-server itself reports it, cache disabled per call."""
    out = []
    for i in range(reps):
        body = json.dumps({
            # unique suffix per rep so we always measure a COLD prefill,
            # otherwise the prompt cache serves it and the number is fiction
            "messages": [{"role": "user", "content": PROMPT + f"\n\nrun {i} {time.time()}"}],
            "max_tokens": 1,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()
        req = urllib.request.Request(f"http://localhost:{PORT}/v1/chat/completions",
                                     data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode())
        t = d.get("timings") or {}
        if t.get("prompt_per_second"):
            out.append(t["prompt_per_second"])
        elif t.get("prompt_ms") and t.get("prompt_n"):
            out.append(t["prompt_n"] / (t["prompt_ms"] / 1000.0))
    return out


def cell(image_tag, ub, mtp, slots):
    extra = ["-b", "2048", "-ub", str(ub)]
    if mtp:
        extra += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "4"]
    else:
        extra += ["--spec-type", "none"]
    if slots == "parallel2":
        extra += ["--parallel", "2", "--kv-unified"]
    label = f"{image_tag} ub={ub} mtp={'on ' if mtp else 'off'} slots={slots}"
    name = None
    try:
        name = start(IMAGES[image_tag], extra)
        if not ready():
            logs = subprocess.run(["docker", "logs", "--tail", "12", name],
                                  capture_output=True, text=True)
            print(f"  {label:<48} FAILED TO START")
            print("      " + (logs.stdout + logs.stderr)[-400:].replace("\n", "\n      "))
            return None
        vals = prefill_ts()
        if not vals:
            print(f"  {label:<48} no timings returned")
            return None
        med = sorted(vals)[len(vals) // 2]
        print(f"  {label:<48} {med:8.1f} t/s   {[round(v,1) for v in vals]}")
        return med
    except subprocess.CalledProcessError as e:
        print(f"  {label:<48} docker error: {e.stderr[-200:] if e.stderr else e}")
        return None
    finally:
        if name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            time.sleep(4)


def main():
    print("# gemma4 prefill hunt — server-level, factorial")
    print(f"# prompt ~{len(PROMPT.split())} words; prefill t/s as llama-server reports it")
    print("# every rep uses a unique suffix so the prompt cache cannot fake it\n")

    res = {}
    # Full factorial on the current image first — that is where the answer is.
    for ub, mtp, slots in itertools.product([512, 2048], [True, False],
                                            ["parallel2", "auto"]):
        res[("b10200", ub, mtp, slots)] = cell("b10200", ub, mtp, slots)

    print()
    # Then the two cells the original claim compared, on the OLD image.
    for ub in (512, 2048):
        res[("b9853", ub, True, "parallel2")] = cell("b9853", ub, True, "parallel2")

    print("\n## summary\n")
    print("| image | ub | MTP | slots | prefill t/s |")
    print("|---|---:|---|---|---:|")
    for k, v in res.items():
        img, ub, mtp, slots = k
        print(f"| {img} | {ub} | {'on' if mtp else 'off'} | {slots} | "
              f"{f'{v:.1f}' if v else '—'} |")

    base = res.get(("b10200", 2048, True, "parallel2"))
    if base:
        print(f"\nvs current live config ({base:.1f} t/s):")
        for k, v in sorted(res.items(), key=lambda x: -(x[1] or 0)):
            if v:
                print(f"  {(k[0]+' ub='+str(k[1])+' mtp='+('on' if k[2] else 'off')+' '+k[3]):<44} "
                      f"{v:8.1f}  {(v-base)/base*100:+6.1f}%")

    with open("/home/dinesh-se/llama-stack/bench/gemma4-prefill-hunt.json", "w") as f:
        json.dump({f"{a}|{b}|{c}|{d}": v for (a, b, c, d), v in res.items()}, f, indent=2)


if __name__ == "__main__":
    main()
