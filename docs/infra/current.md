# AI Infra — Current State

> **Last verified:** 2026-09-04 (later) — **every cloud route moved off OpenCode Zen onto
> OpenCode Go.** Zen's balance was spent (401 `CreditsError`) and the free-tier
> `deepseek-v4-flash-free` was retired (400 "Model is unavailable"), so BOTH `fallback_providers`
> were dead alongside the primary. The new GO key was never at fault — it returned **200** on
> `/zen/go/v1/models`; the failure was **403 `RegionError`** on the DeepSeek family only, which
> needs an explicit China-hosting opt-in (Dinesh opted in; DS4 now 200 on GO). Moved to
> `opencode-go`: `model.provider`, both fallbacks (now `deepseek-v4-pro` -> **local** DS4 at
> :9292, so the last resort cannot bill or expire), `auxiliary.vision`, `delegation`, and
> Hindsight's LLM route. 🔴 Restarting hindsight-daemon **surfaced a latent break from the
> 2026-08-30 hermes 0.21.0 update** — it had run 5 days on already-imported modules: `mcp==2.0.0`
> (Hermes' pin) vs `fastmcp-slim`'s `mcp<2.0`, and `tokenizers 0.23.1` vs `transformers`'
> `<=0.23.0`. **Both error messages were misleading** ("FastMCP server support is not installed",
> "sentence-transformers is required") — the real causes were version skews. Downgraded in the
> shared venv to `mcp 1.29.1` + `tokenizers 0.22.2`; safe because Hermes has no MCP servers and
> imports `mcp` lazily. ⚠️ `hermes update` will re-pin `mcp==2.0.0` and re-break Hindsight.
> ✅ `session_reset.mode` had drifted back to `none` (hand edit, window 09-01 13:07 -> 09-04 09:54;
> `idle_minutes`/`at_hour` untouched and the `~/.hermes-epaq` profile unaffected, so nothing automated
> rewrites it) — **restored to `both` and gateway restarted**; verified via `load_gateway_config()`.
> Serving layer **UNCHANGED** — no `models.ini` / router / model edit.
>
> Prior: 2026-09-04 — **watchdog + router serving layer.** DS4's probe is now
> **opportunistic** (`OPPORTUNISTIC_PROBING_MODELS=deepseek-v4-flash` in watchdog.env): it fires
> only while NO slot is processing — slot 2 becomes a conversation slot under load, idle-time
> wedge detection kept (~3 min), busy-period wedges surface fail-loud via Epa Q.
> Router now runs with **`GGML_VK_MAX_NODES_PER_SUBMIT=64`** (device-lost prevention, upstream
> #24872; verified in the container env). Both live-verified 2026-09-04: watchdog restarted
> clean (skips counter + probe_success OK), gemma loaded, DS4 loading. Baseline: 21 recoveries
> since 2026-08-01 — judge the knob against that over a week.
> Prior: 2026-09-01 — **the research/scrape layer, end to end.** Two components
> were 4 months stale; one was silently broken, the other was not.
> **SearXNG** served coherent-looking results for *unrelated* queries at HTTP 200 with an
> empty `unresponsive_engines` — two stacked faults (a `2026.5.8` image on which google
> returned 0 results silently, plus a broken bing scraper that a prior session had enabled to
> compensate). Now image **`2026.9.1`**, bing **disabled**, google+brave answering: relevance
> **36.7% -> 98.9%** by `docs/infra/scripts/searxng-relevance.py`. 🔴 **`unresponsive_engines`
> is NOT a health check** — it was empty for both the silent-zero and the confident-garbage
> failure. **Firecrawl** was 812 commits behind (base 2026-05-08) but **not** broken; updated
> to **`v2.11.272`** as decay control, A/B'd off-prod 10/10 before promotion, 5/5 after.
> **Camofox** evaluated and installed but deliberately **NOT wired in** (4W-1L-2T over 8 real
> targets; it beats fingerprint checks, loses to commercial WAFs, and returns *less* than
> plain Chromium on LinkedIn). Browser backend forced `off` in both profiles. Serving layer
> **UNCHANGED** — no `models.ini` / router / model edit; DS4 + gemma4-e4b still the only two
> models, both resident.
>
> Prior: 2026-08-31 (later) — **compression split by route.** Hermes'
> summarizer was `gemma4-e4b` for EVERY session including the CLOUD Zen one, and gemma's
> 131,072 window is smaller than the 196,608 trigger it was fed (cause of the 6 gemma
> `Context size has been exceeded` errors on 08-30). Now: new **`~/.hermes-epaq`** profile
> (79 symlinks + one real `config.yaml`) pins Epa Q to a 131,072 *view* -> trigger **73,728**,
> summarizer gemma; main `~/.hermes` goes **`auxiliary.compression.provider: auto`** and drops
> `model.context_length`, so Telegram compacts on Zen. 🔴 **`compression.threshold: 0.5` was
> always INERT** — a 0.75 floor applies under a 512K window; the formula in the
> custom_providers bullet below was wrong and is corrected. `models.ini` UNCHANGED (DS4 stays
> `ctx-size = 262144`). The profile pins `model.provider: custom:local-models` +
> `fallback_providers: []` so local is the DEFAULT, not just a CLI flag, and there is no cloud
> failover. Executor **wired** (`HERMES_HOME` + preflight on the profile config; 1294 tests pass,
> two vacuous leak-guards repointed). ✅ **E2E PASSED — task 28** ran through the dispatcher on
> the profile: 11/11 API calls `provider=custom`, 11 matching DS4 prefills, **zero cloud**; aux
> (title/curator) still on gemma. ⚠️ **The compaction path itself is still unproven** — task 28
> peaked ~23k, under the 73,728 trigger, so no summarizer call was seen; on the first task long
> enough to compact, check the summary lands on gemma's port **58987**, not a DS4 port. Zen's
> real window also unverified (`/v1/models` 403).
> Prior: 2026-08-31 — **44-package `apt upgrade` run; NO kernel update available**
> (running `7.0.0-30-generic`, installed == candidate `7.0.0-30.30`), no reboot, and
> **no infra config touched** — no `models.ini`, no router, no unit files. The serving
> layer was reverified live afterwards (both models `loaded`, `llama-router` container
> never restarted, `Up 26 hours` across the upgrade). 🔴 The **GRUB conffile warning in
> the VRAM-model bullet below was WRONG and has been corrected**: `/etc/default/grub` is
> **ucf-managed**, not a dpkg conffile, and is touched by `grub-efi-amd64` / `shim-signed`,
> NOT by `grub-common`/`grub2-common`. A ucf 3-way prompt is **armed** for the next
> `grub-efi-amd64`/`shim-signed` upgrade — keep the local version. See the changelog.
> Prior: 2026-08-30 (later) — **`Qwen3.8-Flash-Next` (`qwen4exp`) eval
> artifacts removed.** It was never integrated (standalone :10098 eval container,
> never touched `models.ini`, REJECTED 2026-08-26 — decode at real workload depth
> worse than DS4), so this was pure cleanup: deleted the 88 GiB GGUF
> (`~/llama-stack/hf-cache/hub/models--unsloth--Qwen3.8-Flash-Next-GGUF/`) and the
> 671 MiB custom-built `llama.cpp-qwen4exp` eval binary
> (`~/llama-stack/eval-qwen4exp/`), and removed the now-redundant `### Qwen3.8-Flash-Next`
> deep-dive section from this file (the full evaluation write-up already lives in
> the 2026-08-26 changelog entry — nothing was lost, only de-duplicated). No
> config, no router, nothing live was touched.
> Prior: 2026-08-30 — **`qwen3.8-27b-q4` fully decommissioned.** It was the
> last on-demand swap-target model (evaluated 2026-08-19, rejected as a daily driver,
> kept on-demand only); it has had zero real use since. Removed: the `models.ini`
> section (both copies) + its GGUF (17.56 GiB), the `/models/qwen38` bind-mount in
> `llama-router.service`, `HEAVY_MODELS` in `watchdog.env` and `HEAVY_OTHERS` in
> `swap-model.sh` (kept in lockstep as required), the `qwen3.8-27b-q4: {}` entry from
> BOTH `~/.hermes/config.yaml` `custom_providers` entries, and the two now-dead
> `bench-qwen38.sh` / `end-qwen-trial.sh` scripts. `swap-model.sh`'s general
> ds4<->X mechanism was deliberately kept (not retired) for a possible future
> on-demand model — see the changelog for the full edit list. **No on-demand
> alternative model currently exists**; DS4 + gemma4-e4b remain the only two
> models, both always resident. Serving layer otherwise unchanged — router
> restarted once (required, models.ini is parsed only at startup), both models
> confirmed reloaded clean.
> Prior: 2026-08-27 (later) — **Epa Q** (proactive task queue in
> `~/Dev/automated-workflows`) went live: a new DS4 consumer (the *executor*), an
> advisory `ds4_lease` for turn-taking on the single resident model, and a *supervisor*
> that pauses the executor on `/health` failure or critical host-RAM. The `overnight-tasks`
> 23:00 cron and `gpu-price-watch` folded into it; both old crons paused. **Serving layer
> UNCHANGED** — no `models.ini` / `np` / router change (DS4 stays `parallel = 4`). See the
> `### Epa Q` subsection under Hermes, and the changelog.
> Prior: 2026-08-26 21:35 — Qwen3.8-Flash-Next evaluated (standalone :10098 eval
> container, never touched `models.ini`) and REJECTED as a DS4 replacement — decode at real
> workload depth is WORSE than DS4, not just blocked by upstream issues (full write-up in the
> 2026-08-26 changelog entry; its eval artifacts were removed 2026-08-30, see above). Hindsight
> per-op LLM routing regressed to a cloud endpoint and was fixed same session (see Hermes
> section). GRUB flags SSoT drift fixed.
> Prior: 2026-08-26 09:0x — VictoriaMetrics + node-exporter + amdgpu-exporter retired (see changelog).
> Prior: 2026-08-25 — host RAM pressure work (Firecrawl socket-activated, sysctl tuned; see changelog).
> Model/router state (DS4 + gemma resident, qwen3.8-27b-q4 on-demand) is UNCHANGED and was
> reverified live at the end of this session (router `/models`, GTT, `hermes status`).
> **qwen3.8-27b evaluation CLOSED: DS4 is the daily driver
> again, qwen3.8-27b-q4 is on-demand only, and the Q8_0 twin is deleted.**)
> This is the living snapshot. Read this before any infra change. Ground truth
> is the config files themselves; if this file disagrees with them, trust the
> config files and fix this file (and log it in `changelog.md`).

## Host

- **Machine:** Beelink GTR9 Pro — AMD Ryzen AI MAX+ 395 (Strix Halo), Radeon 8060S (gfx1151, RDNA 3.5), 128 GB unified memory, Ubuntu 26.04 LTS (kernel **7.0.0-30-generic**, verified 2026-08-31).
- **Host RAM pressure (2026-08-25):** GTT holds **109.11 GiB of 122.7 GiB**, leaving only **13.59 GiB** for the entire OS and every service. Process RSS totals just 7.1 GiB — **the memory is in GTT and does NOT show in `ps`**, so "nothing is using it" is a misread. 🔴 **The 103.3 GiB figure below (98.4 DS4 + 4.9 gemma) is STALE by +5.81 GiB** — it was measured at `ctx 131072` bare; DS4 runs `ctx 262144 parallel 3` (now `parallel 3` — see the Models table) and gemma `ubatch-size 2048 parallel 4`, and compute buffers scale with ubatch × slots. Partly decomposed 2026-08-25: **`ctx` IS a lever — 262144 -> 131072 returned 2.58 GiB** (measured), well above the ~0.6 GiB the 4.5 MiB/1k KV figure predicts, because compute buffers scale with ctx as well as with ubatch x slots. The earlier "`ctx` is NOT the lever" claim was wrong. ⚠️ **But that 2.58 GiB is NOT claimable** — 128k does not fit the workload (see the DS4 row) and the change was reverted the same day.

🟢 **FULLY DECOMPOSED 2026-08-29 via amdgpu `fdinfo` (per-process `drm-resident-gtt`, read through a `--pid=host` container — no eviction, no restart needed).** This is NOT a leak; it is memory that was never counted:

| client | resident GTT | weights on disk | KV + buffers |
|---|---|---|---|
| DS4 (`deepseek-v4-flash`) | **102.92 GiB** | 97.05 GiB | **+5.87 GiB** |
| gemma4-e4b | **6.48 GiB** | 4.90 GiB | **+1.58 GiB** |
| fdinfo total | 109.40 GiB | | |
| sysfs `mem_info_gtt_used` | 110.09 GiB | | ~0.69 GiB unattributed (desktop/other clients) |

The "+5.81 GiB drift" is exactly this KV + compute-buffer overhead, chained from the 08-25 measurements: ~1.35 GiB base (KV+buffers at ctx 131072/np3) + **2.58 GiB** (ctx 131072→262144, measured above) + ~1.94 GiB (parallel 3→4, inferred by subtraction, not directly measured). **No growth over time observed**: GTT read 110 GiB after 2d9h of router uptime and 110.09 GiB again after 44 min post-restart — static allocation, not accumulating. (Whole-GiB precision on the first reading; call this "no material growth," not proven-zero — a multi-day fdinfo sample would settle it fully.)

🔴 **Consequence: there is no reclaimable waste here.** Every GiB is either model weights or KV/buffer overhead actually in use. gemma's `ctx 131072` is NOT over-provisioned either — its prefill distribution (22 d, 38,439 requests) has **p99.9 = 58,220, max = 99,872** (the compression role summarizes whole conversations), so it needs the headroom. The only large lever on this box is a quant change (UD-IQ2_XXS: **-12.43 GiB**, +3.52% code / +14.70% prose PPL — see the 2026-08-29 changelog entry); `parallel` 4→3 was re-evaluated and APPLIED 2026-09-02 (saves the MEASURED +1.08 GiB): the watchdog-eats-a-slot concern no longer binds because the local caller set shrank (pi dormant; Hermes main chat, delegation children, Hindsight on Zen cloud) — two conversation slots fit the Epa Q executor + lease-serialized briefings. 🔴 2026-09-04: DS4 probing is now **OPPORTUNISTIC** (`OPPORTUNISTIC_PROBING_MODELS=deepseek-v4-flash` in watchdog.env) — the probe fires only while all slots are idle, so slot 2 is a conversation slot under load (3 usable) and the "watchdog eats a slot" framing no longer applies; see the Models table + changelog. NOT -np 2 (one slot, zero margin).
- **VM tuning:** `/etc/sysctl.d/99-llm-host-memory.conf` — `vm.swappiness=10`, `vm.vfs_cache_pressure=50` (2026-08-25). GTT is **unswappable**, so at the stock swappiness=60 the only reclaimable pages were the working sets of hermes / hindsight-daemon / llama-router — they were being paged out and had to fault back in before answering, which is what "Hermes is slow" actually was.
- **VRAM model:** GTT memory model — BIOS UMA carveout at **512 MB minimum**, so the iGPU reaches ~124 GiB drawing from system RAM. 🔴 **SSoT drift fixed 2026-08-26 — this used to list only four of the SIX flags actually in `/etc/default/grub`.** All six, verbatim from `GRUB_CMDLINE_LINUX_DEFAULT`: `amd_iommu=off`, `amdgpu.dcdebugmask=0x12`, `ttm.pages_limit=32505856`, `ttm.page_pool_size=32505856`, `amdgpu.gttsize=126976`, `amdgpu.lockup_timeout=10000,60000,10000,10000`. **`amdgpu.gttsize=126976` is 124 GiB and is the actual source of the "~124 GiB usable" figure above** — it was previously undocumented here. `nogttspill` REMOVED (GTT is the memory model, not an overflow path). 🔴 **CORRECTED 2026-08-31 — `/etc/default/grub` is NOT a dpkg conffile, and `grub2-common` does NOT own it.** `dpkg -S /etc/default/grub` returns *no owning package*; the file is **ucf-managed**, and the only maintainer scripts that invoke `ucf` on it are **`grub-efi-amd64.postinst`**, `shim-signed.postinst` and `kdump-tools.postinst`. The old wording pointed the alarm at the wrong package in both directions: a `grub-common`/`grub2-common` upgrade is **harmless** (verified 2026-08-31 — `grub-common` 2.14-2ubuntu2.1 ships *no maintainer scripts at all*, and `grub2-common`'s postinst contains no `ucf`, no `update-grub` and no `/etc/default/grub` reference; `grub.cfg` is not even regenerated), while a **`grub-efi-amd64` or `shim-signed`** upgrade is the one that can wipe the six flags. 🔴 **A ucf 3-way prompt is ARMED and still pending** for those packages: the ucf cache (`/var/lib/ucf/cache/:etc:default:grub`, `3258f7cb…`, dated 2026-05-22) differs from the currently shipped template (`/usr/share/grub/default/grub`, `6c39b26d…`) on one line — `#GRUB_DISABLE_OS_PROBER=false` → `GRUB_DISABLE_OS_PROBER=true`. Maintainer-side change **plus** local modification = guaranteed conflict prompt on the next `grub-efi-amd64`/`shim-signed` upgrade. **Always keep the locally-modified version** (`N`, the default) — accepting the maintainer's version wipes all six flags. Known-good md5 of the live file: **`7fd80e7a83d6ce0021b8745686b3c836`** — check it after any grub/shim package upgrade.

## Router / serving

- **Service:** `llama-router.service` (systemd user unit) — llama.cpp's **native router server** on **:9292**. llama-swap is **retired** (config moved to `models.ini`).
- **Unit:** `~/.config/systemd/user/llama-router.service` — docker `llama-router` container, image `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c…a0211`, `--oom-score-adj=1000`, `--models-preset /models.ini --models-max 3`, `--network host`, bind-mounts the model repos (`/models/ds4`, `/models/aux`) + `LLAMA_CACHE=/models/empty` (mitigates unconfigurable model auto-discovery — see gotchas). `-e GGML_VK_MAX_NODES_PER_SUBMIT=64` added 2026-09-04 (device-lost prevention knob — upstream ggml-org/llama.cpp#24872; complements the GRUB `amdgpu.lockup_timeout` kernel-side forgiveness; see changelog).
- 🔴 **The router image IS Nathan's fork build** — `kyuz0/amd-strix-halo-toolboxes` tracks `Nathanw1014/llama.cpp:strix-halo-vulkan` (the hand-tuned DS4 Vulkan MoE kernels / `GGML_VK_MMID_F16B` path). Pinned **by digest** (`ca4c4c…a0211`, built 2026-08-04) because the `:vulkan-radv-performance` tag is mutable and gets rebuilt. **Version string `10283 (b7b85da9c)` is a FORK counter, NOT comparable to mainline llama.cpp** — don't read it as "stock Vulkan", and don't propose a switch to Nathan's fork as a change (we are already on it). At this point in time it is the efficient/robust/performant build; re-evaluate only against a genuinely newer/better build. Ground truth = the unit file's digest-pin comment block, lines 83–93.
- **Config file:** `~/llama-stack/config/models.ini` — **RUNTIME copy, bind-mounted to `/models.ini`; this is the one the router actually reads.** Keys are llama-server long options minus `--`; `[*]` is shared defaults; section name IS the model id clients request; router OVERWRITES `--alias` with the section name.
- ⚠️ **There are TWO copies** (they drifted once, found 2026-08-10). `~/Dev/strix-halo-llm-stack/config/models.ini` is the versioned template; the runtime copy above is live. ✅ **Reconciled 2026-08-19 — `diff` is now clean** (the runtime copy was edited in place, then copied over the template). Previously they drifted comment-only. **Editing only the repo copy is a silent no-op.** `diff` them before trusting either. The runtime file is bind-mounted **by inode** (gotchas #8/#12), so it must be edited in place (truncate-and-write, never `sed -i`) and the router restarted afterwards — both were done on 2026-08-19.
- **Shared defaults `[*]`:** `flash-attn=on`, `cache-ram=0` (disables host-RAM prompt cache — 2026-07-19 OOM root cause), `metrics=true`, `no-webui=true`, `jinja=true`, `cache-type-k/v=q8_0`, `n-gpu-layers=999`.

## Models

| id | quant / size | residency | ctx | parallel | t/s (measured) | notes |
|---|---|---|---|---|---|---|
| `gemma4-e4b` | UD-Q4_K_XL + MTP, ~4.9 GiB | **resident** (load-on-startup) | 131072 | 4, kv-unified | ~114 | ALL aux work: Hermes compression/title-gen/curator/background_review, Hindsight retain+consolidation. sps 0.5, draft-mtp n=4, no-mmproj, reasoning-budget 8192 |
| `deepseek-v4-flash` | UD-IQ3_XXS, ~98.4 GiB | **RESIDENT at boot** (2026-08-15) | **262144** | **3, kv-unified** (3->4 on 2026-08-25; 4->3 on 2026-09-02 — caller set shrank: pi dormant, main chat/delegation/Hindsight on Zen; DS4 probe OPPORTUNISTIC since 2026-09-04 — fires only while idle, slot 2 usable under load) | **19.48 decode / 268.98 prefill @pp2053** (2026-08-12) — 🔴 **SHALLOW; do not compare depth numbers to it.** AT REAL DEPTH, MEASURED 2026-08-29: **14.39 decode / 155.50 prefill @ 65,835** (decode -26.1%, prefill -42.2%). One 65,835 turn = 441.5 s wall, of which **95.97% is prefill** — so every decode-side optimisation competes for ~4% of the turn | primary resident heavy model. 🔴 **CALLER LIST CORRECTED 2026-08-29 — it is NOT the "Hermes default".** `~/.hermes/config.yaml` `model.provider` is **`opencode-go` (CLOUD; was `opencode-zen` until 2026-09-04 — Zen credit spent, see changelog)**, so the Telegram conversation does NOT touch this model; the two share the id `deepseek-v4-flash`, which is the same name-collision trap that hid the Epa Q cloud leak on 2026-08-28. Local callers are: **Hermes `delegation` subagents** (config.yaml:374-377, `base_url: 127.0.0.1:9292`), **pi + all 5 pi-kalam roles** (`~/.pi/agent/models.json`), and **the Epa Q executor** (`~/Dev/automated-workflows`, per-task isolated `hermes chat` pinned via `--provider custom:local-models -m deepseek-v4-flash` CLI flags — env-var pin was inert, cloud-leak fixed 2026-08-28; one task at a time — see `### Epa Q` under Hermes). sps 0.5, cache-reuse 256, no dspark sidecar (OOM-killed 2026-08-06). Cold load 3–11 min. Coexists only with gemma4-e4b (98.4+4.9 = 103.3 GiB < 120 GiB cap). `n_ctx_train` is **1048576** — we run 25% of it. 🔴 **Briefly lowered to 131072 on 2026-08-25 and REVERTED the same day — 128k does not fit this workload.** 🔴 **THE FIGURES BELOW MEASURE CLOUD-ZEN TELEGRAM TRAFFIC, NOT THIS MODEL** (corrected 2026-08-29). They were used to justify this model's `ctx-size` and should not have been: Telegram runs on `opencode-zen`. MEASURED over 38 Telegram sessions / 7 d: mean prompt/call **median 65,835, p90 119,394, max 129,403** — *cloud*. **LOCAL DS4's own traffic, measured 2026-08-29 from 3 days of `llama-router` journal (4,395 prefills): 98.7% are <=8,192 tokens; only 5 exceeded 65,536 (0.11%); only 2 exceeded 98,304 (0.05%); ZERO exceeded 131,072; largest single prefill 104,550.** (Those are tokens EVALUATED after cache reuse, so true prompt sizes run somewhat higher.) **Consequence: `ctx 131072` is very likely safe here and would return the measured 2.58 GiB — the compaction-floor objection below is derived from the cloud median and does not apply to local traffic. Not yet actioned; verify against a longer window first, and keep headroom above the 104,550 max.** At ctx 131072 the INPUT budget is only 98,304 (window minus Hermes' `max_tokens` 32768 reservation), so **p90 traffic does not fit**, and the median already exceeded the 64,000 compaction threshold. Session lifespan barely moves this (sub-2h sessions already median 60,614), so `session_reset` does not rescue a smaller window — the size comes from tool-heavy agentic turns inside ONE session. The 131072 experiment did return **2.58 GiB of GTT** (108.65 -> 106.07), which is 4x the KV-only estimate — so **`ctx` IS a memory lever** — but that memory is not available at an acceptable fit. Under kv-unified `n_ctx_slot == ctx-size`, NOT split |

**Model swap:** `~/Dev/strix-halo-llm-stack/tools/swap-model.sh {ds4|status}` (path corrected 2026-08-10 — it is NOT `~/llama-stack/swap-model.sh`; that is runtime data and holds no scripts). No on-demand alternative currently exists — `qwen3.8-27b-q4` (the last one) was decommissioned 2026-08-30; the script's general mechanism was kept for a future model. Image-gen (sd.cpp ~19 GiB GTT + 8.7 GiB host RAM) still requires an eviction wrapper.

🔴 **`HEAVY_OTHERS` in `swap-model.sh` and `HEAVY_MODELS` in `watchdog.env` must stay in lockstep** — both list every heavy id, and a stale list in either is a real OOM path, not cosmetics (that is exactly how the 08-19 `swap-model.sh ds4` bug passed its eviction guard vacuously). Trimmed to `qwen3.8-27b-q4,deepseek-v4-flash` on 2026-08-20; `HEAVY_OTHERS` is now empty and `HEAVY_MODELS` is just `deepseek-v4-flash` (2026-08-30, qwen3.8-27b-q4 decommissioned) — no on-demand model exists to list.

✅ **`load-on-startup` is DS4, and that is now the intended policy** (2026-08-20). A router restart brings DS4 back, regardless of what was resident before — which is what you want, because every unattended caller is pinned to DS4. No on-demand model exists to leave unloaded any more (2026-08-30).

> **Residency is a runtime state, not config.** The table above is the *steady state after a router restart* — DS4 + gemma, both `load-on-startup = true`; no third, on-demand model exists any more (2026-08-30, `qwen3.8-27b-q4` decommissioned). If a future on-demand model is added back, a manual `swap-model.sh <target>` legitimately changes residency and the watchdog heavy-model mutex evicts as designed — **that would be normal operation, not drift.** Check live state with `curl -s localhost:9292/models | python3 -c "import json,sys;[print(m['id'],m['status']['value']) for m in json.load(sys.stdin)['data']]"` before drawing conclusions from this table.
>
> 🔴 **The paragraph that used to be here** (about hand-loading `qwen3.8-27b-q4` and
> the mutex only guarding co-residency, not contention) **no longer applies** — that
> model was decommissioned 2026-08-30 and no on-demand model exists to hand-load. The
> underlying mutex caveat (guards CO-RESIDENCY, not CONTENTION — 2026-08-19 21:37 OOM)
> still stands as a general fact and applies again the moment a future on-demand model
> is added back.

## Role → model mapping (aliases GONE — consumers pin concrete ids)

- `classifier` → **gemma4-e4b** (`~/Dev/automated-workflows/.env` + `workers/llm.py`)
- `extractor` → **gemma4-e4b** (Hindsight reflect, `HINDSIGHT_API_REFLECT_LLM_MODEL`) — ✅ **retargeted off DS4 and VERIFIED LIVE 2026-08-12**
- `memory-writer` → **gemma4-e4b** (Hindsight retain + consolidation, `HINDSIGHT_API_RETAIN_LLM_MODEL` + `HINDSIGHT_API_CONSOLIDATION_LLM_MODEL`)
- ⚠️ These are pinned by hand in consumer config; there is no alias indirection anymore. Swap a model → edit all consumers.

> ✅ **RESOLVED 2026-08-12, REGRESSED + RE-FIXED 2026-08-26.** All three
> Hindsight roles run on gemma4-e4b via the LOCAL router, verified on the
> *running process* (PID 213140):
>
> ```
> /proc/<MainPID>/environ →
>   HINDSIGHT_API_REFLECT_LLM_MODEL=gemma4-e4b        HINDSIGHT_API_REFLECT_LLM_BASE_URL=http://127.0.0.1:9292/v1
>   HINDSIGHT_API_RETAIN_LLM_MODEL=gemma4-e4b         HINDSIGHT_API_RETAIN_LLM_BASE_URL=http://127.0.0.1:9292/v1
>   HINDSIGHT_API_CONSOLIDATION_LLM_MODEL=gemma4-e4b  HINDSIGHT_API_CONSOLIDATION_LLM_BASE_URL=http://127.0.0.1:9292/v1
> ```
>
> Check it that way, not with `systemctl --user cat` alone — the 2026-08-01
> failure was a gateway-spawned orphan holding :9177 while carrying **none** of
> the unit's env. Also confirm `:9177` is owned by `MainPID` (`ss -ltn`, `/health`
> returns `"database":"connected"`).
>
> 🔴 **2026-08-26 regression:** the three `Environment=*_LLM_MODEL` lines were
> found MISSING from the unit — present only as comments — almost certainly
> dropped by the same-day hindsight-all 0.8.4→0.9.2 upgrade and the
> `_MAX_SLOTS`→`_RESERVED_SLOTS` rename. All three roles had silently fallen
> through to the global `EnvironmentFile` (`~/.hindsight/profiles/hermes.env`),
> whose `HINDSIGHT_API_LLM_BASE_URL` is `https://opencode.ai/zen/go/v1` (moved off `/zen/v1` 2026-09-04 with the GO key; Zen balance spent) — **a paid
> cloud endpoint, not the local router.** Consequence: personal memory content
> was leaving the box, and `HINDSIGHT_API_LLM_STRICT_SCHEMA=true` (the fix for
> the 11.7% consolidation failure rate) was **inert**, since GBNF grammar
> enforcement is a llama.cpp-only feature opencode.ai does not implement. Not an
> OOM risk — a cloud endpoint cannot trigger a local heavy-model autoload, so
> gotcha #6 stayed closed throughout.
>
> 🔴 **"Model only" is not a sufficient fix, and the old comment claiming so was
> wrong.** `memory_engine.py` resolves base_url with the identical fallback
> chain as model — `retain_base_url = retain_llm_base_url or config.retain_llm_base_url
> or memory_llm_base_url` — so a model-only override just sends the new model id
> to whatever the global base_url currently is. **Both** `*_LLM_MODEL` and
> `*_LLM_BASE_URL` must be set per op, explicitly, every time. `*_LLM_API_KEY`
> was left on the global `dummy_key` fallback — the local router does not check it.
> Rollback: `~/.config/systemd/user/hindsight-daemon.service.bak-20260826-pre-model-restore`
> (also still has the 08-12 backup for the original fix).
>
> **The lesson from 08-09→08-12 (and now 08-26) stands:** docs and ini comments
> asserted a fix that did not exist. Config beats prose — verify against the
> running process, every time, even for something "already fixed."

## Hermes

- **Config:** `~/.hermes/config.yaml`
- 🔴 **`timezone: 'Europe/Dublin'` (2026-08-25)** — was `''`, which makes `hermes_time.now()` fall back to `datetime.now().astimezone()` and attach a **fixed offset**, not a `ZoneInfo`. `cron/jobs.py` feeds that datetime to `croniter` as its base, so on the autumn DST switch the stale summer offset computed the next fire an hour early **and then fired again at the correct time — a duplicate morning brief** (25 Oct 2026: 06:00 *and* 07:00). Now one fire at 08:00. **Hermes cron is LOCAL wall-clock: never hand-edit a cron expr for DST** — `0 7 * * *` already means 07:00 Dublin year-round. ⚠️ `get_timezone()` **caches per process**, and the cron ticker lives **inside `hermes-gateway.service`** (no separate unit), so restart that and verify on the running process. Today's offset proves nothing (both agree until the transition) — discriminate with a throwaway job scheduled *past* the boundary: `hermes cron create "0 7 25 10 *" ...` → ZoneInfo gives `2026-10-25T08:00:00+00:00`, fixed-offset gives `07:00+01:00`.
- `model.default: deepseek-v4-flash`, `provider: custom:local-models` — **restored 2026-08-20** after the 08-19 qwen evaluation had temporarily pointed it at `qwen3.8-27b-q4`. This is what cron jobs with `model: null` resolve to, so it must always name a RESIDENT model.
- **custom_providers** `Local Models` → `http://127.0.0.1:9292/v1`, api_key `unused-llama-router-direct`, max_tokens 32768, provider-level `model: deepseek-v4-flash`, models = **[deepseek-v4-flash, gemma4-e4b]** (`qwen3.8-27b-q4` removed from this list and from the sibling `Local Models No Think` entry 2026-08-30, model fully decommissioned; the `Local Models No Think` entry itself is **inert since 2026-08-31** — Epa Q's per-task thinking-off routing was reverted, nothing references it now, kept only for manual A/B runs; the Q8_0 `qwen3.8-27b` twin was removed earlier, 2026-08-20, along with its GGUF); `model.context_length: 262144` (3-way sync with models.ini + pi contextWindow; briefly 131072 on 2026-08-25, reverted same day). 🔴 **CORRECTED 2026-08-31 — the formula below omitted the 0.75 small-context floor, so every number it produced was wrong.** `ContextCompressor._effective_threshold_percent()` raises the percentage to **0.75** for any window under `_SMALL_CTX_WINDOW_LIMIT` (512,000), raise-only — so **`compression.threshold: 0.5` is INERT here** and the real trigger at 262144 was **196,608**, not 114,688 (confirmed in `agent.log`: `threshold=196608 (75%)`). `max_tokens` is subtracted only when the provider sets it (32768 on the local custom provider, **None** on Zen). Also note `model.context_length` was **removed 2026-08-31** (it was a leftover from the `custom:local-models` era, never re-derived for Zen) — the window now resolves from provider metadata. The full formula is `max((context_length - max_tokens) x effective_percent, MINIMUM_CONTEXT_LENGTH)` where `MINIMUM_CONTEXT_LENGTH = 64_000` (`agent/model_metadata.py:413`) and `max_tokens = 32768`. All values below are computed with the 0.75 floor applied (verified by calling `_effective_threshold_percent` + `_compute_threshold_tokens` directly, not by hand): **Zen route** (`max_tokens` None) ctx 262144 -> **196,608**; **local** (`max_tokens` 32768) ctx 262144 -> **172,032**, ctx 131072 -> **73,728** (this is what the `~/.hermes-epaq` profile runs). 🔴 **`compression.threshold` is a DEAD knob at or below 0.75** — every value <= 0.75 yields the identical number, which is why the old "`0.8` would give ~183k" framing was misleading: 0.8 does move it (local 262144 -> 183,500, ctx 131072 -> 78,643), but 0.5 through 0.75 are all the same. To lower the trigger, `compression.threshold_tokens` (an absolute cap, applied as `min(ratio_threshold, cap)`) is the only lever — the ratio cannot go below the floor. ⚠️ `compression.threshold: 1.0` at ctx 131072 yields 98,304 input + 32,768 output = exactly the window, zero margin — do not use it. Every compaction rewrites the prefix = unavoidable re-prefill of summary+tail (DS4 logs `cache_reuse is not supported by this context`); the system prompt and `protect_first_n` head stay cached.
- ⚠️ **A running Hermes session keeps its pinned model in session state** — config changes do not move it retroactively. Start a new session (or switch in-session) to pick up a `model.default` change. This is what left the user on the slow Q8_0 on 08-19.
- **Cron model pins (both on `deepseek-v4-flash` as of 2026-08-20):** `morning-check-in` `827131b2c8b5` (07:30), `night-check-in` `5d37a9e1e859` (21:35). Every other job runs `model: null` and therefore resolves to `model.default`. 🔴 **These pins are the load-bearing guard for gotcha #6** — an unattended job naming an unloaded ~98 GiB model is the OOM path. Re-check them after ANY model swap.
- **Epa Q crons (2026-08-27):** `epaq-dispatch` `0fd2c2ecdaed` (*/15), `epaq-supervise` `b97ea1e859c6` (*/10). Both are `--no-agent --script` (`~/.hermes/scripts/run_epaq_{dispatch,supervise}.sh`), so **the cron tick itself makes no model call** — the DS4 consumer is the *executor* that `epaq-dispatch` spawns, and it is pinned to the RESIDENT DS4, so gotcha #6 stays closed. The `.sh` wrappers redirect to their own logs, so `hermes cron --no-agent` sees empty stdout and stays silent each tick.
- 🔴 **`cron.script_timeout_seconds: 21600` (2026-08-28)** — was the **3600 s default**, and `cron/scheduler.py::_run_job_script` applies it to `--no-agent --script` jobs too, then `killpg(SIGTERM)` → `killpg(SIGKILL)`. `epaq-dispatch` runs the Epa Q task *inside* the tick, so **every task was one hour from a silent death**: SIGTERM skips Python's `finally`, so the task stayed `running` and the `ds4_lease` leaked, and both epaq jobs are `deliver: "local"` so no alert went anywhere. Resolution order is env → `config.yaml` → default, and `load_config()` caches on the file's mtime+size, so **no gateway restart is needed** — but verify with `_get_script_timeout()`, not by reading the file. Backup `config.yaml.bak-20260828-pre-script-timeout`.
- Aux roles (title_generation, curator, background_review) → **gemma4-e4b** at :9292. 🔴 **`compression` LEFT this list on 2026-08-31** — it is now `provider: auto`, i.e. each session's OWN main model (Zen for Telegram, local DS4 for anything on the local provider). Epa Q keeps gemma via the separate **`~/.hermes-epaq`** profile. `delegation` also left — it points at Zen since 08-29.
- 🔴 **`auxiliary.vision` is the ONE aux role not on local hardware (2026-08-27):** `provider: opencode-go` (was `opencode-zen`; moved 2026-09-04), `model: minimax-m3` — a **paid** OpenCode Zen model ($0.30 in / $1.20 out per M; a 350-token OCR call measured `"cost": "0.00014442"`). It exists because `deepseek-v4-flash` is text-only, so `decide_image_input_mode()` returns `"text"` and every image is routed to this aux client. 🔴 **OpenCode Zen serves different models on different API surfaces** and the transport is picked from `auxiliary.<task>.api_mode`: `gpt-*`/`grok-*` → `/v1/responses`, `claude-*`/`qwen*` → `/v1/messages` (base_url loses its `/v1`), everything else → `/v1/chat/completions` (`hermes_cli/models.py::opencode_model_api_mode`). 🔴 **The `hermes model` → "Configure auxiliary models…" picker never writes `api_mode`** — `hermes_cli/main.py::_save_aux_choice` writes exactly `provider`/`model`/`base_url`/`api_key` — so picking a `gpt-*`/`claude-*`/`qwen*` Zen model there yields a plain `OpenAI` client aimed at the WRONG endpoint, with no config error. **Therefore: only ever put a natively `chat_completions` Zen model in this slot.** ⚠️ `gemini-3.5-flash-lite` was the first choice and is the better OCR model, but **every `gemini-*` slug on Zen was returning HTTP 500 on 2026-08-27** (3 retries × 3 slugs; `minimax-m3`/`kimi-k2.5` fine on the same key) — recheck and switch back when Zen's Gemini upstream recovers.
- **PDFs never touch the vision model.** `tools/read_extract.py` shells out to poppler `pdftotext` and feeds plain text to the main model (DS4); `tools/vision_tools.py` normalizes every input to jpeg/png/gif/webp and has **no pdf branch**, so a Zen model's `pdf` modality flag is unreachable here and is NOT a selection criterion. ⚠️ Gap: a **scanned** PDF yields empty pages (warning footer appended) and does NOT fall back to OCR via the vision model.
- Vision config is **hot** — `_get_auxiliary_task_config()` calls `load_config_readonly()` per call and that cache is keyed on `(path, mtime_ns, size)`, while the aux client cache key includes the model. No gateway restart needed for an `auxiliary.vision` model change (unlike `session_reset`, which is built into `GatewayConfig` at startup).
- `agent.disabled_toolsets: ['computer_use']` — delegation stays enabled; **`computer_use` was disabled 2026-08-26 17:25** (backup `config.yaml.bak-20260826-pre-computeruse`, which still shows `[]`). ⚠️ **Rationale not recorded** in this log or in memory at the time — reconstruct before re-enabling.
- 🔴 **`session_reset.mode: both`, `idle_minutes: 180`, `at_hour: 4` (2026-08-25 22:46)** — this SUPERSEDES the earlier same-day `idle`/**1440** setting, which was itself the fix for `none`. 24 h idle **never fires on a thread with daily traffic**, so it changed nothing; 180 min + a 04:00 daily sweep does. Before any of it, `none` meant Telegram **topics never rotated**. Hermes keys one session per `chat_id:thread_id`, so each topic is an independent persistent conversation. General got reset by hand 37×/week (median life 2.5 h, prompt back to ~28k); Morning Briefing ran one session for 5 days (**151,920 tok**) and Night Plan for 49.6 h (**115,870 tok**). Those two paid 8–14 min full re-prefills whenever they lost a KV slot — median turn 631 s / 945 s vs General's 349 s. Policy is evaluated **per session entry** (`_should_reset` in `gateway/session.py` reads each entry's own `updated_at`), so every topic rotates on its own 3 h idle clock; the daily openers are posted straight to the Telegram API by `run_daily`, bypassing Hermes, so that clock runs from the last REAL exchange. ⚠️ **Not hot-reloaded — `GatewayConfig` is built at startup; restart `hermes-gateway.service`.** Valid modes: `none`/`idle`/`daily`/`both`. Resets are non-destructive: transcripts stay in `state.db` (`sessions.retention_days: 90`, `auto_prune: false`) and stay session-searchable.
- ✅ **Slot starvation ADDRESSED 2026-08-25 — DS4 `parallel` 3 -> 4.** llama-watchdog's 60 s probe pins to `max(slot_id)` and so permanently owns one slot (MEASURED at -np 3: slot 2 took **6,534** requests at 97 tok avg over 5 days vs 862 / 1,184 real requests on slots 0 / 1). At -np 4 the probe sits on slot 3 and **three** slots hold conversations. Watchdog `pin_slot()` reads `max()` of the live `/slots` ids, so it followed with **no watchdog change** — verify this before any future slot-count change. VERIFIED live: slot 3 = 32 probes @ 1 tok, slot 2 = real traffic @ 5,326 tok avg. **Cost MEASURED: +1.08 GiB of GTT** (108.32 -> 109.40), 4x the ~0.25 GB the old 35b-derived estimate predicted. ⚠️ **Benefit is capped by the shared KV pool** — under kv-unified `n_ctx` is TOTAL, so a 4th slot adds NO KV: at the measured p90 prompt (119,394) three conversations need ~358k > 262,144 and evict anyway; at the median (65,835) three fit in ~198k. Helps typical turns, not the largest. See gotcha #15. **4 -> 3 on 2026-09-02:** local caller set shrank (pi dormant; main chat, delegation children, Hindsight reflect on Zen) → 2 conversation slots fit (executor + lease-serialized briefings); recovers the measured +1.08 GiB; NOT -np 2 (zero conversation margin). 🔴 **2026-09-04: DS4 probing is now OPPORTUNISTIC** — the probe fires only while NO slot is processing (`OPPORTUNISTIC_PROBING_MODELS=deepseek-v4-flash` in watchdog.env), so slot 2 becomes a conversation slot whenever there is real work; the 60s probe only overwrites its cache while the model is fully idle. A wedge during busy periods surfaces fail-loud through the Epa Q executor/supervisor instead of the probe; recovery path unchanged. Also added `GGML_VK_MAX_NODES_PER_SUBMIT=64` to the router unit (device-lost prevention — see changelog 2026-09-04).

### Epa Q — the queue that replaced the overnight cycle (2026-08-27)

Design + operations trail: `~/Dev/automated-workflows/epa_q/` (`REQUIREMENTS.md`,
`RUNBOOK.md`, `BUILD_LOG.md`). Code: `~/Dev/automated-workflows/workers/epaq/`; state in
`epaq.db` (repo root, git-ignored).

- **Executor** — the DS4 consumer. `epaq-dispatch` claims the next queued task and runs it
  as one isolated non-interactive session:
  `hermes chat -Q --provider custom:local-models -m deepseek-v4-flash -q …`.
  🔴 **The `--provider`/`-m` FLAGS are the pin (fixed 2026-08-28). The
  `LLM_BASE_URL`/`LLM_MODEL` env vars the executor also sets are INERT** — `hermes chat`
  takes its provider from `~/.hermes/config.yaml` (`model.provider: opencode-go` since 2026-09-04, cloud,
  since 2026-08-27). **From Epa Q go-live (2026-08-27) until this fix, every executor task
  ran on `opencode.ai/zen`, not local** — both tasks that had executed (a Gmail helper +
  the `ntc-023` home-trolley audit) sent repo source + one `apps/api/.env` dump off-box.
  Now verified resolving to `provider=custom base_url=http://127.0.0.1:9292/v1`.
  **One task at a time** — Epa Q is the serialisation point, so it never adds a *second*
  heavy agentic session on top of the briefings or an interactive Hermes turn. No
  `swap-model.sh`, no cold load — DS4 is already resident. (Model split, per
  `REQUIREMENTS.md` §8/§9.2: **worker/per-task session = local DS4**; orchestrator brain =
  Epa on OpenCode Zen for queue/dispatch decisions and non-sensitive refinement only.)
- **`ds4_lease`** (`workers/epaq/lease_client.py::ds4_lease`) — a single advisory-lock row
  in `epaq.db`. The executor holds it for the duration of a task. `workers/run_daily.py`
  wraps `build_delivery()` in `ds4_lease("morning-brief"|"evening-review")`: it takes the
  lease for the briefing's model turn if free and proceeds either way.
  🔴 **This bullet used to describe a pause / "checkpoint and yield the slot" / resume
  hand-off. That was DELETED 2026-08-28 (see below) and the stale wording survived here
  until 2026-09-01, where it caused a real error: it read as if checkpoint/resume worked,
  when nothing had written a checkpoint since August.** The lease no longer pauses or
  yields anything.
- **Supervisor** (`run_epaq_supervise.py`, `epaq-supervise` */10) — watches the running
  task. On the task's `/health` failure, or **critical** host-RAM pressure, it **pauses the
  executor with a cooldown and auto-resumes** when the signal clears. 🔴 **Thresholds
  retuned 2026-08-28:** `_default_pressure()` now treats `MemAvailable/MemTotal < 2.5 %` as
  the hard `critical` floor (was 4 %) and `< 5 %` as `warn` (was 10 %), **and** only
  escalates to `critical` between those when PSI `/proc/pressure/memory` `full avg60 > 5`
  (real reclaim stalls). Reason: ~110 GiB of "used" RAM here is GTT held by the resident
  models — unswappable and stable *by design* — so the bare ratio sits ~3–10 % at steady
  state and the old 4 % floor false-held `ntc-023` three times on 2026-08-28. This is the
  **first automated back-off in response to the structural GTT oversubscription** described
  at `## Host` — see `## Alerting`.
- **Crons:** `epaq-dispatch` (*/15), `epaq-supervise` (*/10) — `--no-agent --script`,
  negligible cost (see the Cron pins bullet above).
- 🔴 **The supervisor no longer touches a task (2026-08-28).** It used to nudge /
  block / system-hold, each of which requeued a **running** task; the executor
  then dropped the run without checkpointing. Cost on day one: task #2 3
  attempts (79 min for ~13 min of work), task #14 5 attempts (92 min for ~17
  min) — every restart a full cold re-prefill on DS4. The decisive case was a
  nudge at **19 s** of silence on `loop=6`, because the loop heuristic was ORed
  past the silence check. Now observe-and-alert only; loop threshold 4 → 12; one
  transcript per attempt (they used to append, so earlier attempts' lines
  inflated the loop count). **The only thing that ends a task is the executor's
  own 30-min inactivity watchdog** — the overnight cycle's failure model, which
  ran for months without trouble. `Dispatcher._reap` covers the one case it
  cannot see: a runner SIGKILLed without its `finally` (2 h without progress →
  retryable failure + lease released, with a Telegram line).
- 🔴 **The briefings no longer interrupt a running task.** `ds4_lease` takes the
  slot if free and proceeds either way; holding it only stops the next tick
  *starting* a task. A task already running overlaps the briefing on DS4 for its
  few minutes — the pre-Epa-Q behaviour, and better than discarding an hour of
  work. The old `lease_request_pause` / checkpoint hand-off is deleted (nothing
  expired the flag, so a requester dying before its `finally` made every
  subsequent task start immediately requeue).
- 🟢 **Periodic checkpoint — RE-ADDED 2026-09-01** (`executor.py`
  `DEFAULT_CHECKPOINT_INTERVAL = 300.0`, `CHECKPOINT_TAIL_CHARS = 4000`, ctor kwarg
  `checkpoint_interval`, `0` disables). Every 300 s the session loop persists the last
  4000 chars of the child's own stdout via `store.save_checkpoint`; `_build_prompt`'s
  existing `RESUMING` block replays it on the retry.
  🔴 **Before this, checkpointing was HALF-BUILT:** the column, `store.save_checkpoint`
  and the RESUMING reader all existed, but the only callers of the writer were **tests** —
  it went out with the lease/pause hand-off in `1114e1b` (2026-08-28). Cost: task #34 was
  killed by a gateway restart on 2026-09-01 after **~4h25m** with `checkpoint` NULL.
  🔴 **`store.recover()` now PRESERVES the checkpoint** (it used to clear it). `recover`
  is the killed-runner path — precisely what the checkpoint exists to survive — so
  clearing it there sent every reaped task back to a cold start. `record_failure()` still
  clears: a task that failed on its own terms should retry clean, not resume a broken run.
  The writer is deliberately **write-only** — it never pauses, requeues or interrupts, so
  it cannot reproduce the 2026-08-28 supervisor harm, which came from *interfering with*
  healthy tasks rather than from persisting state.
- **Executor fails closed on the provider pin.** `_assert_local_provider()` reads
  `~/.hermes/config.yaml` and aborts before `Popen` unless `custom:local-models`
  resolves to loopback — the 2026-08-28 cloud-leak path can no longer reopen
  silently, and a test asserts it against the live config.
- 🔴 **The old `overnight-tasks` 23:00 cron (`overnight_tasks.py` → `overnight_swap.py` →
  `swap-model.sh`) and its deliberate no-swap-back design were RETIRED 2026-08-27** — the
  cron is paused, the one pending task was migrated. `gpu-price-watch` (a real DS4 agent
  turn, Sat 06:00) is now an Epa Q recurring schedule; its old cron is paused too. The
  `Evolve Email Classifier` and `remote job digest` crons are `--no-agent` scripts that
  make no model call and were left alone. **`overnight-archive` `96390dc20105` paused
  2026-08-28** after `~/night-cycle-tasks/completed/` was drained into `archive/2026-08/`;
  nothing schedules the `overnight_*.py` cluster now (kept only for the RUNBOOK's rollback
  window — delete from ~2026-09-10).

### Research + browser automation — THE WORKING SETUP (2026-09-01)

**Search** = SearXNG (`:8888`) -> **google**. Image **2026.9.1**, bing **disabled**.
🔴 **REVISED 2026-09-01 (later).** The morning's "enable bing" fix was correct for a
4-month-stale image but became the problem once measured: bing was returning **coherent
SERPs for entirely unrelated queries** (`systemd socket activation tutorial` -> a Ducky
keyboard manual; `reverse a linked list python` -> `brake.co.uk`; `SearXNG documentation`
-> `balloons4u.co.uk`), with **HTTP 200, no error, no `unresponsive_engines` entry**.
Non-deterministic, so it survives a single spot-check.

**Measured relevance** (6 queries x 3 rounds x top-5, marker-regex scored; script kept at
`docs/infra/scripts/searxng-relevance.py`):

| config | relevance |
|---|---|
| 2026.5.8 image, bing enabled (the broken state) | **36.7%** |
| 2026.9.1 image, bing enabled | 65.6% |
| **2026.9.1 image, bing disabled (current)** | **98.9%** |

Two independent faults, and the second was masked by the first:
1. **The image was 4 months stale** (`2026.5.8+d8ab61a9e`, pulled 2026-05-08). On it
   **`google` returned 0 results silently** — which is *why* the morning session reached for
   bing. Updating to `2026.9.1+79c8ffe0d` revived google.
2. **bing's scraper is broken upstream** and updating did NOT fix it. With google healthy,
   bing is pure noise, so it is now `disabled: true` again. ⚠️ Do not re-enable it as a
   "fallback" without re-running the relevance script — an engine that returns confident
   garbage is worse than one that returns nothing.

🔴 **`unresponsive_engines` is NOT sufficient.** It catches CAPTCHA/403/429 but is empty
for both failure modes seen here: silent-zero (google on the old image) and
confident-garbage (bing). **Score relevance, don't just count results.** Still worth the
sweep as a first check:
`curl -s "localhost:8888/search?q=X&format=json" | jq .unresponsive_engines`.

🔴 `~/.searxng/settings.yml` is **root-owned** — edit via
`docker run -u 0 -v ~/.searxng:/cfg alpine`, not sudo. A host-side `sed -i` **fails with
"Permission denied" and the pipeline keeps going**, so an unverified edit reads as applied
when it is not — always `grep` the value back out of the *running* container afterwards.
⚠️ SearXNG's entrypoint re-`chown`s the whole config dir to uid 977 on every start.

Still CAPTCHA'd/rate-limited and contributing nothing: `brave` (429), `duckduckgo`,
`startpage`, `karmasearch`, `mojeek` (403). google + wikipedia carry the results.

⚠️ **Not compose-managed** — SearXNG is a plain `docker run`. The recreate command is in
the changelog entry for 2026-09-01; the previous container is parked stopped as
`searxng-old-2026058` for rollback (delete from ~2026-09-15).

**Extract** = self-hosted Firecrawl (`FIRECRAWL_API_URL=http://localhost:3002`). Works.
⚠️ `web_extract_tool` takes a **list** of URLs — pass a bare string and it iterates
per-character, rejecting each as a private-network address.

**Version: `v2.11.272`, updated 2026-09-01** (was upstream `3afe6df1f` of 2026-05-08, 812
commits behind). `~/firecrawl` is on branch `upgrade-2.11.272` = the tag plus **one** local
commit. Rollback: images `firecrawl-*:rollback-20260901`, branch `main`, and
`*.bak-20260901-pre-v2.11.272` (delete from ~2026-09-15).

- 🔴 **The compose file hard-codes `name: firecrawl`.** A second copy in another directory
  is the *same compose project* and will fight prod for containers. Any off-prod test
  **must** pass `-p <other-name>` and a different `PORT`.
- **Only one local delta remains:** `- "127.0.0.1:${PORT:-3002}:${INTERNAL_PORT:-3002}"`.
  Upstream now omits `restart:` (default `no`, which is what socket activation needs) and
  dropped the `ai-stack` network, so those two edits are retired — do not re-add them.
- **FoundationDB is parked behind `profiles: ["fdb"]`.** Upstream ships it as an optional
  queue backend; `NUQ_BACKEND` is unset here so the Postgres queue is used and the two fdb
  containers never start. Do not delete the block — the profile gate is the merge-friendly
  form.
- **Updating means a source build.** ghcr has `firecrawl` + `playwright-service` images but
  **`latest` only, no version tags**, and upstream's own first rule is "Release: an exact
  tag". Cold start is now **16.3 s** (was 12.2 s); images grew to 1.08 GB / 2.08 GB.
- ⚠️ Two startup log lines are **normal, not faults**: a RabbitMQ `noproc` error that
  self-heals to the Postgres fallback, and `Can't accept connection due to RAM/CPU load`
  from Firecrawl's load guard. Neither failed a scrape.
- **Scraping is still Playwright → Chromium** (`apps/playwright-service-ts/api.ts`). The
  v2 API now has a `/browser` route, but it is a thin client requiring `BROWSER_SERVICE_URL`
  and errors with "Browser feature is not configured" when unset — **Firecrawl does not
  bundle a browser that replaces Chromium.** `fire-engine` is in the tree but is the closed
  cloud component.
- Firecrawl also reads `SEARXNG_ENDPOINT` for its own `/v2/search`. **Not set** — Hermes
  uses `search_backend: searxng` directly, so Firecrawl only ever does `/v2/scrape` here.

**Browser** = built-in `browser_*` tools -> `agent-browser` (npx, 0.26.0) -> local Chromium.
🔴 **`browser.backend: off` is REQUIRED and is now set in both profiles.** Unset defaults to
Browser Use CLI (`browser_exec`), which **cannot run headless**: it blocks on a GUI Chrome
"Allow remote debugging?" popup needing a human click, and `uvx browser-use` took >4 min to
resolve on first run. With `off`, `check_browser_requirements()` is True and `hermes doctor`
shows `✓ browser`. Verified headless with `DISPLAY`/`WAYLAND_DISPLAY` unset.
Daemons self-reap via `browser.inactivity_timeout: 120`.

Remaining `⚠ browser-cdp` (needs a CDP endpoint — N/A in local mode) and `⚠ browser-use`
(intentionally off) in doctor are **correct**, not faults.

**Tier B (logged-in portals)** — `chrome-devtools-mcp` scaffold at `~/.config/epaq-browser/`,
pinned 1.8.0, **nothing registered**. Kept only because the built-in stack has no egress
allowlist and `--allowedUrlPattern` is MCP-side CDP interception, not a Chrome flag, so
`browser.cdp_url` cannot provide it. `launch.sh` fails closed without an allowlist.
**Tier C (banking, email, irreversible): no automation.**

**Camofox — EVALUATED 2026-09-01, installed but NOT wired in.** Anti-detect Firefox
(Camoufox 152.0.4 beta.29) behind a REST server; Hermes has a first-class backend for it
(`tools/browser_camofox.py`). Installed on this box, **config deliberately unchanged**:
`browser.cloud_provider` is still `local` in both profiles, so nothing routes to it until
you flip that. Start it with `CAMOFOX_PORT=9377 npx -y @askjo/camofox-browser@^1.5.2`
(binaries already cached — pre-warms in ~0.9 s; the 1.3 GB download is done).

🔴 **The mechanism:** Chromium/agent-browser reports **`navigator.webdriver === true`** —
the single most-checked automation tell — and leaks real host specs
(`hardwareConcurrency: 32`, `deviceMemory: 32`). Camoufox reports
**`WebDriver (New): missing (passed)` and `WebDriver Advanced: passed`** on
`bot.sannysoft.com`. That difference is the whole result below.

| target | chromium | camofox | winner |
|---|---|---|---|
| Kayak | redirected to `/help/bots.html` | **real fares** — `Cheapest €15`, `16:50-19:45 DUB->ORY direct 1h 55m` | **camofox** |
| Daft.ie | 459 ch (nothing) | 16,051 ch | **camofox (35x)** |
| Indeed.ie | 856 ch, `Blocked` | 15,636 ch, clean | **camofox** |
| Aer Lingus | 600 ch stub | 2,760 ch, reached `/app/make/…` | camofox (still no prices) |
| **LinkedIn** | **15,739 ch** | 2,011 ch | **chromium** |
| Amazon.ie | 15,655 ch | 15,353 ch | tie, both fine |
| Skyscanner | PerimeterX CAPTCHA | PerimeterX CAPTCHA | **both lose** |
| Google Flights | consent wall | consent wall | neither — that is EU cookie consent, not bot detection |

**Verdict: 4 wins, 1 loss, 2 ties, 1 mutual loss — a real but NOT universal upgrade.**
✅ **The wins are extractable data, not just a page that loads** — Kayak returns fares and
itineraries; Daft.ie returns **47 listings** at `€2,190 per month · 1 Bed · 1 Bath`.
🔴 **Wait 15-20 s on a JS results page and assert on the DATA, not char count.** The first
A/B waited 6 s and scored Kayak "loaded but no prices"; at 20 s the same page gave
**80,427 chars** of populated results. ⚠️ Snapshots paginate at 80,000 chars.
🔴 **LinkedIn is the counter-example that kills "just make camofox the default"** — keep
both and pick per target. Camofox beats *fingerprint*-based defense; it does nothing for
IP reputation, so commercial-grade WAFs (PerimeterX/DataDome) still win — that needs a
residential proxy, which is the only remaining lever short of an API.

⚠️ **Cost: 1.61 GiB resident** (camoufox-bin 563 MiB + WebExtensions/content procs + Xvfb)
and **1.3 GB on disk** (not the ~300 MB the Hermes docstring claims). On a box with
~3.5 GiB MemAvailable that is ~45% of headroom, so **do not make it always-resident** —
socket-activate it like `firecrawl-proxy`/`hermes-dashboard-proxy` if it gets wired in.
Teardown verified clean: MemAvailable 2.78 -> 4.31 GiB, no orphan Xvfb or camoufox procs.
✅ It starts its own **Xvfb** virtual display, so unlike Browser Use CLI it genuinely runs
headless with no GUI click.

---

### Background — how the browser stack resolves (2026-09-01)

**Hermes' own guidance decides the split.** `guides/use-mcp-with-hermes.md`: *"Do not use
MCP when a built-in Hermes tool already solves the job well."* Browsing is built in, so
the native `browser` toolset is the default path and MCP is the documented exception.

- **Tier A — public research: the native `browser` toolset.** No MCP server. Backend is
  `browser.cloud_provider: local` -> `agent-browser` -> local Chromium
  (`~/.cache/ms-playwright/`). ✅ Verified working 2026-09-01 (`browser_navigate` +
  `browser_snapshot` against example.com, a11y refs returned, `stealth_features:["local"]`).
  No egress allowlist — and none needed: no credentials are in play.
- 🔴 **`~/.npm-global/bin/agent-browser` was a DANGLING SYMLINK** into
  `~/hermes-agent/node_modules/agent-browser/` (created 2026-08-09 by agent-browser's own
  npm postinstall; the target vanished in the 2026-08-30 `hermes update` node_modules
  rebuild). **This is upstream issue #48521 and Hermes GUARDS it** —
  `hermes_constants.agent_browser_runnable()` execs `--version` rather than trusting
  `shutil.which`, so a dead candidate falls through the chain
  (PATH -> extended PATH -> `node_modules/.bin` -> **npx**). Browsing therefore never
  actually broke. Symlink deleted 2026-09-01; resolution is now cleanly `npx agent-browser`.
- 🔴 **Version pin lives in code, not config:** `tools/browser_tool.py`
  `AGENT_BROWSER_NPX_SPEC = "agent-browser@^0.26.0"`. For a 0.x package the caret is
  **patch-only**, so `^0.26.0` means `>=0.26.0 <0.27.0`. npx resolves **0.26.0**. ⚠️ The
  stale global package still on disk is **0.27.0** — outside the spec — and
  `agent_browser_runnable()` only checks that `--version` exits 0, **not** that the
  version matches. So re-creating a global bin symlink would silently serve a
  spec-violating build. **Leave resolution on npx and let Hermes own the version.**
  ✅ **The stale global `agent-browser@0.27.0` was UNINSTALLED 2026-09-01**
  (`npm uninstall -g --ignore-scripts`, 73 MB reclaimed) — `--ignore-scripts` because
  agent-browser's own postinstall is what creates the bad symlink. With no global package
  there is nothing for a future postinstall to re-point: **the trap is gone, not just the
  symlink.** Re-verified after removal: resolver -> `npx agent-browser` -> 0.26.0, and
  `browser_navigate`/`browser_snapshot` both functional.
  🔴 **Do not `npm i -g agent-browser`** to "speed up" resolution — that re-arms it.
- ✅ **`hermes update` will NOT undo this.** Verified in `hermes_cli/update_cmd.py`
  `_update_node_dependencies()`: it calls `tools.browser_tool.warm_agent_browser_npx_cache()`,
  which runs `npx --ignore-scripts --prefer-offline -y agent-browser@^0.26.0` — that
  populates `~/.npm/_npx` **only**. No global package, no bin symlink, and
  `--ignore-scripts` means the postinstall that created the original bad symlink
  **cannot run**. agent-browser is not in `package.json`/`package-lock.json` (0 hits), so
  the npm install step cannot pull it either. `install.sh`'s `ensure_browser()` stopped
  npm-installing agent-browser (PR #44772) and is an *installer* path anyway — `hermes
  update` never calls it, so it also won't install `@askjo/camofox-browser` (absent here).

- 🔴 **THE BUILT-IN `browser_*` TOOLS ARE NOT WHAT THE MODEL ACTUALLY GETS.**
  `tools/browser_use_cli.py::is_browser_use_cli_mode()` — *"Browser Use mode is the
  DEFAULT: an unset `browser.backend` ("") enables it whenever the browser-use CLI is
  runnable"*. `browser.backend` is **unset** in both profiles (the `backend:` keys in the
  config are `terminal.backend: local` and `web.backend: firecrawl` — different blocks),
  and `_find_cli()` resolves to `['~/.hermes/bin/uvx', 'browser-use']`. So
  `is_browser_use_cli_mode()` is **True** and `check_browser_requirements()` returns False
  on its *first* gate, deliberately hiding the whole `browser_*` surface. The model gets
  **`browser_exec`** (Browser Use CLI 3.0, code-driven) instead.
  - This is why `hermes doctor` reports `⚠ browser (system dependency not met)` and
    `⚠ browser-cdp` while still ending with "All checks passed" and `✓ browser-use`:
    the warnings are **intentional suppression**, not a fault.
  - ⚠️ A direct Python call to `browser_navigate()` **bypasses this gate and works** — so
    it is NOT evidence the model can browse. Test model-facing availability with
    `check_browser_requirements()` / `hermes doctor`, never a direct import.
  - To use the built-in stack instead, set `browser.backend: off` (or `/browser use off`).
    Camofox is the one backend that always falls back to the built-in tools.
  - No `BROWSER_USE_API_KEY` is set, so this is the local CLI path, but note it runs via
    **uvx** — a runtime package fetch outside the pinned npm graph.
- **Backends are swappable under the same toolset** (`plugins/browser/`): `local`
  (current), `firecrawl` / `browserbase` / `browser_use` via `browser.cloud_provider`, or
  **your own Chrome** via `browser.cdp_url` / `/browser connect`. A provider plugin only
  supplies session lifecycle + a CDP websocket URL; Hermes drives the page. `browser_cdp`
  and `browser_dialog` register **only** when a CDP endpoint is reachable — on
  `cloud_provider: local` you never get them. Firecrawl is bundled and already
  self-hosted here (`FIRECRAWL_API_URL=http://localhost:3002`), so it is an available
  self-hosted backend, currently unused.
- **Tier B — logged-in portals: `chrome-devtools-mcp`, scaffolded, none registered.**
  This is where MCP earns its place: the native stack has **no egress allowlist**, and
  `--allowedUrlPattern` is implemented by chrome-devtools-mcp via CDP interception (not a
  Chrome flag), so `browser.cdp_url` would **not** give it to you. Kept at
  `~/.config/epaq-browser/` — `launch.sh` (fails closed without an allowlist), `login.sh`,
  `portals/<name>.allow`, `profiles/`, pinned `chrome-devtools-mcp` 1.8.0.
- **Tier C — banking, email, anything irreversible: no automation. Human-driven.**
- 🔴 **`hermes mcp add`/`remove` YAML-round-trips the profile config and STRIPS COMMENTS.**
  It ate the "NO cloud fallback" rationale comment in `~/.hermes-epaq/config.yaml` on
  2026-09-01 (setting survived; comment restored by hand). Prefer editing `mcp_servers:`
  directly, and re-read the file head after any `hermes mcp` write.

## pi

- **Config:** `~/.pi/agent/models.json` — provider `litellm` → `http://localhost:9292/v1`, apiKey `unused-llama-router-direct`, models deepseek-v4-flash + gemma4-e4b, DS4 contextWindow **262144** (briefly 131072 on 2026-08-25, reverted same day), maxTokens 16384. **`defaultModel: deepseek-v4-flash`**. pi-kalam (`~/Dev/pi-kalam`) config.ts already pins all roles to deepseek-v4-flash — no change needed there.
- **2026-08-27:** pi-kalam's `src/steps/interview-core.ts` + `src/criteria-lint.ts` are now
  re-export shims — the logic moved to the standalone **`kalam-elicit`** package
  (`~/Dev/kalam-elicit`, consumed `"kalam-elicit": "file:../kalam-elicit"`); `ledger.ts`
  keeps only pi-kalam's source-taxonomy adapter over the shared algebra. The same package
  backs Hermes/Epa Q's `epaq-refine` bridge (`~/Dev/epaq-refine`). No model impact;
  extraction verified behaviour-preserving (no test files changed, 1457 green on `main`).

## Services (systemd user units, all active)

| service | port | role |
|---|---|---|
| `llama-router.service` | 9292 | llama.cpp router (models) |
| `llama-watchdog.service` | 9611 | **THE alerting engine** (2026-08-25) — GPU device-lost + metrics relay + **host memory pressure + systemd unit outages**, all to the Telegram **topic 5** of `<SUPERGROUP_CHAT_ID>`. Reads `/proc` directly, so it does NOT depend on VM/exporters. ⚠️ Epa Q's supervisor (see `### Epa Q`) is a **separate, consumer-scoped remediation** — it pauses one executor — and is NOT part of llama-watchdog, does not feed it |
| `watchdog-heartbeat.timer` | — | dead-man check every 5 min — a **separate process** curling `:9611`, because a wedged watchdog still holds its port and still reads `active` |
| `hindsight-daemon.service` | 9177 | Hermes memory daemon (bank `hermes`, embedded Postgres). **hindsight-all/api-slim/client/embed all at 0.9.2** (upgraded 2026-08-26, was 0.8.4/0.8.4/0.6.1/0.8.6). `HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS=1` (renamed 2026-08-26 from the deprecated `_MAX_SLOTS` — it was always a *minimum* floor despite the old name, not a maximum; same value, same behavior, deprecation warning gone) |
| `hermes-gateway.service` | — | messaging (Telegram/Slack/WhatsApp) |
| `hermes-dashboard-proxy.socket` | 9119 | **on-demand** dashboard (2026-08-25) — socket always listening; `hermes-dashboard.service` is **disabled at boot**, moved to `:9129`, wakes in ~1 s and stops after 15 min idle. 🔴 Its drop-in sets `Restart=no`; the base unit's `Restart=always` would undo every teardown |
| `firecrawl-proxy.socket` | 3002 | **on-demand** Firecrawl activation (2026-08-25) — socket is always listening; the stack itself is DOWN until a request arrives and stops again after 30 min idle |

Other listeners: SearXNG **:8888** (search). WhatsApp bridge **:3000** (Hermes, self-chat mode). Grafana retired 2026-08-13.

🔴 **VictoriaMetrics + node-exporter + amdgpu-exporter RETIRED 2026-08-26** — the
`mem-sampler` tracker closed the last blocker (the restart-bleed investigation).
`docker compose down` in `~/observability/stack/`; `amdgpu-exporter.service`
stopped + disabled; the `stack_vm-data` volume (VM's TSDB) was reclaimed the
same day via `docker volume rm`. There is now **no
Prometheus-style metrics stack at all** — all alerting runs on
`llama-watchdog` (`:9611`, reads `/proc`/`rocm-smi` directly) and the
`mem-sampler` tracker (`~/observability/stack/mem-sampler/`, 5-min JSONL,
its own systemd timer, unaffected by this retirement). Full detail + smoke
test in `changelog.md`.

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
| **Epa Q executor stall / task `/health` fail / critical host-RAM** | **Epa Q supervisor** (`run_epaq_supervise.py`, `epaq-supervise` */10). REMEDIATION for one consumer, not an alert: pauses the executor + cooldown + auto-resume. `/proc/meminfo` ratio (`critical < 2.5 %`, `warn < 5 %`) + a PSI `full avg60 > 5` cross-check for the mid-band (retuned 2026-08-28), **uncoordinated** with `health_loop`'s absolute 3.0 GiB |

⚠️ **Epa Q's RAM back-off vs `health_loop`:** on a 122.7 GiB box, Epa Q's hard `critical`
floor (< ~3.1 GiB `MemAvailable`, 2.5 %) now sits just above `health_loop`'s 3.0 GiB alert
threshold, and the mid-band (3.1–6.2 GiB) only holds the executor if PSI also shows real
stalls — so a pause and a watchdog RAM alert now land close together rather than the
supervisor firing first by a wide margin. Still: do not read "no watchdog alert" as "no
pressure" — the executor may already be backed off.
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
| 9611 | llama-watchdog | **`0.0.0.0`** | none |
| 3000 | WhatsApp bridge | `127.0.0.1` ✅ | — |

~~9610 / 9100 amdgpu-exporter / node-exporter~~ and ~~8428 VictoriaMetrics~~ — retired 2026-08-26, ports no longer bound.

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
5. **Watchdog pins its 60s probe to the LAST slot** (`id_slot`) — an unpinned 2-token probe scores f_sim ~0, falls through to LRU, and overwrites the idle slot's cached prefix. Pinning confines damage to one slot. 🔴 **2026-09-04: for DS4 the probe is now OPPORTUNISTIC** (`OPPORTUNISTIC_PROBING_MODELS=deepseek-v4-flash`) — it fires only while ALL slots are idle, so the pinned slot is conversation-usable under load; gemma4-e4b keeps always-on pinned probing.
6. **DS4/LLM OOM path — ✅ CLOSED 2026-08-12.** A background reflect (Hindsight `extractor`) could name an unloaded ~98 GiB model and trigger a router autoload with nobody at the keyboard — this OOM-killed llama-server 2026-08-07 11:01:49. Fixed by retargeting reflect to the resident gemma4-e4b (see *Role → model mapping*); verified on the running process, and reflect exercised end-to-end. The watchdog heavy-model mutex is now **defence-in-depth, not the sole guard**.
   ⚠️ **The underlying hazard is unchanged** — the router still has zero memory awareness and `--models-max` still caps model COUNT, not SIZE. **Any** consumer pinned to an unloaded heavy model re-opens this. When adding or repointing a background/unattended caller, pin it to a *resident* model. On this box an OOM is not a tidy process kill: DS4's memory lives in amdgpu GTT and is invisible to the kernel OOM killer, so on 2026-08-06 it reaped the entire GNOME session (pipewire, xdg portals, wezterm, the user systemd manager) instead.
   ✅ **Epa Q is compliant (executor pin FIXED 2026-08-28):** the per-task session is pinned to the RESIDENT DS4 via `--provider custom:local-models -m deepseek-v4-flash` (the earlier `LLM_BASE_URL`/`LLM_MODEL` env pin was inert — tasks were silently hitting cloud Zen from go-live until this fix). `epaq-dispatch` / `epaq-supervise` are `--no-agent --script` (no model call from the tick). The `ds4_lease` + supervisor add a consumer-level defence-in-depth layer alongside the watchdog heavy-model mutex.
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
- **Epa Q** (the DS4 consumer added 2026-08-27, `### Epa Q` above): design + operations
  trail at `~/Dev/automated-workflows/epa_q/` — `REQUIREMENTS.md`, `RUNBOOK.md` (cutover +
  rollbacks), `BUILD_LOG.md`, `flow.html`.
- **`~/docs/local-ai-stack.md`** — SUPERSEDED (last accurate 2026-05-15; describes removed granite-4.1-8b/LiteLLM/96 GiB). Kept for history only.
- **`~/docs/infra-todo.md`** — backlog, last updated 2026-05-15.
