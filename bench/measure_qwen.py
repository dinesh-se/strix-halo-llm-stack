#!/usr/bin/env python3
"""Same as measure.py but disables Qwen3 thinking mode for clean TG measurement.

Thinking-on adds reasoning trace tokens (more output) but TG t/s is roughly
the same; turning thinking off here gives a steady comparison vs granite.
"""
import json
import sys
import time
import urllib.request

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9292/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "orchestrator"

PROMPTS = [
    "Reply with a single sentence: what is the capital of France?",
    "List three programming languages, one per line. Nothing else.",
    "Summarize in 2 sentences why caching matters in web apps.",
    "Explain Theseus' paradox in 3 sentences.",
    "Write a 4-line haiku about Vulkan inference on a Strix Halo APU.",
]


def measure(prompt: str, enable_thinking: bool) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 256,
        "temperature": 0.3,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={"content-type": "application/json"},
    )
    t_start = time.perf_counter()
    ttft = None
    tokens = 0
    last_t = None
    with urllib.request.urlopen(req, timeout=180) as resp:
        for line in resp:
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if payload == b"[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or delta.get("reasoning_content")
            if content:
                now = time.perf_counter()
                if ttft is None:
                    ttft = now - t_start
                tokens += 1
                last_t = now
    total = (last_t or time.perf_counter()) - t_start
    gen_time = total - (ttft or 0)
    tg = (tokens - 1) / gen_time if tokens > 1 and gen_time > 0 else 0.0
    return {"ttft_ms": (ttft or 0) * 1000, "tg_tps": tg, "total_s": total, "tokens": tokens}


def run_set(label: str, enable_thinking: bool):
    print(f"\n=== {label} (enable_thinking={enable_thinking}) ===")
    print(f"{'#':<3} {'TTFT (ms)':>10} {'TG (t/s)':>9} {'tokens':>7} {'total (s)':>10}  prompt")
    print("-" * 78)
    ttfts, tgs = [], []
    for i, p in enumerate(PROMPTS, 1):
        r = measure(p, enable_thinking)
        ttfts.append(r["ttft_ms"])
        tgs.append(r["tg_tps"])
        print(f"{i:<3} {r['ttft_ms']:>10.1f} {r['tg_tps']:>9.2f} {r['tokens']:>7d} {r['total_s']:>10.2f}  {p[:40]}")
    print("-" * 78)
    print(f"median TTFT: {sorted(ttfts)[len(ttfts)//2]:.1f} ms   median TG: {sorted(tgs)[len(tgs)//2]:.2f} t/s")


def main():
    print(f"endpoint={BASE_URL}  model={MODEL}")
    run_set("Non-thinking (fast chat)", False)
    run_set("Thinking (deep dialectic)", True)


if __name__ == "__main__":
    main()
