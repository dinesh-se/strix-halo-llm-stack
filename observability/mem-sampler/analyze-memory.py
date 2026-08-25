#!/usr/bin/env python3
"""Diff two memory samples and rank what actually grew.

Answers the question VictoriaMetrics cannot: MemAvailable fell by N GiB —
*where did it go?* Builds an accounting from the unreclaimable consumers and
reports the residual, so a bleed that is NOT attributable is visible as such
rather than hidden behind a plausible-looking list.

  ./analyze-memory.py               # oldest -> newest sample
  ./analyze-memory.py --hours 10    # 10h ago -> newest
  ./analyze-memory.py --around 21:35 --span 2   # bracket a cron
"""
import argparse
import json
import time
from pathlib import Path

GIB = 2 ** 30
MIB = 2 ** 20
DEFAULT = "/home/YOU/strix-halo-llm-stack/observability/mem-sampler/samples.jsonl"


def load(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue          # a torn final line during rotation
    return sorted(out, key=lambda s: s["ts"])


def nearest(samples, ts):
    return min(samples, key=lambda s: abs(s["ts"] - ts))


def fmt(n, unit=GIB, suf="GiB"):
    return f"{n/unit:+.2f} {suf}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--hours", type=float)
    ap.add_argument("--around", help="HH:MM — centre the window on this time")
    ap.add_argument("--span", type=float, default=2.0, help="hours, with --around")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()

    s = load(a.file)
    if len(s) < 2:
        raise SystemExit(f"need >=2 samples, have {len(s)}. Let the timer run.")

    if a.around:
        hh, mm = (int(x) for x in a.around.split(":"))
        base = time.localtime(s[-1]["ts"])
        centre = time.mktime((base.tm_year, base.tm_mon, base.tm_mday,
                              hh, mm, 0, 0, 0, -1))
        lo, hi = nearest(s, centre - a.span*1800), nearest(s, centre + a.span*1800)
    elif a.hours:
        lo, hi = nearest(s, s[-1]["ts"] - a.hours*3600), s[-1]
    else:
        lo, hi = s[0], s[-1]

    if lo["ts"] == hi["ts"]:
        raise SystemExit("window collapsed to one sample — widen it")

    dur = (hi["ts"] - lo["ts"]) / 3600
    print(f"Window: {lo['iso'][:16]} -> {hi['iso'][:16]}  ({dur:.1f} h, "
          f"{len(s)} samples on file)")
    if lo["models"] != hi["models"]:
        print(f"  ⚠️  MODEL STATE CHANGED: {lo['models']} -> {hi['models']}")
    if hi["uptime"] < lo["uptime"]:
        print("  ⚠️  HOST REBOOTED inside this window — deltas are meaningless")

    avail_d = hi["meminfo"]["MemAvailable"] - lo["meminfo"]["MemAvailable"]
    print(f"\nMemAvailable  {lo['meminfo']['MemAvailable']/GIB:.2f} -> "
          f"{hi['meminfo']['MemAvailable']/GIB:.2f} GiB   ({fmt(avail_d)})")
    if avail_d >= 0:
        print("  (memory was RECOVERED over this window)")

    # --- accounting -------------------------------------------------------
    items = []
    g = hi["gpu"].get("gtt_used", 0) - lo["gpu"].get("gtt_used", 0)
    items.append(("GTT (GPU, unswappable)", g))
    for k in ("SUnreclaim", "SReclaimable", "PageTables", "KernelStack",
              "Shmem", "AnonPages", "Cached"):
        items.append((f"kernel: {k}",
                      hi["meminfo"].get(k, 0) - lo["meminfo"].get(k, 0)))

    lo_c, hi_c = lo["proc"]["by_comm"], hi["proc"]["by_comm"]
    for comm in set(lo_c) | set(hi_c):
        d = hi_c.get(comm, {}).get("rss", 0) - lo_c.get(comm, {}).get("rss", 0)
        if abs(d) > 16 * MIB:
            items.append((f"proc RSS: {comm}", d))

    grew = sorted([i for i in items if i[1] > 0], key=lambda x: -x[1])
    shrank = sorted([i for i in items if i[1] < 0], key=lambda x: x[1])

    print(f"\nGREW (top {a.top}):")
    for name, d in grew[:a.top]:
        print(f"  {fmt(d):>12}  {name}")
    if shrank:
        print(f"\nSHRANK (top {a.top}):")
        for name, d in shrank[:a.top]:
            print(f"  {fmt(d):>12}  {name}")

    # GTT + unreclaimable slab + anon are the things that genuinely remove
    # memory from MemAvailable. Cached/SReclaimable are reclaimable and are
    # deliberately EXCLUDED from the accounting total.
    accounted = (g
                 + hi["meminfo"].get("SUnreclaim", 0) - lo["meminfo"].get("SUnreclaim", 0)
                 + hi["meminfo"].get("PageTables", 0) - lo["meminfo"].get("PageTables", 0)
                 + hi["proc"]["rss_total"] - lo["proc"]["rss_total"])
    print(f"\nACCOUNTING for the MemAvailable change:")
    print(f"  {fmt(-avail_d):>12}  memory lost (positive = lost)")
    print(f"  {fmt(accounted):>12}  explained by GTT + SUnreclaim + PageTables + total RSS")
    print(f"  {fmt(-avail_d - accounted):>12}  UNEXPLAINED residual")

    sw = hi["proc"]["swap_total"] - lo["proc"]["swap_total"]
    print(f"\nSwap held by processes: {lo['proc']['swap_total']/MIB:.0f} -> "
          f"{hi['proc']['swap_total']/MIB:.0f} MiB ({fmt(sw, MIB, 'MiB')})")
    d_scan = hi["vmstat"].get("pgscan_direct", 0) - lo["vmstat"].get("pgscan_direct", 0)
    d_steal = hi["vmstat"].get("pgsteal_direct", 0) - lo["vmstat"].get("pgsteal_direct", 0)
    if d_scan > 0:
        print(f"Direct reclaim over window: {d_scan/max(d_steal,1):.1f}x "
              f"(scanned {d_scan}, stole {d_steal})  — >3x means thrashing")
    else:
        print("Direct reclaim over window: none (healthy)")


if __name__ == "__main__":
    main()
