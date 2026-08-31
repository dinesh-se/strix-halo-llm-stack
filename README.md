# Strix Halo LLM Stack

A local LLM serving stack for AMD Strix Halo (Ryzen AI Max+ 395 / Radeon
8060S) unified-memory hardware, running **llama.cpp's built-in router mode**
on Vulkan/RADV: one small always-resident aux model plus one large resident
model, both served from `:9292`.

This isn't a custom container image — it's the config, kernel tuning, and
hard-won gotchas around a stock upstream image. For a from-source build
toolbox see
[kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes),
which this stack runs on: its build carries Vulkan MoE kernels mainline
llama.cpp lacks, and on DeepSeek-V4-Flash mainline ran **10.2 t/s against
18.8** on the fork. Its version string is a *fork* counter — not comparable to
mainline's, so "is build N >= M?" is not a meaningful test.

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
> production prefill by 19–94%. See [`host/tuning.md`](host/tuning.md).

## Why Vulkan/RADV, not ROCm

Community benchmarks (kyuz0's grid) show RADV winning or tying token
generation against ROCm/HIP on every model tested on this hardware class;
ROCm only leads on prompt processing for dense/BF16 models, which isn't the
common case here. RADV also avoids the ROCm install surface entirely — the
container bundles its own Mesa/RADV, so the host Mesa version is irrelevant to
inference performance.

## Model lineup

Both models are resident together — 98.4 + 4.9 = 103.3 GiB against ~124 GiB
of GTT.

| Role | Model | Quant | Size | ctx | Measured |
|---|---|---|---|---|---|
| aux | Gemma 4 E4B QAT (MTP) | UD-Q4_K_XL, q8_0 KV | 4.9 GiB | 131072 | ~114 t/s TG |
| heavy | DeepSeek-V4-Flash | UD-IQ3_XXS, q8_0 KV | 98.4 GiB | 262144 | see below |

Both run `parallel = 4`, `kv-unified`. DS4 cold-loads in 3–11 minutes, so the
router is configured to load it at startup rather than on demand.

[`tools/swap-model.sh`](tools/swap-model.sh) (`{ds4|status}`) does an ordered
unload-then-load when you need to free the heavy slot — for image generation,
say, which wants ~19 GiB of its own. There is no second heavy model in this
lineup today; the script keeps the mechanism for when one is added back.

**Why an aux model at all?** A model resident 24/7 that is *also* strong
enough for hard reasoning wants essentially all of memory, leaving nothing for
the background work — title generation, compression, memory writes — which on
an agentic workload is most of the request volume by count. A permanently
resident 4.9 GiB model absorbs all of it without ever touching the heavy
model's slots or paying its prefill.

### Read throughput numbers at the right depth

DS4 measured two ways on the same box:

| | decode | prefill |
|---|---|---|
| shallow (pp 2053) | 19.48 t/s | 268.98 t/s |
| **real depth (65,835 tokens)** | **14.39 t/s** | **155.50 t/s** |

Decode falls 26% and prefill 42% between them, so a shallow benchmark
flatters the box by a wide margin. The number that actually matters: one
65,835-token turn takes 441.5 s wall, and **95.97% of it is prefill** — every
decode-side optimisation is competing for the remaining ~4%. Measure at your
own depth before tuning anything.

## Quick start

1. Set the BIOS VRAM carveout (small — see above) and kernel params; see
   [`host/tuning.md`](host/tuning.md).
2. Download the GGUFs you intend to serve and point
   [`config/models.ini`](config/models.ini) at them. Section names are the
   model ids clients request; keys are llama-server long options minus `--`.
3. Copy [`systemd/llama-router.service`](systemd/llama-router.service) to
   `~/.config/systemd/user/`, replace `/home/YOUR_USER/` throughout, and pin
   your own image digest (`PIN_YOUR_OWN_DIGEST`) — resolve it by *pulling*,
   never from a registry HEAD:
   ```sh
   docker pull <tag> && docker inspect --format '{{index .RepoDigests 0}}' <tag>
   ```
4. `systemctl --user daemon-reload && systemctl --user enable --now llama-router`
5. `curl http://localhost:9292/v1/models` to confirm the lineup is live.

Three things in that unit are load-bearing, not incidental:

- **`--network host` is mandatory.** Router children get *ephemeral* ports;
  under bridge networking they are unpublished and a host-side watchdog cannot
  reach `/slots` — the only signal that distinguishes "wedged" from
  "mid-prefill".
- **Scope the bind-mounts.** There is no discovery-disable flag, and
  `--models-max` caps model **count, not size** — one stray model id can pull
  a 73 GiB model into memory. Mount only the model repos you serve (the repo
  *root*, not the snapshot dir — the GGUFs are symlinks into `blobs/`), and
  point `LLAMA_CACHE` at an empty directory.
- **Role aliases do not exist.** The router overwrites `--alias` with the INI
  section name, so every consumer must pin a concrete model id. Swapping a
  model means editing every consumer by hand.

> ⚠️ **`models.ini` is bind-mounted and only read when a child spawns.**
> Editing it — or the unit — changes nothing until
> `systemctl --user daemon-reload && systemctl --user restart llama-router`.
> Skipping that produces a confusing failure: clients are configured for a
> model the router has never heard of, every request returns
> `400 - model '<id>' not found`, and the config on disk looks correct.
> The mount is **by inode**, so edit in place (truncate-and-write, never
> `sed -i`). Keeping a versioned template *and* a live copy invites silent
> drift — `diff` them before trusting either.

## Known gotchas

- **A wedged GPU passes every liveness check.** `VK_ERROR_DEVICE_LOST` on this
  hardware leaves llama-server alive, answering `/health` and `/v1/models`
  with 200, while every real completion 500s — and the process never exits, so
  nothing restarts it. Only a request that actually decodes a token can tell
  live from wedged.
- **Never run `parallel = 1` on a model something probes.** A watchdog probe
  pins to a slot, and at one slot every 60-second probe evicts the prompt
  cache — a ~2-token request scoring `f_sim ~0` falls through to LRU and
  overwrites a real conversation's cached prefix. Give the probe a slot of its
  own; measured ~36% throughput gain going from 1 to 2.
- **llama.cpp Vulkan GPU detection is non-monotonic across builds** — some
  builds silently fall back to CPU with no error, just much lower throughput.
  Probe `--list-devices` before bumping the pinned image, and pin **by
  digest**: a version-pinned tag was rebuilt under this stack mid-session once.
- **A model id containing a dot corrupts YAML written by a dotted-path
  setter.** `qwen3.6-35b` became `qwen3: {6-35b: {...}}` in a downstream
  client's config. It is *valid YAML*, so nothing errors and no log line
  appears; the model simply has no configured context window.
- **`--no-mmproj` matters even for text-only use.** Several community GGUF
  repos bundle a vision projector that llama-server auto-loads; loading it can
  silently disable other features (like `--cache-reuse`) with only a warning.
- **A reasoning model can burn its whole budget on the trace and return empty
  content**, especially at a modest `max_tokens` — and some families think by
  default with no trigger at all. If a request comes back empty with
  `finish_reason: length`, look for a hidden reasoning channel before assuming
  the model is broken. Most OpenAI-compatible servers accept
  `chat_template_kwargs: {enable_thinking: false}`, and `--reasoning-budget`
  is a good server-side backstop. And check what an effort level actually
  *does* in the chat template before setting one — on DS4, `high` and `max`
  are prompt injections ("Absolute maximum with no shortcuts permitted…"),
  which is precisely how you get a 32,768-token, zero-output response.
- **Measure MTP draft acceptance on varied text, and report a distribution.**
  A repeated-sentence prompt drives acceptance to 1.000 on every MTP model
  here — the drafter is just predicting the repetition. A single sample also
  hides the shape: the aux model spans **0.288 to 0.981** across 12 runs, 17%
  of them below the 0.70 "investigate" floor, median 0.854. Test rather than
  assume in either direction — an open llama.cpp issue reports ~0% acceptance
  for Gemma4-family MTP with quantized KV, and it does not reproduce here.
- **If decode throughput is bimodal, suspect the drafter before thermals.**
  Aux decode swings between ~45 and ~95 t/s run to run, which looks like
  throttling or contention. It is neither: across 12 runs, the correlation
  between draft acceptance and decode rate was **+0.99**. Plot that before
  touching clocks, fan curves, or batch sizes.
- **Don't let a metrics scraper double as a liveness signal.** A scraper
  hitting per-model endpoints on a short interval resets an orchestrator's
  idle-eviction timer on every scrape, making a `ttl` setting inert. Any
  orchestrator that treats "was polled" as "was used" has this bug.
- See [`host/tuning.md`](host/tuning.md) for the VRAM-vs-GTT distinction, why
  `--no-mmap` should be used deliberately rather than everywhere, and a
  host-RAM OOM watch-item that's easy to misdiagnose as a GPU memory problem.

## GPU watchdog

[`observability/llama-watchdog`](observability/llama-watchdog) is a small
stdlib-only Python service that runs alongside the router and is the only
alerting this stack has — there is no Prometheus/Grafana deployment here any
more. It covers:

| | |
|---|---|
| device-lost / wedged model | real one-token completion probe, then unload + re-warm |
| host memory pressure | `MemAvailable`, swap, direct-reclaim ratio, read from `/proc` |
| unit outages | llama-router and friends |
| its own death | `OnFailure=` plus a heartbeat timer |

A slot-aware check separates "busy behind a long prefill" from "actually
wedged" — both report `is_processing: true`, and only a progressing token
counter tells them apart — so it won't kill a model doing real work.
Prometheus-format metrics are on `:9611` if you want to scrape them.

> ⚠️ **Verify the alert path itself, on a schedule.** This watchdog was
> silently dead for 30 days: enabling forum topics upgraded the destination
> Telegram group to a supergroup, re-issuing its chat id, and **50 alerts were
> dropped**. Both ids still resolved via `getChat` with the same title, which
> is why it hid. The counter that would have caught it was watched by a
> dashboard rule that had itself been deleted — the outage began when its own
> supervision was removed. Alerting that is only checked when it fires is
> indistinguishable from alerting that does not work.

```sh
cp observability/llama-watchdog/watchdog.env.example observability/llama-watchdog/watchdog.env
# fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (both optional)
cp observability/llama-watchdog/llama-watchdog.service ~/.config/systemd/user/
# edit the two /home/YOU/ paths in that file to match your checkout
systemctl --user daemon-reload && systemctl --user enable --now llama-watchdog
curl localhost:9611/metrics
```

## Benchmarking

`bench/mesa_baseline.py [out.md]` runs a PP/TG baseline per model against the
OpenAI-compatible endpoint, for before/after Mesa or llama.cpp comparisons.
Produces diffable Markdown; set `LLAMA_LOG_DIR` to control where it writes.

`tools/gguf-vram-estimator.py` estimates weights + KV for a candidate GGUF at
a given context length, reading only the GGUF header — no download or load
needed.

> ⚠️ **The estimator is wrong on hybrid/state-space models.** It assumes every
> block carries a KV cache. On a model with `*.ssm.*` metadata (state-space
> layers hold a fixed-size state instead) it overstated KV by **~4×** here —
> 32.5 GiB predicted against ~8.0 GiB measured, a confident and completely
> wrong "this won't fit" call. Grep the GGUF metadata for `ssm.` first; if
> it's there, measure instead of estimating.

## License

MIT — see [`LICENSE`](LICENSE).
