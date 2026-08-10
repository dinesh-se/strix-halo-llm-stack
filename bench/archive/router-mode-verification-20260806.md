# llama-server router mode on kyuz0 fork — verification, 2026-08-06

Three tests run before deciding whether to drop llama-swap in favour of
`llama-server --models-preset` (router mode) on kyuz0's fork image.

- image: `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c17d7357b6d69c787d734beb537bc5ae03279d557edfb4b40b1800a0211`
- llama.cpp: `10283 (b7b85da9c)` — **fork counter, not mainline**
- preset: `config/models.ini`, `--models-max 2`, port 10098
- llama-swap idle throughout; `hindsight-daemon` and `llama-watchdog` stopped

## TEST 1 — router starts, spawns child processes: **PASS**

`starting server in router mode. models will be automatically loaded on-demand`.
Child runs as its own process on its own port (log lines prefixed `[44449]`,
`cmd_child_to_router:state:{...}` protocol). `RestartCount: 0`, one load
attempt, no failures. **Crash isolation is preserved** — same robustness model
as llama-swap, which was the main open risk given the device-lost history.

`load-on-startup = true` auto-loaded DS4 with no request. This is **strictly
better than llama-swap's `ttl: 0`**, which prevents eviction but never
auto-loads and forced the manual warm-up loop after every restart.

### Preset translation is complete
`GET /models` returns the resolved child argv. Every INI key mapped:

```
--cache-reuse 256 --host 127.0.0.1 --jinja --metrics --port 44449 --no-webui
--alias deepseek-v4-flash --batch-size 2048 --ctx-size 131072 --cache-ram 0
--cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on --model /root/.cache/...
```

Note `--alias deepseek-v4-flash` — the router overwrites alias with the section
name, as documented. `"aliases": []`. Custom aliases are NOT available.

## TEST 2 — DS4 gets the fork kernels: **PASS**

Gate: zero `not supported, set to disabled` lines. Measured **0**, and 0
mentions of `Lightning Indexer` at all (mainline b10290 emits four disables).

Same harness, same 920-token prompt, three measured runs:

| DS4 host | PP (t/s) | TG (t/s) |
|---|---:|---:|
| llama-swap, mainline b10290 | 157.8 | 10.2 |
| **router mode, kyuz0 fork** | **250.7** | **18.8** |

**+59% prefill, +84% decode.** Decode matches the standalone baseline (18.99),
confirming the router adds no measurable overhead over `docker run` directly.

⚠️ The `MUL_MAT_ID f16-B path engaged (GGML_VK_MMID_F16B)` line seen in the
standalone log did NOT appear here. Kernels are demonstrably active (0 disables,
full speed), so this is presumably a logging difference in router mode — but it
was not explained. Do not use that line as a gate; use the disable count.

## TEST 3 — DS4 + gemma4 co-resident under a large prefill: **PASS, NO MARGIN**

Guard thresholds from the 2026-08-06 overnight run: abort at GTT > 118 GiB or
MemAvailable < 4 GiB. Never tripped.

| | GTT | host RAM avail |
|---|---:|---:|
| DS4 alone | 98.5 GiB | 19.7 GiB |
| + gemma4-12b | **109.9 GiB** | **8.5 GiB** |

gemma4 cost **~11.4 GiB**, not the ~8.7 GiB the old three-model measurement
implied. Forced full prefill (`cache_prompt: false`, random nonce prefix):

```
prompt_n=20452  PP=223.78 t/s  TG=16.40 t/s  wall=100s
peak gtt=109.9 GiB   min mem_avail=8.5 GiB
```

🔴 **Read this as marginal, not comfortable.** The 2026-08-06 morning OOM kills
happened at **108.1 GiB / ~9 GiB free on a 23,412-token prefill**. This
configuration sits at **109.9 GiB / 8.5 GiB free** and was tested at **20,452**
tokens. It survived, but it is the same band that failed, and it was not tested
at 23k+, at 50k, or at 100k where DS4's KV keeps growing. The sidecar was
swapped out for gemma4 and the total barely moved.

### Two measurement traps hit while running this
1. First attempt sized the prompt at **13,049 tokens**, not the intended 23k —
   the chunk-to-token ratio was assumed, not measured. A PASS at 13k would have
   been meaningless. Always print `prompt_n`.
2. Second attempt sent 126,300 chars but reported `prompt_n=11693` — **lower**
   than the shorter first run, because prefix caching served ~12k tokens.
   `cache_prompt: false` **plus a random nonce prefix** is required to force a
   genuine cold prefill; the flag alone is not enough when slot selection can
   still match on LCP.

## Other findings

🔴 **The router auto-discovers every GGUF in the cache and exposes it.**
`/models` listed `gpt-oss-120b`, `Qwen3.5-122B-A10B` (73 GiB), the old
`DeepSeek-V4-Flash IQ2_XXS`, both stale 27b quants, and two 35b quants — all
`unloaded` but all loadable by name. **`--models-max` caps COUNT, not SIZE**, so
one mistyped model id can pull 73 GiB in beside DS4. Before adopting: prune
`hf-cache` or point `--models-dir` at a directory holding only served models.

⚠️ **Cold load was ~11 minutes** from a cold page cache (vs ~3 min warm). With
`--models-max 2`, anything that evicts DS4 costs that. `load-on-startup` covers
boot; it does not cover mid-day eviction.

## Verdict

Router mode is functionally sufficient to replace llama-swap for a DS4 + one-aux
lineup, and it is the only way to run DS4 at full speed under an orchestrator.
The blocker is not the orchestrator — it is that **DS4 + any co-resident model
leaves ~8.5 GiB of host RAM**, in the band that has already OOM-killed twice.
