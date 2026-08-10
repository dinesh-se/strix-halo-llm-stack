# AI Infra — Current State

> **Last verified:** 2026-08-10 (by Hermes, from live system)
> This is the living snapshot. Read this before any infra change. Ground truth
> is the config files themselves; if this file disagrees with them, trust the
> config files and fix this file (and log it in `changelog.md`).

## Host

- **Machine:** Beelink GTR9 Pro — AMD Ryzen AI MAX+ 395 (Strix Halo), Radeon 8060S (gfx1151, RDNA 3.5), 128 GB unified memory, Ubuntu (kernel 7.0.0-29).
- **VRAM model:** GTT memory model — BIOS UMA carveout at **512 MB minimum**, so the iGPU reaches ~124 GiB drawing from system RAM. `ttm.pages_limit=32505856`, `amd_iommu=off`, `amdgpu.dcdebugmask=0x12`, `amdgpu.lockup_timeout=10000,60000,10000,10000` in GRUB. `nogttspill` REMOVED (GTT is the memory model, not an overflow path).

## Router / serving

- **Service:** `llama-router.service` (systemd user unit) — llama.cpp's **native router server** on **:9292**. llama-swap is **retired** (config moved to `models.ini`).
- **Unit:** `~/.config/systemd/user/llama-router.service` — docker `llama-router` container, image `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c…a0211`, `--oom-score-adj=1000`, `--models-preset /models.ini --models-max 3`, `--network host`, bind-mounts only the 3 model repos (`/models/ds4`, `/models/aux`, `/models/qwen`) + `LLAMA_CACHE=/models/empty` (mitigates unconfigurable model auto-discovery — see gotchas).
- **Config file (single source):** `~/llama-stack/config/models.ini` — keys are llama-server long options minus `--`; `[*]` is shared defaults; section name IS the model id clients request; router OVERWRITES `--alias` with the section name.
- **Shared defaults `[*]`:** `flash-attn=on`, `cache-ram=0` (disables host-RAM prompt cache — 2026-07-19 OOM root cause), `metrics=true`, `no-webui=true`, `jinja=true`, `cache-type-k/v=q8_0`, `n-gpu-layers=999`.

## Models

| id | quant / size | residency | ctx | parallel | t/s (measured) | notes |
|---|---|---|---|---|---|---|
| `gemma4-e4b` | UD-Q4_K_XL + MTP, ~4.9 GiB | **resident** (load-on-startup) | 131072 | 4, kv-unified | ~114 | ALL aux work: Hermes compression/title-gen/curator/background_review, Hindsight retain+consolidation. sps 0.5, draft-mtp n=4, no-mmproj, reasoning-budget 8192 |
| `qwen3.6-35b` | Q8_0 + MTP, ~34 GiB | **resident** (load-on-startup) | 131072 | 2, kv-unified | ~60 (67 w/ MTP) | daytime daily driver; Hermes/pi complex tasks, planning, coding, web search. sps 0.5, draft-mtp n=3, cache-reuse 256, no-mmproj |
| `deepseek-v4-flash` | UD-IQ3_XXS, ~98.4 GiB | **EVICTED at boot** — on demand | 131072 | 3, kv-unified | ~18.8 bare | loaded via `swap-model.sh`; general chat/web/search/agentic. sps 0.5, cache-reuse 256, no dspark sidecar (OOM-killed 2026-08-06). Cold load 3–11 min. CANNOT coexist with qwen3.6-35b (98.4+34+4.9 = 137 > ~120 GiB cap) |

**Model swap:** `~/llama-stack/swap-model.sh` — evicts/loads `deepseek-v4-flash` on demand. Image-gen (sd.cpp ~19 GiB GTT + 8.7 GiB host RAM) also requires an eviction wrapper.

## Role → model mapping (aliases GONE — consumers pin concrete ids)

- `classifier` → **gemma4-e4b** (`~/Dev/automated-workflows/.env` + `workers/llm.py`)
- `extractor` → **qwen3.6-35b** (Hindsight reflect, `HINDSIGHT_API_REFLECT_LLM_MODEL`)
- `memory-writer` → **gemma4-e4b** (Hindsight retain + consolidation)
- ⚠️ These are pinned by hand in consumer config; there is no alias indirection anymore. Swap a model → edit all consumers.

## Hermes

- **Config:** `~/.hermes/config.yaml`
- `model.default: deepseek-v4-flash`, `provider: custom:local-models`
- **custom_providers** `Local Models` → `http://127.0.0.1:9292/v1`, api_key `unused-llama-router-direct`, max_tokens 32768, models = [deepseek-v4-flash, gemma4-e4b, qwen3.6-35b]
- Aux roles (compression, title_generation, curator, background_review, delegation) → **gemma4-e4b** at :9292
- `agent.disabled_toolsets: []` (delegation re-enabled)

