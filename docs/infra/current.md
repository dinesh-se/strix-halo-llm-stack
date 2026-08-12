# AI Infra — Current State

> **Last verified:** 2026-08-12 21:40 IST (by Claude Code, from live system — router `/models` + `/slots` + `/props`, child `/metrics`, `/proc/<pid>/environ`, `ss -tlnp`, live pp2053 benchmark, live Hindsight `/reflect`)
> This is the living snapshot. Read this before any infra change. Ground truth
> is the config files themselves; if this file disagrees with them, trust the
> config files and fix this file (and log it in `changelog.md`).

## Host

- **Machine:** Beelink GTR9 Pro — AMD Ryzen AI MAX+ 395 (Strix Halo), Radeon 8060S (gfx1151, RDNA 3.5), 128 GB unified memory, Ubuntu (kernel 7.0.0-29).
- **VRAM model:** GTT memory model — BIOS UMA carveout at **512 MB minimum**, so the iGPU reaches ~124 GiB drawing from system RAM. `ttm.pages_limit=32505856`, `amd_iommu=off`, `amdgpu.dcdebugmask=0x12`, `amdgpu.lockup_timeout=10000,60000,10000,10000` in GRUB. `nogttspill` REMOVED (GTT is the memory model, not an overflow path).

## Router / serving

