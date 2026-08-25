# AI Infra — Current State

> **Last verified:** 2026-08-25 — host RAM pressure work (Firecrawl socket-activated, sysctl tuned; see changelog).
> Model/router state is UNCHANGED by that work and was last verified 2026-08-20 (from live system — router `:9292` after a clean
> restart, `docker inspect` mounts, child argv, cron pins, and the running
> watchdog process. **qwen3.8-27b evaluation CLOSED: DS4 is the daily driver
> again, qwen3.8-27b-q4 is on-demand only, and the Q8_0 twin is deleted.**)
> This is the living snapshot. Read this before any infra change. Ground truth
> is the config files themselves; if this file disagrees with them, trust the
> config files and fix this file (and log it in `changelog.md`).

## Host

- **Machine:** Beelink GTR9 Pro — AMD Ryzen AI MAX+ 395 (Strix Halo), Radeon 8060S (gfx1151, RDNA 3.5), 128 GB unified memory, Ubuntu (kernel 7.0.0-29).
- **Host RAM pressure (2026-08-25):** GTT holds **109.11 GiB of 122.7 GiB**, leaving only **13.59 GiB** for the entire OS and every service. Process RSS totals just 7.1 GiB — **the memory is in GTT and does NOT show in `ps`**, so "nothing is using it" is a misread. 🔴 **The 103.3 GiB figure below (98.4 DS4 + 4.9 gemma) is STALE by +5.81 GiB** — it was measured at `ctx 131072` bare; DS4 runs `ctx 262144 parallel 3` and gemma `ubatch-size 2048 parallel 4`, and compute buffers scale with ubatch × slots. Partly decomposed 2026-08-25: **`ctx` IS a lever — 262144 -> 131072 returned 2.58 GiB** (measured), well above the ~0.6 GiB the 4.5 MiB/1k KV figure predicts, because compute buffers scale with ctx as well as with ubatch x slots. The earlier "`ctx` is NOT the lever" claim was wrong. ⚠️ **But that 2.58 GiB is NOT claimable** — 128k does not fit the workload (see the DS4 row) and the change was reverted the same day. Remainder still undecomposed — MEASURE, do not estimate.
- **VM tuning:** `/etc/sysctl.d/99-llm-host-memory.conf` — `vm.swappiness=10`, `vm.vfs_cache_pressure=50` (2026-08-25). GTT is **unswappable**, so at the stock swappiness=60 the only reclaimable pages were the working sets of hermes / hindsight-daemon / llama-router — they were being paged out and had to fault back in before answering, which is what "Hermes is slow" actually was.
- **VRAM model:** GTT memory model — BIOS UMA carveout at **512 MB minimum**, so the iGPU reaches ~124 GiB drawing from system RAM. `ttm.pages_limit=32505856`, `amd_iommu=off`, `amdgpu.dcdebugmask=0x12`, `amdgpu.lockup_timeout=10000,60000,10000,10000` in GRUB. `nogttspill` REMOVED (GTT is the memory model, not an overflow path).

## Router / serving

