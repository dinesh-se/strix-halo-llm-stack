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

| Role | Model | Quant | Residency | Measured |
|---|---|---|---|---|
| `orchestrator` | Qwen3.6-35B-A3B (MTP) | Q8_0 | always resident | ~59 t/s TG |
| `coder` | Qwen3.6-27B (MTP) | IQ4_XS | on-demand, 30 min idle TTL | ~28 t/s TG, 85% MTP accept |
| `aux-fast` | gpt-oss-20B | MXFP4 | on-demand, 10 min idle TTL | ~78 t/s TG |

All three fit co-resident within the 96 GiB carveout with headroom to spare.
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
  you expect to idle-evict.
- **`--no-mmproj` matters even for text-only use.** Several community GGUF
  repos bundle a vision projector that llama-server auto-loads by default;
  loading it can silently disable other features (like `--cache-reuse`) with
  only a log-line warning. If your client only ever sends text, skip the
  projector explicitly.
- **Speculative decoding (MTP) can be broken by KV cache quantization on some
  architectures** — worth checking upstream issues for your specific model
  family before assuming a quantized-KV + MTP combination works cleanly.
- See [`host/tuning.md`](host/tuning.md) for the VRAM-vs-GTT distinction, why
  `--no-mmap` should be used deliberately rather than everywhere, and a
  host-RAM OOM watch-item that's easy to misdiagnose as a GPU memory problem.

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