## pi

- **Config:** `~/.pi/agent/models.json` — provider `litellm` → `http://localhost:9292/v1`, apiKey `unused-llama-router-direct`, models qwen3.6-35b + deepseek-v4-flash, contextWindow 131072, maxTokens 16384.

## Services (systemd user units, all active)

| service | port | role |
|---|---|---|
| `llama-router.service` | 9292 | llama.cpp router (models) |
| `llama-watchdog.service` | 9611 | GPU device-lost watchdog + per-model `llamacpp:*` metrics relay |
| `hindsight-daemon.service` | 9177 | Hermes memory daemon (bank `hermes`, embedded Postgres) |
| `hermes-gateway.service` | — | messaging (Telegram/Slack/WhatsApp) |
| `hermes-dashboard.service` | 9119 | web UI |

Other listeners: SearXNG **:8888** (search). OpenWebUI **:3001** (via compose, points at :9292).

## Memory architecture

- **Always-injected curated layer:** `~/.hermes/memories/MEMORY.md` + `USER.md` (small, curated).
- **Episodic/searchable layer:** **Hindsight**, `local_embedded` mode, bank `hermes`, **:9177**, local `bge-small-en-v1.5` embeddings + cross-encoder reranker. ⚠️ Daemon must run with `-p hermes` — default profile wants :8888 which collides with SearXNG.
- **Session store:** `~/.hermes/state.db`.
- **Notes vault:** `~/pa-notes` (indexed into Hindsight via `scripts/index_notes.py`, idempotent via mtime state file).

## Known gotchas

1. **Model discovery not configurable** in llama.cpp router — no allowlist/disable flag; `--models-max` caps COUNT not SIZE. Mitigation is the bind-mount + empty `LLAMA_CACHE` in the unit file. Keep them together.
2. **`-cram 0` mandatory** — llama-server's default 8192 MiB/model host-RAM prompt cache is not capacity-enforced on Linux (ggml-org/llama.cpp#22629); it caused the 2026-07-19 OOM kills.
3. **`--parallel` + `--kv-unified` must be explicit** — setting `-np` makes slots non-auto and flips unified KV off, splitting `n_ctx` into N×(N/131072), which lands on Hermes' 64k minimum with zero margin.
4. **`-sps 0.5`** (not 0.10 default) — a short prompt scores f_sim ~0.15 off the chat-template preamble alone and gets routed onto a slot holding a long prefix, destroying it.
5. **Watchdog pins its 60s probe to the LAST slot** (`id_slot`) — an unpinned 2-token probe scores f_sim ~0, falls through to LRU, and overwrites the idle slot's cached prefix. Pinning confines damage to one slot.
6. **DS4/LLM OOM path:** a background reflect (Hindsight `extractor`) can trigger a ~98 GiB autoload with nobody at the keyboard — this OOM-killed llama-server 2026-08-07 11:01:49. `extractor` is pinned to qwen3.6-35b to avoid it.
7. **Hindsight port landmine:** default local URL is :8888 which SearXNG owns. Always explicit `http://127.0.0.1:9177`.
8. **Single-file bind-mount inode trap:** config files bind-mounted into containers are pinned to an INODE — editors that write temp-file-and-rename silently diverge. Verify via `/running`/behavior, not container health.
9. **`persistent`/`load-on-startup` ≠ auto-load guarantee** — after restart, resident models may not be warm until first request. Warm them by hand.
10. **ctx-size 3-way sync** for gemma4-e4b: `models.ini` must match `auxiliary.compression.context_length` AND `custom_providers[Local Models].models.gemma4-e4b.context_length` in `~/.hermes/config.yaml`, AND `contextWindow` in `~/.pi/agent/models.json`.

## Docs

- **This canonical store:** `~/Dev/strix-halo-llm-stack/docs/infra/` (`current.md` + `changelog.md` + `README.md`), versioned in the `strix-halo-llm-stack` git repo (pushed to GitHub). **Note the split:** this repo holds code/config/docs; the **runtime model weights + live `config/models.ini` live in `~/llama-stack/`** (gitignored, mounted by `llama-router.service`) — never delete/move those.
- **`~/docs/local-ai-stack.md`** — SUPERSEDED (last accurate 2026-05-15; describes removed granite-4.1-8b/LiteLLM/96 GiB). Kept for history only.
- **`~/docs/infra-todo.md`** — backlog, last updated 2026-05-15.
