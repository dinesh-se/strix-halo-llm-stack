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
8060S) unified-memory hardware, running **llama.cpp's built-in router mode**
on Vulkan/RADV: a small always-resident aux model plus one heavy model, with
the heavy slot swapped on demand.

This isn't a custom container image — it's the config, kernel tuning, and
hard-won gotchas around a stock upstream image. If you're looking for a
from-source build toolbox, see
[kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes),
which this stack runs on (its fork carries Vulkan kernels mainline lacks) and
draws on for backend benchmarking.

## Hardware

- AMD Ryzen AI Max+ 395 (Strix Halo), Radeon 8060S iGPU (RADV GFX1151)
- 128 GiB unified RAM
- BIOS UMA carveout set to **512 MB**, giving **~124 GiB of GTT**
- Ubuntu, current HWE kernel track

> ⚠️ **The carveout should be small, not large.** An earlier revision of this
> repo told you to set a 96 GiB VRAM carveout. That was wrong on Linux — it is
> a *Windows* constraint. On Linux the GPU reaches unified memory through GTT,
> and a large carveout just fences off memory the OS can no longer use, while
> capping you *below* what GTT would have given. Measured here: dropping the
> carveout 96 GiB → 512 MB raised usable GPU memory to ~124 GiB and improved
> production prefill by 19–94%. See [`host/tuning.md`](host/tuning.md) for the
> VRAM-vs-GTT distinction.

## Why Vulkan/RADV, not ROCm

