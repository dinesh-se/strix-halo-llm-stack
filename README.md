> ## ⚠️ 2026-08-06: llama-swap has been REPLACED by llama-server's router mode
>
> `llama.cpp` gained a built-in multi-model router (`llama-server` launched with
> no `-m`, driven by `--models-preset` / `--models-max`). It does what llama-swap
> did — per-model flags, child-process crash isolation, idle unload, residency —
> so llama-swap is gone from this stack. See `config/models.ini` and
> `systemd/llama-router.service`.
>
> **The reason it had to happen here:** DeepSeek-V4-Flash's Vulkan kernels
> (Lightning Indexer + fused HC ops) and the `GGML_VK_MMID_F16B` MoE matmul path
> exist only in kyuz0's *fork* build, not in mainline llama.cpp. Mainline
> b10290 ran DS4 at **10.2 t/s vs 18.8** on the fork. Since llama-swap ships
> mainline, no llama-swap image could ever serve DS4 well. ⚠️ kyuz0's build
> number is a FORK counter — it is not comparable to mainline's, so "is build N
> >= 10283?" is not a meaningful test.
>
> **Three things worth stealing from this config:**
> 1. `--network host` is mandatory. Router children get *ephemeral* ports; under
>    bridge networking they are unpublished and a host-side watchdog cannot reach
>    `/slots` — which is the only signal distinguishing "wedged" from
>    "mid-prefill".
> 2. **Scope the bind-mounts.** There is no discovery-disable flag, and
>    `--models-max` caps model COUNT, not SIZE — so one stray model id can pull a
>    73 GiB model into memory. Mount only the model repos you serve (the repo
>    ROOT, not the snapshot dir — the GGUFs are symlinks into `blobs/`), and
>    point `LLAMA_CACHE` at an empty directory.
> 3. **Role aliases do not exist.** The router overwrites `--alias` with the INI
>    section name. Consumers must pin concrete model ids.

# Strix Halo LLM Stack

