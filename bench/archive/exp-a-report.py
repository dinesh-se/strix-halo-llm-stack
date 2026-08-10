#!/usr/bin/env python3
"""Turn the exp-a JSON dumps into one markdown report answering Q1-Q4."""
import argparse, glob, json, os

REF_TG = 92.60    # bench/baseline-b10200-np1.md, same Q8_0 file, post-GTT-flip
REF_PP = 1135.90
POST = {"sustained": 121.0, "floor": 64.8, "prefill_peak": 1211.0, "ifeval": 78.6}

ORDER = ["radv-q4-mtp", "radv-q4-ngram64", "radv-q8-mtp", "radv-q8-ngram64",
         "radv-q4-none", "radv-q8-none", "radv-q8-ngramdef",
         "hip-q4-mtp", "hip-q4-ngram64", "hip-q8-mtp"]

LABEL = {
    "radv-q4-mtp": "radv · Q4_K_M · draft-mtp 4  ← daytime candidate",
    "radv-q4-ngram64": "radv · Q4_K_M · ngram-mod 64  ← the post, fair 4-bit",
    "radv-q8-mtp": "radv · Q8_0 · draft-mtp 4  ← production reference",
    "radv-q8-ngram64": "radv · Q8_0 · ngram-mod 64",
    "radv-q4-none": "radv · Q4_K_M · no speculation (floor)",
    "radv-q8-none": "radv · Q8_0 · no speculation (floor)",
    "radv-q8-ngramdef": "radv · Q8_0 · ngram-mod, n-max DEFAULT (clamp test)",
    "hip-q4-mtp": "HIP · Q4_K_M · draft-mtp 4",
    "hip-q4-ngram64": "HIP · Q4_K_M · ngram-mod 64",
    "hip-q8-mtp": "HIP · Q8_0 · draft-mtp 4",
}


