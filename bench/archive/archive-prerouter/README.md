# Pre-router bench harnesses (archived 2026-08-06)

These were written against the llama-swap lineup (qwen3.6-35b / qwen3.6-27b /
gemma4-12b) and/or llama-swap's own endpoints. They are kept for their
methodology, not because they run as-is.

**What breaks if you run them unchanged:**
- Model ids are retired. Worse than failing: the llama-server router **auto-loads
  any model you name**, so a stale id in a MODELS list either 404s or pulls
  something unintended into memory.
- `radv_perf_ab.py` / `rollback_ab.py` A/B against llama-swap *images*; there is
  no llama-swap any more.
- `ubatch_curve.py` used `/app/llama bench` inside the llama-swap image. The
  router image (kyuz0 fork) puts binaries at `/usr/bin/`.

**Still live:** `../mesa_baseline.py` — ported to the router lineup and still the
standing before/after perf gate. Port from that one, not from these.