- **Service:** `llama-router.service` (systemd user unit) — llama.cpp's **native router server** on **:9292**. llama-swap is **retired** (config moved to `models.ini`).
- **Unit:** `~/.config/systemd/user/llama-router.service` — docker `llama-router` container, image `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c…a0211`, `--oom-score-adj=1000`, `--models-preset /models.ini --models-max 3`, `--network host`, bind-mounts the model repos (`/models/ds4`, `/models/aux`, `/models/qwen38`) + `LLAMA_CACHE=/models/empty` (mitigates unconfigurable model auto-discovery — see gotchas).
- 🔴 **The router image IS Nathan's fork build** — `kyuz0/amd-strix-halo-toolboxes` tracks `Nathanw1014/llama.cpp:strix-halo-vulkan` (the hand-tuned DS4 Vulkan MoE kernels / `GGML_VK_MMID_F16B` path). Pinned **by digest** (`ca4c4c…a0211`, built 2026-08-04) because the `:vulkan-radv-performance` tag is mutable and gets rebuilt. **Version string `10283 (b7b85da9c)` is a FORK counter, NOT comparable to mainline llama.cpp** — don't read it as "stock Vulkan", and don't propose a switch to Nathan's fork as a change (we are already on it). At this point in time it is the efficient/robust/performant build; re-evaluate only against a genuinely newer/better build. Ground truth = the unit file's digest-pin comment block, lines 83–93.
- **Config file:** `~/llama-stack/config/models.ini` — **RUNTIME copy, bind-mounted to `/models.ini`; this is the one the router actually reads.** Keys are llama-server long options minus `--`; `[*]` is shared defaults; section name IS the model id clients request; router OVERWRITES `--alias` with the section name.
- ⚠️ **There are TWO copies** (they drifted once, found 2026-08-10). `~/Dev/strix-halo-llm-stack/config/models.ini` is the versioned template; the runtime copy above is live. ✅ **Reconciled 2026-08-19 — `diff` is now clean** (the runtime copy was edited in place, then copied over the template). Previously they drifted comment-only. **Editing only the repo copy is a silent no-op.** `diff` them before trusting either. The runtime file is bind-mounted **by inode** (gotchas #8/#12), so it must be edited in place (truncate-and-write, never `sed -i`) and the router restarted afterwards — both were done on 2026-08-19.
- **Shared defaults `[*]`:** `flash-attn=on`, `cache-ram=0` (disables host-RAM prompt cache — 2026-07-19 OOM root cause), `metrics=true`, `no-webui=true`, `jinja=true`, `cache-type-k/v=q8_0`, `n-gpu-layers=999`.

## Models

| id | quant / size | residency | ctx | parallel | t/s (measured) | notes |
|---|---|---|---|---|---|---|
| `gemma4-e4b` | UD-Q4_K_XL + MTP, ~4.9 GiB | **resident** (load-on-startup) | 131072 | 4, kv-unified | ~114 | ALL aux work: Hermes compression/title-gen/curator/background_review, Hindsight retain+consolidation. sps 0.5, draft-mtp n=4, no-mmproj, reasoning-budget 8192 |
| `deepseek-v4-flash` | UD-IQ3_XXS, ~98.4 GiB | **RESIDENT at boot** (2026-08-15) | **262144** | 3, kv-unified | **19.48 decode / 268.98 prefill @pp2053** (MEASURED live 2026-08-12) | primary resident heavy model: Hermes/pi default, all 5 pi-kalam roles, Hindsight reflect, email digest. sps 0.5, cache-reuse 256, no dspark sidecar (OOM-killed 2026-08-06). Cold load 3–11 min. Coexists only with gemma4-e4b (98.4+4.9 = 103.3 GiB < 120 GiB cap). `n_ctx_train` is **1048576** — we run 25% of it. 🔴 **Briefly lowered to 131072 on 2026-08-25 and REVERTED the same day — 128k does not fit this workload.** MEASURED over 38 Telegram sessions / 7 d: mean prompt/call **median 65,835, p90 119,394, max 129,403**. At ctx 131072 the INPUT budget is only 98,304 (window minus Hermes' `max_tokens` 32768 reservation), so **p90 traffic does not fit**, and the median already exceeded the 64,000 compaction threshold. Session lifespan barely moves this (sub-2h sessions already median 60,614), so `session_reset` does not rescue a smaller window — the size comes from tool-heavy agentic turns inside ONE session. The 131072 experiment did return **2.58 GiB of GTT** (108.65 -> 106.07), which is 4x the KV-only estimate — so **`ctx` IS a memory lever** — but that memory is not available at an acceptable fit. Under kv-unified `n_ctx_slot == ctx-size`, NOT split |
| **`qwen3.8-27b-q4`** | **UD-Q4_K_XL, ~34.2 GiB resident** | **on-demand — the ONLY swap target** | 262144 | 1, kv-unified | **31.3 / 32.6 decode** (MEASURED in prod 2026-08-19); 12.4 spec-off | `spec-type = draft-mtp`, **`spec-draft-n-max = 5`**. sha256 `3f227079…bc8b01e`. **Evaluated 2026-08-19 and REJECTED as a daily driver** — faster per token than DS4 but `parallel = 1` with reasoning always on, so it loses on real multi-caller work. MTP embedded (`blk.64.nextn.*`), no sidecar. The Q8_0 twin was deleted 2026-08-20 (section + 29 GB GGUF) |

**Model swap:** `~/Dev/strix-halo-llm-stack/tools/swap-model.sh {ds4|qwen|status}` (path corrected 2026-08-10 — it is NOT `~/llama-stack/swap-model.sh`; that is runtime data and holds no scripts). `qwen` means **qwen3.8-27b-q4**. Both directions measured at **~23 s**, and the script refuses to load a second heavy model unless the first is CONFIRMED evicted. Image-gen (sd.cpp ~19 GiB GTT + 8.7 GiB host RAM) still requires an eviction wrapper.

🔴 **`HEAVY_OTHERS` in `swap-model.sh` and `HEAVY_MODELS` in `watchdog.env` must stay in lockstep** — both list every heavy id, and a stale list in either is a real OOM path, not cosmetics (that is exactly how the 08-19 `swap-model.sh ds4` bug passed its eviction guard vacuously). Both were trimmed to `qwen3.8-27b-q4,deepseek-v4-flash` on 2026-08-20.

✅ **`load-on-startup` is DS4, and that is now the intended policy** (2026-08-20). A router restart brings DS4 back and leaves qwen3.8-27b-q4 unloaded, regardless of what was resident before — which is what you want, because every unattended caller is pinned to DS4. If qwen is wanted after a restart, run `swap-model.sh qwen` by hand.

> **Residency is a runtime state, not config.** The table above is the *steady state after a router restart* — DS4 + gemma; qwen3.8-27b-q4 is `load-on-startup = false` and never comes up on its own. **As of 2026-08-20 the live state matches it again.** A manual `swap-model.sh qwen` or any request naming the unloaded Q4 legitimately changes residency; the watchdog heavy-model mutex evicts as designed. **That is normal operation — do not "fix" it and do not log it as drift.** Check live state with `curl -s localhost:9292/models | python3 -c "import json,sys;[print(m['id'],m['status']['value']) for m in json.load(sys.stdin)['data']]"` before drawing conclusions from this table.
>
> 🔴 **But a hand-loaded Q4 is not free of consequences:** while it is resident, DS4 is not, and every unattended caller pinned to DS4 (both check-in crons, Hermes `model.default`, the overnight cycle) will trigger a router autoload that evicts it. If two callers want two different heavy models at once, the mutex cannot save you — it guards CO-RESIDENCY, not CONTENTION (2026-08-19 21:37 OOM). Load the Q4 when you are at the keyboard, and expect to lose it at 21:35 or 23:00.

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
- `model.default: deepseek-v4-flash`, `provider: custom:local-models` — **restored 2026-08-20** after the 08-19 qwen evaluation had temporarily pointed it at `qwen3.8-27b-q4`. This is what cron jobs with `model: null` resolve to, so it must always name a RESIDENT model.
- **custom_providers** `Local Models` → `http://127.0.0.1:9292/v1`, api_key `unused-llama-router-direct`, max_tokens 32768, provider-level `model: deepseek-v4-flash`, models = **[deepseek-v4-flash, gemma4-e4b, qwen3.8-27b-q4]** (the Q8_0 `qwen3.8-27b` was removed 2026-08-20 along with its GGUF); `model.context_length: 262144` (3-way sync with models.ini + pi contextWindow; briefly 131072 on 2026-08-25, reverted same day). 🔴 **This key also sets Hermes' compaction point, and the formula is NOT `0.5 x ctx`:** it is `max((context_length - max_tokens) x compression.threshold, MINIMUM_CONTEXT_LENGTH)` where `MINIMUM_CONTEXT_LENGTH = 64_000` (`agent/model_metadata.py:413`) and `max_tokens = 32768`. At 262144 -> **114,688** (the percentage governs, so `compression.threshold` is a live knob — `0.8` would give ~183k). At 131072 -> 49,152 floored up to **64,000**, where the floor binds and **`compression.threshold` becomes INERT** (any value <= 0.65 gives the same number). ⚠️ `compression.threshold: 1.0` at ctx 131072 yields 98,304 input + 32,768 output = exactly the window, zero margin — do not use it. Every compaction rewrites the prefix = unavoidable re-prefill of summary+tail (DS4 logs `cache_reuse is not supported by this context`); the system prompt and `protect_first_n` head stay cached.
- ⚠️ **A running Hermes session keeps its pinned model in session state** — config changes do not move it retroactively. Start a new session (or switch in-session) to pick up a `model.default` change. This is what left the user on the slow Q8_0 on 08-19.
- **Cron model pins (both on `deepseek-v4-flash` as of 2026-08-20):** `morning-check-in` `827131b2c8b5` (07:30), `night-check-in` `5d37a9e1e859` (21:35). Every other job runs `model: null` and therefore resolves to `model.default`. 🔴 **These pins are the load-bearing guard for gotcha #6** — an unattended job naming an unloaded ~98 GiB model is the OOM path. Re-check them after ANY model swap.
- Aux roles (compression, title_generation, curator, background_review, delegation) → **gemma4-e4b** at :9292
- `agent.disabled_toolsets: []` (delegation re-enabled)
- 🔴 **`session_reset.mode: idle`, `idle_minutes: 1440` (2026-08-25)** — was `none`, which meant Telegram **topics never rotated**. Hermes keys one session per `chat_id:thread_id`, so each topic is an independent persistent conversation. General got reset by hand 37×/week (median life 2.5 h, prompt back to ~28k); Morning Briefing ran one session for 5 days (**151,920 tok**) and Night Plan for 49.6 h (**115,870 tok**). Those two paid 8–14 min full re-prefills whenever they lost a KV slot — median turn 631 s / 945 s vs General's 349 s. Policy is evaluated **per session entry**, so every topic now rotates on its own 24 h idle clock. ⚠️ **Not hot-reloaded — `GatewayConfig` is built at startup; restart `hermes-gateway.service`.** Valid modes: `none`/`idle`/`daily`/`both`. Resets are non-destructive: transcripts stay in `state.db` (`sessions.retention_days: 90`, `auto_prune: false`) and stay session-searchable.
- ⚠️ **The topic slowness is only half fixed.** The other half is slot starvation: DS4 runs `parallel = 3` but **llama-watchdog's 60 s probe permanently owns slot 2** (6,534 requests at 97 tok avg over 5 days, vs 862 / 1,184 real-conversation requests on slots 0 / 1). Every Hermes topic + the DM + pi + cron agents contend for **two** slots. See gotcha #15.

### Overnight cycle (`overnight-tasks` cron, 23:00)

`~/Dev/automated-workflows/workers/overnight_tasks.py` → `overnight_swap.py` → `swap-model.sh`.

- Skips entirely when there are no pending task files — no swap, no cold load.
- Otherwise runs `swap-model.sh ds4`, which since 2026-08-20 is normally a **no-op confirmation** (DS4 is already resident). It is still FATAL on failure, because the only way it does real work is if a `qwen3.8-27b-q4` was hand-loaded, and the script's confirmed-eviction guard is what stops DS4 loading on top of it.
- 🔴 **There is deliberately NO swap back** (removed 2026-08-20; `swap_to_qwen` and `QWEN_35B` are gone from `overnight_swap.py`, and the tests spec their mocks against the handler so a reintroduced swap-back fails loudly). Before this, the cycle ended by swapping to whatever `swap-model.sh`'s `QWEN` pointed at — which had become the Q4 — so every night with pending tasks would have evicted the daily driver.

## pi

- **Config:** `~/.pi/agent/models.json` — provider `litellm` → `http://localhost:9292/v1`, apiKey `unused-llama-router-direct`, models deepseek-v4-flash + gemma4-e4b, DS4 contextWindow **262144** (briefly 131072 on 2026-08-25, reverted same day), maxTokens 16384. **`defaultModel: deepseek-v4-flash`**. pi-kalam (`~/Dev/pi-kalam`) config.ts already pins all roles to deepseek-v4-flash — no change needed there.

## Services (systemd user units, all active)

| service | port | role |
|---|---|---|
| `llama-router.service` | 9292 | llama.cpp router (models) |
| `llama-watchdog.service` | 9611 | **THE alerting engine** (2026-08-25) — GPU device-lost + metrics relay + **host memory pressure + systemd unit outages**, all to the Telegram **topic 5** of `<SUPERGROUP_CHAT_ID>`. Reads `/proc` directly, so it does NOT depend on VM/exporters |
| `watchdog-heartbeat.timer` | — | dead-man check every 5 min — a **separate process** curling `:9611`, because a wedged watchdog still holds its port and still reads `active` |
| `hindsight-daemon.service` | 9177 | Hermes memory daemon (bank `hermes`, embedded Postgres) |
| `hermes-gateway.service` | — | messaging (Telegram/Slack/WhatsApp) |
| `hermes-dashboard-proxy.socket` | 9119 | **on-demand** dashboard (2026-08-25) — socket always listening; `hermes-dashboard.service` is **disabled at boot**, moved to `:9129`, wakes in ~1 s and stops after 15 min idle. 🔴 Its drop-in sets `Restart=no`; the base unit's `Restart=always` would undo every teardown |
| `firecrawl-proxy.socket` | 3002 | **on-demand** Firecrawl activation (2026-08-25) — socket is always listening; the stack itself is DOWN until a request arrives and stops again after 30 min idle |

Other listeners: SearXNG **:8888** (search). WhatsApp bridge **:3000** (Hermes, self-chat mode). Grafana retired 2026-08-13.

## Alerting (2026-08-25 — replaces Grafana rules)

🔴 **Alerting had been 100% BROKEN and silent.** Enabling forum topics upgraded
"Hermes Group" to a supergroup, re-issuing its chat id; `watchdog.env` still held
the pre-migration `<OLD_GROUP_CHAT_ID>` and **50 alerts were dropped in 30 days**. Both
ids still resolve via `getChat` with the same title, which is why it hid. The
counter that would have caught it (`alerts_failed`) was watched by a **Grafana**
rule that died 2026-08-13 — so the alerting outage started when its own
supervision was removed. `telegram()` now **follows `migrate_to_chat_id`
automatically** and retries HTML failures as plain text.

| covered | by |
|---|---|
| device-lost / wedged model | `probe_loop` (+ recovery) |
| two heavy models co-resident | `heavy_mutex_loop` (OOM guard) |
| Hindsight down/wedged | `hindsight_loop` (`/health` + `database=connected`) |
| **host memory pressure** | `health_loop` — MemAvailable < 3.0 GiB, swap > 80%, direct-reclaim > 3.0×. 🔴 The reclaim check is SUPPRESSED while any model is `loading`: a ~98 GiB cold load drives reclaim to ~16× by design (measured) and would otherwise page on every router restart. MemAvailable/swap still apply during a load |
| **unit outages** | `health_loop` — llama-router, hermes-gateway, both on-demand sockets |
| **watchdog death** | `OnFailure=` + `watchdog-heartbeat.timer` |

⚠️ `hindsight-daemon` is deliberately absent from `HEALTH_UNITS` — its HTTP probe
is strictly stronger than `is-active`. 🔴 The reclaim check is a **rate between
samples**; the cumulative `pgscan_direct/pgsteal_direct` quotient would latch
forever and never recover.

## Network exposure / bind addresses

**Added 2026-08-10** — this file previously recorded no bind addresses, which is part of why the state below went unnoticed.

| port | service | bind | auth |
|---|---|---|---|
| 9292 | llama-router (`--network host`, `--host 127.0.0.1`) | `127.0.0.1` ✅ | none needed — **loopback only since 2026-08-25** (was `0.0.0.0` no-auth). ⚠️ Still IPv4-only; `localhost` consumers reach it by falling back from `::1`, exactly as before |
| 3002 | Firecrawl (systemd socket → `127.0.0.1:3012`) | `127.0.0.1` + `[::1]` ✅ | none (loopback only) — **fixed 2026-08-25**, was `0.0.0.0` no-auth |
| 9611 / 9610 / 9100 | watchdog / amdgpu exporter / node-exporter | **`0.0.0.0`** | none |
| 3000 / 8428 | WhatsApp bridge / VictoriaMetrics | `127.0.0.1` ✅ | — |

> **2026-08-13: Grafana retired.** The `grafana` service was removed from `~/observability/stack/docker-compose.yml` and its container deleted; **:3000 is now owned by the Hermes WhatsApp bridge** (`whatsapp.extra.bridge_port: 3000`). VictoriaMetrics (:8428) and node-exporter (:9100) remain. Grafana-provisioned Telegram alert rules are gone; the llama-watchdog's own Telegram alerts remain.

**Host firewall state: NONE.** `/etc/ufw/ufw.conf` → `ENABLED=no`; firewalld and nftables both inactive. ⚠️ `systemctl is-active ufw` returns **`active`** even when disabled (oneshot unit, `SubState=exited`) — **never use it to check firewall state; read `ufw.conf` or `sudo ufw status verbose`.** Likewise, curling the host's own LAN IP *from the host* proves nothing: ufw accepts everything on `lo` and that traffic takes the `lo` path.

Consequences while unfirewalled — any LAN device can: use the models unauthenticated; trigger a ~98 GiB DS4 autoload (**one unauthenticated request = host-memory DoS**); drive Firecrawl to fetch arbitrary internal URLs (SSRF pivot); read host/GPU telemetry. Scope is LAN-only behind NAT — confirm no port-forward/UPnP on the router.

**Hardening (pending, not applied 2026-08-10):**
- `ufw default deny incoming` + `ufw default allow outgoing` + **`ufw allow from 172.16.0.0/12`** + `ufw enable`. That third rule is mandatory: containers reach host services across the bridges (VictoriaMetrics → the exporters on 9100/9610/9611) and that traffic hits INPUT. Use the `172.16.0.0/12` supernet, not `-i br-…` rules — it covers all three bridges (`172.17`/`172.18`/`172.23`) and survives `br-*` names changing when a compose network is recreated.
- No sshd is listening, so enabling ufw **cannot** lock you out.
- **ufw will NOT cover Firecrawl :3002** — docker-published ports are DNAT'd ahead of filter INPUT. Fix in its compose (`127.0.0.1:3002:3002`), not in ufw.
- ✅ **DONE 2026-08-25 — the router IS now bound to `127.0.0.1`.** This previously said "do not", because OpenWebUI reached `:9292` via `host.docker.internal` from a bridge network. **OpenWebUI was deleted on 2026-08-25 and it was the only such consumer.** VERIFIED: the only remaining container with `extra_hosts` is VictoriaMetrics, and it scrapes 9100/9610/9611 — **never 9292**; every other consumer (Hermes, pi, pi-kalam, Hindsight, email digest) is a host process using `127.0.0.1`. This closes the largest remaining exposure in the table above (`:9292`, `0.0.0.0`, no auth). **Applied and smoke-tested 2026-08-25** (DS4 cold load 2.5 min): `ss` shows `127.0.0.1:9292` only, `<LAN_IP>:9292` is refused, both `localhost` and `127.0.0.1` return 200, and real completions pass on both models. Rollback: `~/.config/systemd/user/llama-router.service.bak-20260825-pre-loopback`.

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
13. 🔴 **`StartLimitBurst=3` / `StartLimitIntervalSec=3600` on `llama-router` counts start ATTEMPTS, not failures.** Four `systemctl --user restart` calls inside an hour left the router DOWN with `Job for llama-router.service failed because start of the service was attempted too often` — which reads like a config error and is not. Recover with `systemctl --user reset-failed llama-router.service && systemctl --user start llama-router.service`. **Budget 3 production restarts per hour**; do tuning sweeps in a standalone container on another port and restart prod once at the end.
15. 🔴 **A Telegram topic is a SEPARATE Hermes session — and `session_reset.mode` decides whether it ever rotates.** Sessions key on `chat_id:thread_id`, so the topic you reset by hand stays fast while the ones you don't grow to 120–150k tokens and pay 8–14 min re-prefills. Measured 2026-08-25: General 349 s median turn vs Night Plan 945 s, purely from session age. `mode` is read **at gateway startup only** — restart `hermes-gateway.service` after changing it, and verify on the RUNNING process (a `Session expiry: N sessions to finalize` line in `~/.hermes/logs/gateway.log`), never on the file. Under `mode: none` that line can never appear, so its presence is the proof.
16. 🔴 **`until grep -q <pattern> <logfile>` is NOT a smoke test on an append-only log** — it matches HISTORICAL lines and exits instantly. Burned on 2026-08-25: the expiry check "passed" against June entries from a policy that had since been switched off. Always scope to lines after the restart timestamp (`awk '$0>="YYYY-MM-DD HH:MM:SS"' log | grep ...`).
14. 🔴 **`spec-draft-n-max` is hardware/build-specific — MEASURE it, never inherit a published value.** On qwen3.8-27b UD-Q4_K_XL the inherited `n=12` cost ~1.6×: `n=3 29.47 | n=5 31.05 | n=6 30.21 | n=7 27.24 | n=12 19.03 | n=20 15.16` t/s. The published "champion" config scored **17.83** here vs our **31.32**.
11. 🔴 **`models.ini` is parsed ONCE, at router startup — a model unload/reload does NOT re-read it.** PROVEN 2026-08-19: after adding `spec-type` to `[qwen3.8-27b]` and doing a full `POST /models/unload` + `/models/load` cycle, the respawned child's argv still carried the OLD flag set with no `--spec-type`. Only `systemctl --user restart llama-router` picks up a config change. This compounds the existing unit-file landmine: **a models.ini edit and a unit edit BOTH need the same restart, and both fail silently until then.** Verify by reading the child argv out of `docker logs llama-router | grep 'I srv          load:'` — never by reading the file.
12. **Editing `models.ini` must preserve the INODE** (see #8). `sed -i` writes a temp file and renames, producing a NEW inode that the container never sees. Use in-place truncate-and-write (Python `open(p,"w")`, or shell `> file` redirection) and re-check with `stat -c %i` inside and outside the container.
10. **ctx-size 3-way sync** for gemma4-e4b: `models.ini` must match `auxiliary.compression.context_length` AND `custom_providers[Local Models].models.gemma4-e4b.context_length` in `~/.hermes/config.yaml`, AND `contextWindow` in `~/.pi/agent/models.json`.

## Docs

- **This canonical store:** `~/Dev/strix-halo-llm-stack/docs/infra/` (`current.md` + `changelog.md` + `README.md`), versioned in the `strix-halo-llm-stack` git repo (pushed to GitHub). **Note the split:** this repo holds code/config/docs; the **runtime model weights + live `config/models.ini` live in `~/llama-stack/`** (gitignored, mounted by `llama-router.service`) — never delete/move those.
- **`~/docs/local-ai-stack.md`** — SUPERSEDED (last accurate 2026-05-15; describes removed granite-4.1-8b/LiteLLM/96 GiB). Kept for history only.
- **`~/docs/infra-todo.md`** — backlog, last updated 2026-05-15.