def load(d, tag, suf):
    p = os.path.join(d, f"{tag}.{suf}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def fmt(v, n=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:      # NaN
            return "—"
        return f"{v:.{n}f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d, L = a.dir, []

    dev_lost = set()
    dl = os.path.join(d, "DEVICE_LOST_ARMS")
    if os.path.exists(dl):
        dev_lost = {x.strip() for x in open(dl) if x.strip()}

    L.append("# Experiment A — ngram-mod vs MTP on Qwen3.6-35B-A3B (Strix Halo)\n")
    L.append("Weights: `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` **Q8_0** — the same file behind "
             "`bench/baseline-b10200-np1.md` (PP 1135.9 / TG 92.6 post-GTT-flip).\n")
    L.append("Every prompt unique; no `ignore_eos`. Repeating prompts is what produces the "
             "source post's own fake 430 t/s row.\n")
    if dev_lost:
        L.append(f"\n> 🔴 **Device-lost / backend abort detected in:** {', '.join(sorted(dev_lost))}\n")

    # ---- single stream ----
    L.append("\n## Single stream (32 unique prompts, ~6k depth, n_predict 256)\n")
    L.append("TTFT = server-side prefill on a ~6k prompt; the metric that decides "
             "'fast enough for daytime chat'.\n")
    L.append("| Arm | TTFT med (ms) | PP med (t/s) | TG med (t/s) | vs Q8 prod | draft acc | errors | short |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    base = load(d, "radv-q8-mtp", "throughput-c1")
    base_tg = base.get("tg_med") if base else None
    for t in ORDER:
        r = load(d, t, "throughput-c1")
        if not r:
            L.append(f"| {LABEL.get(t,t)} | — | — | — | — | — | *not run* | |")
            continue
        tg = r.get("tg_med")
        delta = "—"
        if base_tg and tg and base_tg == base_tg and tg == tg:
            delta = f"{(tg/base_tg-1)*100:+.1f}%"
        L.append(f"| {LABEL.get(t,t)} | {fmt(r.get('ttft_ms_med'),0)} | {fmt(r.get('pp_med'))} | "
                 f"{fmt(r.get('tg_med'),2)} | "
                 f"{delta} | {fmt(r.get('draft_acceptance'),3)} | {r.get('errors')} | "
                 f"{r.get('discarded_short')} |")

    # ---- 4-way ----
    L.append("\n## 4 concurrent streams — the post's '121 t/s sustained' comparison\n")
    L.append("| Arm | sustained (tok/s) | TG med/stream | PP med | draft acc | errors |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for t in ORDER:
        r = load(d, t, "throughput-c4")
        if not r:
            L.append(f"| {LABEL.get(t,t)} | — | — | — | — | *not run* |")
            continue
        L.append(f"| {LABEL.get(t,t)} | **{fmt(r.get('sustained_tok_s'),1)}** | "
                 f"{fmt(r.get('tg_med'),2)} | {fmt(r.get('pp_med'))} | "
                 f"{fmt(r.get('draft_acceptance'),3)} | {r.get('errors')} |")
    L.append(f"\nPost claims **{POST['sustained']} t/s** sustained across 4 slots on "
             f"ROCmFP4 (4-bit); this is Q8_0 (8-bit), so bandwidth-bound decode is "
             f"expected to be lower here regardless of speculation.\n")

    # ---- depth / wedge ----
    L.append("\n## Depth sweep — does radv wedge on wide speculative batches?\n")
    L.append("The post's central claim for requiring HIP. `rep` = repeated-line ratio "
             "(doom-loop detector), `acc` = draft acceptance.\n")
    L.append("| Arm | depth | prompt_n | PP | TG | pred_n | rep | acc | status |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for t in ORDER:
        r = load(d, t, "depth")
        if not r:
            L.append(f"| {LABEL.get(t,t)} | — | | | | | | | *not run* |")
            continue
        for row in r.get("rows", []):
            if "error" in row:
                L.append(f"| {LABEL.get(t,t)} | {row['depth']} | | | | | | | 🔴 {row['error'][:60]} |")
            else:
                flag = "ok"
                if (row.get("repeat_ratio") or 0) > 0.5:
                    flag = "⚠️ doom-loop"
                elif (row.get("nonascii_ratio") or 0) > 0.2:
                    flag = "⚠️ gibberish"
                L.append(f"| {LABEL.get(t,t)} | {row['depth']} | {row.get('prompt_n')} | "
                         f"{fmt(row.get('pp'))} | {fmt(row.get('tg'),2)} | {row.get('predicted_n')} | "
                         f"{fmt(row.get('repeat_ratio'),2)} | {fmt(row.get('draft_acceptance'),3)} | {flag} |")

    # ---- verdicts ----
    L.append("\n## Verdicts\n")
    mtp = load(d, "radv-q8-mtp", "throughput-c1")
    ng = load(d, "radv-q8-ngram64", "throughput-c1")
    none = load(d, "radv-q8-none", "throughput-c1")
    ngd = load(d, "radv-q8-ngramdef", "throughput-c1")
    q4m = load(d, "radv-q4-mtp", "throughput-c1")
    q4n = load(d, "radv-q4-ngram64", "throughput-c1")

    def tg(r):
        return r.get("tg_med") if r else None

    if tg(mtp) and tg(ng):
        if tg(ng) > tg(mtp) * 1.05:
            L.append(f"- **Q1 ngram-mod WINS**: {tg(ng):.1f} vs {tg(mtp):.1f} t/s "
                     f"({(tg(ng)/tg(mtp)-1)*100:+.0f}%). Worth pursuing ACE SABER.")
        elif tg(ng) < tg(mtp) * 0.95:
            L.append(f"- **Q1 ngram-mod LOSES**: {tg(ng):.1f} vs {tg(mtp):.1f} t/s "
                     f"({(tg(ng)/tg(mtp)-1)*100:+.0f}%). Keep draft-mtp.")
        else:
            L.append(f"- **Q1 ngram-mod ties MTP** ({tg(ng):.1f} vs {tg(mtp):.1f} t/s).")
    if tg(none) and tg(ng):
        L.append(f"- **ngram vs no-speculation floor**: {tg(ng):.1f} vs {tg(none):.1f} t/s "
                 f"({(tg(ng)/tg(none)-1)*100:+.0f}%). If ~0, ngram is doing nothing on this workload.")
    if tg(ngd) and tg(ng):
        L.append(f"- **Q2 clamp**: n_max 64 gives {tg(ng):.1f} vs default {tg(ngd):.1f} t/s "
                 f"({(tg(ng)/tg(ngd)-1)*100:+.0f}%). The post claims leaving the default "
                 f"'quietly costs most of the speedup'.")
    if tg(q4m) and tg(mtp):
        L.append(f"- **Quant**: Q4_K_M {tg(q4m):.1f} vs Q8_0 {tg(mtp):.1f} t/s "
                 f"({(tg(q4m)/tg(mtp)-1)*100:+.0f}%) on MTP. Q4_K_M is the fair "
                 f"comparison to the post's 4-bit ROCmFP4.")
    if tg(q4n) and tg(q4m):
        L.append(f"- **ngram vs MTP at 4-bit**: {tg(q4n):.1f} vs {tg(q4m):.1f} t/s "
                 f"({(tg(q4n)/tg(q4m)-1)*100:+.0f}%).")
    hipm = load(d, "hip-q4-mtp", "throughput-c1")
    if tg(hipm) and tg(q4m):
        L.append(f"- **Q4 HIP vs radv (Q4_K_M, MTP)**: {tg(hipm):.1f} vs {tg(q4m):.1f} t/s "
                 f"({(tg(hipm)/tg(q4m)-1)*100:+.0f}%). Prior finding: backends tie "
                 f"within 2% on Qwen3.6.")
    # Daytime-use verdict: TTFT + decode on the best 4-bit arm.
    best = max([r for r in (q4m, q4n) if r and (r.get("tg_med") or 0) == (r.get("tg_med") or 0)],
               key=lambda r: r.get("tg_med") or 0, default=None)
    if best:
        L.append(f"\n- **Daytime-chat verdict**: best 4-bit arm `{best['tag']}` — "
                 f"TTFT {fmt(best.get('ttft_ms_med'),0)} ms on a ~6k prompt, "
                 f"decode {fmt(best.get('tg_med'),1)} t/s. "
                 f"DS4 for comparison is ~19 t/s decode with a much longer cold path.")
    L.append(f"- **Q3 wedge**: see depth table. Device-lost arms: "
             f"{', '.join(sorted(dev_lost)) if dev_lost else 'NONE'}.")
    L.append(f"\nReference: prior radv baseline TG {REF_TG} / PP {REF_PP}. "
             f"Post's no-spec floor {POST['floor']} t/s, prefill peak {POST['prefill_peak']} t/s.\n")

    with open(a.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
