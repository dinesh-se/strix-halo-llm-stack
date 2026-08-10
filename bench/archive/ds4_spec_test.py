#!/usr/bin/env python3
"""DS4 IQ3_XXS + dspark sidecar — resolve --spec-type and measure the payoff.

Phase 3+5 of the 2026-08-05 overnight GTT-flip run.

MTP and DSpark are NOT the same thing: `--spec-type` exposes `draft-mtp`,
`draft-dflash` and `draft-dspark` as separate modes (confirmed against this
binary's --help). Unsloth ships a `dspark-…-Q8_0.gguf`, but the r/StrixHalo
thread author runs `--spec-type draft-mtp` against a file named `…-MTP-Q8_0`.
So the mode is resolved EMPIRICALLY here: try draft-dspark, fall back to
draft-mtp, then draft-dflash.

`--draft` / `--draft-n` / `--draft-max` are removed upstream; the knob is
`--spec-draft-n-max` (default 3). Sidecar path is `-md`.

The container runs DETACHED on purpose. An earlier phase had its Python
wrapper killed by the harness mid-run while the container kept going, and the
result was only recoverable because docker owned the process. Detached +
`docker logs` makes that the normal path rather than a lucky recovery.

Budget: 97.05 (IQ3) + 10.15 (sidecar) = 107.2 GiB against 124 GiB GTT.
Context is deliberately modest (16k) so KV does not eat the remaining ~17 GiB.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE = "kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance"
BINARY = "/usr/bin/llama-server"
HF_HOST = "/home/dinesh-se/llama-stack/hf-cache"
SNAP = ("/root/.cache/huggingface/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
        "/snapshots/1290dcca3f84612f646fb546fb9e8433c1b339b0")
MODEL = f"{SNAP}/UD-IQ3_XXS/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00001-of-00004.gguf"
SIDECAR = ("/root/.cache/huggingface/models--unsloth--DeepSeek-V4-Flash-0731-GGUF"
           "/snapshots/1290dcca3f84612f646fb546fb9e8433c1b339b0"
           "/dspark-DeepSeek-V4-Flash-0731-Q8_0.gguf")
PORT = 10099
NAME = "ds4-spec"
OUT = Path("/home/dinesh-se/llama-stack/bench")

GTT_USED_F = "/sys/class/drm/card1/device/mem_info_gtt_used"
LOAD_TIMEOUT = 900


def gib(p):
    try:
        return int(Path(p).read_text().strip()) / 1073741824
    except OSError:
        return 0.0


def mem_avail():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1048576
    return 0.0


def kill():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def start(spec_type, n_max=2, ubatch=1024):
    kill()
    args = [
        "docker", "run", "-d", "--rm", "--name", NAME,
        "--device", "/dev/dri:/dev/dri",
        "--group-add", "video", "--group-add", "992",
        "--security-opt", "seccomp=unconfined",
        "-v", f"{HF_HOST}:/root/.cache/huggingface:rw",
        "-p", f"{PORT}:{PORT}",
        "--entrypoint", BINARY, IMAGE,
        "-m", MODEL,
        "--port", str(PORT), "--host", "0.0.0.0",
        "-c", "16384",
        "-ngl", "999", "-fa", "on",
        "-b", "2048", "-ub", str(ubatch),
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--jinja", "--no-webui",
    ]
    if spec_type != "none":
        args += ["-md", SIDECAR, "--spec-type", spec_type,
                 "--spec-draft-n-max", str(n_max)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return False, r.stderr.strip()[-400:]
    return True, None


def wait_ready():
    """Poll until the server answers, or the container dies.

    Silence is not success: a dead container must break the loop, otherwise
    this waits the full timeout on a model that failed to load.
    """
    t0 = time.time()
    peak = 0.0
    while time.time() - t0 < LOAD_TIMEOUT:
        peak = max(peak, gib(GTT_USED_F))
        alive = subprocess.run(["docker", "ps", "-q", "-f", f"name={NAME}"],
                               capture_output=True, text=True).stdout.strip()
        if not alive:
            return False, peak, "container exited during load"
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                if r.status == 200:
                    return True, peak, None
        except Exception:
            pass
        time.sleep(5)
    return False, peak, f"not ready after {LOAD_TIMEOUT}s"


def complete(prompt, n_predict=128):
    body = json.dumps({
        "prompt": prompt, "n_predict": n_predict,
        "temperature": 0.0, "cache_prompt": False,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/completion", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def acceptance():
    """draft acceptance is printed per finished task on stderr."""
    logs = subprocess.run(["docker", "logs", "--tail", "400", NAME],
                          capture_output=True, text=True)
    blob = logs.stdout + logs.stderr
    import re
    vals = [float(x) for x in re.findall(r"draft acceptance = ([0-9.]+)", blob)]
    return vals


PROMPT = ("Explain, in careful detail, how a unified memory architecture "
          "changes the tradeoffs of running a large mixture-of-experts model "
          "compared with a discrete GPU that has dedicated VRAM. ") * 3


def trial(spec_type, n_max=2):
    print(f"=== spec-type={spec_type} n_max={n_max} ===", flush=True)
    ok, err = start(spec_type, n_max)
    if not ok:
        print(json.dumps({"spec_type": spec_type, "error": f"start: {err}"}),
              flush=True)
        return None
    ready, peak, err = wait_ready()
    if not ready:
        tail = subprocess.run(["docker", "logs", "--tail", "12", NAME],
                              capture_output=True, text=True)
        msg = (tail.stdout + tail.stderr).replace("\n", " | ")[-500:]
        kill()
        rec = {"spec_type": spec_type, "error": err, "peak_gtt_gib": round(peak, 1),
               "log_tail": msg}
        print(json.dumps(rec), flush=True)
        return rec
    try:
        r = complete(PROMPT)
        t = r.get("timings", {})
        peak = max(peak, gib(GTT_USED_F))
        acc = acceptance()
        rec = {
            "spec_type": spec_type, "n_max": n_max,
            "pp": round(t.get("prompt_per_second", 0), 2),
            "tg": round(t.get("predicted_per_second", 0), 2),
            "prompt_n": t.get("prompt_n"), "predicted_n": t.get("predicted_n"),
            "peak_gtt_gib": round(peak, 1),
            "min_ram_avail_gib": round(mem_avail(), 1),
            "draft_acceptance": acc,
        }
    except Exception as e:
        rec = {"spec_type": spec_type, "error": f"completion: {e}",
               "peak_gtt_gib": round(peak, 1)}
    finally:
        kill()
    print(json.dumps(rec), flush=True)
    return rec


def main():
    modes = sys.argv[1:] or ["draft-dspark", "draft-mtp", "none"]
    out = []
    for m in modes:
        rec = trial(m)
        out.append(rec)
        # A working spec mode makes the remaining fallbacks unnecessary.
        if rec and rec.get("tg") and m != "none":
            print(f"{m} WORKS — skipping remaining fallback modes", flush=True)
            modes_left = [x for x in modes[modes.index(m) + 1:] if x == "none"]
            for m2 in modes_left:
                out.append(trial(m2))
            break
        time.sleep(5)
    (OUT / "ds4-spec-results.json").write_text(json.dumps(out, indent=2))
    print("WROTE ds4-spec-results.json", flush=True)


if __name__ == "__main__":
    main()
