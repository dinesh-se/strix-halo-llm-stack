#!/usr/bin/env python3
"""Score SearXNG result RELEVANCE, not just result count.

Why this exists: on 2026-09-01 SearXNG's bing engine returned HTTP 200, a full
result set, and no `unresponsive_engines` entry -- while serving coherent SERPs
for entirely unrelated queries. Counting results said "healthy"; only scoring
relevance caught it. Run this after any SearXNG image/engine change.

Usage:  python3 searxng-relevance.py [label] [port]
        python3 searxng-relevance.py "after bing disable" 8888
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

# (query, regex that a genuinely relevant result's URL or title should match)
CASES = [
    ("reverse a linked list python",
     r"geeksforgeeks|stackoverflow|leetcode|programiz|realpython|python\.org|linked"),
    ("systemd socket activation tutorial",
     r"systemd|freedesktop|redhat|archlinux|socket|ilmanzo|digitalocean|man7|0pointer"),
    ("SearXNG documentation", r"searxng|searx"),
    ("Ryanair cabin baggage allowance", r"ryanair|baggage|cabin.?bag"),
    ("llama.cpp github releases", r"github|ggml|llama\.cpp|ggerganov"),
    ("Beelink GTR9 Pro review", r"beelink|gtr9|bee-link"),
]
ROUNDS = 3
TOP_N = 5

label = sys.argv[1] if len(sys.argv) > 1 else "searxng"
port = sys.argv[2] if len(sys.argv) > 2 else "8888"

hits = slots = 0
for query, pattern in CASES:
    rx = re.compile(pattern, re.I)
    q_hits = q_slots = 0
    for _ in range(ROUNDS):
        url = f"http://localhost:{port}/search?q={urllib.parse.quote(query)}&format=json"
        try:
            with urllib.request.urlopen(url, timeout=45) as resp:
                results = json.load(resp).get("results", [])[:TOP_N]
        except Exception as exc:  # a dead engine must score 0, not crash the run
            print(f"  ! {type(exc).__name__} on {query!r}")
            results = []
        for item in results:
            q_slots += 1
            if rx.search(item.get("url", "")) or rx.search(item.get("title", "")):
                q_hits += 1
        time.sleep(1.2)
    hits += q_hits
    slots += q_slots
    pct = (q_hits / q_slots * 100) if q_slots else 0.0
    print(f"  {pct:5.1f}%  {query}")

overall = (hits / slots * 100) if slots else 0.0
print(f"  ----> {label}: {hits}/{slots} = {overall:.1f}%")

# Reference points measured 2026-09-01:
#   2026.5.8 image, bing enabled   ->  36.7%   (the broken state)
#   2026.9.1 image, bing enabled   ->  65.6%
#   2026.9.1 image, bing disabled  ->  98.9%   (current)