A local LLM serving stack for AMD Strix Halo (Ryzen AI Max+ 395 / Radeon
8060S) unified-memory hardware, running [llama-swap](https://github.com/mostlygeek/llama-swap)
on Vulkan/RADV. Three co-resident models (an always-on orchestrator, an
on-demand coding model, and a fast/light aux model) sized to fit inside a
single BIOS VRAM carveout with no swapping between them.

This isn't a custom container image — it's the config, kernel tuning, and
hard-won gotchas around the stock upstream `llama-swap:vulkan` image. If
you're looking for a from-source build toolbox, see
[kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes),
which this setup draws on for backend benchmarking.

## Hardware

- AMD Ryzen AI Max+ 395 (Strix Halo), Radeon 8060S iGPU (RADV GFX1151)
- 128 GiB unified RAM, BIOS UMA carveout set to 96 GiB VRAM / ~30 GiB OS
- Ubuntu, current HWE kernel track

## Why Vulkan/RADV, not ROCm

Community benchmarks (kyuz0's grid) show Vulkan RADV winning or tying
token-generation throughput against ROCm/HIP on every model tested on this
hardware class; ROCm only pulls ahead on prompt-processing for dense/BF16
models, which isn't the common case here. RADV also avoids the ROCm install
surface entirely — the container bundles its own Mesa/RADV build, so host
Mesa version is irrelevant to inference performance.

## Model lineup

| Role | Model | Quant | KV | Residency | Measured (llama.cpp b10200) |
|---|---|---|---|---|---|
| `orchestrator` | Qwen3.6-35B-A3B (MTP) | Q8_0 | q8_0 | always resident | 92.6 t/s TG, 831 t/s PP |
| `coder` | Qwen3.6-27B (MTP) | Q6_K | bf16 | on-demand, 30 min idle TTL | 21.2 t/s TG, 218 t/s PP @16.7k, 0.84 MTP accept |
| `aux-fast` | Gemma 4 12B QAT (MTP) | Q4_K_XL | q8_0 | on-demand, 10 min idle TTL | 87.3 t/s TG (median, high variance), 456 t/s PP, 0.85 MTP accept |

All three fit co-resident within the 96 GiB carveout with headroom to spare
(measured: 84.9 GiB with all three loaded; the coder alone is 28.3 GiB at
131k context).

Two notes on reading that table honestly:

- **The coder is the slow one on purpose.** Q6_K + bf16 KV was chosen for
  output quality; it decodes roughly 25% slower than the IQ4_XS + q4_0 config
  this repo shipped earlier. If VRAM or latency matters more to you than
  coding accuracy, that older combination is a reasonable trade.
- **Measure MTP acceptance on varied text.** A repeated-sentence benchmark
  prompt pushes acceptance to 1.000 on these models — the drafter is just
  predicting the repetition. Every acceptance figure above comes from
  randomized prose.
The orchestrator also answers to role aliases (`classifier`, `extractor`) so
downstream consumers can pin a stable name across future model swaps.

**Why not one big model for everything?** A model resident 24/7 that's also
large enough to be a strong coder (e.g. ~120B class) wants most of the 96 GiB
carveout to itself, which leaves no room for a fast aux model or enough
context for a second, on-demand coding-focused model. This lineup trades
"one very strong resident model" for "three co-resident specialists," which
suits an agentic/tool-calling workload better than a single large model does.

## Quick start

1. Set the BIOS VRAM carveout and kernel params — see [`host/tuning.md`](host/tuning.md).
2. `docker compose up -d` — models auto-download via `-hf` on first request
   (large; expect the first pull per model to take a while — `healthCheckTimeout`
   is set generously in `config/llama-swap.yaml` for exactly this).
3. `curl http://localhost:9292/v1/models` to confirm the lineup is live.
4. Size any model swap first: `python3 tools/gguf-vram-estimator.py <gguf> -c <ctx>`.

## Known gotchas

- **llama.cpp Vulkan GPU detection is non-monotonic across builds** — some
  builds silently fall back to CPU with no error, just much lower throughput.
  Always probe `--list-devices` before bumping the pinned image (see
  `docker-compose.yml` for the known-good/bad build list this stack has hit).
- **MXFP4 quants were broken on Vulkan RADV in older Mesa/llama.cpp builds**
  and produced garbage output — fixed upstream; confirmed clean as of the
  build pinned in this repo.
- **A metrics scraper hitting `/upstream/<model>/metrics` on a short interval
  resets llama-swap's idle-eviction counter on every scrape**, making a `ttl`
  setting functionally inert. If you wire up Prometheus/VictoriaMetrics
  scraping, don't point it at the per-model upstream endpoints for a model
  you expect to idle-evict. See [`observability/llama-watchdog`](observability/llama-watchdog)
  for a scraper that avoids this by going direct to the upstream llama-server
  process instead of through llama-swap's proxy.
- **A wedged GPU can pass every liveness check.** `VK_ERROR_DEVICE_LOST` on
  this hardware class (see `host/tuning.md`) leaves llama-server alive,
  answering `/health` and `/v1/models` with 200, while every real completion
  500s — and the process never exits, so nothing restarts it. Only a request
  that actually decodes a token can tell live from wedged. See
  [`observability/llama-watchdog`](observability/llama-watchdog) for the
  probe-and-recover script this repo uses.
- **`--no-mmproj` matters even for text-only use.** Several community GGUF
  repos bundle a vision projector that llama-server auto-loads by default;
  loading it can silently disable other features (like `--cache-reuse`) with
  only a log-line warning. If your client only ever sends text, skip the
  projector explicitly.
- **Speculative decoding (MTP) can be broken by KV cache quantization on some
  architectures** — worth checking upstream issues for your specific model
  family before assuming a quantized-KV + MTP combination works cleanly. (On
  this box: an open llama.cpp issue reports ~0% draft acceptance for
  Gemma4-family MTP with quantized KV. Re-measured 2026-08-01 on b10200 with
  q8_0 KV, 12 runs of randomized prose: **median 0.854, mean 0.777**. It does
  not reproduce here. Test, don't assume either way.)
- **Measure draft acceptance on VARIED text, and report a distribution, not a
  single number.** A repeated-sentence benchmark prompt drives acceptance to
  1.000 on every MTP model in this lineup — the drafter is just predicting the
  repetition, so the figure is meaningless. Worse, a single sample hides the
  shape: `aux-fast` measured over 12 runs spans **0.288 to 0.981**, with 17% of
  runs below the 0.70 "investigate" floor even though the median is 0.854.
- **If decode throughput is bimodal, suspect the drafter before thermals or
  contention.** `aux-fast` decode swings between ~45 and ~95 t/s run to run,
  which looks like throttling or GPU contention. It is neither: across 12 runs
  the correlation between draft acceptance and decode rate was **+0.99**. When
  speculation lands you get several tokens per verification step; when it
  misses you pay full sequential decode. Plot acceptance against throughput
  before touching clocks, fan curves, or batch sizes. A longer draft
  (`--spec-draft-n-max 4` here vs 2 on the coder) plausibly widens this spread,
  since a longer speculative run is more all-or-nothing — untested.
- **A "reasoning" or "thinking" model can burn its entire token budget on the
  reasoning trace and return empty final content**, especially at a modest
  `max_tokens`, and some model families think by default even with no system
  prompt or explicit trigger. If a request is coming back empty with
  `finish_reason: length`, check for a hidden reasoning/thought channel
  before assuming the model is broken — most OpenAI-compatible servers accept
  a `chat_template_kwargs: {enable_thinking: false}` (or equivalent) field to
  suppress it, and a server-side `--reasoning-budget`-style flag (where
  available) is a good backstop for callers that forget to pass it.
- See [`host/tuning.md`](host/tuning.md) for the VRAM-vs-GTT distinction, why
  `--no-mmap` should be used deliberately rather than everywhere, and a
  host-RAM OOM watch-item that's easy to misdiagnose as a GPU memory problem.

## GPU watchdog + metrics relay

[`observability/llama-watchdog`](observability/llama-watchdog) is a small
(~600-line, stdlib-only) Python service that runs alongside llama-swap and
does three things in one loop:

1. **Probes every loaded model with a real one-token completion**, not a
   liveness endpoint. A wedged `VK_ERROR_DEVICE_LOST` server still answers
   `/health` and `/v1/models` — only an actual decode distinguishes it from a
   healthy one. On this box it went unnoticed for ~20 minutes the first time,
   silently 500ing every real request.
2. **Recovers automatically**: unload + re-warm on a confirmed device-lost,
   Telegram alert either way. A slot-aware check distinguishes "busy behind a
   long prefill" from "actually wedged" (both show `is_processing: true` —
   only progressing token counters tell them apart), so it won't kill a model
   that's just doing real work under `--parallel 1`.
3. **Relays each model's `llamacpp:*` metrics** to whatever's scraping
   `:9611`, going **direct to the upstream llama-server** rather than through
   llama-swap's proxy — scraping through the proxy resets the idle-eviction
   timer on every poll and makes `ttl` inert (see Known gotchas above).

Optionally probes a companion service's `/health` too (disabled by default —
see `HINDSIGHT_URL` in `watchdog.env.example`), for anything else you run
alongside this stack that you'd also want paged on.

```sh
cp observability/llama-watchdog/watchdog.env.example observability/llama-watchdog/watchdog.env
# fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (both optional)
mkdir -p ~/.config/systemd/user
cp observability/llama-watchdog/llama-watchdog.service ~/.config/systemd/user/
# edit the two /home/YOU/ paths in that file to match your checkout location
systemctl --user daemon-reload
systemctl --user enable --now llama-watchdog
curl localhost:9611/metrics
```

Prometheus/VictoriaMetrics-format on `:9611`. Point your scrape config at
this port, not at the llama-server upstreams directly, for exactly the
idle-timer reason above.

## Benchmarking

`bench/` has three scripts against the OpenAI-compatible endpoint:

- `mesa_baseline.py [out.md]` — PP/TG baseline per model, for before/after
  Mesa or llama.cpp version comparisons. Produces diffable Markdown.
- `measure.py` / `measure_qwen.py` — streaming TTFT + steady-state TG across
  a small fixed prompt set (the `_qwen` variant also compares thinking-mode
  on/off).
- `toolcall.py` — a 10-case tool-routing fidelity check for an
  orchestrator-style model deciding between delegate/search tools.

`tools/gguf-vram-estimator.py` estimates total VRAM (weights + KV cache) for
a candidate GGUF at a given context length, reading only the GGUF header —
no need to download or load the full model first.

## License

MIT — see [`LICENSE`](LICENSE).
