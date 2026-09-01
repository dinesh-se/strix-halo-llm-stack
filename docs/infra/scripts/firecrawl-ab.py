#!/usr/bin/env python3
"""A/B two Firecrawl instances on identical targets.

Usage:  python3 firecrawl-ab.py [PORT ...]      # default: 3002 3013
        python3 firecrawl-ab.py 3002            # verify prod alone

Written for the 2026-09-01 v2.11.272 upgrade. To test a new build off-prod:
the compose file hard-codes `name: firecrawl`, so the test stack MUST use a
different project name AND port, or it fights prod for containers:

    git worktree add ~/firecrawl-next -b upgrade-<tag> <tag>
    cp ~/firecrawl/.env ~/firecrawl-next/ && sed -i s/^PORT=3012/PORT=3013/ .env
    docker compose -p firecrawl-next up -d --build

Result 2026-09-01 (old 2026-05-08 build vs v2.11.272): 10/10 PASS, 0 regressions.

Asserts on a MARKER STRING that must appear in the markdown, never on response
size -- a block page or a loaded-but-empty render is still thousands of chars.
"""
import json, sys, time, urllib.request

TARGETS = [
    ("js-render",  "https://quotes.toscrape.com/js/",        "Albert Einstein"),
    ("static",     "https://example.com/",                   "Example Domain"),
    ("wikipedia",  "https://en.wikipedia.org/wiki/Dublin",   "Ireland"),
    ("real-site",  "https://news.ycombinator.com/",          "Hacker News"),
    ("docs",       "https://www.python.org/",                "Python"),
]

def scrape(port, url, timeout=180):
    body = json.dumps({"url": url, "formats": ["markdown"]}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v2/scrape", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        md = (d.get("data") or {}).get("markdown") or ""
        return {"ok": bool(d.get("success")), "md": md, "s": time.time() - t0}
    except Exception as e:
        return {"ok": False, "md": "", "s": time.time() - t0, "err": str(e)[:120]}

def main():
    ports = sys.argv[1:] or ["3002", "3013"]
    fails = 0
    for name, url, marker in TARGETS:
        print(f"\n### {name}  {url}")
        for p in ports:
            r = scrape(p, url)
            hit = marker.lower() in r["md"].lower()
            status = "PASS" if (r["ok"] and hit) else "FAIL"
            if status == "FAIL":
                fails += 1
            print(f"  :{p}  {status}  marker={'Y' if hit else 'N'} "
                  f"chars={len(r['md']):>6} {r['s']:.1f}s {r.get('err','')}")
    print(f"\n==> total FAIL count: {fails}")
    return 1 if fails else 0

sys.exit(main())