Community benchmarks (kyuz0's grid) show Vulkan RADV winning or tying
token-generation throughput against ROCm/HIP on every model tested on this
hardware class; ROCm only pulls ahead on prompt-processing for dense/BF16
models, which isn't the common case here. RADV also avoids the ROCm install
surface entirely — the container bundles its own Mesa/RADV build, so host
Mesa version is irrelevant to inference performance.

## Model lineup

**One always-resident aux model, plus one of two heavy models.**

| Role | Model | Quant | KV | Size | Residency |
|---|---|---|---|---|---|
| `aux-fast` | Gemma 4 E4B QAT (MTP) | Q4_K_XL | q8_0 | 4.9 GiB | always resident |
| heavy (default) | Qwen3.6 35B A3B MTP | Q8_0 | q8_0 | 34 GiB | resident by default |
| heavy (opt-in) | DeepSeek-V4-Flash | UD-IQ3_XXS | q8_0 | ~97.5 GiB | on demand, evicts Qwen3.6 |

**The two heavy models cannot coexist** — 34 + 97.5 + 4.9 = 137 GiB against
~124 GiB of GTT. [`tools/swap-model.sh`](tools/swap-model.sh) does the
unload-then-load in the right order:

```sh
tools/swap-model.sh status     # which heavy model is resident
tools/swap-model.sh qwen       # evicts DS4, loads Qwen3.6
tools/swap-model.sh ds4        # evicts Qwen3.6, loads DS4
```

Measured on this box:

| | Load time | GTT with aux | Throughput |
|---|---|---|---|
| Qwen3.6 35B | ~30 s | 43.5 GiB | ~60 t/s TG (67 with MTP) |
| DeepSeek-V4-Flash | ~3 min (warm cache) | ~106 GiB | 18.8 t/s TG, 250.7 t/s PP |
| Gemma 4 E4B | seconds | 4.9 GiB | 114 t/s TG, 858 t/s PP (cold) |

With Qwen3.6 resident the box sits at **43.5 GiB GTT with ~71 GiB of host RAM
free**. With DS4 resident that free figure drops to **under 10 GiB**, which is
the band this stack has been OOM-killed in twice — DS4 is genuinely a
you-asked-for-it mode, not a default. Qwen3.6's throughput comes from local
harness measurements on this box, not third-party benchmarks.

**Why not one big model for everything?** A model resident 24/7 that is also
strong enough for hard reasoning wants essentially all of memory, leaving no
room for a fast aux model to absorb background work (title generation,
compression, memory writes) — which on an agentic workload is most of the
request volume by count. Keeping a 4.9 GiB aux model permanently resident
means that traffic never touches the heavy model's slots, and never pays its
prefill.

> ⚠️ **`--models-max` caps model COUNT, not SIZE.** It will not save you from
> the 137 GiB arithmetic above. Ordering the swap yourself (unload, then load)
> is the only thing that does.

## Quick start

1. Set the BIOS VRAM carveout (small — see the warning above) and kernel
   params — see [`host/tuning.md`](host/tuning.md).
2. Download the GGUFs you intend to serve, and edit the paths in
   [`config/models.ini`](config/models.ini). The placeholders
   (`YOUR_SNAPSHOT_HASH`) mark what you must fill in.
3. Copy [`systemd/llama-router.service`](systemd/llama-router.service) to
   `~/.config/systemd/user/`, then replace `/home/YOUR_USER/` throughout and
   pin your own image digest (`PIN_YOUR_OWN_DIGEST`) — resolve it by *pulling*,
   never from a registry HEAD:
   ```sh
   docker pull <tag> && docker inspect --format '{{index .RepoDigests 0}}' <tag>
   ```
4. `systemctl --user daemon-reload && systemctl --user enable --now llama-router`
5. `curl http://localhost:9292/v1/models` to confirm the lineup is live.
6. Size any model swap first: `python3 tools/gguf-vram-estimator.py <gguf> -c <ctx>`
   — but see the estimator caveat under Known gotchas.

> ⚠️ **`models.ini` is bind-mounted into a running container and is only read
> when a child spawns.** Editing it — or the unit — changes nothing until you
> `systemctl --user daemon-reload && systemctl --user restart llama-router`.
> Skipping that reload produces a very confusing failure: clients are
> configured for a model the router has never heard of and every request comes
> back `400 - model '<id>' not found`, while the config on disk looks correct.
> `systemctl cat llama-router` warns `changed on disk … version systemd has
> loaded is outdated` — read that line.

## Known gotchas

- **llama.cpp Vulkan GPU detection is non-monotonic across builds** — some
  builds silently fall back to CPU with no error, just much lower throughput.
  Always probe `--list-devices` before bumping the pinned image. Pin the
  image **by digest**, not by tag: a version-pinned tag was rebuilt under this
  stack mid-session once, and a digest resolved 30 minutes earlier then failed
  `manifest unknown`.
- **MXFP4 quants were broken on Vulkan RADV in older Mesa/llama.cpp builds**
  and produced garbage output — fixed upstream; confirmed clean as of the
  build pinned in this repo.
- *(Historical, llama-swap era — kept because the shape recurs.)* **A metrics
  scraper hitting `/upstream/<model>/metrics` on a short interval resets
  llama-swap's idle-eviction counter on every scrape**, making a `ttl` setting
  functionally inert. Any orchestrator that treats "was polled" as "was used"
  has this bug. If you wire up Prometheus/VictoriaMetrics scraping, don't point
  it at per-model endpoints for a model you expect to idle-evict.
- **A model id containing a dot will corrupt YAML config written by a
  dotted-path setter.** Adding `qwen3.6-35b` to a downstream client's config
  produced `qwen3: {6-35b: {context_length: ...}}` — the id was split on the
  `.` in "3.6". It is *valid YAML*, so nothing errors and no log line appears;
  the model simply has no configured context window. After editing any config
  by tooling, re-read it and eyeball the keys.
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
(~600-line, stdlib-only) Python service that runs alongside the router and
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
   `:9611`, going **direct to the child llama-server** rather than through the
   orchestrator's proxy — see the idle-eviction gotcha above for why that
   indirection is worth avoiding.

Note that the router gives children **ephemeral** ports, so the watchdog can
only reach a child's `/slots` if the container runs with `--network host`.
That is why the unit sets it, and it is what makes point 2 possible at all.

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

`bench/mesa_baseline.py [out.md]` runs a PP/TG baseline per model against the
OpenAI-compatible endpoint, for before/after Mesa or llama.cpp version
comparisons. Produces diffable Markdown. Set `LLAMA_LOG_DIR` to control where
it writes.

(Earlier revisions of this repo also shipped `measure.py`, `measure_qwen.py`
and `toolcall.py`. They were pinned to the retired Qwen lineup and were dropped
rather than left to rot; they're in git history if you want them.)

`tools/gguf-vram-estimator.py` estimates total VRAM (weights + KV cache) for
a candidate GGUF at a given context length, reading only the GGUF header —
no need to download or load the full model first.

> ⚠️ **The estimator is wrong on hybrid/state-space models.** It assumes every
> block carries a KV cache. On a model with `*.ssm.*` metadata (state-space
> layers hold a fixed-size state instead), it overstated KV by **~4×** here —
> 32.5 GiB predicted against ~8.0 GiB measured, which produced a confident and
> completely wrong "this won't fit" call. Grep the GGUF metadata for `ssm.`
> first, and if it's there, measure instead of estimating.

## License

MIT — see [`LICENSE`](LICENSE).
