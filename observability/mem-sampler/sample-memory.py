#!/usr/bin/env python3
"""Attribution sampler for the unexplained host-memory bleed (2026-08-25).

WHY THIS EXISTS
    VictoriaMetrics records `node_memory_MemAvailable_bytes`, which proves
    *that* ~3.2 GiB disappears over ~10 h after a router restart but says
    nothing about *where it went* — which is exactly where the 2026-08-25
    diagnosis had to stop.

WHAT IS ALREADY KNOWN (do not re-derive)
    - GTT is FLAT across the bleed (109.11 -> 109.29 GiB over the day), so it
      is NOT the models. The loss is host-side.
    - It is NOT an unbounded leak: it falls to ~3.8 GiB and then sits perfectly
      flat for ~9 h. Something allocates and holds.
    - The steepest drop brackets the 21:35 night-check-in and 23:00
      overnight-tasks crons — roughly 1 GiB across that window, never returned.

One JSON object per line, ~2 KB, every 5 min => ~0.6 MB/day.
Read it with analyze-memory.py, which diffs two samples and ranks what grew.
"""
import json
import os
import time
from pathlib import Path

OUT = Path(os.environ.get(
    "MEM_SAMPLE_FILE",
    "/home/YOU/strix-halo-llm-stack/observability/mem-sampler/samples.jsonl"))
MAX_BYTES = int(os.environ.get("MEM_SAMPLE_MAX_BYTES", str(64 * 1024 * 1024)))
TOP_N = int(os.environ.get("MEM_SAMPLE_TOP_N", "30"))

MEMINFO_KEYS = {
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapCached",
    "SwapTotal", "SwapFree", "Dirty", "Writeback", "AnonPages", "Mapped",
    "Shmem", "Slab", "SReclaimable", "SUnreclaim", "KernelStack",
    "PageTables", "Committed_AS",
}
VMSTAT_KEYS = {
    "pgscan_direct", "pgsteal_direct", "pgscan_kswapd", "pgsteal_kswapd",
    "pswpin", "pswpout", "pgmajfault", "nr_slab_unreclaimable",
}


def meminfo() -> dict:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            if k in MEMINFO_KEYS:
                out[k] = int(v.split()[0]) * 1024  # kB -> bytes
    return out


def vmstat() -> dict:
    out = {}
    with open("/proc/vmstat") as f:
        for line in f:
            k, _, v = line.partition(" ")
            if k in VMSTAT_KEYS:
                out[k] = int(v)
    return out


def gpu() -> dict:
    out = {}
    for name, fn in (("gtt_used", "mem_info_gtt_used"),
                     ("vram_used", "mem_info_vram_used")):
        for card in ("card1", "card0"):
            p = Path(f"/sys/class/drm/{card}/device/{fn}")
            if p.exists():
                try:
                    out[name] = int(p.read_text().strip())
                except (OSError, ValueError):
                    pass
                break
    return out


def processes():
    """Per-process RSS+swap. Aggregated by comm as well, because the bleed may
    be spread across many same-named workers rather than one fat process."""
    procs = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            rss = swap = 0
            comm = ""
            with open(f"/proc/{entry.name}/status") as f:
                for line in f:
                    if line.startswith("Name:"):
                        comm = line.split(None, 1)[1].strip()
                    elif line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
                    elif line.startswith("VmSwap:"):
                        swap = int(line.split()[1]) * 1024
                        break
            if rss or swap:
                procs.append({"pid": int(entry.name), "comm": comm,
                              "rss": rss, "swap": swap})
        except (OSError, ValueError, IndexError):
            continue  # process exited mid-read; normal

    by_comm = {}
    for p in procs:
        e = by_comm.setdefault(p["comm"], {"rss": 0, "swap": 0, "n": 0})
        e["rss"] += p["rss"]
        e["swap"] += p["swap"]
        e["n"] += 1
    procs.sort(key=lambda p: p["rss"], reverse=True)
    return {
        "rss_total": sum(p["rss"] for p in procs),
        "swap_total": sum(p["swap"] for p in procs),
        "count": len(procs),
        "top": procs[:TOP_N],
        "by_comm": dict(sorted(by_comm.items(),
                               key=lambda kv: kv[1]["rss"], reverse=True)[:TOP_N]),
    }


def router_models():
    """Which models are resident — so a restart or swap is visible in the trace
    and cannot be mistaken for a bleed."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9292/models", timeout=5) as r:
            data = json.load(r)
        return {m["id"]: m.get("status", {}).get("value") for m in data.get("data", [])}
    except Exception:  # noqa: BLE001 - never let this kill a sample
        return {}


def rotate():
    """Truncate rather than delete: an open reader keeps working, and this file
    is diagnostic data, not something to grow without bound on a box whose
    whole problem is resource exhaustion."""
    try:
        if OUT.exists() and OUT.stat().st_size > MAX_BYTES:
            keep = OUT.read_text().splitlines()[-5000:]
            OUT.write_text("\n".join(keep) + "\n")
    except OSError:
        pass


def main() -> None:
    sample = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "uptime": float(open("/proc/uptime").read().split()[0]),
        "meminfo": meminfo(),
        "vmstat": vmstat(),
        "gpu": gpu(),
        "proc": processes(),
        "models": router_models(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rotate()
    with open(OUT, "a") as f:
        f.write(json.dumps(sample, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