- **Service:** `llama-router.service` (systemd user unit) — llama.cpp's **native router server** on **:9292**. llama-swap is **retired** (config moved to `models.ini`).
- **Unit:** `~/.config/systemd/user/llama-router.service` — docker `llama-router` container, image `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c…a0211`, `--oom-score-adj=1000`, `--models-preset /models.ini --models-max 3`, `--network host`, bind-mounts only the 3 model repos (`/models/ds4`, `/models/aux`, `/models/qwen`) + `LLAMA_CACHE=/models/empty` (mitigates unconfigurable model auto-discovery — see gotchas).
- 🔴 **The router image IS Nathan's fork build** — `kyuz0/amd-strix-halo-toolboxes` tracks `Nathanw1014/llama.cpp:strix-halo-vulkan` (the hand-tuned DS4 Vulkan MoE kernels / `GGML_VK_MMID_F16B` path). Pinned **by digest** (`ca4c4c…a0211`, built 2026-08-04) because the `:vulkan-radv-performance` tag is mutable and gets rebuilt. **Version string `10283 (b7b85da9c)` is a FORK counter, NOT comparable to mainline llama.cpp** — don't read it as "stock Vulkan", and don't propose a switch to Nathan's fork as a change (we are already on it). At this point in time it is the efficient/robust/performant build; re-evaluate only against a genuinely newer/better build. Ground truth = the unit file's digest-pin comment block, lines 83–93.
- **Config file:** `~/llama-stack/config/models.ini` — **RUNTIME copy, bind-mounted to `/models.ini`; this is the one the router actually reads.** Keys are llama-server long options minus `--`; `[*]` is shared defaults; section name IS the model id clients request; router OVERWRITES `--alias` with the section name.
- ⚠️ **There are TWO copies and they have drifted** (found 2026-08-10). `~/Dev/strix-halo-llm-stack/config/models.ini` is the versioned template; the runtime copy above is live. Differences as of 2026-08-10 are **comment-only** (header date, mount count — the repo copy still says "the two needed model repos" when three are mounted, and the two disagree about the `extractor` pin). Nothing is functionally broken, but **editing only the repo copy is a silent no-op.** `diff` them before trusting either. Not reconciled on 2026-08-10: the runtime file is bind-mounted **by inode** (gotcha #8), so editing it in place risks silently diverging from the container's view — do it deliberately, then restart the router.
- **Shared defaults `[*]`:** `flash-attn=on`, `cache-ram=0` (disables host-RAM prompt cache — 2026-07-19 OOM root cause), `metrics=true`, `no-webui=true`, `jinja=true`, `cache-type-k/v=q8_0`, `n-gpu-layers=999`.

## Models

| id | quant / size | residency | ctx | parallel | t/s (measured) | notes |
|---|---|---|---|---|---|---|
| `gemma4-e4b` | UD-Q4_K_XL + MTP, ~4.9 GiB | **resident** (load-on-startup) | 131072 | 4, kv-unified | ~114 | ALL aux work: Hermes compression/title-gen/curator/background_review, Hindsight retain+consolidation. sps 0.5, draft-mtp n=4, no-mmproj, reasoning-budget 8192 |
| `qwen3.6-35b` | Q8_0 + MTP, ~34 GiB | **resident** (load-on-startup) | 131072 | 2, kv-unified | ~60 (67 w/ MTP) | daytime daily driver; Hermes/pi complex tasks, planning, coding, web search. sps 0.5, draft-mtp n=3, cache-reuse 256, no-mmproj |
| `deepseek-v4-flash` | UD-IQ3_XXS, ~98.4 GiB | **EVICTED at boot** — on demand | 131072 | 3, kv-unified | **19.48 decode / 268.98 prefill @pp2053** (MEASURED live 2026-08-12) | loaded via `swap-model.sh`; general chat/web/search/agentic. sps 0.5, cache-reuse 256, no dspark sidecar (OOM-killed 2026-08-06). Cold load 3–11 min. CANNOT coexist with qwen3.6-35b (98.4+34+4.9 = 137 > ~120 GiB cap). `n_ctx_train` is **1048576** — we run 12.5% of it |

**Model swap:** `~/Dev/strix-halo-llm-stack/tools/swap-model.sh` (path corrected 2026-08-10 — it is NOT `~/llama-stack/swap-model.sh`; that is runtime data and holds no scripts) — evicts/loads `deepseek-v4-flash` on demand. Image-gen (sd.cpp ~19 GiB GTT + 8.7 GiB host RAM) also requires an eviction wrapper.

> **Residency is a runtime state, not config.** The table above is the *steady state after a router restart*. A manual `swap-model.sh ds4` (or any request naming an unloaded heavy model) legitimately inverts it: DS4 loaded, `qwen3.6-35b` evicted by the watchdog heavy-model mutex, host RAM down to ~8 GiB available. **That is normal operation — do not "fix" it and do not log it as drift.** Check live state with `curl -s localhost:9292/models | jq '.[]|{id,status:.status.value}'` before drawing conclusions from this table. Observed example: 2026-08-10 14:15 (user-initiated DS4 load; mutex evicted qwen at 14:15:50, exactly as designed).

## Role → model mapping (aliases GONE — consumers pin concrete ids)

- `classifier` → **gemma4-e4b** (`~/Dev/automated-workflows/.env` + `workers/llm.py`)
- `extractor` → **gemma4-e4b** (Hindsight reflect, `HINDSIGHT_API_REFLECT_LLM_MODEL`) — ✅ **retargeted off DS4 and VERIFIED LIVE 2026-08-12**
- `memory-writer` → **gemma4-e4b** (Hindsight retain + consolidation, `HINDSIGHT_API_RETAIN_LLM_MODEL` + `HINDSIGHT_API_CONSOLIDATION_LLM_MODEL`)
- ⚠️ These are pinned by hand in consumer config; there is no alias indirection anymore. Swap a model → edit all consumers.

> ✅ **RESOLVED 2026-08-12 — all three Hindsight roles now run on gemma4-e4b.**
> The 2026-08-09 retarget had been written into this file and the `models.ini`
> header but **never applied to the unit** (caught 2026-08-10, still unapplied on
> 08-12). It is now applied and verified on the *running process*, not just the
> unit file:
>
> ```
> /proc/<MainPID>/environ → HINDSIGHT_API_REFLECT_LLM_MODEL=gemma4-e4b
> ```
>
> Check it that way, not with `systemctl --user cat` alone — the 2026-08-01
> failure was a gateway-spawned orphan holding :9177 while carrying **none** of
> the unit's env. Also confirm `:9177` is owned by `MainPID`.
>
> **This one line was the whole change:** only the three role vars are set, the
> bank config overrides no models, and `ReflectRequest` has no per-request model
> field — so nothing else silently stayed on DS4.
> Rollback: `~/.config/systemd/user/hindsight-daemon.service.bak-20260812-pre-reflect-gemma4`.
>
> **The lesson from 08-09→08-12 stands even though the bug is fixed:** docs and
> ini comments asserted a fix that did not exist for three days. Config beats
> prose — verify against the running process.

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

## Network exposure / bind addresses

**Added 2026-08-10** — this file previously recorded no bind addresses, which is part of why the state below went unnoticed.

| port | service | bind | auth |
|---|---|---|---|
| 9292 | llama-router (`--network host`, `--host 0.0.0.0`) | **`0.0.0.0`** | **none** — the api_key `unused-llama-router-direct` is a placeholder the router ignores |
| 3002 | Firecrawl API (docker `-p 0.0.0.0:3002`) | **`0.0.0.0`** | **none** |
| 9611 / 9610 / 9100 | watchdog / amdgpu exporter / node-exporter | **`0.0.0.0`** | none |
| 3001 / 3000 / 8428 | OpenWebUI / Grafana / VictoriaMetrics | `127.0.0.1` ✅ | — |

**Host firewall state: NONE.** `/etc/ufw/ufw.conf` → `ENABLED=no`; firewalld and nftables both inactive. ⚠️ `systemctl is-active ufw` returns **`active`** even when disabled (oneshot unit, `SubState=exited`) — **never use it to check firewall state; read `ufw.conf` or `sudo ufw status verbose`.** Likewise, curling the host's own LAN IP *from the host* proves nothing: ufw accepts everything on `lo` and that traffic takes the `lo` path.

Consequences while unfirewalled — any LAN device can: use the models unauthenticated; trigger a ~98 GiB DS4 autoload (**one unauthenticated request = host-memory DoS**); drive Firecrawl to fetch arbitrary internal URLs (SSRF pivot); read host/GPU telemetry. Scope is LAN-only behind NAT — confirm no port-forward/UPnP on the router.

**Hardening (pending, not applied 2026-08-10):**
- `ufw default deny incoming` + `ufw default allow outgoing` + **`ufw allow from 172.16.0.0/12`** + `ufw enable`. That third rule is mandatory: containers reach host services across the bridges (OpenWebUI → `host.docker.internal:9292`, VictoriaMetrics → the exporters) and that traffic hits INPUT. Use the `172.16.0.0/12` supernet, not `-i br-…` rules — it covers all three bridges (`172.17`/`172.18`/`172.23`) and survives `br-*` names changing when a compose network is recreated.
- No sshd is listening, so enabling ufw **cannot** lock you out.
- **ufw will NOT cover Firecrawl :3002** — docker-published ports are DNAT'd ahead of filter INPUT. Fix in its compose (`127.0.0.1:3002:3002`), not in ufw.
- Do **not** simply rebind the router to `127.0.0.1` — OpenWebUI reaches it via `host.docker.internal:9292` from a bridge network and would break.

## Memory architecture

- **Always-injected curated layer:** `~/.hermes/memories/MEMORY.md` + `USER.md` (small, curated).
- **Episodic/searchable layer:** **Hindsight**, `local_embedded` mode, bank `hermes`, **:9177**, local `bge-small-en-v1.5` embeddings + cross-encoder reranker. ⚠️ Daemon must run with `-p hermes` — default profile wants :8888 which collides with SearXNG.
- **Structured output is GRAMMAR-ENFORCED** (`HINDSIGHT_API_LLM_STRICT_SCHEMA=true`, set 2026-08-12). Upstream defaults this to **False**, which only describes the schema in the prompt and validates with pydantic afterwards — that cost **11.7% of consolidation calls** (9/77 in one day) to `updates[N].observation_id` missing/None, since consolidation is the only *batch* schema (8 ids per call) and gemma4-e4b loses track. With `true`, llama.cpp GBNF-enforces and the field cannot be omitted (verified 40/40 across 5 trials). ⚠️ If a future Hindsight schema ever adds `anyOf`/`oneOf`/`pattern`, llama.cpp's json_schema→GBNF converter may reject it — that failure is HARD, not soft. Roll back to `hindsight-daemon.service.bak-20260812-pre-strict-schema`, or drop `consolidation_llm_batch_size` 8→4 instead.
- **Session store:** `~/.hermes/state.db`.
- **Notes vault:** `~/pa-notes` (indexed into Hindsight via `scripts/index_notes.py`, idempotent via mtime state file).

## Known gotchas

1. **Model discovery not configurable** in llama.cpp router — no allowlist/disable flag; `--models-max` caps COUNT not SIZE. Mitigation is the bind-mount + empty `LLAMA_CACHE` in the unit file. Keep them together.
2. **`-cram 0` mandatory** — llama-server's default 8192 MiB/model host-RAM prompt cache is not capacity-enforced on Linux (ggml-org/llama.cpp#22629); it caused the 2026-07-19 OOM kills.
3. **`--parallel` + `--kv-unified` must be explicit** — setting `-np` makes slots non-auto and flips unified KV off, splitting `n_ctx` into N×(N/131072), which lands on Hermes' 64k minimum with zero margin.
4. **`-sps 0.5`** (not 0.10 default) — a short prompt scores f_sim ~0.15 off the chat-template preamble alone and gets routed onto a slot holding a long prefix, destroying it.
5. **Watchdog pins its 60s probe to the LAST slot** (`id_slot`) — an unpinned 2-token probe scores f_sim ~0, falls through to LRU, and overwrites the idle slot's cached prefix. Pinning confines damage to one slot.
6. **DS4/LLM OOM path — ✅ CLOSED 2026-08-12.** A background reflect (Hindsight `extractor`) could name an unloaded ~98 GiB model and trigger a router autoload with nobody at the keyboard — this OOM-killed llama-server 2026-08-07 11:01:49. Fixed by retargeting reflect to the resident gemma4-e4b (see *Role → model mapping*); verified on the running process, and reflect exercised end-to-end. The watchdog heavy-model mutex is now **defence-in-depth, not the sole guard**.
   ⚠️ **The underlying hazard is unchanged** — the router still has zero memory awareness and `--models-max` still caps model COUNT, not SIZE. **Any** consumer pinned to an unloaded heavy model re-opens this. When adding or repointing a background/unattended caller, pin it to a *resident* model. On this box an OOM is not a tidy process kill: DS4's memory lives in amdgpu GTT and is invisible to the kernel OOM killer, so on 2026-08-06 it reaped the entire GNOME session (pipewire, xdg portals, wezterm, the user systemd manager) instead.
7. **Hindsight port landmine:** default local URL is :8888 which SearXNG owns. Always explicit `http://127.0.0.1:9177`.
8. **Single-file bind-mount inode trap:** config files bind-mounted into containers are pinned to an INODE — editors that write temp-file-and-rename silently diverge. Verify via `/running`/behavior, not container health.
9. **`persistent`/`load-on-startup` ≠ auto-load guarantee** — after restart, resident models may not be warm until first request. Warm them by hand.
10. **ctx-size 3-way sync** for gemma4-e4b: `models.ini` must match `auxiliary.compression.context_length` AND `custom_providers[Local Models].models.gemma4-e4b.context_length` in `~/.hermes/config.yaml`, AND `contextWindow` in `~/.pi/agent/models.json`.

## Docs

- **This canonical store:** `~/Dev/strix-halo-llm-stack/docs/infra/` (`current.md` + `changelog.md` + `README.md`), versioned in the `strix-halo-llm-stack` git repo (pushed to GitHub). **Note the split:** this repo holds code/config/docs; the **runtime model weights + live `config/models.ini` live in `~/llama-stack/`** (gitignored, mounted by `llama-router.service`) — never delete/move those.
- **`~/docs/local-ai-stack.md`** — SUPERSEDED (last accurate 2026-05-15; describes removed granite-4.1-8b/LiteLLM/96 GiB). Kept for history only.
- **`~/docs/infra-todo.md`** — backlog, last updated 2026-05-15.
