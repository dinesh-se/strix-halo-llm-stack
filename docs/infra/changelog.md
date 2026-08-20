# AI Infra History

Append-only log of changes to the local AI stack (llama-swap model lineup,
Hermes, pi, Hindsight, LiteLLM/observability). One entry per change, newest
first. Update this file in the same session as any infra change — see
`feedback_update_memory_after_infra_change` in Claude's memory.

**Entry format:**
```
## YYYY-MM-DD — Title
**Observed:** what was seen/measured that prompted this (symptom, benchmark, or opportunity)
**Changed:** the actual config/code change, file(s) touched
**Expected:** what this was supposed to fix or improve
**Refs:** external sources consulted (issues, docs, benchmarks)
**Smoke test:** what was run to verify, and the result
```

Entries before 2026-07-19 are a best-effort reconstruction from session
memory, not live contemporaneous notes — dates and specifics are as accurate
as the source memories, but some smoke-test detail from those dates was not
captured at the time and is marked accordingly.

---

## 2026-08-20 (later 4) — ⏳ TEMPORARY: Hermes on qwen3.8-27b-q4 for a trial day. REVERT WITH `tools/end-qwen-trial.sh`.

**Observed:** With `parallel = 2` proven to fix the cache wipe (entry below), the
qwen verdict is unsettled and the user wants a real day of use to re-take it.
Also re-measured DS4 after today's two restarts to confirm nothing drifted:
**250.23 PP / 18.62 TG @pp2053** (trials 244–259 PP, 18.53–18.63 TG) vs this
morning's 256.68 / 18.59 and the 08-12 baseline 268.98 / 19.48. **Decode
identical to 3 significant figures; DS4 is stable.**

**Changed — TWO keys only, both in `~/.hermes/config.yaml`** (backup
`config.yaml.bak-20260820-pre-qwen-trial`):
- `model.default`: `deepseek-v4-flash` → **`qwen3.8-27b-q4`**
- `custom_providers[Local Models].model`: same
Then `swap-model.sh qwen` (18 s). `model.context_length` 262144, the selectable
models list, and all aux roles (gemma4-e4b) are untouched.

**🔴 DELIBERATELY NOT CHANGED — the user's explicit decision after being shown
the risk.** Both check-in crons stay pinned to `deepseek-v4-flash`, and
OpenWebUI keeps `DEFAULT_MODELS=deepseek-v4-flash`. Consequences, stated so the
next session does not read this as drift:
- **The trial window is ~11:40 → 21:35 today.** At 21:35 `night-check-in` names
  DS4, the router autoloads it, the heavy mutex evicts qwen, and DS4 stays
  resident from then on (the overnight cycle no longer swaps away — see the
  08-20 night-cycle change).
- **There IS a real OOM window at 21:35** if the user is actively driving qwen
  while the cron retries for DS4 — that is precisely the 2026-08-19 21:37
  contention that killed the router. The mutex guards co-residency, not
  contention. Risk accepted knowingly.
- `overnight-tasks` (23:00) will **skip** tonight: 0 pending task files, and the
  parser checks before any swap. If a task is added during the day it will swap
  to DS4 and stay there.
- Opening OpenWebUI during the trial silently evicts qwen.

**Revert:** `tools/end-qwen-trial.sh` (new, `bash -n` clean) — reverts both keys
and runs `swap-model.sh ds4`. It reverts *only* the two keys because only two
were changed. ⚠️ A RUNNING Hermes session keeps its pinned model in session
state, so a **new session** is required both to enter and to leave the trial.

**Smoke test — PASSED.** `hermes config get model.default` → `qwen3.8-27b-q4`.
Live: `qwen3.8-27b-q4 loaded np=2 ctx=262144` + `gemma4-e4b loaded`, DS4
unloaded. Real completion through the router: a non-trivial probability question
answered correctly (`5/14`), `finish=stop`. All five services `active`,
`NRestarts=0`, MemAvailable **87.0 GiB**, no OOM/amdgpu events.

**What this trial is FOR:** the 08-19/20 rejection was measured at
`parallel = 1`, i.e. 4–10 min per turn of pure re-prefill. Judge it now on
multi-turn feel, not on a benchmark. Decode is 31–33 t/s vs DS4's 18.6, but qwen
has 2 slots vs DS4's 3 and reasoning is always on.

---

## 2026-08-20 (later 3) — 🔴 `parallel = 1` DESTROYS the prompt cache via the watchdog probe. It is very likely why qwen was judged slow.

**Observed:** User asked whether `--parallel 1` would improve DS4. Investigating
turned up a **serious latent misconfiguration on `qwen3.8-27b-q4`**, and it
retroactively undermines yesterday's qwen verdict.

**Mechanism (verified in code AND live).** `watchdog.py:pin_slot()` returns
`max(slot_ids)` — the probe is pinned to the HIGHEST slot so it cannot LRU-steal
a conversation's cached prefix (the 2026-08-07 fix). The probe is a 2-token raw
`/completion`, so it has ~zero LCP with any real conversation and **overwrites
whatever prefix sits in its slot, every 60 s**.

**At `parallel = 1` there is only slot 0, so `max(ids) = 0` — the probe slot IS
the conversation slot.** Every turn then pays a full re-prefill.

**PROVEN LIVE on DS4 slot 2 (already the probe slot, so nothing was lost):**
```
before           : {0: 25584, 1: 90018, 2: 1}
after my request : {0: 25584, 1: 90018, 2: 2701}   <- slot 2 holds a 2701-tok prompt
... 75 s (one probe cycle) ...
after probe      : {0: 25584, 1: 90018, 2: 1}      <- WIPED. slots 0/1 untouched.
```

**MEASURED consequence across 9,698 logged Hermes calls — the split is by
`parallel`, not by model:**

| model | `parallel` | calls | with `cache=` | med prompt | **med latency** |
|---|---|---|---|---|---|
| deepseek-v4-flash | 3 | 4225 | 94% | 76,217 | **41.4 s** |
| qwen3.6-35b | 2 | 2326 | 90% | 60,100 | **15.0 s** |
| gemma4-e4b | 4 | 918 | 96% | 43,676 | 23.8 s |
| gpt-oss-120b | ≥2 | 608 | 95% | 39,642 | 10.4 s |
| qwen3.6-27b | ≥2 | 338 | 95% | 57,370 | 43.0 s |
| **qwen3.8-27b-q4** | **1** | 16 | **18%** | 56,611 | **373.0 s** |
| **qwen3.8-27b** | **1** | 1 | **0%** | 26,220 | 211.6 s |

**Every model at `parallel ≥ 2` gets 79–96% cache reuse. Both `parallel = 1`
models get 0–18%.** `qwen3.6-35b` at `parallel = 2` (confirmed from
`bc61987^:config/models.ini`) served a nearly identical median prompt (60,100 vs
56,611) in **15.0 s vs 373.0 s — 25× faster**, despite the Q4 being the faster
model per token (31–33 vs ~17 t/s).

The Q4's own call sequence is the giveaway — `in=` 72024 → 73054 → 77467 → 78805
→ 80627 → 81571 is a **growing multi-turn conversation**, exactly the shape that
gets 98% cache hits on DS4, and it got **none**.

**🔴 This reframes the 2026-08-19/20 qwen verdict.** The model was benchmarked at
31–33 t/s decode and then judged "not great as a daily driver" from lived
experience. That lived experience was 4–10 minutes per turn — but the cause was
`parallel = 1` letting the watchdog wipe the cache every 60 s, **not the model**.
The rejection may still stand on other grounds; it has NOT been tested with a
sane slot count.

**Answer to the original question — `parallel = 1` on DS4 would be far worse:**
- Slot 0 becomes the probe slot → a full ~11 min re-prefill on essentially every
  turn (see the 90k prefill measurement in the previous entry).
- It also reintroduces head-of-line blocking, the reason `-np` was raised to 2
  and then 3 in the first place.
- It saves only ~0.25 GB of compute buffer per dropped slot; under `kv-unified`
  the KV buffer is `n_ctx` TOTAL, so `parallel` does **not** change KV size.
- **Do not do it.**

**Changed (APPLIED at the user's request, 11:01–11:04):**
`~/llama-stack/config/models.ini` + repo template, inode-preserving
(`12714493` before and after; `diff` clean between copies; backup
`models.ini.bak-20260820-pre-qwen-np2`): **`[qwen3.8-27b-q4] parallel = 1 → 2`**,
with the full rationale, the live proof and the 9,698-call measurement recorded
in-file so it cannot be "tidied" back to 1. A comments-stripped diff confirms
**exactly one effective line changed** and nothing in `[deepseek-v4-flash]` or
`[gemma4-e4b]` moved. Then `daemon-reload` + one `restart llama-router`.

**Smoke test — PASSED.**
- Restart window was clean: **0 start attempts in the prior hour** (last restart
  09:32, this one 11:01), so the `StartLimitBurst=3` budget was not at risk. DS4
  confirmed idle on `/slots` (`is_processing: false` × 3, `requests_deferred 0`)
  before restarting.
- Router picked the change up — verified on the **preset argv the router will
  spawn**, not the file: `--parallel 2`, with `--ctx-size 262144`,
  `--spec-type draft-mtp`, `--spec-draft-n-max 5`, `--kv-unified` all unchanged.
- Final lineup: `deepseek-v4-flash loaded np=3 ctx=262144` /
  `gemma4-e4b loaded np=4 ctx=131072` / `qwen3.8-27b-q4 unloaded np=2 ctx=262144`.
- DS4 cold-loaded in **~3.5 min** (11:01 → 11:04:34) and served a real completion
  (`finish=stop`). All five services `active`, `NRestarts=0`, watchdog probes
  green on both resident models, `heavy_coresident 0`, **no OOM / amdgpu events**.
  MemAvailable 10.4 GiB.

**✅ BEHAVIOURAL PROOF — RUN AND PASSED (11:16–11:20).** Swapped to qwen (18 s),
ran a real 2-turn conversation through the ROUTER `:9292` exactly as Hermes does:

```
TURN 1 (cold)          prompt=19664  cached=0     (0%)  lat=71.4s
  slots after turn 1:  {0: 19874, 1: 1}     <- conversation on 0, PROBE on 1
  ... 80 s = one full 60 s probe cycle ...
  slots after probe:   {0: 19874, 1: 1}     <- CONVERSATION SURVIVED
TURN 2 (warm)          prompt=19705  cached=19660 (99%) lat=2.7s
```
**99% cache reuse and 2.7 s vs 71.4 s cold — 26× faster on turn 2**, where at
`parallel = 1` every single logged call reused nothing. The probe's own 60 s LRU
refresh on slot 1 is what steers new conversations onto slot 0; the separation is
self-maintaining.

**🔴 The FIRST run of this test was a FALSE PASS, and the trap is worth
recording.** `PROBE_GRACE_SECONDS = 300` — for 5 minutes after a model loads,
`probe_loop` sets `probe_ok[name] = 1` **optimistically and returns without
probing** (`watchdog.py:546-550`). qwen loaded at ~11:10, so the first test ran
entirely inside the grace window and "passed" because *nothing was probing it*.
**The tell is `llama_watchdog_probe_latency_seconds{model=...}` being ABSENT
while `probe_success` reads 1** — success is a gauge that starts optimistic,
latency only appears once a real probe lands. Waited for the latency series to
appear (11:16:10, 0.3211 s), re-ran, and got the result above. On re-check the
first run's conversation had indeed been sitting on slot 1 and was wiped
(`n_prompt` 19769 → 1) the moment the real probe started — which independently
re-confirms the failure mode.
**Rule: never validate watchdog-interacting behaviour within 5 min of a model
load; require a `probe_latency_seconds` sample first.**

⚠️ Turn-2 decode read ~17 t/s here, but that is a 46-token generation dominated
by overhead and is NOT comparable to the 31–33 t/s steady-state benchmark. The
child's own counter reported `predicted_tokens_seconds 23.07`. This test measured
CACHE BEHAVIOUR, not throughput.

**🔴 The qwen verdict is now formally UNSETTLED** and should be re-taken on a
real multi-turn workload at `parallel = 2` before the model is written off.

**⚠️ Caveat stated honestly:** n=16 calls for the Q4 is a small sample. The
mechanism is independently proven above, and the conversation-shaped call
sequence is strong corroboration, but the 25× figure itself rests on few calls.

**Standing rule this establishes:** **never run a conversational model at
`parallel = 1` while llama-watchdog is probing it.** `parallel` must be at least
2 — one slot for the probe, one for the conversation. Keep this in lockstep with
any new model section in `models.ini`.

---

## 2026-08-20 (later 2) — Web survey against our config: one hypothesis REFUTED by test, prefill curve extended to 90k, three "do not do" answers

**Observed:** User asked whether anything published would improve our setup.
Surveyed upstream llama.cpp issues/PRs and the current Strix Halo guides against
the live config. **Changed nothing** — this entry is findings.

**🔴 My own hypothesis for the stream drops was WRONG, and the test says so.**
llama.cpp [#18760](https://github.com/ggml-org/llama.cpp/issues/18760) reports
that router mode's internal proxy ignores `--timeout` and uses a hardcoded
read timeout (~300 s), producing `Failed to read connection` — which matches
both our 08-15 cron error string and the 08-20 stream drops. Our router runs
with **no `--timeout`**, so it looked like a direct hit.

**MEASURED A/B, 90,000-token prompt, streamed, generous client timeout:**

| leg | result |
|---|---|
| via ROUTER `:9292` | **OK — survived 648.7 s**, ttfb 30.3 s, 32 chunks |
| direct to child | OK 1.0 s (prefix already cached by the router leg — not a clean control, and not needed) |

**The router did NOT drop at 300 s, or at all.** The reason is
`--sse-ping-interval` (default **30 s**, active): the first "chunk" at 30.3 s and
most of the 32 chunks are SSE keepalive pings, which hold the connection open
through a prefill that produces no tokens for ~11 minutes.
- ⚠️ **Do not set `--sse-ping-interval -1`.** It is what makes long prefills
  survivable on this box.
- PR [#22003](https://github.com/ggml-org/llama.cpp/pull/22003) (merged 2026-04-21,
  so it predates our 08-04 image) made `--timeout` work in router mode, and our
  build's `--timeout` default is **3600 s**, not 300. Both consistent with the
  observed pass.
- **Conclusion: the stream drops are NOT a router proxy timeout.** Cause still
  unknown; they run 1–6/day since at least 08-13 and are the real UX cost.

**🟢 New measurement — the prefill decay curve, extended:**

| prompt | prefill t/s |
|---|---|
| 2,053 | 256.7 |
| 16,389 | 235.4 |
| 19,605 | 225.6 |
| **90,000** | **~139** (648.7 s wall) |

**A cold 90k prefill costs ~11 minutes.** This is the single number that explains
every "slow" report: one stream drop on a 100k conversation forces a full
re-prefill, and Hermes retries 3×. It is prefill-time, exactly as
`prefill_amplification_2026_08_02` concluded — nothing to do with throughput.

**⚠️ Capacity note that follows from it:** DS4 runs `--parallel 3 --kv-unified`,
so `n_ctx = 262144` is the TOTAL across all 3 slots, not per slot. Observed live
conversations reach **118k tokens**. Two of those fill 90% of the unified KV; a
third forces eviction and therefore a full ~11 min re-prefill on whichever
conversation loses. DS4's KV is only ~4.5 MiB/1k tokens (so 262144 ≈ 1.2 GiB, and
`n_ctx_train` is 1048576) — raising `ctx-size` is cheap in GPU terms. **Not
actioned:** host RAM is the constraint, not GTT — MemAvailable is ~9 GiB and swap
is 92% used with DS4 resident, which is the band that preceded the 07-19 and
08-06 OOM kills. Measure before raising.

**❌ Checked and REJECTED — do not apply these published recommendations:**
1. **`RADV_PERFTEST=nogttspill`** — recommended by llm-tracker/strix-halo guides
   as fixing "a bunch of performance issues". **Wrong for THIS box.** With the
   BIOS UMA carveout at 512 MB, effectively all model memory *is* GTT; suppressing
   GTT spill is backwards. Already removed here once (see `gtt_memory_model_2026_08_06`).
   Confirmed the router container sets no RADV env vars at all — correct.
2. **Re-enabling `--cache-ram`** — [#22629](https://github.com/ggml-org/llama.cpp/issues/22629)
   is **closed as NOT PLANNED**. Linux overcommit means the limit still never
   enforces (malloc never fails, so the `std::bad_alloc` eviction path never
   runs). `-cram 0` stays. This closes the question rather than leaving it open.
3. **ROCm/HIP builds** — the TinyComputers Strix Halo DS4 write-up reports
   "1–2 t/s" on a HIP build. We measure **18.6 t/s** on radv. Nothing to take;
   independently re-confirms the radv decision. Do not re-litigate.

**🟡 Genuinely open / worth testing (none applied):**
- **CPU governor.** Driver is `amd-pstate-epp`, governor **`powersave`**, EPP
  `balance_performance`. The strix-halo-guide runs tuned's
  `accelerator-performance` profile. Needs root, so untested here. Cheap and
  reversible: `sudo cpupower frequency-set -g performance`, re-run the pp2053
  bench (baseline **256.7 PP / 18.59 TG**), revert if flat. Expect little — decode
  is GPU-bandwidth bound — but prefill has real CPU-side work.
- **`cache-reuse = 256` on DS4 is INERT**, confirmed on every load:
  `W srv load_model: cache_reuse is not supported by this context, it will be
  disabled`. Same class as [#21468](https://github.com/ggml-org/llama.cpp/issues/21468)
  (Gemma 4). MLA contexts do not support the KV-shifting reuse path. The setting
  has been a no-op since 08-07; keep or drop, but stop believing it does anything.
- **Context checkpoints** are ON by default in our build (`-ctxcp` 32/slot,
  `-cms` 8192) and are the mechanism that could blunt re-prefill. ⚠️
  [#24055](https://github.com/ggml-org/llama.cpp/issues/24055): checkpoints are
  **always invalidated on hybrid/recurrent models** — that hits
  `qwen3.8-27b-q4` (hybrid SSM, ~1 layer in 4 full-attention), NOT DS4.

**Changed:** nothing.

---

## 2026-08-20 (later) — "DS4 feels slow" — MEASURED, it is NOT. The cost is stream-drop re-prefill, not throughput

**Observed:** User suspected DS4 was slower than when it was set up. Followed the
standing debug order from `prefill_amplification_2026_08_02` — `requests_deferred`
→ prompt sizes → only then benchmark.

1. **`requests_deferred = 0`**, `requests_processing = 0`, all 3 slots idle.
2. Child argv byte-for-byte matches the documented config (`--parallel 3
   --ctx-size 262144 --ubatch-size 1024 --slot-prompt-similarity 0.5 --kv-unified`).
3. GPU idle at 629 MHz (DPM level 1 of 3 — idle state, not throttling); memory
   pressure `some avg10=0.00`.

**MEASURED** (child `/completion`, greedy, `cache_prompt=false`, unique prefix per
trial so nothing can be replayed, `id_slot` pinned to the last slot so it cannot
LRU-steal a conversation's cached prefix):

| prompt | prefill t/s | decode t/s |
|---|---|---|
| pp2053 (like-for-like vs the 08-12 baseline) | **256.68** (251.8–261.1) | **18.59** (18.58/18.60/18.59) |
| pp16389 | 235.36 | 16.67 |
| pp19605 | 225.57 | 16.52 |

**vs the 2026-08-12 baseline of 268.98 PP / 19.48 TG @pp2053: 95.4% on BOTH axes.**
Decode was dead stable to ±0.01 t/s across trials. **There is no throughput
regression.**

**Steady-state user-facing latency is also unchanged.** Effective seconds per
OUTPUT token (which includes the prefill wait) from `agent.log`, DS4 calls only:

```
08-06 0.11 | 08-07 0.10 | 08-08 0.10 | 08-09 0.09 | 08-10 0.09 | 08-11 0.10
08-12 0.11 | 08-13 0.10 | 08-14 0.09 | 08-15 0.10 | 08-16 0.10 | 08-17 0.10
08-18 0.10 | 08-19 0.08 | 08-20 0.09   <- today is mid-range
```
p50 latency today 51.1 s, inside the 31–60 s band of every other day.

**🔴 What the user actually felt — the TAIL, and it is stream drops.** Two calls
today were pathological, and both are explained:

- `09:20:56 in=100781 latency=1717.0s` (28.6 min) — **no `cache=` field at all**,
  i.e. 0% prefix reuse. Preceded at 09:07:19 by
  `agent.stream_diag: Stream drop on attempt 2/3 — RemoteProtocolError: peer
  closed connection without sending complete message body (incomplete chunked
  read)`. **Every stream drop forces a full re-prefill**, and at ~100k tokens on
  the measured decay curve that is 8–11 minutes per attempt.
- `10:06:37 in=4669 latency=1120.9s` (18.7 min) — the `night-check-in` cron
  triggered manually as part of the earlier restore work, on a DS4 whose slot
  caches had just been wiped by the router restart. Self-inflicted by the test.

**Neither the drops nor the big outliers are new:**

| | stream drops | max latency | calls >300 s |
|---|---|---|---|
| 08-13 | 5 | 645.8 s | 14 |
| 08-18 | 5 | **2517.0 s** | 12 |
| 08-19 | 6 | 585.8 s | 1 |
| 08-20 | 3 | 1717.0 s | 2 of 19 |

08-18 had a WORSE outlier than today. Today's *rate* looks high (2/19 = 10%) only
because the sample is 19 calls; the other days run 4–6%.

**Changed:** nothing. This is a measurement.

**🔴 The real open lever is the stream drops**, not the model. `RemoteProtocolError
(incomplete chunked read)` against `http://127.0.0.1:9292/v1` means the router
closed a streaming response mid-body. Hermes retries 3×, and on a ~100k-token
conversation each retry costs a full re-prefill — so ONE drop turns a 60 s turn
into a ~28 min one. That is the whole "slow" experience. Not investigated yet;
it has been happening at 1–6/day since at least 08-13.

**⚠️ Also noticed, unrelated:** `SwapFree` is 630 MB of 7.6 GB (92% used) with DS4
resident. No stall right now (`pressure some avg10=0.00`), but it is the tight
band that preceded the 07-19 and 08-06 OOM kills. Worth watching.

---

## 2026-08-20 — qwen3.8-27b evaluation CLOSED: DS4 restored as daily driver, Q8_0 deleted, night cycle no longer swaps away

**Observed:** User's verdict after a day of testing: "the speed is not that great
to be a daily driver." The tuned `qwen3.8-27b-q4` does 31–33 t/s vs DS4's 19.48,
but it runs `parallel = 1` with reasoning always on, so on real multi-caller work
(Hermes + pi-kalam + Hindsight + OpenWebUI, all hitting one model) it loses to
DS4's 3 unified slots. Request: DS4 back as the daily driver, Q4 retained as an
on-demand model the user switches to by hand in Hermes, 256k context on both,
gemma left alone.

**Audit found four things that would have pulled the box back off DS4**, three of
them silently:

1. `~/.hermes/config.yaml` `model.default` and the provider-level `model` were
   both still `qwen3.8-27b-q4` (set 08-19). Every cron with `model: null`
   resolves to `model.default`.
2. The `night-check-in` cron (21:35) was pinned to `qwen3.8-27b-q4`.
3. 🔴 **`workers/overnight_tasks.py` called `swap_to_qwen()` at the end of the
   night cycle.** `swap-model.sh`'s `QWEN` had been retargeted to
   `qwen3.8-27b-q4` on 08-19, so **every night with pending tasks would have
   ended by evicting DS4** and left the Q4 resident by morning — against the pins
   in (1) and (2). This was the one that would not have announced itself.
4. `watchdog.env` `HEAVY_MODELS` and `swap-model.sh` `HEAVY_OTHERS` both still
   listed the Q8_0 id being deleted.

**Answering the user's question — no, the workflow files were NOT changed on
2026-08-19.** `~/Dev/automated-workflows` has no commits after `2a83c52`
(08-18) and a clean tree apart from a long-standing untracked
`workers/run_night_checkin.py` (mtime 08-17). The only Hermes skill touched on
08-19 was `productivity/google-tasks-scheduling/SKILL.md` at 08:53, hours before
the model work and unrelated to it. The workflow files were nonetheless in scope
because of finding (3): they were correct on 08-18 and were invalidated by a
change made *elsewhere* on 08-19.

**Changed:**
- `models.ini` (BOTH copies, runtime edited INODE-PRESERVING then copied to the
  repo template; `diff` clean after): deleted the `[qwen3.8-27b]` Q8_0 section
  and folded its MTP / `spec-draft-n-max` documentation into
  `[qwen3.8-27b-q4]`, which is now the only on-demand target. Fixed the stale
  header bind-mount path (`/models/qwen` → `/models/qwen38`) and restated the DS4
  comment block as the daily driver. **No setting changed** — DS4 `ctx-size =
  262144` / `load-on-startup = true`, Q4 `ctx-size = 262144` /
  `load-on-startup = false`, gemma 131072 were all already correct.
- `~/.hermes/config.yaml` (backup `config.yaml.bak-20260820-pre-ds4-restore`):
  `model.default` and provider `model` → `deepseek-v4-flash`; `qwen3.8-27b`
  dropped from the selectable models list. Edited as YAML directly, not via
  `hermes config set` (that CLI has mangled this file twice).
- `hermes cron edit 5d37a9e1e859 --model deepseek-v4-flash` — `night-check-in`
  back on DS4. Both check-ins are now DS4; no cron pins an unloadable model.
- `watchdog.env`: `HEAVY_MODELS` → `qwen3.8-27b-q4,deepseek-v4-flash`.
  `tools/swap-model.sh`: `HEAVY_OTHERS` → `qwen3.8-27b-q4`. Kept in lockstep, as
  the in-file comment demands.
- `~/Dev/automated-workflows` (branch `chore/night-cycle-stays-on-ds4`, commit
  `7c0906e`, **not pushed**): `_swap_back_to_qwen`/`_restore_qwen_best_effort` →
  a single `_no_swap_back`; `swap_to_qwen()` and `QWEN_35B` deleted from
  `overnight_swap.py`. Tests now build their swap mocks with
  `MagicMock(spec=OvernightSwapHandler)`, so **a reintroduced swap-back raises
  AttributeError instead of passing silently**, and assert the cycle's only swap
  call is `swap_to_deepseek()`.
- `~/.hermes/skills/automation/night-cycle-{tasks,automation}/SKILL.md`: the
  stale `qwen3.6-35b`-as-daytime-model prose (flagged as "user's call" on 08-19)
  is now correct.
- Deleted `Qwen3.8-27B-Q8_0.gguf` (29,047,086,048 bytes) after confirming no
  process held it and the Q4 was intact. **Disk 854 → 881 GB free.**

**Expected:** DS4 resident at all times; nothing unattended can name a model that
is not loaded; the Q4 reachable only by a deliberate `swap-model.sh qwen` or an
in-session Hermes switch; 262144 context on both heavy models.

**Refs:** the 2026-08-19 21:37 OOM entry below — its "🔴 STILL ARMED — recurs
2026-08-20 21:35" warning is what this entry closes. The root-cause fix is not
just repointing the cron: it is that **only one heavy model is ever wanted by an
unattended caller now**, so the cron-vs-user contention that the mutex cannot
guard has no way to arise on its own.

**Smoke test — PASSED, all of it on the running system.**
- Watchdog verified on the RUNNING PROCESS, not the unit file:
  `/proc/489169/environ` → `HEAVY_MODELS=qwen3.8-27b-q4,deepseek-v4-flash`, and
  `:9611` confirmed owned by MainPID (no env-less orphan, the 08-01 failure mode).
- Restart discipline: DS4's `/slots` showed a live 118k-token prefill from a
  Telegram Hermes session, so the restart was HELD until that session completed
  and all three slots read `is_processing: false`. **One** restart total, well
  inside the `StartLimitBurst=3`/hour budget (0 prior attempts in the window).
- `daemon-reload && restart llama-router` → gemma4-e4b serving in seconds
  (ordering intent held), DS4 cold-loaded in **~3.5 min**. `NRestarts=0`.
- **Router registers exactly 3 ids**; the Q8_0 is gone. Verified from CHILD ARGV,
  not the file (gotcha #11):
  ```
  deepseek-v4-flash  loaded    ctx=262144 np=3
  gemma4-e4b         loaded    ctx=131072 np=4 spec=draft-mtp n_max=4
  qwen3.8-27b-q4     unloaded  ctx=262144 np=1 spec=draft-mtp n_max=5
  ```
- Effective-settings diff of `models.ini` old vs new (comments stripped) is
  **exactly the removed `[qwen3.8-27b]` block and nothing else** — no DS4, gemma
  or Q4 setting was touched. Runtime inode preserved (`12714493` before and
  after); repo template `diff`-clean against it.
- Real completions both ways: DS4 → `"DS4 daily driver online"`, `finish=stop`;
  Q4 → `"Q4 on demand online"`, `finish=stop`, `reasoning_content` populated
  (native xhigh reasoning intact).
- **Both swap directions exercised** — this is the path whose `HEAVY_OTHERS`
  staleness was the 08-19 latent OOM: `swap qwen` **18 s** (DS4 CONFIRMED
  unloaded before the Q4 loaded), `swap ds4` **3 min 9 s**. `heavy_evictions_total
  0` and `heavy_coresident 0` throughout — the mutex correctly did NOT fire on
  clean swaps, matching the 08-07 finding that it only triggers on genuine
  co-residency.
- `journalctl -k`: **no OOM, no amdgpu error/timeout/reset** across the window.
  All five user services `active`, `NRestarts=0`. Watchdog probes green on both
  resident models.
- Consumers: `hermes config get model.default` → `deepseek-v4-flash`; both
  check-in crons pinned to DS4; the other 8 jobs are script-mode or resolve to
  `model.default`, so **no unattended caller can name an unloaded model.**
- Workflows: **641 tests pass** in `~/Dev/automated-workflows` (the one
  collection error, `e2e/test_full_briefing.py` → `daily_briefing.main`, is
  PRE-EXISTING and untouched by this change). Orchestrator dry-run confirms both
  branches: empty pending → zero swap calls; one task → `[call.swap_to_deepseek()]`
  and nothing else.
- Disk: 854 → **881 GB free** after deleting the Q8_0 GGUF; the Q4 still resolves
  inside the container and served a live completion afterwards.
- `night-check-in` (`5d37a9e1e859`) manually re-triggered — the job that failed
  at 21:38 on 08-19 with `RuntimeError: Connection error.` — and it **completed**
  (11 API calls, `finish_reason=stop`). ⚠️ It took **~25 min** because DS4's
  prompt cache was cold right after the reload, and it logged one client-side
  `TimeoutError (attempt 1/3)` before succeeding on retry. At the real 21:35 slot
  DS4 will have been warm all day, so this is a cold-cache artifact of the test,
  not the job. Worth knowing that a manual trigger right after a router restart
  is expensive and occupies DS4 for the duration.

**⚠️ Pre-existing, NOT caused by this change, worth a look later:** DS4's child
logs `W srv load_model: cache_reuse is not supported by this context, it will be
disabled` on every load. `cache-reuse = 256` has been in the DS4 preset since
08-07 and is therefore INERT. Not investigated here — it changes nothing about
this change, but the setting is not doing what the config implies.

---

## 2026-08-19 — Wire qwen3.8-27b as the on-demand swap target (fix stale container mount + heavy-mutex blind spot)

**Observed:** User reported qwen3.8-27b "wasn't wired properly". The router
listed the id but `/models` showed `qwen3.8-27b unloaded failed=true`. Router
log gave the exact cause:

```
[35777] E gguf_init_from_file: failed to open GGUF file
        '/models/qwen38/Qwen3.8-27B-Q8_0.gguf' (No such file or directory)
[35777] E srv load_model: failed to load model
1176.18 I srv operator(): instance name=qwen3.8-27b exited with status 1
```

**The config files were all already correct.** `models.ini` (both copies —
`diff` clean), the unit's mount line, `~/.hermes/config.yaml`
`custom_providers`, and `swap-model.sh` all named qwen3.8-27b. What was wrong
was the RUNNING CONTAINER: `systemctl --user show` reported
`NeedDaemonReload=yes`, and `docker inspect` showed the live mount set still
carried the PREVIOUS generation —

```
/home/dinesh-se/llama-stack/hf-cache-archive/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF -> /models/qwen
```

— i.e. the 08-18 unit edit that swapped `/models/qwen` → `/models/qwen38` was
never `daemon-reload`ed or restarted, so the container had a mount for the
RETIRED 35b and none for the new 27b. `models.ini` itself was fine and in sync
(same inode inside and out, container `sed` confirmed it saw the
`[qwen3.8-27b]` section) — this was purely the unit half of the edit going
inert. **Textbook `llama_server_router_mode` landmine: unit edits do nothing
until `daemon-reload && restart`.**

**Second, independent gap (safety, would not have surfaced as an error):**
`llama-watchdog`'s `HEAVY_MODELS` still defaulted to
`"qwen3.6-35b,deepseek-v4-flash"` (`watchdog.py:133`), and `watchdog.env` did
not override it. qwen3.6-35b was removed from all configs on 08-17, so the
heavy-model mutex was tracking a model that no longer exists and **did not
recognise qwen3.8-27b as heavy at all.** An on-demand load of qwen3.8-27b on
top of resident DS4 (27 + 98.4 + 4.9 + KV > 124 GiB) would have been completely
unguarded — the exact 2026-08-07 OOM path, which on this box reaps the GNOME
session rather than the model.

**Changed:**
- `~/observability/stack/llama-watchdog/watchdog.env`: added
  `HEAVY_MODELS=qwen3.8-27b,deepseek-v4-flash` + rationale comment. Restarted
  watchdog. **Done FIRST, before the router restart**, so the mutex was armed
  before qwen3.8-27b became loadable at all.
  Backup: `watchdog.env.bak-20260819-pre-qwen38`.
- `systemctl --user daemon-reload && systemctl --user restart llama-router.service`
  — the actual fix. No file edit was needed; the unit was already correct.
- `tools/swap-model.sh:166`: display label `qwen3.6-35b` → `qwen3.8-27b` in the
  `ds4` branch. Cosmetic only — `$QWEN` was already correct, so behaviour was
  never affected.

**Expected:** `/models/qwen38/Qwen3.8-27B-Q8_0.gguf` resolves inside the
container; qwen3.8-27b loads on demand via `swap-model.sh qwen` or by being
named in a Hermes request; the heavy mutex evicts DS4 rather than allowing
co-residency.

**Refs:** GGUF metadata read directly from the file — `general.architecture =
qwen35`, `block_count 65`, `full_attention_interval 4` (hybrid SSM/attention:
only ~16 of 65 layers carry a KV cache), `head_count_kv 4`, `key/value_length
256`, `context_length 262144`, `nextn_predict_layers 1`.

**Smoke test — PASSED.**
- Integrity: `sha256sum` = `a680f44a…b67e348`, matches the HF
  `.metadata` etag exactly. 29,047,086,048 bytes, GGUF v3, 866 tensors. Not a
  truncated download.
- Mount: `docker exec llama-router ls /models/qwen38/` now lists the GGUF; the
  stale `/models/qwen` is gone. `NeedDaemonReload=no`.
- Watchdog verified **on the running process, not the file**:
  `/proc/3612341/environ` → `HEAVY_MODELS=qwen3.8-27b,deepseek-v4-flash`, and
  `:9611` confirmed owned by MainPID (no orphan holding the port).
- Router restart: gemma4-e4b serving within seconds, DS4 cold-loaded in **~4
  min** (20:19 → 20:23). Ordering intent in models.ini held.
- `swap-model.sh qwen`: DS4 evicted and CONFIRMED unloaded before the load —
  **23 s end to end**, no co-residency window.
- Real completion through the Hermes path
  (`POST /v1/chat/completions`, model=qwen3.8-27b): correct answer to a
  non-trivial probability question (13/28), `finish_reason=stop`,
  `reasoning_content` populated. Default xhigh reasoning left intact as
  intended.
- **Memory footprint MEASURED (not estimated — `ssm.*` hybrids break the
  estimator ~4×): GTT 41.5 GiB with qwen3.8-27b + gemma4-e4b resident,
  MemAvailable 77.3 GiB.** So qwen3.8-27b ≈ **36.6 GiB** at the full 262144
  ctx. Very comfortable; the hybrid architecture keeps KV small (~9 GiB at
  262k) because only ~1 layer in 4 is full-attention.
- **Throughput MEASURED, warm, pp3393: 265.19 t/s prefill / 7.78 t/s decode.**
  Decode was identical (7.78) on both the cold and warm runs — stable, and
  quant-bound rather than misconfigured: 27 GiB of Q8_0 weights read per token
  against ~256 GB/s of bandwidth puts the roofline near 9.5 t/s, so we are at
  ~82% of it.
- Metrics relay picked the model up (`llamacpp:*{model="qwen3.8-27b"}`),
  watchdog probe green (`probe_success=1`, `consecutive_failures=0`).
- `heavy_evictions_total 0`, `heavy_coresident 0` for the whole window — the
  clean swap correctly did NOT trip the mutex, matching the 08-07 finding that
  it only fires on genuine co-residency.
- `journalctl -k`: **no OOM, no amdgpu error/timeout/reset** across the window.
- Swapped back toward DS4, then **the user asked mid-run not to restore**, so
  the swap-back was aborted and `swap-model.sh qwen` re-run. Both directions of
  the swap are therefore now exercised, including an abort mid-load with no ill
  effect.

**END STATE (deliberate, differs from the previous steady state):**
**qwen3.8-27b + gemma4-e4b resident; deepseek-v4-flash UNLOADED.** GTT 41.5 GiB,
MemAvailable 77.5 GiB.

**🔴 Consequence the user must be aware of:** `~/.hermes/config.yaml`
`model.default` is still `deepseek-v4-flash`, and the **`night-check-in` cron
(`35 21 * * *`) is pinned to DS4** (pinned on 08-15 precisely so it would stop
resolving to an evicted model). At 21:35 it will name DS4, the router will
autoload it, and the heavy mutex will **evict qwen3.8-27b** — a 3–11 min load,
unattended. This is correct, designed behaviour, not a fault, but it means the
qwen residency chosen here will NOT survive the night unless one of these is
changed: repoint `model.default` and/or the night-check-in cron to
qwen3.8-27b, or accept the eviction. Not actioned — the user's call.

**🔴 Open finding — Q8_0 is the wrong quant for this role, NOT a bug.** At 7.78
t/s decode, qwen3.8-27b generates **2.5× SLOWER than the 98 GiB DS4** (19.48
t/s), because DS4 is an MoE with few active params per token while this 27b
reads all 27 GiB every token. It is billed in `models.ini` as the "fast
on-demand swap target: benchmark/coding" and it is currently the opposite of
fast. It only uses 36.6 of ~124 GiB, so the headroom is being spent on
precision nobody asked for. Levers, cheapest first:
1. **Re-download at Q4_K_XL or Q6_K** (~16–22 GiB) → roughly 12–16 t/s
   expected. The 07-31 precedent (`project_27b_q8_parked`) already settled on
   Q6_K for the previous 27b.
2. **MTP speculative decoding** — the GGUF carries
   `qwen35.nextn_predict_layers = 1`, so an MTP sidecar in the unsloth repo
   would enable the same `spec-type = draft-mtp` path gemma4-e4b uses. Not
   checked yet.
3. `ubatch-size` is at 512 per the "tune during bench" note; prefill already
   measures 265 t/s, so this is the least valuable lever.
Not actioned — quant choice is the user's call.

**Housekeeping — ✅ DONE 2026-08-19** (deleted at user request): the 08-18
download left **43.92 GB of abandoned `.incomplete` partials** in
`hf-cache/hub/models--unsloth--Qwen3.8-27B-GGUF/.cache/huggingface/download/`
(19.9 + 12.6 + 11.4 GB), all fragments of the Q8_0 whose final file is complete
and sha-verified. Deleted with `find … -name '*.incomplete' -type f -delete`
after confirming no process held them (`fuser`) and no download was running.
**Disk 813 GB → 854 GB free.** Both real GGUFs verified byte-exact afterwards
and the container still resolves them; `qwen3.8-27b-q4` served a live completion
post-delete.

**Also not actioned:** `~/.pi/agent/models.json` does NOT list qwen3.8-27b (only
DS4 + gemma). The request was scoped to Hermes; pi cannot select the model
until an entry is added there.

---

### Follow-up the same session — MTP speculative decoding: 7.78 → 17.7 t/s (2.3×)

**Observed:** User asked whether the speed could be raised, and what other Strix
Halo owners get. The 7.78 t/s baseline turned out to be **exactly the
memory-bandwidth roofline, not a misconfiguration**: 29 GB of Q8_0 weights read
per token against ~220–256 GB/s of LPDDR5X puts the ceiling near 7.9–9.5 t/s.
No llama.cpp flag can beat that, because decode at batch 1 is bytes-per-token
bound. Only two things can: **read fewer bytes** (smaller quant) or **emit more
tokens per forward pass** (speculation).

**The find:** `qwen35.nextn_predict_layers = 1` in the GGUF metadata, and a
direct tensor-table dump showed the MTP layer is **EMBEDDED in the main file** —
`blk.64.nextn.{eh_proj,enorm,hnorm,shared_head_norm}`. So `spec-type =
draft-mtp` works with **no `model-draft` sidecar at all**, unlike gemma4-e4b
which needs an explicit MTP file. This was free performance sitting unused.

**Changed** (`models.ini`, BOTH copies, edited in place to preserve the inode):
```
[qwen3.8-27b]
spec-type = draft-mtp,ngram-mod
spec-draft-n-max = 12
spec-ngram-mod-n-min = 24
```
then `systemctl --user restart llama-router` + `swap-model.sh qwen`.

**MEASURED — clean A/B, two standalone containers identical except for the spec
flags, greedy (`temperature 0`, `top_k 1`) so the token sequence matches:**

| config | decode t/s | acceptance | mean len |
|---|---|---|---|
| spec off | **7.78, 7.78** (dead stable) | — | — |
| `draft-mtp` n12 | **17.69 – 19.67** | 0.36–0.41 | 5.3–5.9 |
| `draft-mtp,ngram-mod` first-exposure | **17.67** | 0.35 | 5.38 |
| `draft-mtp,ngram-mod` REPEATED prompt | 67.71 ⚠️ artifact | 0.79 | 28.29 |

**Verified live in production through `:9292`: 17.69 t/s**, child argv carries
`--spec-type draft-mtp,ngram-mod`, and the child logs `common_speculative_init_result:
creating MTP draft context against the target model`. GTT 41.5 → **45.5 GiB**
(the MTP draft context costs ~4 GiB). **2.27× for one config line and ~4 GiB.**

⚠️ **Two measurement traps hit and worth remembering:**
1. **`speculative.n_max` in the REQUEST BODY is silently ignored.** An n_max
   sweep via the JSON body produced 13.5–18.4 t/s and looked like a real curve;
   it was pure run-to-run variance, proven because mean len stayed at 5.53 when
   n_max was supposedly 4. Draft length can only be set as a server flag.
   **`spec-draft-n-max` is therefore still UNTUNED at 12.**
2. **The headline "warm" numbers in public write-ups are a repetition
   artifact.** Re-sending an identical prompt let ngram-mod replay its own prior
   output at acceptance 0.79 / mean len 28.3 for 67.71 t/s. The KyaniteLabs
   authors say so themselves ("the label is part of the number"). ngram-mod is
   kept because it is free and genuinely helps repetitive code edits, but it
   adds ~nothing (17.67 vs 18.7) on first-exposure text.

**Refs — what other Strix Halo owners measure on this exact model:**
- [KyaniteLabs/qwen38-27b-strix-halo](https://github.com/KyaniteLabs/qwen38-27b-strix-halo):
  champion config is **UD-Q4_K_XL** + `draft-mtp,ngram-mod` n12 — honest real-traffic
  numbers **prose 11–24 t/s, code 30–40 t/s**. Also reports KV `q4_0` saves ~47%
  vs `q8_0`, and that `GGML_HIP_ROCWMMA_FATTN=ON` costs −41% prefill on gfx1151.
- [julianmb/q38rocm](https://github.com/julianmb/q38rocm): **Q4_K_M baseline 12.27 t/s**,
  ROCmFP4 (13.55 GB) 14.02 t/s unassisted, **36.04 t/s** with MTP — and
  explicitly **Vulkan/RADV 34.8–36.0 vs ROCm 18.5**, which independently
  CONFIRMS our long-standing radv-over-ROCm decision. (Note this directly
  contradicts KyaniteLabs' claim that Vulkan spec-decode is half of ROCm; we are
  on radv and measuring well, so treat the KyaniteLabs Vulkan claim as not
  applicable to this build.)
- Their Q4_K_M 12.27 t/s vs our Q8_0 7.78 t/s scales almost exactly by the
  weight-size ratio (29.0/16.5 × 7.78 = 13.7). **The roofline model is
  confirmed by an independent third party.**

**🔴 The remaining lever is the QUANT, and it is the big one.** We are on Q8_0
(29 GB) purely because that is what got downloaded. `unsloth/Qwen3.8-27B-GGUF`
also ships **UD-Q4_K_XL**, **UD-Q5_K_M**, **UD-Q6_K**, and a **Q4_0 MTP**
variant. Dropping to UD-Q4_K_XL (~16 GB) should roughly double decode again on
top of MTP — **projected ~30–36 t/s, matching both third-party reports** — while
also freeing ~13 GiB. That is a ~4× total gain over where this started. Not
actioned: it is a fresh ~16 GB download and the quant/quality trade-off is the
user's call. A second free lever remains: KV `q4_0` instead of `q8_0` (~47% KV
saving, reported harmless on this model).

## 2026-08-19 (final) — Make the Q4 the default everywhere; fix a latent OOM in swap-model.sh

**Observed:** User asked for "the best performed version for testing", i.e.
`qwen3.8-27b-q4` should be what they land on by default. Auditing for anything
that could pull them off it surfaced a **latent OOM bug**.

**🔴 `swap-model.sh ds4` would have OOM'd the box at 23:00 tonight.** The script
hardcoded `QWEN="qwen3.8-27b"` and evicted only that id. With the **Q4** resident
(`qwen3.8-27b-q4`), `unload qwen3.8-27b` returns "✓ already unloaded", so the
CONFIRMED-eviction guard — the entire reason every failure path in that script is
fatal — **passes vacuously**, and DS4 then loads on top of a resident heavy model
(98.4 + 34 + 4.9 = 137 GiB > 124 GiB cap). This was live: the
`night-cycle-tasks` skill runs `swap-model.sh ds4` at 23:00 via the
`overnight-tasks` cron.

**Changed:**
- `tools/swap-model.sh`: added `HEAVY_OTHERS="qwen3.8-27b-q4 qwen3.8-27b"`; the
  `ds4` branch now evicts **every** id in that list, not just `$QWEN`. `QWEN`
  retargeted to `qwen3.8-27b-q4`. Labels de-hardcoded. **Keep `HEAVY_OTHERS` in
  lockstep with `HEAVY_MODELS` in `watchdog.env`** — a stale list here is an OOM
  path, not cosmetics.
- `~/.hermes/config.yaml`: `model.default` → **`qwen3.8-27b-q4`**;
  provider-level `model` → same; **`qwen3.8-27b` (Q8_0) REMOVED from the
  selectable models list** so the slow variant cannot be picked by accident (it
  is still defined in `models.ini` and reachable by id for quality A/B).
  Backup: `config.yaml.bak-20260819-pre-default-q4`.
- `hermes cron edit 5d37a9e1e859 --model qwen3.8-27b-q4 --provider custom:local-models`
  — the `night-check-in` job (21:35) that caused the 21:37 OOM is no longer
  pinned to DS4. **This is the root-cause fix for that incident.**

**⚠️ Not changed, user's call:**
- The `overnight-tasks` cron (23:00) still swaps to DS4 by design — that is the
  intended overnight-heavy-work workflow. It WILL evict the Q4. The skill says
  it skips the swap entirely when there are no pending tasks. Safe now that
  swap-model.sh evicts correctly, but it does take the test model away.
- `~/.hermes/skills/automation/night-cycle-*/SKILL.md` still describe the
  retired **`qwen3.6-35b`** as the daytime model. Stale prose, not executable
  config, but it will mislead an agent reading it.
- `~/.pi/agent/models.json` still has no qwen3.8 entry of either quant.

**Smoke test:** `swap-model.sh` syntax OK (`bash -n`), `status` prints the true
4-model state. Q4 loaded and served a correct completion. Config verified by
re-reading the file. ⚠️ The user's LIVE Hermes session has `qwen3.8-27b` pinned
in session state and keeps reloading the Q8 — config changes do not retroactively
move a running session; a new session (or an in-session switch) is required.

## 2026-08-19 21:37 — 🔴 OOM INCIDENT: cron-vs-user heavy-model thrash killed the router

**Observed:** User switched their Hermes session to `qwen3.8-27b` at ~21:36. The
router was OOM-killed at **21:37:50** (`status=137`) and their request hung with
no response.

```
21:36:01 HEAVY-MUTEX: 2 heavy models resident
         (deepseek-v4-flash=loading, qwen3.8-27b=loaded) — evicting qwen3.8-27b
21:36:01 HEAVY-MUTEX: unload qwen3.8-27b ok
21:37:47 instance name=gemma4-e4b exited with status 1
21:37:50 kernel: Out of memory: Killed process 3723237 (llama-server)
         total-vm:41462744kB anon-rss:75304kB   <- GTT invisible to OOM killer, as always
21:37:51 llama-router.service: Main process exited, code=exited, status=137
```

**Root cause — a THRASH between two independent callers, not a single bad load.**
The 21:35 `night-check-in` cron is pinned to `deepseek-v4-flash` (pinned there on
08-15 for good reasons) and **retries 3×**. Each retry asked the router for DS4
(~98.4 GiB); meanwhile the user's Hermes session kept asking for qwen3.8-27b
(Q8_0, ~45 GiB). The mutex evicted qwen correctly, the cron retried, the user's
session reloaded qwen, and the two heavy loads overlapped in flight until host
RAM was exhausted. Cron log confirms the retry storm:
`API call failed (attempt 2/3, 3/3) ... model=deepseek-v4-flash` →
`Job 'night-check-in' failed: RuntimeError: Connection error.`

**🔴 The lesson the mutex does NOT cover:** the heavy-model mutex is a
*point-in-time* guard — it evicts when it SEES two heavy models resident. It
cannot stop two independent callers from each re-triggering a load faster than
it can evict. **Co-residency protection ≠ contention protection.** The real fix
is to stop two callers wanting different heavy models at the same time.

**Blast radius — contained, better than 2026-08-06.** `--oom-score-adj=1000` did
its job: the kernel took llama-server, NOT the desktop. gnome-shell and wezterm
survived; all five user services came back on `Restart=on-failure` with
`NRestarts=1`. MemAvailable recovered to 111.9 GiB.

**Changed (before the incident, and it turned out to matter):**
- `watchdog.env`: `HEAVY_MODELS` → `qwen3.8-27b,qwen3.8-27b-q4,deepseek-v4-flash`.
  **`qwen3.8-27b-q4` had been missing** — the new preferred model was NOT
  recognised as heavy at all, so the mutex would not have fired for it. Verified
  on `/proc/<MainPID>/environ`; `:9611` owned by MainPID.
- `~/.hermes/config.yaml`: added `qwen3.8-27b-q4` to
  `custom_providers[Local Models].models` — it was missing, which is why the user
  landed on the SLOW Q8_0 (21 t/s) instead of the Q4 (31–33 t/s).
  Backup: `config.yaml.bak-20260819-pre-q4`.

**Recovery:** unloaded the in-flight DS4, loaded `qwen3.8-27b-q4`, verified a
real completion (`finish=stop`, correct answer). GTT 34.3 GiB, MemAvailable
84.1 GiB. Router `active`, all services `active`.

**🔴 STILL ARMED — recurs 2026-08-20 21:35.** The `night-check-in` cron is
unchanged and still pinned to `deepseek-v4-flash`. If any heavy model other than
DS4 is resident at 21:35 tomorrow, this repeats. Options, none applied (user's
call): repoint the cron to `qwen3.8-27b-q4` or `gemma4-e4b`; or change
`model.default`; or give the cron a single-attempt/no-retry policy so it cannot
storm. **Whatever is chosen, the principle from gotcha #6 stands: every
unattended caller must be pinned to a model that is actually resident.**

## 2026-08-19 (later) — Q4_K_XL A/B + `spec-draft-n-max` tuning: 7.78 → 31–33 t/s (4.2×)

**Observed:** Projected UD-Q4_K_XL at ~27–29 t/s from the roofline model. The
first A/B **refuted that**: at the then-current `spec-draft-n-max = 12`, Q4
measured 17.59 vs Q8's 16.89 — no gain at all. The control run explained why.

**MEASURED (greedy, code prompt, identical containers):**

| | spec off | MTP n=12 | **MTP n=5 (tuned)** |
|---|---|---|---|
| Q8_0 (29.05 GB) | 7.78 | ~17.7 | **21.3–21.9** |
| UD-Q4_K_XL (17.56 GB) | **12.39** | 19.03 | **31.0** |

The roofline model was CORRECT — Q4 spec-off came in at 12.37/12.41 vs ~11.6
predicted. What was wrong was the assumption that the MTP multiplier is constant:
it is **2.2× on Q8 but only 1.4× on Q4**, because the batched verify pass is
compute-bound, so cheaper weights stop helping once speculation is on.

**🔴 The real lever was `spec-draft-n-max`, not the quant.** Sweep on Q4:
`n=3 29.47 | n=5 31.05 | n=6 30.21 | n=7 27.24 | n=12 19.03 | n=20 15.16`.
Long drafts collapse acceptance (0.81 at n=3 → 0.25 at n=20) and waste the
verify pass. The inherited `n=12` was costing **~1.6×**. On Q8: `n=3 21.34 |
n=6 21.86 | n=8 21.73 | n=12 ~17.7`.

**Changed** (`models.ini`, both copies, inode-preserving):
- new `[qwen3.8-27b-q4]` → `/models/qwen38/Qwen3.8-27B-UD-Q4_K_XL.gguf`. Same HF
  repo dir, so the existing `/models/qwen38` mount covers it — **no unit change**.
- BOTH qwen entries: `spec-draft-n-max 12 → 5`, and **`ngram-mod` REMOVED** —
  measured a ~4% net LOSS on first-exposure text (28.95/24.89 with vs
  30.21/26.55 without). Its only gain is replaying its own prior output.

**PRODUCTION, verified on the running child (`--spec-type draft-mtp`,
`--spec-draft-n-max 5`): 31.32 / 32.57 t/s.** GTT **34.2 GiB** (vs 45.4 on Q8) —
Q4 is both **~1.45× faster and ~11 GiB smaller**. Integrity: sha256
`3f227079…bc8b01e` matches HF.

**Checked the published "best" configs and OURS WINS — do not copy them:**

| config | t/s |
|---|---|
| **ours** | **31.32 / 32.57** |
| published KV `q4_0` + `--threads 16` + `-fit off`, our n=5 | 29.00 / 27.35 |
| KyaniteLabs champion verbatim (n12 + ngram + KV q4_0 + t16 + fit off) | 17.83 / 14.80 |

So KV `q4_0` and `--threads 16` are both mild NEGATIVES here, and their `n=12`
+ ngram is catastrophic on this build. **`spec-draft-n-max` is
hardware/build-specific — measure it, never inherit it.**

**Net: 7.78 → 31–33 t/s, 4.2× over where the day started**, of which the quant
contributed ~1.45× and draft-length tuning ~1.6×.

**⚠️ Operational hazard hit:** `systemctl --user restart llama-router` **four
times in one hour tripped the unit's own `StartLimitBurst=3` /
`StartLimitIntervalSec=3600`**, and the router stayed DOWN — systemd counts
start ATTEMPTS, not just failures. It presents as
`Job for llama-router.service failed because start of the service was attempted
too often`, which reads like a config error and is not. Recovery is
`systemctl --user reset-failed llama-router.service && systemctl --user start …`.
**Budget 3 router restarts per hour** when tuning; do the exploration in a
standalone container on another port (as was done here) and restart production
only once at the end.

**End state:** `qwen3.8-27b-q4` + `gemma4-e4b` resident, DS4 unloaded. GTT 34.2
GiB, MemAvailable 84.5 GiB. No OOM, all five user services active. The Q8_0
entry is retained as `qwen3.8-27b` for comparison; it can be deleted along with
its 29 GB GGUF once Q4 output quality is judged acceptable.

## 2026-08-17 — Remove qwen3.6-35b from configs; raise DS4 context to 262144 (3-way sync)
**Observed:** User flagged that the router reported 3 models (`deepseek-v4-flash`, `gemma4-e4b`, `qwen3.6-35b`) when the expected lineup is only 2 (DS4 + gemma). The configs genuinely still carried qwen3.6-35b despite the 08-15 "pause" — it remained registered in all three config files and loaded at startup. Separately, DS4 context was being raised 131072 → 262144 (memory-safe: DS4 KV ~4.5 MiB/1k tokens, +~590 MiB for the extra 131k; peak ~103.3 GiB with gemma, well under the ~120 GiB cap).
**Changed:**
- `~/llama-stack/config/models.ini` + repo template `~/Dev/strix-halo-llm-stack/config/models.ini`: removed the entire `[qwen3.6-35b]` section + its comment block; `[deepseek-v4-flash] ctx-size = 131072 → 262144`
- `~/.hermes/config.yaml`: `custom_providers[Local Models].models` = `[deepseek-v4-flash, gemma4-e4b]` (qwen removed); `model.context_length` set to 262144 via `hermes config set` (note: CLI stored the models list as a JSON string — fixed in-place to a proper YAML list)
- `~/.pi/agent/models.json`: removed the qwen3.6-35b entry; DS4 `contextWindow: 131072 → 262144`
- `docs/infra/current.md`: updated model table (qwen row removed, DS4 ctx 262144), router unit mount list, Hermes/pi config references, residency note
**Expected:** Router serves only DS4 + gemma; DS4 loads with n_ctx 262144 (25% of native 1048576); 3-way ctx sync (models.ini / config.yaml / models.json) holds at 262144. qwen3.6-35b no longer available on demand via swap — removed per user instruction.
**Refs:** llama.cpp router `/v1/models`; Hermes source (`agent_init.py` context resolution, `hermes_cli/config.py`) confirmed per-model `context_length` is checked in the models list at runtime.
**Smoke test:** `docker restart llama-router` → `/v1/models` returns `['deepseek-v4-flash', 'gemma4-e4b']`; router log shows `n_ctx_slot = 262144`, `"n_ctx": 262144, "n_ctx_train": 1048576`; DS4 worker cold-loaded and serving (proxying requests on its port). All three configs verified: models.ini sections = `[*]`, `[gemma4-e4b]`, `[deepseek-v4-flash]`; config.yaml models = list of 2; pi models.json ids = 2. Backup of config.yaml at `~/.hermes/config.yaml.bak-ctx-qwen-rm`.

## 2026-08-15 — Pause qwen3.6-35b; DS4 becomes the sole resident heavy model
**Observed:** The 07:30 morning check-in and 21:30 night check-in cron jobs failed repeatedly with `HTTP 500: proxy error: Failed to read connection`. Root cause: cron jobs with `model: null` resolved to `model.default: qwen3.6-35b` in `~/.hermes/config.yaml`, which was EVICTED (paused) — the router returned 500 when asked to serve an unloaded model. User also reported qwen3.6-35b results unreliable ("I don't get correct results from it all the time").
**Changed:**
- `~/.hermes/config.yaml`: `model.default` → `deepseek-v4-flash` (via `hermes config set model.default`)
- Both check-in crons (`827131b2c8b5` morning, `5d37a9e1e859` night): pinned `--model deepseek-v4-flash --provider custom:local-models`; stored model/provider snapshots now resolve to the resident DS4
- `~/llama-stack/config/models.ini` + repo template `~/Dev/strix-halo-llm-stack/config/models.ini`: `[qwen3.6-35b] load-on-startup = false` (paused, on-demand), `[deepseek-v4-flash] load-on-startup = true` (resident); updated role-alias header comments
- `~/.config/systemd/user/llama-router.service`: Description updated to reflect new lineup
- pi: `~/.pi/agent/models.json` defaultModel already `deepseek-v4-flash`; pi-kalam config.ts already pins all roles to ds4 — no change needed
**Expected:** Cron check-ins resolve to the loaded DS4 (no more 500s); only ds4 + gemma resident; qwen available on demand via `swap-model.sh qwen3.6-35b`.
**Refs:** (internal) router `/v1/models` state after restart — ds4 + gemma loaded, qwen unloaded
**Smoke test:** `hermes config get model.default` → deepseek-v4-flash; cron job listing shows `model: deepseek-v4-flash, provider: custom:local-models`; router restart applied — `/v1/models` shows ds4/gemma loaded, qwen unloaded. Morning check-in re-triggered (job `827131b2c8b5`) to confirm it now completes without the 500.

## 2026-08-13 — Retire Grafana, free :3000 for WhatsApp bridge
**Observed:** Grafana unused for a long time; port 3000 wanted by the WhatsApp bridge (adapter default + the `bridge_port` config was mis-nested under `whatsapp:` instead of `whatsapp.extra:`, so the bridge kept binding the default :3000 and failing against Grafana).
**Changed:**
- `~/observability/stack/docker-compose.yml`: removed the `grafana` service and `grafana-data` volume; `docker compose up -d --remove-orphans` + `docker rm -f grafana`.
- `~/.hermes/config.yaml`: moved `whatsapp.bridge_port` under `whatsapp.extra.bridge_port: 3000` (adapter reads `config.extra.get("bridge_port")`).
- Gateway restarted (`hermes gateway restart`).
**Expected:** Grafana gone, :3000 freed; WhatsApp bridge binds :3000 and connects to Hermes.
**Refs:** Hermes WhatsApp adapter (`plugins/platforms/whatsapp/adapter.py` reads `extra.bridge_port`, default 3000); bridge.js `--port`.
**Smoke test:** `ss -tlnp | grep 3000` → bridge MainThread pid; `curl 127.0.0.1:3000/health` → `{"status":"connected",...}`; bridge.log shows "WhatsApp bridge listening on port 3000" + "WhatsApp connected!"; gateway log "[Whatsapp] Bridge started on port 3000". Grafana container no longer in `docker ps`.
**Note:** Grafana-provisioned Telegram alert rules (ai-stack-watchdog-down, hindsight-down, llama-queue, model-wedged) are retired with Grafana; the watchdog's own Telegram alerts remain. VictoriaMetrics (:8428) + node-exporter (:9100) kept.

## 2026-08-12 (later 2) — Docs: record router image IS Nathan's fork build (prevent "stock Vulkan" misread)
**Observed:** A Hermes analysis of the r/LocalLLaMA DS4 tuning guide reached a false conclusion — that the router runs stock mainline llama.cpp Vulkan and needs an upgrade to Nathan's fork. The premise was wrong: `kyuz0/amd-strix-halo-toolboxes` (pinned digest `ca4c4c…a0211`) IS Nathan's fork build (`Nathanw1014/llama.cpp:strix-halo-vulkan`), and the `10283 (b7b85da9c)` version string is a FORK counter, not mainline. The fork identity was documented only in the `llama-router.service` unit file's digest-pin comment (lines 83–93), not in `current.md` or memory, so a session that read `current.md` + `strings` on the binary (GGML_VK_MMID* are compile-time macros, absent from strings) misidentified the build.
**Changed:** Docs only.
- `current.md`: added a 🔴 note under *Router / serving* that the router image IS Nathan's fork build, version string is a fork counter not mainline, and ground truth is the unit file's digest-pin comment — do not infer stock Vulkan, do not propose a fork upgrade.
- Hermes memory + `ai-infra-state` skill: added the same fork-identity rule (verify build from the unit file, never binary strings).
**Expected:** Prevent a future session from recommending a non-existent build upgrade or misreading the DS4 numbers. Config beats prose — the unit file's digest pin is the ground truth for the build.
**Refs:** r/LocalLLaMA `1vlmh0b` (DS4 Vulkan + DSpark guide); `llama-router.service` lines 83–93.
**Smoke test:** `docker inspect llama-router --format '{{.Config.Image}}'` → `kyuz0/amd-strix-halo-toolboxes@sha256:ca4c4c…a0211` (matches pinned digest); `docker images` shows `:vulkan-radv-performance` tag present; unit file documents fork + fork-counter version.

---

## 2026-08-12 (later) — Hindsight structured output grammar-enforced (`LLM_STRICT_SCHEMA=true`); fixes 11.7% consolidation failure rate
**Observed:** Follow-up on the 9 errors spotted in the LLM-request log during the reflect work (previous entry). All nine are **one failure class**:
```
ValidationError: _ConsolidationBatchResponse
  updates.N.observation_id
    Field required [type=missing]         x6
    Input should be a valid string (None) x3
```
- **Rate: 9 of 77 consolidation calls = 11.7%.** All 74 non-consolidation calls (retain, dedup, reflect) were clean — **0 errors**.
- **Only consolidation fails, and the reason is structural:** it is the sole **batch** schema (`consolidation_llm_batch_size=8`), so gemma4-e4b has to keep 8 observation ids straight across an array and sometimes emits an update carrying `text` but no `observation_id`. Failing index varied (`updates.0`–`updates.4`), so it is not positional.
- **Root cause is an upstream default, not our config.** `hindsight_api/config.py:728` `DEFAULT_LLM_STRICT_SCHEMA = False` keeps the **soft** path: schema described in the prompt, JSON validated with pydantic *after* generation. Nothing prevents a weak model omitting a required field. Upstream's own comment names this exact scenario ("weaker self-hosted instruction-followers can violate ... wedging retain/consolidation on parse retries").
- **Impact was churn, not data loss:** all 34 consolidation *operations* completed, `retry_count=0` at operation level — the wrapper's `MAX_RETRIES=3` absorbed every failure. Cost was **~5.2 min of wasted GPU time in one day** (worst single call 100.4 s) plus the latent risk of all three retries failing.
- Unrelated to the reflect retarget: all 9 errors fall between 05:00–19:00 IST; the retarget went in at 21:45:59.
**Changed:** `~/.config/systemd/user/hindsight-daemon.service` — added `Environment=HINDSIGHT_API_LLM_STRICT_SCHEMA=true` plus a comment block recording the measurement, the GBNF-rejection risk, and the fallback (drop `consolidation_llm_batch_size` 8→4). `daemon-reload` + restart. Backup: `hindsight-daemon.service.bak-20260812-pre-strict-schema`.
**Expected:** OpenAI-compatible providers (our llama-router) receive `response_format: json_schema strict`, so llama.cpp GBNF-enforces the shape and `observation_id` **cannot** be omitted. Eliminates the retry churn and the tail risk.
**Refs:** `hindsight_api/config.py:718-728` (`DEFAULT_LLAMACPP_NO_GRAMMAR`, `DEFAULT_LLM_STRICT_SCHEMA`), `engine/llm_wrapper.py:867` (strict resolved centrally, per-call arg OR server flag), `engine/consolidation/consolidator.py:494`.
**Smoke test:** Restart clean — `active running`, **NRestarts=0**, `/proc/2541069/environ` shows `LLM_STRICT_SCHEMA=true`, `:9177` owned by MainPID, `/health` healthy. Then three checks:
1. **Does llama.cpp accept the schema?** `_ConsolidationBatchResponse.model_json_schema()` uses `$defs` + `$ref` but **no `anyOf`/`oneOf`/`allOf`/`pattern`/`format`** — i.e. none of the constructs that break llama.cpp's json_schema→GBNF converter. Posted it directly to :9292 as `response_format: json_schema strict`: **accepted, 1.1 s, `finish_reason=stop`**, output parsed and passed pydantic validation.
2. **Is the failing field actually enforced?** Forced the `updates` branch with 8-item batches, 5 trials, temperature 0.7, distinct seeds: **40 of 40 update objects carried a valid non-null `observation_id`**, 5/5 calls clean, 2.2–2.8 s each. Under the soft path the per-call failure rate was 11.7%.
3. **Does Hindsight's own wrapper send it?** Non-persisting `/memories/dry-run-extract` → real `retain` call logged `status=success`, `finish_reason=stop`, 7.3 s, `response_schema=FactExtractionResponse`. Production code path confirmed.
⚠️ **NOT yet proven:** a real *consolidation* batch through Hindsight — a manual `/consolidate` completed with **zero LLM calls** because nothing was pending. That will exercise naturally with use; verify via `llm-requests?limit=200` filtered to `operation=consolidation` and confirm `status=error` count stays 0.

---

## 2026-08-12 — Hindsight reflect retargeted DS4 → gemma4-e4b (gotcha #6 CLOSED); DS4 config validated against the Reddit tuning guide
**Observed:** Two threads in one session.

1. **Config validation vs. an external DS4 tuning guide** (r/LocalLLaMA "DeepSeek V4 Flash 0731 at 27+ t/s decode on Strix Halo", incl. its 2026-08-12 ROCm re-test edit). Diffed every parameter against our live config. **We match or beat it on everything structural**: boot params byte-identical (`amd_iommu=off gttsize=126976 ttm.pages_limit/page_pool_size=32505856`), BIOS carve-out better (512 MB vs their 4 GB), KV/ctx/batch identical. `-ub` is a confirmed non-lever on both boxes independently (they: ub2048 ≈ ub8192; us: 0.4% PP spread over 512/1024/2048). Their ROCm re-test on 7.14 confirms our standing radv decision — do not re-litigate.
   - 🔴 **Corrected a long-standing apples-to-oranges comparison.** The "205 t/s prefill" in the 2026-08-06 entry came from a ~700-token server request (and the spec-test's 61.22 t/s from a **106-token** prompt); the guide's 284.98 is `llama-bench` pp2048. **MEASURED like-for-like on the live child at pp2053: 268.98 t/s median (268.84–269.96 over 3 trials), decode 19.48 t/s.** That is **94.4% of their prefill and ABOVE their plain decode (18.33)**. The residual ~6% is explained by our server HTTP path + `-np 3` + `-ub 1024` vs their `llama-bench` + `-np 1` + `-ub 2048`. **There was never a prefill gap, and the build-upgrade question (kyuz0 b10283 → Nathan v0.6.1) is CLOSED — no upgrade warranted.** The 0.4% trial spread also rules out the guide's gotcha #5 (v0.6 stride bug dropping 43 attention layers to CPU, which presents as ~half speed).
   - Also corrected: `models.ini`'s DS4 comment says the dspark sidecar buys "~30% decode (18.99 → 24.55)". The 2026-08-04 like-for-like pair is **+40.0% (18.81 → 26.33)**; 24.55 was the 128k-context figure. (Comment not yet edited — noted for the next `models.ini` touch.)
   - **DSpark restore remains blocked, and it is OUR constraint, not a tuning gap.** The guide's author runs one model, one slot, no aux stack. Live here: GTT 107.1 GiB with DS4 + gemma4-e4b, host RAM 6 GiB available, swap 4/7 used. +10.15 GiB sidecar ⇒ host RAM ≈ −4 GiB ⇒ OOM. Feasible only in a "DS4 exclusive" mode that also evicts gemma4 and takes the aux stack dark.
   - **Untested lever recorded for later:** our best-ever DS4 number (26.33 t/s @ 0.786 acceptance) was measured at **`--spec-draft-n-max 2`** (`bench/archive/ds4_spec_test.py:67`; llama.cpp default is 3). The guide runs **64**, with mean accepted length 4.02. Depth 2 hard-caps accepted length at 2. Zero memory cost, plausible 26.3 → 28–31 t/s — but only meaningful once the sidecar can be loaded at all.
   - ✅ Also confirmed from our own GGUF metadata: DS4 `n_ctx_train = 1048576` (we run 131072). KV is ~4.5 MiB/1k tokens, so even full 1M context costs only ~4.6 GiB. **Context is not memory-bound — it is prefill-time-bound** (observed: a 100,489-token prefill took 751 s to first token). Not raised.

2. **Hindsight reflect retarget — the queued (a) item from the 2026-08-10 entry, now APPLIED.** Re-verified the live unit still read `HINDSIGHT_API_REFLECT_LLM_MODEL=deepseek-v4-flash`, i.e. the retarget documented on 2026-08-09 had still never been applied. Established via the Hindsight LLM-request log that **reflect had logged ZERO calls in the 7-day window** (155 requests: 75 retain + 76 consolidation/dedup, all already on gemma4-e4b), so there was no live behaviour to regress.
**Changed:**
- `~/.config/systemd/user/hindsight-daemon.service` — `HINDSIGHT_API_REFLECT_LLM_MODEL`: `deepseek-v4-flash` → **`gemma4-e4b`**, plus a comment block recording the full OOM rationale (background op + router autoload + GTT invisible to the OOM killer ⇒ the 2026-08-06 desktop-session kill) and the rollback path. `daemon-reload` + restart. Backup: `hindsight-daemon.service.bak-20260812-pre-reflect-gemma4`.
- Verified **no hidden fallback**: only the three role vars are set, the bank config overrides no models, and `ReflectRequest` has no per-request model field — so this one line is the complete change.
- Docs: this entry + `current.md` (role mapping, gotcha #6, DS4 measured numbers, `Last verified`).
**Expected:** Gotcha #6 CLOSED. A background reflect with nobody at the keyboard can no longer name an unloaded ~98 GiB model and trigger a router autoload on top of the resident set. The llama-watchdog heavy-model mutex becomes defence-in-depth instead of the sole guard. Secondary: reflect gets much faster (unit had documented ~2 min/call on DS4).
**Refs:** r/LocalLLaMA post `1vlmh0b` (DS4 Vulkan + DSpark guide, incl. 2026-08-12 ROCm 7.14 re-test edit). Ecosystem survey of wedge detection: [ROCm #6165](https://github.com/ROCm/ROCm/issues/6165) — **gfx1151 silent hard hang where amdgpu hangcheck NEVER fires and dmesg is entirely silent**; tell is `/sys/kernel/debug/dri/*/amdgpu_fence_info` "last signaled == last emitted" with reset counters at zero. [vLLM #36960](https://github.com/vllm-project/vllm/issues/36960) — proposes a `/health/ready` running a real 1-token dummy forward pass, i.e. our watchdog probe, arrived at independently; still unmerged. [llama.cpp #24810](https://github.com/ggml-org/llama.cpp/issues/24810) — upstream says operators need an external watchdog. Vulkan DeviceLost is endemic: [#19955](https://github.com/ggml-org/llama.cpp/issues/19955), [#22774](https://github.com/ggml-org/llama.cpp/issues/22774), [#20462](https://github.com/ggml-org/llama.cpp/issues/20462), [#21724](https://github.com/ggml-org/llama.cpp/issues/21724).
**Smoke test:** `daemon-reload` + restart → `active running`, **NRestarts=0**, and `/proc/<MainPID>/environ` confirms `REFLECT_LLM_MODEL=gemma4-e4b` on the *running* process (the 2026-08-01 orphan bug was an env-less gateway-spawned competitor, so the pid env is the check that matters). `:9177` owned by MainPID 2524260 — no orphan. `/health` → `{"status":"healthy","database":"connected"}`. **Real `/reflect` call succeeded: 18.7 s wall, 2 LLM calls both `model=gemma4-e4b status=success` (3.6 s @ 2187→60 tok planning; 13.7 s @ 7746→675 tok synthesis), 2799-char grounded, well-structured answer, no thinking leakage.** vs the ~2 min/call the unit documented for DS4. **Zero collateral damage:** DS4 slots 0/1 still hold 53,805 / 55,791 tokens (untouched), residency unchanged (ds4 + gemma4 loaded, qwen evicted), host RAM 7 GiB available. Note the reflect answer surfaced stale *content* (ornith-1.0-35b, "ds4 is the permanent default") — that is the memory bank's historical contents, not a model regression.

---

## 2026-08-10 — Docs correction: extractor retarget was never applied; network exposure recorded; no host firewall
**Observed:** Audit of live state against `current.md` during a memory-note update (no infra change intended). Four discrepancies, all doc-side:
1. **`extractor`/Hindsight-reflect pin.** `current.md` and the runtime `models.ini` header (2026-08-09) both claim `extractor` was retargeted qwen3.6-35b "so reflect stays on the resident heavy model". The live `hindsight-daemon.service` says `HINDSIGHT_API_REFLECT_LLM_MODEL=deepseek-v4-flash`. **The retarget was documented but never applied to the unit.** Gotcha #6 (the ~98 GiB unattended-autoload OOM path that killed llama-server on 2026-08-07 11:01:49) is therefore still OPEN, and the `models.ini` comment asserts a fix that does not exist.
2. **No host firewall.** `/etc/ufw/ufw.conf` → `ENABLED=no`; firewalld + nftables inactive. `llama-router` binds `0.0.0.0:9292` with no auth (`unused-llama-router-direct` is a placeholder), Firecrawl `0.0.0.0:3002`, exporters `0.0.0.0:9610/9611/9100`. Any LAN device gets free inference, host/GPU telemetry, a Firecrawl SSRF pivot, and a one-request 98 GiB memory DoS. `current.md` recorded no bind addresses at all. **Trap:** `systemctl is-active ufw` returns `active` even when disabled (oneshot, `SubState=exited`) — it misled this audit until `ufw.conf` was read. Curling the host's own LAN IP also proves nothing (ufw accepts all on `lo`).
3. **`swap-model.sh` path wrong** in `current.md` (`~/llama-stack/swap-model.sh` — does not exist; it lives at `~/Dev/strix-halo-llm-stack/tools/swap-model.sh`).
4. **The two `models.ini` copies have drifted** (repo template vs runtime). Comment-only as of today, so no functional impact — but editing only the repo copy is a silent no-op.

Also confirmed NOT drift: DS4 loaded / qwen3.6-35b evicted at the time of audit was a **user-initiated `swap-model.sh` load** for active work; the watchdog heavy-model mutex evicted qwen at 14:15:50 exactly as designed. Normal operation, logged here only to stop a future session "fixing" it.
**Changed:** Docs only — no infra touched, by explicit instruction (DS4 was loaded for user work).
- `current.md`: corrected the `extractor` row to `deepseek-v4-flash` + added a source-comparison table explaining which config wins and why; rewrote gotcha #6 as STILL OPEN; added a **Network exposure / bind addresses** section (port/bind/auth table, firewall state, the `is-active` trap, hardening steps incl. the mandatory `ufw allow from 172.16.0.0/12` for container→host traffic and the note that ufw cannot cover docker-published :3002); corrected the `swap-model.sh` path; flagged the dual-`models.ini` drift; added a note that residency inversion after a manual swap is normal and must not be logged as drift. `Last verified` → 2026-08-10 21:05 IST.
- Claude memory: `feedback_update_memory_after_infra_change` rewritten around this SSoT + the Source vs. Runtime split; `pi_models_json_context_sync` corrected (item 1 named the retired `llama-swap.yaml`; now names both `models.ini` copies); `reference_strix_halo_llm_stack_repo` rewritten (repo is now the SOURCE half, not a publishing mirror); `swap-model.sh` path fixed in `llama_server_router_mode` + `MEMORY.md`.
**Expected:** A session reading `current.md` no longer believes the OOM path is mitigated or that the box is firewalled. Two follow-up infra changes are now queued and explicitly NOT done: (a) retarget `HINDSIGHT_API_REFLECT_LLM_MODEL` in `hindsight-daemon.service` + `daemon-reload` + restart; (b) enable ufw + rebind Firecrawl to `127.0.0.1:3002`.
**Refs:** live `systemctl --user cat hindsight-daemon.service`; `curl localhost:9292/models`; `journalctl --user -u llama-watchdog` (14:15:50 HEAVY-MUTEX eviction); `ss -tlnp`; `/etc/ufw/ufw.conf`; `docker network inspect` (172.17/172.18/172.23).
**Smoke test:** None required — documentation-only change, no service touched, nothing restarted. Verified `llama-router.service`, `llama-watchdog.service`, `hindsight-daemon.service`, `hermes-gateway.service`, `hermes-dashboard.service` all still `active running` and DS4 still loaded after the edits.

---

## 2026-08-10 — Repo consolidation: SSoT moved to strix-halo-llm-stack; Source vs. Runtime Data split documented
**Observed:** The infra SSoT was built in `~/llama-stack` — a local-only repo with
no remote. The GitHub-tracked repo is `~/Dev/strix-halo-llm-stack`
(`github.com/dinesh-se/strix-halo-llm-stack.git`). One agent nearly deleted
`~/llama-stack` entirely, which would have broken `llama-router.service`.
**Changed:**
- Moved `docs/infra/` (current.md, changelog.md, README.md) from `~/llama-stack` → `~/Dev/strix-halo-llm-stack/docs/infra/`. Canonical path is now `~/Dev/strix-halo-llm-stack/docs/infra/`.
- Archived one-off bench experiment harnesses + raw results to `bench/archive/` (decisions already captured in this changelog; canonical `mesa_baseline.py`/`gguf-vram-estimator.py`/`swap-model.sh` kept live in `tools/` + `bench/`).
- Documented the **Source vs. Runtime Data split** in README.md + current.md + ai-infra-state skill: repo = code/config/docs/units; `~/llama-stack` = runtime weights + mounted `config/models.ini` (NEVER delete).
- Updated Hermes MEMORY.md + ai-infra-state skill pointers to the new path.
**Expected:** Both agents treat `~/Dev/strix-halo-llm-stack/docs/infra/` as the SSoT and never touch `~/llama-stack` runtime data.
**Refs:** commit `1d8844c` (initial move+archive+push), this consolidation.
**Smoke test:** `git -C ~/Dev/strix-halo-llm-stack status` clean; pushed to `origin/master`; `llama-router.service` mounts still resolve `~/llama-stack` paths.

---

## 2026-08-10 — Unified infra SSoT established at ~/llama-stack/docs/infra/; file relocated from ~/docs/ai-infra
**Observed:** Infra was tracked two ways — Claude kept this append-only history at `~/AI-INFRA-HISTORY.md`; Hermes relied on fragmented stores (MEMORY.md, Hindsight, state.db) with no pointer to this log. Meanwhile `~/docs/local-ai-stack.md` was stale (described removed granite-4.1-8b, LiteLLM, 96 GiB carveout, llama-swap). No single file both agents read.
**Changed:** Created canonical store `~/llama-stack/docs/infra/` (git-tracked in the llama-stack repo): `current.md` (living snapshot), `changelog.md` (this file, moved from `~/.AI-INFRA-HISTORY.md`), `README.md` (protocol). Left pointer stub at `~/.AI-INFRA-HISTORY.md`. Marked `~/.docs/local-ai-stack.md` SUPERSEDED. Created Hermes skill `ai-infra-state` + MEMORY.md pointer. `current.md` written from live system state (router `llama-router.service` on :9292, native llama.cpp server; llama-swap retired).
**Expected:** Both Claude Code and Hermes read `current.md` before any infra change and update `current.md` + `changelog.md` after, keeping a single source of truth.
**Refs:** Claude's `AI-INFRA-HISTORY.md`; `~/llama-stack/config/models.ini`; `~/.config/systemd/user/llama-router.service`; `~/.hermes/config.yaml`.
**Smoke test:** Files present at new path; pointer stub resolves; git repo initialized in llama-stack; skill loads; MEMORY.md updated. Pending: first Claude/Hermes cross-agent read.

---

## 2026-08-06 (midday, 2nd) — DS4 dspark sidecar REMOVED; real OOM cause found to be Hermes aux roles pulling a 2nd model in beside DS4
**Observed:** After the `--oom-score-adj=1000` fix, DS4 was OOM-killed AGAIN at 10:16:05 (`status=137`), this time cleanly — **only `llama-server` died, the desktop survived, and `Restart=on-failure` recovered it at 10:16:36 (`NRestarts=1`)**. The user-visible symptom degraded from "logged off" to a single `InternalServerError 503`, confirming the oom-score fix does what it was meant to. Digging into *why* the memory ran out: `docker logs llama-swap -t` (UTC; local is UTC+1) showed **`gemma4-12b` being loaded by a `"OpenAI/Python 2.24.0"` client at the exact timestamp of BOTH OOM kills** — 09:52:25 (200, 24.4 s) and 10:16:07-08 (health check + 200, 21.7 s). `hindsight-daemon` was already stopped (10:07:31) at the second one, so it was not the caller; `ss -tnp` showed **`hermes` (pid 98315) holding a live ESTABLISHED socket to :9292**. Root cause: the DS4 session wiring repointed `model.default` and disabled the delegation toolset, but left **four auxiliary roles on llama-swap** — `auxiliary.compression`, `auxiliary.title_generation`, `background_review` (all `gemma4-12b`, ~13 GiB) and **`curator` (`qwen3.6-35b`, ~38.7 GiB)**. So every Hermes turn silently co-loaded a second model beside a service that cannot co-reside with anything. `title_generation` firing on turn 1 is exactly why "first message worked, second returned 503".
**Changed:** (1) `~/.config/systemd/user/ds4-server.service` — **removed the dspark sidecar** (`-md …dspark-…Q8_0.gguf`, `--spec-type draft-dspark`, `--spec-draft-n-max 2`) plus a comment block recording why and how to restore it. Also unloaded the stray `gemma4-12b` via `POST /api/models/unload/gemma4-12b`. (2) `~/.hermes/config.yaml` — **all four aux roles disabled for the DS4 session** (backup `config.yaml.bak-20260806-pre-auxfix`): `compression.enabled: false`, `curator.enabled: false`, `auxiliary.title_generation.enabled: false` (key added; default is `true`), and for `background_review` — which has **no `enabled` key of its own** — both halves of its spawn condition at `agent/turn_finalizer.py:718` (`_should_review_memory or _should_review_skills`): `memory.memory_enabled: false` + `memory.user_profile_enabled: false`, and `skills.creation_nudge_interval: 0` (gates `_skill_nudge_interval > 0`, `agent/agent_init.py:1772`, default 10). Disabling memory costs nothing extra during a DS4 session because the protocol already stops `hindsight-daemon` and `memory.provider` is `hindsight`. Gateway restarted.
**Expected:** Roughly double the host-RAM headroom, at a known decode cost, AND no path left by which a Hermes turn can co-load a second model beside DS4.
**Refs:** `docker logs llama-swap -t`; `ss -tnp | grep 9292`; `~/.hermes/config.yaml` `auxiliary.*` / `curator` / `memory` / `skills`; `agent/turn_finalizer.py:718`; `agent/agent_init.py:1772`; `hermes_cli/config_defaults.py:919,1040`.
**Smoke test:** MEASURED after restart — **GTT 98.3 GiB (was 108.1, −9.8), available host RAM 19 GiB (was 9, 2.1×), cold load ~70 s (was ~200 s, 2.9× faster), decode 18.99 t/s (was 24.55, −23%)**, `n_ctx=131072` intact, chat completion clean. 18.99 matches the ~18.8 t/s bare-decode prediction. Sidecar acceptance on the real 23k-token workload had been 0.583 — under the 0.70 floor — so the 10 GiB was buying very little. After the Hermes edits: `yaml.safe_load` parses clean, all six toggles read back correct, gateway restarted active, **`ss -tnp | grep 9292` returns nothing and `/running` is empty** — the co-load path is closed. Final state 10:32: ds4-server/hermes-gateway/llama-watchdog active, hindsight-daemon inactive (intentional), `/health` 200, GTT 98.4 GiB, 18 GiB free, `oom_score_adj=1000` on pid 112054, `NRestarts=0`.

---

## 2026-08-06 (midday) — Global OOM wiped the GNOME session under DS4; `--oom-score-adj=1000` added to ds4-server
**Observed:** Hermes threw `APIConnectionError` against `:10097`. Two distinct causes, one per incident. (1) **09:18:12 — a clean, deliberate `systemd-reboot`** (confirmed via `journalctl --list-boots`: boot `-1` ended 09:18:12, boot `0` began 09:21:01; shutdown log is orderly, no OOM, no panic) killed the first DS4 run; the unit is `disabled`, so it stayed down and Hermes kept pointing at a dead port. (2) After restarting DS4 at 09:31 it served a 23,412-token Hermes turn at 09:50 (prefill 110.9 s @ 211 t/s, decode 14.27 t/s, **draft acceptance 0.583 — under the 0.70 floor**), then at **09:52:25 the kernel OOM killer fired a global sweep** and destroyed essentially the whole user session: `pipewire`, `wireplumber`, `gnome-session-monitor`, all four `xdg-desktop-portal*`, `ssh-agent`, `gcr-ssh-agent`, `gvfs-daemon`, `evolution-*`, `localsearch`, `ibus`, the **wezterm scope**, and **the user systemd manager itself (pid 2958)** — then `victoria-metrics`, `python3` and finally `llama-server`. Presents to the user as "I got logged off." **Root cause: DS4's 109 GiB lives in amdgpu GTT and is invisible to the OOM killer** — the kernel task dump totalled **1.58 GiB RSS across 198 processes** (largest single entry a `node` at 0.18 GiB), so the killer found nothing worth reaping and ate the desktop instead. `Free swap` was already down to 4.4 GiB of 7.3 GiB. This is the documented ~8.8 GiB host-RAM floor at ~109 GiB GTT being met head-on. `Restart=on-failure` never fired because the manager that would have run it was killed in the same sweep.
**Changed:** `~/.config/systemd/user/ds4-server.service` — added **`--oom-score-adj=1000`** to the `docker run` argument list (plus a comment block recording the incident). **Must be the docker flag, not systemd's `OOMScoreAdjust=`**: ExecStart is the docker *client*, so a unit-level setting never reaches the containerized `llama-server` in its own cgroup. `daemon-reload` + restart. Also restarted `llama-watchdog` (OOM-killed at 09:52 and did not come back) and re-stopped `hindsight-daemon`.
**Expected:** OOM pressure kills DS4 alone instead of the desktop session, and because the user systemd manager survives, `Restart=on-failure` can actually recover. Does not add memory headroom — it converts an unsurvivable session-wide wipeout into a clean single-unit failure.
**Refs:** `journalctl -k -S 09:45` OOM task dump; `journalctl --list-boots`; `watchdog.py:28` (probe-only-what-is-in-`/running` TTL safety rule).
**Smoke test:** `docker inspect` → `OomScoreAdj=1000`; live process `pid 88842 /usr/bin/llama-server` → `oom_score_adj=1000`, `oom_score=1642`, ranked **#1 system-wide by more than 2× over the next candidate at 800** (verified by sorting `/proc/*/oom_score`). DS4 `/health` 200 after ~200 s, `/props` `n_ctx=131072`, GTT 109.0 GiB, chat completion returned `content: 'DS4 online'`, `finish_reason: stop`. **Gotcha found: `pgrep -f llama-server` matches the `docker run` client first (adj=200, inherited from the session) — check the pid whose cmdline is `/usr/bin/llama-server`, not the docker one.**

---

## 2026-08-06 (morning) — DS4 wired in as a STANDALONE server, not via llama-swap; Hermes repointed at it; llama-swap groups restructured
**Observed:** After the overnight GTT flip made DS4 viable (23–26 t/s), the goal was to wire it into llama-swap as an opt-in model and point Hermes/pi at it. **It does not fit in llama-swap today.** Two findings, both measured:
1. **`persistent: true` BEATS `exclusive: true`** in llama-swap groups. Verified with an 8.7 GiB stand-in rather than a 110 GiB one: with `ondemand.exclusive=true`, loading gemma4 did **not** evict the persistent 27b — `/running` showed both. Had DS4 been added as an exclusive group while `resident` stayed persistent, DS4 (~110 GiB) would have loaded **alongside** the 27b (31.5 GiB) = ~141 GiB against a 124 GiB pool — the 2026-05-22 thrash shape. The docs do not state this precedence; it was determined empirically.
2. **No llama-swap image can run DS4 well.** DS4 needs the DeepSeek-V4 kernels (Lightning Indexer + fused HC pre/comb/post) that landed **between b10257 and b10283**. Measured: **b10200** (our pin) — bare 12.39 t/s, sidecar **hard-fails** at load (`key not found in model: dflash.attention.sliding_window_pattern`); **b10257** (newest llama-swap image that exists) — sidecar loads, acceptance 0.759, but decodes **9.00 t/s**, *worse than b10200 bare*. Probed `v247/v248/v249/v250-vulkan-b<N>` across the b10258–b10320 window: **nothing exists**. b10257 is the ceiling.
**Changed:**
- `~/llama-stack/config/llama-swap.yaml` (backup `llama-swap.yaml.bak-20260806-pre-ds4-entry`): merged `resident` + `ondemand` into a single **`production`** group (`swap: false`, `exclusive: true`, **`persistent` REMOVED**). Production behaviour is unchanged — the three still co-reside, and per-model `ttl` (27b 0, 35b 1800, gemma4 600) still governs idle eviction, which is what actually enforced residency. The DS4 model entries and the paired `deepseek` group were added, tested, and then **REVERTED**; a large comment block now records the build gate so this is not retried blindly.
- **New `~/.config/systemd/user/ds4-server.service`** — DS4 standalone on **:10097**, image `kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance` (**build 10283**), IQ3_XXS + dspark sidecar, `-c 65536 -ub 1024 --parallel 1 -cram 0 --spec-draft-n-max 2`. Deliberately **left `disabled`** (not enabled at boot): it grabs 109.8 GiB and would fight the lineup on a reboot. ⚠️ User units cannot `Requires=docker.service` (system unit — start fails with "Unit docker.service not found"); both directives removed.
- `~/.hermes/config.yaml` (backup `config.yaml.bak-20260806-pre-ds4`): new `custom_providers` entry **DS4** → `http://127.0.0.1:10097/v1` (separate port; DS4 is NOT behind llama-swap), `model.default` → `deepseek-v4-flash` / `custom:ds4`, and **`agent.disabled_toolsets: [delegation]`** — `delegate_task` takes no model param and would spawn the 27b alongside DS4 (~141 GiB).
- `~/.pi/agent/models.json` (backup `models.json.bak-20260806-pre-ds4`): added a `ds4` provider at :10097.
- **`~/Dev/pi-kalam/src/config.ts` deliberately NOT changed** — its roles (design/decomposition/coder/reviewer/checker/triage) are pinned to 27b/35b, and the file's own comment requires a role's model to differ from the coder's. Collapsing all roles onto one 110 GiB model defeats the design and would run brownfield work at 205 t/s prefill.
- `hindsight-daemon` **stopped** for the session: its `memory-writer` alias → gemma4 and its `extractor` alias → **qwen3.6-35b (38.7 GiB)**, either of which would evict DS4 on a schedule. `llama-watchdog` left **stopped/disabled** — with the lineup unloaded it has nothing to probe, and it would alert on `hindsight_up 0`. No user crontab exists, so the email digest needed no action.
**Expected:** DS4 usable all day at full speed as Hermes' model, with zero risk to the production lineup (which is simply unloaded).
**Refs:** llama-swap wiki Configuration (groups: swap/exclusive/persistent — precedence unspecified, hence the experiment); ghcr tag probes.
**Smoke test:** llama-swap recreated and verified **by inode** (host 12744021 == container 12744021) plus `/v1/models` showing exactly the 3 production models and **0 deepseek refs** in the container's config. DS4 standalone: `--list-devices` → `Vulkan0: Radeon 8060S Graphics (RADV STRIX_HALO) (127488 MiB)` (no silent CPU fallback), build **10283**, **zero** `Lightning Indexer ... disabled` lines, loaded in ~3.7 min to **108.6 GiB**, **decode 24.69 t/s**, MemAvailable 10 GiB. Hermes gateway restarted `active` with `default: deepseek-v4-flash`, `provider: custom:ds4`, `disabled_toolsets: ['delegation']`; `:10097/v1/models` returns `deepseek-v4-flash`.
⚠️ **Draft acceptance is 0.657 at 64k** — *below* the 0.70 investigate-floor (it was 0.786 at 16k; longer context degrades draft quality). Left as-is because the sidecar is still clearly paying (24.69 vs ~18.8 bare), but this is the number to watch if DS4 speed regresses.
⚠️ **No eviction coordination exists between ds4-server and llama-swap.** Starting DS4 while the lineup is loaded WILL overcommit. Always `curl :9292/unload` first, and stop `hindsight-daemon`.
**→ 128k SAME DAY:** `-c 65536` → **131072** (backup `ds4-server.service.bak-64k`), and `context_length`/`contextWindow` synced to 131072 in `~/.hermes/config.yaml` + `~/.pi/agent/models.json`. **Cost: nothing.** GTT 108.6 → **109.3 GiB**, decode 24.69 → **24.55 t/s**, MemAvailable 9 GiB, `/props` confirms `n_ctx=131072`. **DS4's KV is ~4.5 MiB per 1k tokens** (MEASURED: 16k bare 97.9 GiB vs 128k bare 98.4 GiB) — roughly 10x cheaper than an earlier extrapolation of ~53 MiB/1k that had wrongly implied 128k and the dspark sidecar were mutually exclusive. **They are not — you get both.** Draft acceptance unchanged at 0.657.

**Revert:** `systemctl --user stop ds4-server`; restore `~/.hermes/config.yaml` and `~/.pi/agent/models.json` from their `.bak-20260806-pre-ds4` copies; `systemctl --user restart hermes-gateway`; `systemctl --user start hindsight-daemon`; `systemctl --user enable --now llama-watchdog`; re-warm the lineup. The llama-swap group restructure can stay — it is correct and is a prerequisite for wiring DS4 in properly once a b10283+ image ships.

---

## 2026-08-06 (overnight) — GTT memory flip: BIOS carveout 96 GiB → 512 MB, `nogttspill` removed. Production got FASTER; DS4 Flash at IQ3_XXS is now viable at 26.33 t/s
**Observed:** The 2026-08-04 "DS4 is not a daily driver" verdict rested on a false premise — that the 96 GiB VRAM carveout was a hard ceiling, which made `UD-IQ3_XXS` (97.05 GiB) physically impossible. **96 GiB is a *Windows* limit.** On Linux the iGPU reaches ~124 GiB by *minimising* the BIOS UMA carveout, because GTT draws from system RAM. Corroborated by r/StrixHalo `1vec4gy` + `strix-halo-toolboxes.com`, and by this box already reporting `126976 MiB` addressable through ROCm — the address space was always right; only the physical backing was wrong.
**Changed:** User set the BIOS UMA frame buffer to its **512 MB minimum** and rebooted (only manual step; no GRUB edit — `amdgpu.gttsize` judged redundant since GTT already reported the full pool). `ttm.pages_limit=32505856`, `amd_iommu=off`, `amdgpu.dcdebugmask=0x12`, `amdgpu.lockup_timeout=10000,60000,10000,10000` all untouched. Then: **`RADV_PERFTEST=nogttspill` removed** from `~/llama-stack/docker-compose.yml` (backup `docker-compose.yml.bak-20260805-pre-gtt-flip`) and from **4** bench harnesses (`radv_perf_ab.py`, `rollback_ab.py`, `gemma4_prefill_hunt.py`, `server_config_ab.py`) — **5 sites, not the 2 expected**. The flag forbids RADV placing buffers in GTT when VRAM is exhausted: correct under a 96 GiB carveout (a spill hit the ~30 GiB left to the OS = the 2026-05-22 thrash), but with a 512 MB carveout GTT is not the overflow path, **it is the memory model**, so the flag blocked everything. New harnesses: `bench/ds4_arm_ab.py`, `bench/ds4_spec_test.py`, `bench/ds4_quality.py`. Full report: **`~/llama-stack/bench/deepseek-overnight-20260806.md`**.
**Expected:** Enough memory to run DS4 above 96 GiB, without regressing the daily lineup (explicit abort gate: any model >10% below baseline → revert and stop).
**Refs:** r/StrixHalo `1vec4gy`; strix-halo-toolboxes.com; unsloth/DeepSeek-V4-Flash-0731-GGUF; kyuz0 `docs/toolbox-performance-results.json`.
**Smoke test:** **Production gate PASSED with room** (`bench/gttflip-b10200-np1.md` vs `baseline-b10200-np1.md`, same `mesa_baseline.py` method): 35b **831.05 → 1135.90 PP** (+36.7%) / 92.60 → 91.40 TG (−1.3%); 27b **213.17 → 253.21** (+18.8%) / 23.72 → 23.58 (−0.6%); gemma4 **369.22 → 716.77** (+94.1%) / 90.71 → **93.75** (+3.4%). MTP acceptance 1.000/1.000/0.971. All three co-resident in 69.6 GiB of GTT. Worst delta −1.3%. Post-run restore verified: all 3 re-warmed at normal GPU cold-load times (26/39/10 s — no silent CPU fallback), correct answers, TTLs correct (27b `ttl=0`, 35b 1800, gemma4 600), 80.7/124 GiB co-resident, `llama-watchdog` re-enabled with `models_loaded 3`, `probe_success=1` ×3, `device_lost_total 0`, `hindsight_up 1`. **Zero incidents all night** — no ring timeouts, no device-lost, no OOM, no GPU reset; peak GTT 108.9/124, the 118 GiB guard never tripped.
⚠️ **Attribution caveat:** the 08-01 baseline predates the 08-04 `-ub 256`/`-sps 0.5` changes, the 08-05 residency swap, and kernel 7.0.0-28 → 7.0.0-29. **gemma4's +94% prefill is the largest unexplained number and sits on top of the parked "gemma4 prefill hunt" — NOT proven to be GTT.**
**DS4 results (radv arm, build 10283):** IQ2_XXS **142.06/13.21 (08-04) → 218.31/20.39** tonight. IQ3_XXS 205.62/19.03 (`-ub 512`), 205.87/**19.34** (1024), 205.15/19.32 (2048) — **`-ub` is a non-lever for DS4** (0.4% PP / 1.6% TG spread), *scoped to depth 0*. **IQ3_XXS + `--spec-type draft-dspark` sidecar: 26.33 t/s, draft acceptance 0.786, peak 108.9 GiB** — vs 18.81 for `--spec-type none` on an identical server config, i.e. **+40.0%**. That is **faster than the resident 27b coder (23.58)** and 2× the number that shelved DS4. Flags on this build: sidecar `-md`; `--spec-type` ∈ {none, draft-simple, draft-eagle3, draft-mtp, draft-dflash, draft-dspark, ngram-*}; depth knob `--spec-draft-n-max` (`--draft`/`--draft-n`/`--draft-max` removed). Sidecar is **10.15 GiB** (root `dspark-…-Q8_0.gguf`; the `dspark/` folder holds only BF16). Host RAM floor 8.8 GiB at 16k ctx is the binding constraint, not GTT.
**Quality IQ3 vs IQ2 (the question 08-04 left open): NULL RESULT.** Identical bodies, temp 0, 2048 cap, no sidecar. Coding task (`merge_intervals`) — both answers extracted and **executed against 8 edge cases: 8/8 both**. Reasoning task **inconclusive**: both consumed the whole budget inside the reasoning channel and emitted zero content (`finish_reason: length`). **Bit-depth question narrowed, not settled.**
⚠️ **ROCm arm HUNG and was dropped.** `rocm-7.14_20260805T174643` (build 10288) on IQ2_XXS allocated all 84.8 GiB, printed `found 1 ROCm devices (Total VRAM: 126976 MiB) … gfx1151`, then one thread sat in `R` at ~93% CPU with **0% GPU and zero disk I/O for 17 min** (`read_bytes` delta 0/20 s). Killed (rc 137), memory released cleanly, not retried. **Cause unknown.** Decision value was already gone: radv's 20.39 beats the 16.22 ROCm figure that motivated the arm, so the one documented radv-loses-on-DS4 exception is **retired**.
⚠️ **THREE NEW TRAPS.** (1) **`pgrep -f 'hf download…'` self-matches the waiting shell** — two wait-loops matched their own command lines and blocked forever; ~40 min lost, sidecar retry never fired. Same class as the documented `pkill -f` trap. Don't poll by command-line pattern. (2) **`du` on `blobs/` is NOT a progress signal for xet downloads** — chunks stage elsewhere, `blobs/`+`*.incomplete` look idle while GBs land; use `write_bytes` from `/proc/<pid>/io`. Real rate was **~38 MB/s (~25 min for 97 GiB)**, not the 6.2 MB/s / 4 h 27 m assumed. (3) **`hf download --cache-dir hf-cache` writes to `hf-cache/models--…` while the older IQ2 cache is at `hf-cache/hub/models--…`** — both layouts now coexist under the same bind mount, and **the IQ3 in-container path has no `hub/` segment**. Also: `ds4_arm_ab.py`'s parser matched llama-bench's pretty-printed JSON *array* with a lookahead the inter-element comma defeats — a fully successful run reported `pp: null` at `returncode 0`. **rc=0 + empty results ⇒ suspect the parser, not the benchmark.**
**Rollback (needs BIOS + reboot, user-only):** UMA back to 96 GiB, restore `nogttspill` in compose + the 4 harnesses. `ttm.pages_limit` was never touched.

---

## 2026-08-05 — Residency swapped: 27b coder is now the resident model, 35b PA/orchestrator demoted to on-demand
**Observed:** User wants a complete daily-driver feel for the 27b coder, which `ttl: 1800` cannot give — every idle gap >30 min paid a cold load, and the eviction race documented in the 2026-07-08 entry surfaced in pi as "Request timed out". Before changing anything, re-checked the two standing objections to pinning the 27b (from `oom-thrash-incident-2026-05-22`), and both turned out not to bind:
1. **Peak VRAM is unaffected by ttl.** Both groups are `swap: false` and llama-swap has no VRAM-aware eviction, so all three models co-reside at peak regardless of who is "resident". ttl only decides who is loaded when *idle* — residency raises the floor, not the ceiling.
2. **The incident's actual mechanism was `--no-mmap`**, which forces weights into unevictable anonymous RAM behind the 96 GiB carveout that leaves the OS ~30 GiB. The 27b has not carried that flag since 2026-07-12 (I5) and did not get it now. `-cram 0` (2026-07-20) removed the other host-RAM leak.
Also verified neither TTL-defeating mechanism applies to the newly-evictable 35b: VictoriaMetrics scrapes `llama-watchdog:9611`, not llama-swap's proxy, and the watchdog probes upstream ports **direct** (`http://{host}:{port}/completion`, watchdog.py:192) — neither path touches the idle timer.
**Changed:** `~/llama-stack/config/llama-swap.yaml` (backup `llama-swap.yaml.bak-20260805-pre-residency-swap`):
- `qwen3.6-27b`: `ttl: 1800 → 0`, moved into the `resident` group (`persistent: true`).
- `qwen3.6-35b`: `ttl: 0 → 1800`, moved into the `ondemand` group, gained `timeouts.connect: 60`.
- **`--no-mmap` REMOVED from the 35b.** It was justified only by "loads once at boot, never reloads". As an on-demand model it is now the largest repeatedly-loading model in the lineup (38.7 GiB), and repeated `--no-mmap` loads are exactly the 2026-05-22 shape. This brings it in line with the other two on-demand models. **This is the one change that was not a pure position swap** — flagged to the user as such.
- Groups keep `swap: false` on both sides; `swap: true` between 35b and 27b must never be used (Hermes parent/child alternation would force a 38.7 GiB reload per hand-off).
**Expected:** Coder stays hot indefinitely; 35b's many callers (Hermes main chat + delegation orchestrator, `classifier` → email digest cron, `extractor` → Hindsight reflect) each pay one ~38.7 GiB cold load after a >30 min idle gap, then run warm. Host RAM should *improve*, since 38.7 GiB of 35b weights move from anonymous to file-backed/reclaimable.
**Refs:** none external — decided against `oom-thrash-incident-2026-05-22`, `host-ram-oom-kills-2026-07-19`, and the measured 2026-07-31 VRAM figures.
**Smoke test:** `docker compose restart`; config re-read confirmed by matching inodes (host 12744021 == container 12744021, 34273 B — the single-file bind-mount inode trap from 2026-08-04 did NOT bite here) and by `/running` showing the new values, not container health. 27b cold-loaded on first request: **25.8 t/s decode** (above the 22.93 t/s baseline of 2026-07-31, and far above the ~18 t/s silent-CPU-fallback tell) — GPU detection fine. 35b cold-loaded on demand without `--no-mmap`: **76.2 t/s decode**, correct output. `/running` after both: `qwen3.6-27b ttl=0` + `qwen3.6-35b ttl=1800`, 35b cmd confirmed free of `--no-mmap`. VRAM 73.9 of 96 GiB with both loaded (gemma adds ~8.7 → ~82.6 peak, consistent with the measured 79.7). Host RAM improved as predicted: used 9 → 6 GiB, buff/cache 18 → 23, available 20 → 23 GiB. llama-watchdog reports `probe_success=1` on all three models and `hindsight_up=1`.
**Note:** `persistent: true` still does NOT auto-load (confirmed again — `/running` was empty immediately after restart until the first request). The warm-up loop in `llama-swap-stack` still applies after every restart; only the model name to warm first has changed.

---

## 2026-08-04 (night) — DeepSeek V4 Flash 0731 evaluated on this box: it runs, but is not viable as a daily driver
**Observed:** User asked to measure DeepSeek V4 Flash 0731 at IQ3_XXS with all models evicted, and suggested the "antirez build". Two blockers found before any download: (1) **UD-IQ3_XXS is 97.05 GiB against a 96 GiB VRAM carveout** — weights alone exceed it, before KV, so it would spill into GTT backed by only ~30 GiB of system RAM, i.e. the documented 4-hour OOM-thrash freeze mode. Every quant from UD-IQ3_XXS upward is over the line (IQ3_S 108.1, Q3_K_XL 119.4, IQ4_XS 127.3, Q4_K_XL 144.4, Q8_K_XL 150.8). (2) The **antirez build is not llama.cpp** — `schlaflos/DeepSeek-V4-Flash-0731-antirez-ds4-GGUF` targets antirez's separate `ds4` engine ("These quants target the DS4 inference engine, not llama.cpp... Compatibility with llama.cpp, Ollama or LM Studio is untested"), uses DS4's `deepseek4` tensor layout, is tagged apple-silicon/metal, was still uploading, and at 90.89 + 5.58 GiB would overflow the carveout anyway. Substituted **UD-IQ2_XXS (84.6 GiB)**, which is also the quant kyuz0 publishes numbers for, giving a direct box-to-box comparison.
**Changed:** Nothing persistent. Test was run in a throwaway container on image **v247-vulkan-b10257** (not our pinned b10200) because the DSv4 work — #25871 K/V cache type enforcement, **#26474 allocate indexer cache only in "full" indexer layers** (a memory saving that matters at 84.6/96 GiB), #25784 DeepseekV4 MTP + DSpark — all landed in the b10200→b10257 window. llama-watchdog stopped and all three models unloaded for the duration; both restored afterwards. A guard script was armed to kill the bench if GTT exceeded 8 GiB.
**Expected:** A yes/no on whether DeepSeek V4 Flash is usable here, and a validation of this box against community numbers.
**Refs:** unsloth/DeepSeek-V4-Flash-0731-GGUF; kyuz0 `docs/toolbox-performance-results.json`; antirez ds4 https://github.com/antirez/ds4.
**Smoke test:** Loaded clean — **VRAM 87.0 GiB, GTT flat at 132 MiB, no spill, guard never tripped**, container exited cleanly. Measured on 284B params / 84.6 GiB / 2.06 bpw: **prefill (p=512, ub=512) 142.06 t/s; decode (n=64) 13.21 t/s**. Against kyuz0's published IQ2_XXS figures our decode is within **1%** of their `vulkan-radv-performance` (13.08) — this box performs exactly as the community reference predicts. **Their rocm-7.14 does 16.22 t/s decode, ~24% faster than Vulkan — architecture-specific to DSv4 and NOT true of Qwen3.6, where the backends tie within 2%.** Post-test restore verified: all 3 models ready with the new flags, all 3 aliases routing, VRAM 80.7/96 GiB, watchdog `models_loaded 3` / `device_lost_total 0` / `hindsight_up 1`.
**Verdict — not a daily driver here:** 13.2 t/s decode against 92.6 (35b) and 23.7 (27b); it occupies 87 GiB so it cannot co-reside with *any* other model (loading it means evicting the entire lineup); and IQ2_XXS is a 2.06 bpw quant of a 284B model, so quality is a real question at that bit depth. Keep as an occasional deep-reasoning option at best. If it ever becomes a daily driver, that is the one case where moving to a ROCm toolbox would genuinely pay (~24%).

## 2026-08-04 (night) — Perf/stability investigation: NO build regression (rollback rejected); gemma4 `-sps 0.5` and 27b `-ub 256` applied; single-file bind-mount inode trap found
**Observed:** User reported responsiveness "went for a toss" plus device-lost issues since the 2026-08-01 (Friday) upgrades, and asked whether to move to kyuz0/Donato's toolboxes for a more community-tested stack. Four things were measured rather than assumed:
1. **The b9853 → b10200 bump is NOT a regression.** Kernel-level A/B (`llama bench`, throwaway containers, live stack untouched), 5 points across gemma4 and 27b: max delta **+0.7%**, and the tightest pair (gemma4 ub=512 d=8192, ±0.5 stddev) was 541.60 vs 540.87. **Rolling back would buy nothing** — b10121 pull was cancelled as redundant.
2. **The yaml's `#25240` story for gemma4 was wrong.** Recorded as "-ub 512 costs -45%"; measured at kernel level it costs **-6%** (ub sweep: 256/512/1024/2048 → 490.5/491.8/523.1/518.0 t/s depth-averaged). So whatever produced the historical 677→369 reading lives in the **server path**, not compute.
3. **27b `-ub 1024` was the worst setting available.** Depth sweep is monotonic — smaller is better at every depth: avg 233.9 (ub256) / 229.7 (512) / 202.7 (1024) / 192.7 (2048); at d=32768 it is 168.6 vs 117.7, i.e. **-30%**. kyuz0 independently calibrated Qwen3.6-27B to ub=256 on Vulkan from a different build.
4. **gemma4 was still on llama.cpp's default `-sps 0.10`.** Live-log analysis of 297 slot selections found **15 (5.1%)** in the 0.10–0.49 band (min 0.263) — low-similarity prompts accepted onto the slot holding a long prefix, destroying it. Same mechanism as the 35b's ~45× re-prefill. 27b was checked identically and does **not** need it (1 of 108) — it receives one serial conversation stream, not interleaved short/long traffic.
**Changed:** `config/llama-swap.yaml` — gemma4-12b gained `-sps 0.5`; qwen3.6-27b `-ub 1024 → 256`. Both cmd lines plus large corrective comment blocks (the wrong #25240 explanation is now explicitly marked as refuted in-file, with the measurements, so it does not get re-derived). Backup: `config/llama-swap.yaml.bak-20260804-pre-sps-ub`. New bench harnesses committed: `bench/ubatch_curve.py` (depth-aware ubatch sweep, kyuz0 methodology, run against OUR image), `bench/rollback_ab.py` (cross-image kernel A/B), `bench/server_config_ab.py` (server-level config A/B measuring prefill amplification). Results in `bench/ubatch-*.txt`, `bench/rollback-ab.txt`, `bench/server-ab.txt`.
**Expected:** ~42% less wall time on gemma4's aux/Hindsight traffic and ~12% on 27b coder turns, with the 27b change also cutting GPU dispatch length — the direction the 2026-07-21 ring-timeout mitigation wanted, so faster and safer at once. No runtime/image change, so no new instability surface.
**Refs:** kyuz0 published benchmarks `docs/toolbox-performance-results.json` (ROCm 7.14 vs vulkan-radv-performance on our exact models — decode differs by **<2%**, so backend choice is not a throughput lever); llama.cpp b10200…b10257 compare (57 commits, essentially all SYCL/CUDA/Metal/OpenCL — the only items touching us are #26252 qwen3 chat parser and #26320 draft-replay stats, plus DSpark #25784/#26452/#26458 which b10200 lacks); llama.cpp #25240.
**Smoke test:** Server-level A/B on a real llama-server with the live flags and identical mixed short/long traffic — gemma4 `-sps 0.10`: 42,744 of 127,890 submitted tokens prefilled (0.33×) in 98.9s vs `-sps 0.5`: 21,460 (0.17×) in **57.6s**; 27b `-ub 1024`: 231.6s vs `-ub 256`: **203.7s**. Post-apply: all 3 models load and answer ("OK"), all 3 aliases (`classifier`/`extractor`/`memory-writer`) route correctly, `/running` confirms the new flags are live on the actual processes, VRAM 80.7/96 GiB with all three co-resident, watchdog `models_loaded 3`, `device_lost_total 0`, `recoveries_total 0`.
**⚠️ LANDMINE FOUND — single-file bind mount is pinned to an INODE.** `config/llama-swap.yaml` is bind-mounted as a *file*. Any editor/tool that writes via temp-file-and-rename (most of them, including Claude's Edit tool) creates a **new inode**, and the container keeps reading the **old** one — silently. First apply looked successful (llama-swap healthy, models reloaded, `/v1/models` fine) but `/running` showed the OLD flags, and `stat` confirmed host inode 12744016 / 30319 bytes vs container inode 12743860 / 27371 bytes dated two days earlier. `-watch-config` cannot help — it never sees the write. **Always `docker compose up -d --force-recreate` after editing this file (a plain hot-reload is not enough), and always verify via `/running`, not via container health or `/v1/models`.**

## 2026-08-04 (later evening) — `hermes update` failure root-caused: npm 11.13.0 in Hermes' blacklisted engine range; npm → 12.0.2, update completed manually
**Observed:** `hermes update` at 19:27 ended "partially complete — Node.js dependencies for repo root did not refresh" (dashboard restart skipped too). NOT the npm-audit vulnerabilities (user's guess): the real error, reproduced with a manual `npm install`, was `EBADENGINE` — hermes-agent requires `npm <11.10.0 || >=11.17.0` and the fnm-bundled npm was **11.13.0**, inside the excluded range (node v24.16.0 itself was fine). Two follow-up traps: re-running `hermes update` (also with `--force --yes`) short-circuits at git "Already up to date!" and never reaches the dep-refresh step, so the updater cannot heal its own partial state.
**Changed:** upgraded npm **11.13.0 → 12.0.2** *inside the fnm node installation* (`npm install -g npm@latest --prefix ~/.local/share/fnm/node-versions/v24.16.0/installation`) — installing to the default `~/.npm-global` prefix would be shadowed by fnm's bin earlier in PATH. Then ran the updater's own two steps manually (read from `update_cmd.py:_update_node_dependencies`): `npm ci --workspaces=false` + `npm ci --workspace ui-tui --workspace web` in `~/hermes-agent`. Restarted `hermes-dashboard` + `hermes-gateway` (the updater had left them on stale deps).
**Expected:** Hermes install no longer in "updated code, stale Node deps" mixed state; future `hermes update` runs pass the engine gate.
**Refs:** none external — engine range is upstream Hermes' package.json `engines` pin.
**Smoke test:** both `npm ci` runs exit 0, `package-lock.json` untouched (`git status` clean); **`import hindsight_api` OK in the hermes venv** (the mandatory post-update check from the 2026-08-01 outage); `:9177/health` healthy+connected; gateway/dashboard/hindsight-daemon all `active` post-restart; `hermes update --check` clean. `hermes doctor` still lists 3 npm-vulnerability warnings (agent-browser/web/ui-tui) — upstream advisories, informational, NOT what broke the update.

---

## 2026-08-04 (evening) — Full MODE2 GPU reset investigated; Hindsight thinking disabled (`LLM_EXTRA_BODY`); watchdog cooldown made per-model
**Observed:** User reported 2 `VK_ERROR_DEVICE_LOST` events in ~2h and a 27b delegation that never progressed. Kernel log: **four ring timeouts today** (16:45:20 comp_1.3.1, 17:41:55 comp_1.0.1, 17:47:44 comp_1.2.1, 17:49:32 comp_1.2.0) — and at 17:49:32 the per-ring reset **FAILED**, escalating to the box's **first-ever full MODE2 GPU reset** (`Ring comp_1.2.0 reset failed` → `GPU reset(8) succeeded`). A full reset kills EVERY Vulkan context, so the 27b and 35b wedged simultaneously (ErrorDeviceLost counts in stderr logs: gemma 62, 35b 30, 27b 7). Watchdog recovered the 27b at 17:54 (26.7s re-warm) but the 35b sat wedged serving 500s from ~17:52 to 18:11 because `RECOVERY_COOLDOWN` was **global** — the 27b's recovery consumed it (`SKIP recovery for qwen3.6-35b: cooldown 719s remaining`, counting down across 6 more failed probes). The delegation stall = child 27b wedged 17:49–17:55 + parent 35b wedged 17:52–18:11.
**Load context:** every probe of gemma4-12b from before 16:00 to 18:39 logged `BUSY after 90s — slots advancing`: gemma ground the retain-fan-out backlog (see entry below) continuously for ~6h, each memory-writer call running to the `--reasoning-budget 8192` ceiling (~5 min/call). All four ring timeouts fired under that sustained 3-way decode load. Ring-timeout history (journal, covers Jul 22→today): Jul 22–31 **zero**; Aug 01 **3** (the `-np` auto incident + a documented restart-teardown artifact); Aug 02 **1** (09:22); Aug 04 **4**. The structural change since Friday: gemma became Hindsight's `memory-writer` (08-02 tiered routing), adding a sustained third decode stream. Also: `modinfo -p amdgpu` on kernel 7.0.0-28 shows **`lockup_timeout` default = 2000 ms** — under 3-way contention a queued dispatch can exceed that on completion time alone.
**Changed:** (1) `~/.config/systemd/user/hindsight-daemon.service` — added `Environment="HINDSIGHT_API_LLM_EXTRA_BODY={\"chat_template_kwargs\":{\"enable_thinking\":false}}"`. Global on purpose: `memory_engine.py` builds retain/reflect/consolidation providers with `extra_body=config.llm_extra_body` and there is NO per-op `*_LLM_EXTRA_BODY` for the primary member. ⚠️ The `\"` escaping is REQUIRED — systemd strips bare double quotes from `Environment=` values; the unescaped first attempt crash-looped the daemon on `json.loads` (JSONDecodeError) until escaped. (2) `~/observability/stack/llama-watchdog/watchdog.py` — `_state["last_recovery"]` float → per-model dict, so one model's recovery never blocks another's (exactly what a full GPU reset needs: all models recovered back-to-back).
**Expected:** Memory-writer calls drop from ~5 min (7k+ reasoning tokens) to seconds — removes the multi-hour GPU-saturation windows that all of today's ring timeouts fired under. After any future full-GPU-reset multi-model wedge, the watchdog recovers every wedged model on first eligible probe instead of serializing them 15 min apart.
**Refs:** llama.cpp issue #21724 + PR #24872 (Vulkan submission batching exceeding amdgpu lockup timeout on APUs → DeviceLost; validates raising `amdgpu.lockup_timeout`); llama.cpp discussion #20856 (known-good Strix Halo stack); kyuz0 amd-strix-halo-toolboxes (gfx1151: avoid kernels <6.18.4 — ours is 7.0.0-28, fine).
**Smoke test:** watchdog: `py_compile` clean, service restarted, `:9611/metrics` exporting `probe_success=1` for both loaded models. Hindsight: env verified via `/proc/<pid>/environ` as valid JSON; `dry-run-extract` through the API → gemma returned facts in **19.5s with `thoughts_tokens: 0`, output 238 tokens** (previously ~319s / 7,368 output tokens on a comparable call). `/health` healthy+connected.
**Still open (user action):** raise `amdgpu.lockup_timeout` via GRUB (needs sudo + reboot) — recommended `amdgpu.lockup_timeout=10000,60000,10000,10000`. Watch: whether ring timeouts recur now that sustained saturation is gone.
**→ DONE 19:41 same day:** user applied the GRUB change + rebooted. Verified `/proc/cmdline` carries `amdgpu.lockup_timeout=10000,60000,10000,10000`; 0 ring timeouts on the new boot; all containers + user services back `active`; resident models re-warmed by hand per the `persistent`≠auto-load gotcha (35b 32.8s, 27b 16.6s — both matching known-good GPU cold-load times, so no silent CPU fallback).

---

## 2026-08-04 — Hindsight retain fan-out capped: `RETAIN_LLM_MAX_CONCURRENT=2` (was an uncapped 32 into gemma4-12b's 2 slots)
**Observed:** Four `llama-server queue building up` alerts (Grafana rule `ai-stack-llama-queue`, `max(llamacpp:requests_deferred) > 5 for 5m`) between 11:57 and 13:43 local, each firing and self-resolving. Two reached Telegram (12:21 fire → 12:45 resolve; 13:12 fire → 13:28 resolve); the 12:02 one was dropped by a Telegram `connection reset by peer` and a fourth was firing at 13:43 while investigating.
`llamacpp:requests_deferred` was **0 on qwen3.6-35b throughout** and peaked at **14 on gemma4-12b** — so this never presented on the interactive path, only on the aux model that serves the `memory-writer` alias (Hindsight retain + consolidation).
Root cause: `HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT` was never set. Unlike reflect (capped 1) and consolidation (capped 2), it has **no numeric default of its own** — `config.py:2369` reads it as `int(env) if env else None`, and `None` falls through to the global `llm_max_concurrent` = `DEFAULT_LLM_MAX_CONCURRENT` = **32** (`config.py:729`). A single retain op also fans out over its chunks via `asyncio.gather()` (visible as `_GatheringFuture` in the poller's `[STUCK_STACK]` dumps), so `WORKER_MAX_SLOTS=3` bounded concurrent *operations* but nothing at the llama-server end. Compounding factors: each `memory-writer` call runs to the `--reasoning-budget 8192` ceiling (one logged consolidation call: `input=7111, output=7368, time=319.349s`), the 600s `LLM_TIMEOUT` then expired and retried at `attempt=2/4`/`3/4` adding more load, llama-swap returned **39× HTTP 429** `Too many requests`, and ops failed outright with `RuntimeError: Fact extraction failed: 1/1 chunks failed`. Pending backlog grew 2 → 20 rather than draining.
**Changed:** `~/.config/systemd/user/hindsight-daemon.service` — added `Environment=HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=2` (matching gemma4-12b's `--parallel 2`), with a comment block recording the incident. Also amended the pre-existing `WORKER_MAX_SLOTS` comment, which asserted "at most one background task ever queues" — that claim is only true per-operation and is precisely what hid this; it now points at the new block. `daemon-reload` + `restart hindsight-daemon`.
**Expected:** Retain issues at most 2 concurrent completions into a 2-slot model, so `requests_deferred` on gemma4-12b stays ≤1. Eliminates the 429s and the timeout/retry amplification; the backlog should drain instead of growing. No throughput loss — raising concurrency above `--parallel` only lengthens the llama.cpp queue.
**Refs:** `hindsight_api/config.py:729,2369`; `hindsight_api/worker/poller.py:306,750`; `llama-stack/config/llama-swap.yaml` (gemma4-12b `--parallel 2`, `memory-writer` alias). No external issues consulted.
**Smoke test:** Env confirmed live in `/proc/<MainPID>/environ` (`RETAIN_LLM_MAX_CONCURRENT=2`). Startup landmine line re-checked as the unit comment requires: `Worker strix-halo starting polling loop (max_slots=3, reservations=[consolidation=1], shared_pool=2)` — `shared_pool` non-zero, so retain can still run. `GET :9177/health` → `{"status":"healthy","database":"connected"}`.
Post-restart queue depth on gemma4-12b, per minute (13:46→13:56): `0 3 3 2 3 1 2 1 2 1 1` — **max 3, zero minutes above the threshold of 5**, measured under the live backlog rather than an idle box. Over the same window: **0 HTTP 429s** (was 39 today) and **0 timeout/`attempt=N/4` retries** (was 47). Backlog turned from growing to draining (15 → 13 at 13:50). qwen3.6-35b stayed at 0 deferred throughout, as it had all day.
⚠️ Verification-method note: a first attempt at this smoke test sampled the metric with an *unencoded* `{model="gemma4-12b"}` label selector in the URL query string; VictoriaMetrics rejected all 16 samples and the script's error sentinel made it report a bogus `max_deferred=0`. Figures above were re-derived from `query_range` with `--data-urlencode` — which is also what the alert rule itself evaluates. **Always URL-encode label selectors, and treat an all-identical sample series as a script bug until proven otherwise.**

---

## 2026-08-02 — "Models feel slow" root-caused: ~45× re-prefill amplification. 35b to `--parallel 2 --kv-unified -sps 0.5`; Hindsight given tiered routing + throttles

**Observed:** User reported responses taking longer since the nine changes of 2026-08-01. **Throughput had not regressed** — measured from the pre/post stderr logs, the 35b's prefill went 316 → 480 t/s median and decode 57.2 → 58.3 t/s. What changed was the *prompt mix*: median 35b prompt size went **1,749 → 47,113 tokens (27×)**, distribution sharply bimodal with a hard gap at 8k–20k.

Traced to Hindsight's `reflect` loop, which is agentic and re-sends its whole accumulated context each iteration. Prompt sizes in service order showed the signature ramp: `53.2 → 58.6 → 60.7 → 65.4 → 70.4 → 72.6 → 78.0 → 82.4 → 84.9 → 87.7` (k tokens), interleaved with a separate 0.3–6.5k stream. The ceiling is by design: `DEFAULT_REFLECT_MAX_CONTEXT_TOKENS = 100_000` (`config.py:1057`) × `_FINAL_PROMPT_CONTEXT_FRACTION = 0.8` (`engine/reflect/prompts.py:17`) = **80k final prompts**, and `recall_budget: "high"` is a prompt instruction ("explore comprehensively", "multiple query variations", "use expand()") that maximizes iteration count, so the loop reliably walks to the ceiling. `HINDSIGHT_API_REFLECT_MAX_CONTEXT_TOKENS` was unset on the live daemon (checked `/proc/<pid>/environ`).

**The waste, quantified.** llama.cpp's `prompt eval time = … / N tokens` reports tokens processed *after* cache reuse, so these are direct measurements of zero reuse, not estimates:

| | 24h, qwen3.6-35b |
|---|---:|
| prompts ≥20k | 97 requests, 6.70M tokens, **4h 26m = 97% of all prefill** |
| prompts <20k | 68 requests, 0.18M tokens, 5.9 min |
| unique content in those requests | ~150k tokens |

**~45× redundant work.** Client-side totals agreed: `OpenAI/Python` (Hermes) 164 req / median 123s / 411 min, `AsyncOpenAI` (Hindsight) 135 req / median 96s / 190 min — **10 hours of model work in 24h through one slot**. `requests_deferred{qwen3.6-35b}` read 2–3 across 07:00–11:00 on a healthy stack.

**Why the cache never helped — three settings interacting, each individually correct:**
1. `--parallel 1` (2026-08-01 device-lost fix) — one KV slot, so prefix affinity is *mathematically impossible*. Measured: **46 small requests landed between two large ones**, each evicting the prefix and forcing a full ~87k re-prefill at ~207s of the only slot.
2. `-cram 0` (2026-07-20 host-RAM OOM fix) — `--cache-idle-slots` is documented as *"save idle slots to the prompt cache on new task … requires cache-ram"*. With cache-ram disabled a displaced prefix has nowhere to survive. **This is a previously unrecorded side effect of that fix.**
3. Hindsight configured for elastic cloud capacity: `WORKER_MAX_SLOTS=10`, `CONSOLIDATION_LLM_PARALLELISM=4`, and all three per-operation model slots unset so `retain`/`reflect`/`consolidation` all fell back to `extractor` → the 35b.

**Changed:**
1. `llama-stack/config/llama-swap.yaml` (backup `llama-swap.yaml.bak-20260802-pre-affinity`) — qwen3.6-35b `--parallel 1` → `--parallel 2 --kv-unified -sps 0.5`. Added `aliases: [memory-writer]` to gemma4-12b (it had none). Comment blocks rewritten with the measurements and the KV arithmetic.
2. `~/.config/systemd/user/hindsight-daemon.service` (backup `.bak-20260802-pre-tiering`) — `REFLECT_MAX_CONTEXT_TOKENS=32000`, `REFLECT_LLM_MAX_CONCURRENT=1`, `WORKER_MAX_SLOTS=3`, `WORKER_CONSOLIDATION_MAX_SLOTS=1`, `CONSOLIDATION_LLM_PARALLELISM=2`, and per-op routing `REFLECT_LLM_MODEL=extractor` / `RETAIN_LLM_MODEL=memory-writer` / `CONSOLIDATION_LLM_MODEL=memory-writer`. Model-only is sufficient: `memory_engine.py:1111,1149` resolve `retain_model = retain_llm_model or config.retain_llm_model or memory_llm_model`, so provider/base_url/api_key inherit.

**`-sps 0.5` was NOT in the plan — it was found by measurement and the change is worthless without it.** With 2 slots at the default threshold of 0.10, the fix still failed: a 41,510-token prompt took slot 0, then `"What is 2+2? Number only."` (20 tokens) logged `selected slot by LCP similarity, f_sim_best = 0.150 (> 0.100 thold)` and was routed **onto slot 0**, destroying the 41k prefix; the next request for that prefix fell to slot 1 and re-prefilled all 41,510 tokens. **`f_sim` is normalized by the incoming prompt's length, not the slot's**, so a short prompt scores high just by matching the shared chat-template preamble. Adding slots without raising this threshold buys nothing.

**Mid-flight correction:** the plan's `WORKER_MAX_SLOTS=2` was wrong and would have silently broken memory ingestion. `worker/poller.py:306` computes `shared_pool_size = max(0, max_slots - sum(reservations))`; `consolidation` reserves 2 by default while `retain` (and every other op type) reserves 0 and can *only* draw from the shared pool. `max_slots=2` yielded `shared_pool=0` → **retain could never run**, with the daemon still reporting healthy. Caught from the startup line, the only place it surfaces: `Worker … starting polling loop (max_slots=2, reservations=[consolidation=2], shared_pool=0)`. Corrected to `max_slots=3` + an explicit `WORKER_CONSOLIDATION_MAX_SLOTS=1` → `shared_pool=2`.

**Expected:** The reflect loop keeps its prefix across interleaved chat turns, removing the ~45× amplification rather than relocating it. Background memory work moves off the interactive model except for reflect. KV exhaustion — the measured device-lost trigger — stays arithmetically impossible: at most one 25.6k memory prompt resident, leaving >105k of the shared 131,072 buffer for chat.

**Refs:** none external — `llama-server --help` in image b10200 (`-sps`, `--cache-idle-slots`, `-cram`), and the installed `hindsight_api` 0.8.4 source (`config.py`, `engine/reflect/prompts.py`, `worker/poller.py`, `engine/memory_engine.py`).

**Smoke test:**
- **The primary gate, measured directly.** A 41,510-token prompt, then an unrelated 20-token prompt, then the first prompt again:

| step | slot | prefilled | wall |
|---|---|---:|---:|
| A cold | 0 | 41,510 | 74.0s |
| B interleaved | **1** | 20 | 1.5s |
| A again | 0, `LCP sim=1.000` | **2,048** | **7.4s** |

  **95% of the prefix avoided, 10× faster.** The 2,048 floor is one `-b 2048` batch — llama.cpp always re-evaluates the last batch. The same test at `-sps 0.10` re-prefilled all 41,510 (see above), so this is a clean A/B on that flag alone.
- Concurrency confirmed independently: a 1-token request completed on slot 0 in **178ms while a 41k prefill occupied slot 1**. Under `--parallel 1` it would have queued behind 73s.
- Slot config: `n_slots = 2, n_ctx_slot = 131072, kv_unified = 'true'` — the explicit `--kv-unified` held n_ctx_slot at 131072 (without it, setting `-np` makes slots non-auto and would split to 2 × 65536, under Hermes' 64k floor).
- **VRAM apples-to-apples (35b alone, both times): 41.34 GB → 41.59 GB.** `--parallel 2` cost **0.25 GB** (per-slot compute buffers); the unified KV buffer is unchanged. An initial 41.3 → 52.3 GB reading was gemma4 loading, not the slot change.
- Routing end-to-end: `memory-writer` → `gemma4-12b`, `extractor` → `qwen3.6-35b` (both verified by the `model` field in the response). A real `dry-run-extract` (the retain path) **cold-loaded gemma4** and returned a well-formed fact with correct entities and dates — routing and 12b extraction quality both confirmed.
- Worker pools after correction: `slots=0/3 | reserved: [consolidation=0/1(avail=1)] | shared=0/2`.
- All 9 env vars present on the live pid; exactly one `hindsight_api` process; unit's pid owns :9177, pg0 owns :5432; `NRestarts=0`; `/health` healthy/connected.
- **Data integrity — all counts grew, none reset:** memory_units 225 (was 199), memory_links 3501 (2872), entities 199 (175), async_operations 367 (340), chunks 41 (35), documents 19 (18).
- Zero `failed to find free space in the KV cache`, zero `ErrorDeviceLost`, zero ring timeouts since the cutover. MTP acceptance on the 35b with 2 slots: 1.00 on four of the last five real samples, one 0.63 on the synthetic table-lookup prompt (adversarial for speculation) — above the <0.70 investigate floor on real work.
- Host RAM unchanged at 8 used / 22 available, swap flat at 3 GB. `-cram 0` deliberately left in place on all three models — 2 slots keep the prefix in VRAM, which is better than spilling to a 30 GB host budget.

**Open / not done:** (a) `-sps 0.5` applied **only to the 35b**, where it was measured. The 27b and gemma4-12b also run `--parallel 2 --kv-unified` at the default 0.10 and have the identical hazard — the 27b especially, where long pi-kalam build turns interleave with short delegate calls. Worth applying, but should be measured there rather than assumed. (b) `recall_budget: "high"` left unchanged in `~/.hermes/hindsight/config.json`; the 32000 cap now bounds its cost, so this is the next lever only if reflect latency is still poor. (c) The 25.6k reflect cap is a real reduction in research depth from 80k — watch memory quality and consider 48000 (→38.4k, still safe: 38.4k + chat < 131k) if recall gets shallower.

---

## 2026-08-01 (late evening) — Hindsight restart loop, root-caused for real: switched to `local_external` mode; llama-watchdog gained a Hindsight probe

**Observed:** User reported the resident 35b "feels slow." It wasn't — raw decode measured 81–83 t/s with MTP acceptance 0.85–0.88 throughout, unchanged from baseline. `llamacpp:requests_deferred{model="qwen3.6-35b"}` told the real story: 0 all afternoon, then continuously 1–5 from ~19:10 onward. Investigating the queue source found the same evening's "Hindsight restart loop stopped" fix (previous entry) had already regressed: `hindsight-daemon.service` was back in a bind-fail restart loop, `NRestarts` climbing past 128, with a gateway-spawned orphan holding :9177 carrying **none** of the unit's env overrides (`/proc/<pid>/environ` confirmed neither `HINDSIGHT_API_LLM_TIMEOUT` nor `SKIP_LLM_VERIFICATION` present) — so the 120s default timeout was back, the exactly-2m00s 502s had resumed, and each abort re-queued a 33k–85k-token prefill against the 35b's single (`--parallel 1`) slot. That queueing, not throughput, was what read as "slow."

Traced to actual source this time, not just symptom. `plugins/memory/hindsight/__init__.py:_start_daemon` — the `local_embedded`-mode daemon-start thread — runs on **every** provider initialization (every Hermes session/turn that touches memory, not only gateway restarts) and diffs `~/.hindsight/profiles/hermes.env` against a small expected-keys dict (`_build_embedded_profile_env`). The file on disk is the full shipped template with extra keys (`HINDSIGHT_API_PORT`, `LOG_LEVEL`, commented examples, …) that never appear in the expected dict, so the comparison reads `config_changed = True` essentially always. When true, the plugin calls `client._manager.stop(profile)` — **SIGTERMing whichever daemon currently holds :9177**, including the systemd unit's — then spawns its own via `hindsight_embed`, which carries none of the unit-only env overrides. `~/.hermes/logs/hindsight-embed.log` shows `=== Config changed, restarting daemon ===` at exactly 22:02:39, matching an unattributed SIGTERM the unit's process received with the gateway untouched (`ExecMainStartTimestamp` unchanged since 19:01) — proving this is not `gateway.cgroup_cleanup` (already fixed earlier today) but a *third*, independent kill mechanism. This also retroactively explains the original 19:01 incident from the previous entry: the plugin's next session-init after the gateway came back up did the same thing.

**Changed:**
1. `~/.hermes/hindsight/config.json` (backup `config.json.bak-20260801-pre-external`): `mode` → `"local_external"`, added explicit `"api_url": "http://127.0.0.1:9177"`. Explicit is mandatory — the mode's coded default is `http://localhost:8888`, which SearXNG owns on this box (the same landmine documented in the earlier Hindsight-outage entry). In `local_external` mode the plugin never imports `hindsight_embed` at all (`__init__.py:1065-1074` builds a plain `hindsight_client.Hindsight(base_url=...)` HTTP client instead), so the daemon-management code path is gone structurally, not just less likely to fire.
2. Recovery, in order so nothing could respawn mid-sequence: `systemctl --user stop hindsight-daemon` → SIGTERM (never SIGKILL — it owns the embedded pg0 Postgres) the orphan on :9177 → both :9177 and :5432 freed within 1s → `daemon-reload` + `reset-failed` + `start` → healthy in 2s → `systemctl --user restart hermes-gateway` to flush any live plugin instances still in `local_embedded` mode.
3. `~/.config/systemd/user/hindsight-daemon.service`: rewrote the `[Unit]` header and `EnvironmentFile` comments. The old "`ensure_running()` ADOPTS a healthy daemon, no config change needed" claim is disproven — replaced with the real contract (Hermes runs `local_external`, never touches this daemon; the unit is the sole supervisor). `~/.hindsight/profiles/hermes.env` is now static, daemon-side LLM config we own directly — Hermes no longer rewrites it via `_materialize_embedded_profile_env()`.
4. `~/observability/stack/llama-watchdog/watchdog.py`: added a fourth, independent loop (`hindsight_loop`) probing `http://127.0.0.1:9177/health` every 60s, requiring both `status=healthy` AND `database=connected` in the JSON body (a bare 200 isn't enough — that's exactly how the :8888/SearXNG landmine fools naive checks). Exports `llama_watchdog_hindsight_up` (gauge) + `llama_watchdog_hindsight_consecutive_failures`, debounced Telegram alert after 3 consecutive failures (one alert down, one recovery message up — not a message per interval). Deliberately probe-only: `hindsight-daemon.service` already has `Restart=always`, and a second recovery path here would just be a second place for this exact two-supervisor bug class to recur.
5. `~/observability/stack/grafana/provisioning/alerting/alert-rules.yaml`: added `ai-stack-hindsight-down` (`min(llama_watchdog_hindsight_up) < 1` for 10m, `noDataState: OK` — a dead watchdog itself is covered separately by the existing `ai-stack-watchdog-down`). Grafana container restarted to load it (file-provisioner poll timing wasn't worth trusting for a same-session verification).

**Expected:** Hindsight daemon lifecycle is now owned by exactly one supervisor. The unit's `HINDSIGHT_API_LLM_TIMEOUT=600` / `SKIP_LLM_VERIFICATION=true` (from the earlier entry) can never again be silently bypassed by a plugin-spawned competitor. Both incidents that went unnoticed for hours (22h outage 2026-07-31, ~130-restart loop this evening) now have alerting coverage.

**Refs:** none external — diagnosed from `plugins/memory/hindsight/__init__.py` (on-disk Hermes source), `hindsight_embed/daemon_embed_manager.py`, `~/.hermes/logs/hindsight-embed.log`, `journalctl --user -u hindsight-daemon`, `/proc/<pid>/environ`, and VictoriaMetrics (`llamacpp:requests_deferred`).

**Smoke test:**
- Single owner: exactly one `hindsight_api` process (`ps aux`), unit's own pid owns both :9177 and :5432, `NRestarts` held at 0 after recovery.
- No respawns: `~/.hermes/logs/hindsight-embed.log` mtime frozen at 22:17:22 — unchanged through a real `systemctl --user restart hermes-gateway` (daemon PID unchanged, confirmed by `ExecMainPID`) and a deliberate `kill -9` of the daemon.
- Supervision regression test: `kill -9` the daemon → systemd restarted it in 14s with a new PID, `NRestarts` 0→1 (expected for the deliberate test), exactly one `hindsight_api` process afterward, no orphan appeared. This is the test that would have caught today's bug before it shipped.
- `hindsight health` CLI → healthy, database connected.
- Data integrity: direct `asyncpg` query against the resolved pg0 DSN (`hindsight_api.pg0.resolve_database_url('pg0://hindsight-embed-hermes')`) — memory_units 199, memory_links 2872, entities 175, async_operations 340, chunks 35, documents 18, all **higher** than this morning's baseline (173/2572/148/328/32/16) — growth from continued real usage, no loss or reset.
- `requests_deferred{model="qwen3.6-35b"}`: continuously 1–2 through 22:05–22:30 while the loop was still live, dropped to 0 at 22:35 (the moment of the fix), flat at 0 for 35+ minutes afterward.
- Monitoring: `llama_watchdog_hindsight_up` confirmed flowing into VictoriaMetrics via `docker exec victoriametrics wget` (matches the container's actual scrape path) and via direct query. Grafana: `ai-stack-hindsight-down` present in `/api/v1/provisioning/alert-rules`, evaluating `inactive`/`ok` alongside the other rules; `ai-stack-litellm-errors` correctly still absent (the `deleteRules:` block still works).
- No live chat traffic occurred after ~22:27 (nothing running), so a fresh post-fix 502-free HTTP trace wasn't directly observable — the `requests_deferred` server-side signal is the stronger, independent confirmation instead.

---

## 2026-08-01 — Hindsight restart loop stopped; LLM timeout 120s→600s, startup verification disabled
**Observed:** A steady ~1 `HTTP 502` per 2–3 min on llama-swap (`POST /v1/chat/completions`, `AsyncOpenAI/Python 2.24.0`), most at **exactly 2m00.1s**, 25+ in two hours. **llama-swap was not at fault.** Every 502 was immediately preceded by `http: proxy error: context canceled` — Go's `ReverseProxy` reporting that the *client* hung up. Three stacked causes:

1. **`DEFAULT_LLM_TIMEOUT = 120.0`** (`hindsight_api/config.py:733`) is passed straight to `AsyncOpenAI(timeout=...)` and was overridden nowhere (verified against the live process env, `hermes.env`, `config.json` and the unit). It is unreachable on this box: `--parallel 1` means a 33k–85k-token prefill holds the only slot 2–4 min.
2. **Duplicate daemon.** `hermes-gateway` restarted at 19:01:17 and the `hindsight-daemon.service` process was **SIGKILLed 16s later** (`code=killed, status=9/KILL`). At 19:08:02 the gateway spawned its *own* embedded daemon (pid 3781686, parent = gateway) which took :9177. From 19:12 the unit could never bind — `[Errno 98] address already in use` → exit 1 → restart, **`NRestarts=30`**. Each doomed startup ran `verify_connection()` = up to 3 real 35b calls before dying, which *was* the 502 stream. Memory itself stayed healthy throughout (served by the orphan); the cost was GPU time and slot contention, which also fed the watchdog BUSY storm.
3. **Startup verification can never succeed against this model.** `verify_connection()` sends `"Say 'ok'"` with `max_completion_tokens=100`; qwen3.6-35b is a reasoning model and burns the whole budget thinking → `finish_reason=length` with empty content (logged all day as "Provider returned empty message content (scope=verification)"). It runs inside the uvicorn lifespan **before the socket binds**, once per LLM config at 3 attempts each.

Cause 1 predates cause 2 — the 502 cadence is identical before 19:00 and after 19:12 — so the low timeout was independently breaking real memory work, not just the loop.

**Changed:**
- Orphan daemon pid 3781686 **SIGTERMed** (not killed — it owns the embedded pg0 Postgres for `hindsight-embed-hermes`); exited cleanly in 16s and took Postgres down with it.
- `~/.config/systemd/user/hindsight-daemon.service` — added `Environment=HINDSIGHT_API_LLM_TIMEOUT=600` and `Environment=HINDSIGHT_API_SKIP_LLM_VERIFICATION=true`, each with a comment recording the reasoning. Both must live in the unit, **not** `~/.hindsight/profiles/hermes.env`, which `_materialize_embedded_profile_env()` rewrites wholesale.
- Unit restarted so it is the sole owner of :9177.

**Mid-flight correction:** raising the timeout alone made things worse — the first restart blocked in `verify_connection()` and never bound the port, because the doomed check now had 600s per attempt instead of 120s. The skip flag is what makes the timeout raise safe; the two keys are a pair, not independent knobs.

**Expected:** No more 502s from Hindsight; genuine extraction jobs that legitimately take 2–4 min now complete instead of being aborted at 2m00s; the daemon starts once and binds in seconds. 600s stays under llama-swap's `healthCheckTimeout` (1200) and does not mask a wedged GPU — llama-watchdog owns that detection independently.

**Refs:** none external — diagnosed from llama-swap container logs, `journalctl --user -u hindsight-daemon`, `systemctl show`, `ss -tlnp`, `/proc/<pid>/environ`, and the installed `hindsight_api` source (0.8.4).

**Smoke test:** Unit `active/running`, `NRestarts=0`, bind confirmed in <25s (`Application startup complete`, `Uvicorn running on http://127.0.0.1:9177`), `/health` → `{"status":"healthy","database":"connected"}` in 3.6ms. Both env keys confirmed on the live pid. Exactly one `hindsight_api` process; gateway still `active`. llama-swap 502s: the last one is at 21:10:39 IST, the instant of the restart itself (the stuck process's in-flight call being cancelled) — **none since**, against a prior rate of ~1 per 2–3 min.

**Strength of that evidence, stated honestly:** the restart-loop half is solid (`NRestarts` 30→0, single daemon, port owned by the unit, env keys confirmed on the live pid). The **timeout half is correct-by-construction but not yet empirically exercised.** Only ~5 min of clean observation (~2 missed 502 cycles), and the sole `AsyncOpenAI` request in that window completed in 23.9s — under the old 120s cap, so it would have succeeded regardless. Three 200s at 4m02s/4m07s/4m09s in the same window are **`OpenAI/Python` (sync client), a different caller** with its own longer timeout — they show the server serves multi-minute requests, they do **not** validate Hindsight's new 600s. Confirm later by finding an `AsyncOpenAI` 200 with a duration >2m00s.

**Open / not done:** (a) `hindsight-api-slim`/`hindsight-all` are **0.8.4** while `hindsight-embed` is already **0.8.6** (latest) — version skew, upgrade deliberately deferred as a separate change. (b) The SIGKILL-16s-after-gateway-restart mechanism is unproven (correlation only) and means the unit does not fully escape `gateway.cgroup_cleanup`; the next gateway restart could re-run this sequence.

## 2026-08-01 — Watchdog probe made slot-aware (busy ≠ wedged)
**Observed:** Telegram alert "llama-server model failing real completions" on `qwen3.6-35b`. **False positive — no GPU fault.** Every failure logged `device_lost=False` and `TimeoutError` at exactly 90.1s (= `PROBE_TIMEOUT`), while in-container `/health` returned 200 in 0.7ms and `/slots` showed steady forward progress. Real cause: `--parallel 1` (from the 2026-07-21 device-lost fix) gives the model one slot, and callers were sending 33k–85k-token prompts. Prefill alone runs 2–4 min at 575–790 t/s, so the watchdog's one-token probe queues behind real work and times out. `watchdog.py` counted that queue-wait as a health failure — it had no way to tell "busy" from "wedged".

I initially called this a latent edge case ("never reached 3/3 in 37 min of the heaviest load on record"). **That was wrong.** ~13 min later it hit 3/3 and fired: `20:03:48 RECOVER unloading` → `20:04:35 re-warm ok in 36.4s`, `recoveries_total` 0→1. The re-warm succeeded but the unload destroyed the in-flight request it was queued behind. Absence of a failure over a 37-min window was never evidence the threshold was unreachable.

Separately, chased an observed on-the-wire `max_tokens: 65536` as suspected config drift against the documented 32768. **It was not drift, and my first diagnosis of it was wrong.** I read `gateway/run.py:2385` (reads top-level `model.max_tokens`) and `:2391` (fallback reads `runtime["max_output_tokens"]`), saw the config only had `max_tokens` under `custom_providers`, and concluded the key matched neither path and the backstop was dead. Wrong: `_lift_max_output_tokens` (`hermes_cli/runtime_provider.py:609`) **accepts either `max_output_tokens` or `max_tokens`** on a provider entry and lifts it onto the runtime dict. Verified against the pre-edit config — the lift yields `{'max_output_tokens': 32768}`, so Hermes was already capping correctly the whole time. `qwen_thinking_runaway_mitigations` had this right and I contradicted it before checking. **No Hermes config change was warranted; none was kept** (a briefly-added redundant top-level key was reverted, config byte-identical to `config.yaml.bak-20260801-pre-maxtokens`).

Hermes is therefore ruled out as the 65536 sender — it caps at 32768. Separately confirmed that omitting `max_tokens` records `n_predict = -1`, not 65536 (tested on `gemma4-12b`), so 65536 is explicitly caller-sent. **The source remains unattributed** — most likely Hindsight (the `AsyncOpenAI` client, `llm_model: extractor` → the 35b), which passes `max_completion_tokens` per-operation rather than from a config constant. It is not a static value anywhere on disk.

**Changed:**
- `observability/stack/llama-watchdog/watchdog.py` — added `slots_progress()` (snapshot of per-slot `is_processing` / `n_prompt_tokens_processed` / `n_decoded`; reads both the nested `next_token[0]` and flat shapes so a container bump can't silently blind it) and `slot_busy_advancing()`. `probe_loop` now snapshots `/slots` before the probe and re-reads after a failure: if a slot is still processing **and** counters moved, it logs `BUSY`, sets `probe_ok=1`, and counts no failure. A device-lost signature is never excused. Unreadable `/slots` falls through to the old fail-and-recover path rather than disarming the watchdog. New counter `llama_watchdog_probe_busy_total`.
- `~/.hermes/config.yaml` — **no net change.** Backup `config.yaml.bak-20260801-pre-maxtokens` retained as the verification baseline.

**Expected:** Heavy-load periods stop paging and stop triggering destructive auto-unloads, while a genuine device-lost (slots processing but counters *frozen*) still recovers on the same 3-failure threshold.

**Refs:** none external — diagnosed from `/slots`, `/health`, container logs, and the watchdog source.

**Smoke test:**
- Unit-tested the classifier on real captured slot shapes, 8/8 pass — critically, "processing but frozen" still classifies as a **failure**, and unreadable `/slots` does not excuse.
- Live pre-restart: probe timed out at 90.09s (`ok=False lost=False`) while slots advanced 38958→43054 → correctly classified BUSY.
- Post-restart under the same load: `20:30:00 PROBE qwen3.6-35b BUSY after 90.1s — slots advancing, model healthy, not counting a failure`; `probe_busy_total 1`, `probe_failures_total 0`, `consecutive_failures 0`, `probe_success 1` (alert condition cleared).
- Hermes: `_lift_max_output_tokens()` run against the untouched pre-edit provider entry → `{'max_output_tokens': 32768}`, i.e. the 32768 cap was already live and needed no change. (Useful aside if a real config edit is ever needed here: `load_config()` is cached on the file's `(mtime_ns, size)`, so it self-invalidates on edit — no gateway restart required.)

**Still open:** 25 × HTTP 502 at exactly 2m0s to the `AsyncOpenAI` client in the 2h before this change (21 in the 18:00 UTC hour, 4 in 19:00). Those are upstream timeouts on long prefills, unrelated to the watchdog and **not fixed here** — a 2-minute client/proxy timeout against 2–4 min prefills. Needs its own look.

---

## 2026-08-01 — Hindsight given its own systemd unit; gateway restarts no longer kill memory
**Observed:** Chased down why the Hindsight daemon never comes back after a `hermes-gateway` restart. My first explanation ("it's a child process, it dies with the parent") was **wrong** — the daemon is properly daemonized. Traced to source and live process state:

1. `hindsight_embed/daemon_embed_manager.py` spawns it with `start_new_session=True` → `setsid(2)`. Verified on the live process: **PID == PGID == SID**, its own session leader. It would survive a plain parent exit.
2. **setsid does not escape a cgroup.** The daemon and its pg0 Postgres stayed in `hermes-gateway.service`'s cgroup (confirmed via `/proc/<pid>/cgroup` and `systemctl status`).
3. The gateway unit runs `ExecStopPost=-…/python -m gateway.cgroup_cleanup`, and `~/hermes-agent/gateway/cgroup_cleanup.py` **SIGKILLs every PID in the unit's cgroup with no allowlist** — deliberate, added for Hermes issue #37454 so untracked helpers (`adb`, platform bridges) can't block `Restart=always`. Unit is `KillMode=mixed`.
4. Nothing restored it: `ensure_running()` is only called lazily inside a real Hermes session.

Neither project is buggy alone; the reaper simply has no allowlist. Upstream knows and hasn't fixed it: **#8973** asked for exactly this unit (launchd plist + "parallel systemd unit") citing "no automatic restart" and "the client caches connections without health checks" — **closed, P3, unimplemented**. **#7149** is the same "isn't brought back" gap via the idle timeout (neutralized here by `idle_timeout: 0`).

**Changed:** New `~/.config/systemd/user/hindsight-daemon.service`, `Restart=always`, enabled. **No Hermes config change** — still `local_embedded`, because `DaemonEmbedManager.ensure_running()` opens with `if self.is_running(profile): return True` and therefore *adopts* a healthy daemon already on the port. No source patches.

Three non-obvious requirements, each of which would have failed silently:
- **Do not pass `--daemon`.** Per `hindsight_api/daemon.py`, without `_HINDSIGHT_DAEMON_CHILD=1` that flag makes the process re-exec detached and `sys.exit(0)`; systemd would see an instant clean exit and restart-loop forever. Run `python -m hindsight_api.main` in the foreground.
- **`--host 127.0.0.1` explicitly.** This code path defaults to `0.0.0.0` (per `--help`), which would expose the memory API to the network; the embedded daemon bound loopback.
- **`HINDSIGHT_API_DATABASE_URL=pg0://hindsight-embed-hermes` explicitly.** The env file leaves it commented as "uses embedded pg0 by default" — that default is a *different* instance, so relying on it opens a fresh empty database and still reports `{"status":"healthy","database":"connected"}` while every memory appears gone.

`EnvironmentFile=~/.hindsight/profiles/hermes.env` carries LLM provider/key/model/base_url, which Hermes regenerates from `config.json` via `_materialize_embedded_profile_env()` — so this adds no 4th config copy to rot. But the port must NOT be read from there: that function rewrites the file wholesale from a small dict and drops `HINDSIGHT_API_PORT`, which exists today only as a leftover of the original template.

**Expected:** Gateway restarts stop taking memory down; a Hindsight crash self-heals instead of waiting for a human; pg0 gets a graceful stop instead of SIGKILL.

**Smoke test:**
- Old daemon stopped via the supported `DaemonEmbedManager().stop()`; ports 9177 and 5432 confirmed free before starting the unit.
- **Data verified row-for-row across the migration** (pg0 DSN resolved via `hindsight_api.pg0.resolve_database_url`): memory_units 173, memory_links 2572, unit_entities 318, entity_cooccurrences 268, entities 148, async_operations 328, chunks 32, documents 16 — **identical before and after**.
- **Cgroup separation confirmed:** daemon now in `…/hindsight-daemon.service`, gateway in `…/hermes-gateway.service`; pg0 moved under the Hindsight unit.
- **The actual regression test: restarted `hermes-gateway` and the daemon kept the SAME PID (3770000)** — it was never touched. Health stayed 200 throughout.
- **Supervision test:** `kill -9` the daemon → auto-restarted ~10s later as a new PID, healthy. Previously it stayed dead.
- **Adoption test:** `ensure_running(cfg,"hermes")` returned True without spawning a rival; exactly one listener on 127.0.0.1:9177.

---

## 2026-08-01 — open-webui 0.9.5 → 0.11.0, and pinned by digest
**Observed:** Asked whether open-webui had been upgraded — it had not. Stage 1 earlier the same day only *repointed* it (off the dead `litellm:4000`, added `extra_hosts`, fixed RAG embeddings, replaced the healthcheck). That `compose up -d` reused the locally cached `:main` image, so the binary never moved: it was still running **v0.9.5 from an image built 2026-05-10**, ~2.7 months stale, while upstream `:main` had a different digest and the latest release was **v0.11.0** (2026-07-27).

This is the floating-tag failure mode stated plainly: `:main` did not keep the deployment current, it only made it impossible to say what was running. (Same week, ghcr's `llama-swap:v245-vulkan-b10200` was rebuilt underneath us mid-session — even version-pinned tags move on this registry.)

Upgrade motive was primarily the v0.11.0 release note: *"security and access-control fixes"* with the explicit caveat that *"not all security fixes in this version may be enumerated… Some may be withheld for a short time to give administrators time to upgrade"* — standard phrasing for embargoed CVEs. Exposure here is low (localhost-bound, `ENABLE_SIGNUP=false`, single user) but this is the one component that renders untrusted model output in a browser.

**Changed:** `openwebui/docker-compose.yml` — `image:` moved from the floating `ghcr.io/open-webui/open-webui:main` to `@sha256:6a773e5c3a246b65cbe74ce942b294292c0e5f81c138f703d111bc162f7d7c3d` (v0.11.0, built 2026-07-27), with the upgrade + rollback recipe in a comment. Digest resolved by **pulling** and reading `RepoDigests`, not via the registry API.

**Expected:** Current security fixes; a deployment whose running version is knowable from the config file.

**Smoke test:**
- **Backed up first**, container stopped so the SQLite WAL checkpointed cleanly: `data.bak-20260801-pre-0.11.0.tar.gz`, 843 MB, 89 entries, both `webui.db` and `vector_db/chroma.sqlite3` verified present in the archive.
- Pre-upgrade `PRAGMA integrity_check` = ok on both DBs; alembic rev `a0b1c2d3e4f5`; 1 user, 2 chats.
- **14 alembic migrations ran clean**, `a0b1c2d3e4f5 → f0bd01a18a3d` (knowledge_directory, per-key config reshape, memory type/path/meta, chat variables, user variables, unique normalized user-email index, …). No errors or tracebacks anywhere in the startup log.
- Post-upgrade: version reports **0.11.0**, `integrity_check` ok, **1 user and 2 chats intact**.
- Upstream path still good: `host.docker.internal` resolves (172.17.0.1), container lists all three models from llama-swap `:9292`, and the Stage 1 healthcheck (which probes the real upstream, not its own port) reports **healthy**.
- All Stage 1 env survived the recreate: `OPENAI_API_BASE_URL`, empty `RAG_EMBEDDING_ENGINE`, local `all-MiniLM-L6-v2` (loaded from the on-disk cache), `ENABLE_OLLAMA_API=false`, `ENABLE_SIGNUP=false`.
- **Rollback:** previous digest `sha256:74093dadc9c6aabc23987a74fd8c2fb8d995b1a5b22e83b0036fb9d6af590e8c` + restore the tarball. The data restore is **mandatory** on a downgrade — the schema was migrated forward.

---

## 2026-08-01 — llama-swap v234/b9853 → v245/b10200 (attended cutover)
**Observed:** A month of drift with directly-relevant fixes in it: llama-swap v242's TTL-vs-request **deadlock** fix (#946 via #949) on a stack that leans hard on `ttl` (0/1800/600), llama.cpp **#25240** (reduce Vulkan submission threshold by CU count, merged Jul 8 — aimed squarely at the amdgpu ring-timeout class that wedged this box), SSE ping on silent streams, and several MTP/spec-decode fixes.

**Changed:** `llama-stack/docker-compose.yml` repinned to `sha256:49546f75ddf24fcadfbbd12dab1985fadaddaf1abcae63fd68be369dc0e419fc` (v245-vulkan-b10200). Backup `docker-compose.yml.bak-20260801-pre-b10200`. Also added `-b 2048 -ub 2048` to gemma4-12b — see the regression below.

⚠️ **The tag MOVED mid-session.** A registry HEAD taken ~30 min before the pull returned `sha256:532f162f…`, which then failed with `manifest unknown`; the image had been rebuilt at 12:19 UTC. `docker manifest inspect -v` and `docker pull` now agree on `49546f75`. **Resolve digests by pulling, not by querying the registry API** — even a version-pinned tag is not immutable here. The rollback ladder in the compose comment was re-verified the same way.

**Expected:** Fewer ring timeouts; a fixed TTL deadlock; no throughput regression on the two load-bearing models.

**Smoke test:**
- `--list-devices` on the candidate BEFORE cutover: `Vulkan0: Radeon 8060S Graphics (RADV STRIX_HALO)` — non-empty, so no silent CPU fallback (the documented non-monotonic hazard).
- Versions live: llama-server `10200 (5f55650a7)`, llama-swap `v245 (30470a4)`. All three models load and answer; slot config survived (`n_slots` 1/2/2, `n_ctx_slot` 131072 throughout).
- **Bench A/B (both runs on the same `--parallel` config, so the image is the only variable):**

| model | PP b9853 | PP b10200 | Δ | TG b9853 | TG b10200 | Δ | MTP |
|---|---:|---:|---:|---:|---:|---:|---:|
| qwen3.6-35b | 829.79 | 831.05 | +0.2% | 92.68 | 92.60 | −0.1% | 1.000 |
| qwen3.6-27b | 211.88 | 213.17 | +0.6% | 23.78 | 23.72 | −0.3% | 1.000 |
| gemma4-12b | 677.27 | 369.22 | **−45.5%** | 94.05 | 90.71 | −3.6% | 0.943 |

- **gemma4 prefill regression is real, reproducible, and now partly explained.** Tight ranges in both runs (674–679 vs 368–372), and a standalone re-measure on a fully idle box reproduced 367–374. gemma4 was the only model still on llama.cpp's default `-ub 512`; setting `-b 2048 -ub 2048` recovered it to **456 t/s (+23%)**. The defaults themselves did not change between builds (checked both `--help`), so the likely cause is #25240 — more, smaller Vulkan submissions, a deliberate stability-for-throughput trade that hits small ubatches hardest, and precisely the fix we wanted. The remaining ~33% is accepted: aux model, short prompts, ~1.5s → 2.2s per call. Follow-up experiment if aux latency matters: v242-vulkan-b10121 (still has #25240 and the TTL fix, 79 fewer builds of drift).
- **Gate:** decode within 5% and MTP ≥ 0.70 on all three → PASS, promoted.
- Zero ring timeouts, zero `ErrorDeviceLost`, zero KV-exhaustion lines, and zero 5xx served since the cutover.
- **A ring timeout is NOT always an incident:** the one at 13:46:42 was `docker restart llama-swap` killing a 59,551-token in-flight request. It produced 3 `ErrorDeviceLost` lines followed immediately by `srv stop: cancel task`, and no client saw an error. Drain before restarting, and check restart timestamps before blaming a fresh timeout.
- **TTL deadlock regression test (the reason for taking v242+) PASSED:** gemma4 idle-evicted at 570s (`ttl: 600`), and the next request reloaded and replied in **4s**. Clean evict→reload cycle.
- **Gotcha confirmed: `persistent: true` + `ttl: 0` prevents eviction but does NOT auto-load.** After a restart the "resident" 35b stayed unloaded until requested (VRAM sat at 11 GB), so the next real caller would have paid a 33s cold load. Warm the residents by hand after every restart. Final steady state: all three loaded, 84.88 GB / 103.08 GB.

---

## 2026-08-01 — GPU device-lost ROOT CAUSE: `-np` auto = 4 slots. `--parallel` pinned; llama-watchdog built; per-model telemetry restored
**Observed:** Chasing the morning's device-lost wedge past "concurrent decode contention" found the actual mechanism. `llama-server --help` in the pinned image:
```
-np,  --parallel N   default: -1, -1 = AUTO
-kvu, --kv-unified   default: ENABLED IF NUMBER OF SLOTS IS AUTO
```
llama.cpp's `-np` default changed from 1 to auto, auto resolved to **4**, and because slots were auto `kv_unified` auto-enabled with them. Both big models were logging `n_slots = 4, n_ctx_slot = 131072, kv_unified = 'true'` — four concurrent decode streams sharing **one** 131072-token KV buffer. Two overlapping long conversations exhaust it: the 35b logged **17×** `failed to find free space in the KV cache` (27b: 0) in the same run that ended in the 32× `ErrorDeviceLost` cascade.

The yaml comment at `llama-swap.yaml:205-208` claiming `--parallel 2` "needs `-c 262144` (+~7 GiB KV)" was **wrong** — under unified KV the buffer is `n_ctx` *total*, shared. The config had been running 4× its intended concurrency, for free, in the worst direction.

Separately: `bench/mesa_baseline.py` had been un-runnable since 2026-05-19 (`MODELS` still listed the removed `granite-4.1-8b`), so there was no usable "before" for any bump.

**Changed:**
1. `llama-stack/config/llama-swap.yaml` — pinned `--parallel` on all three models: 35b `--parallel 1`; 27b and gemma4-12b `--parallel 2 --kv-unified`. The explicit `--kv-unified` is required — setting `-np` makes slots non-auto, which would flip unified KV off and halve `n_ctx_slot` to 65536, breaking pi-kalam's 131072 need. Replaced the false comment block with the real mechanism. Backup `llama-swap.yaml.bak-20260801-pre-parallel`.
2. `~/.hermes/config.yaml` — `delegation.max_concurrent_children` 3 → 2, matching the 27b's 2 slots. Backup `config.yaml.bak-20260801-pre-concurrency`.
3. **New service `llama-watchdog`** — `~/observability/stack/llama-watchdog/watchdog.py` + `~/.config/systemd/user/llama-watchdog.service` (`Restart=always`), listening on :9611. Probes every loaded model with a **real `n_predict: 1` completion** (a wedged server passes `/health` AND `/v1/models` — proven this morning), auto-recovers on device-lost, alerts to Telegram, and relays each model's `llamacpp:*` metrics with a `model=` label. Credentials in `watchdog.env` (mode 600).
4. `observability/stack/victoriametrics/scrape.yml` — **re-enabled per-model LLM telemetry**, dead since 2026-05-23. The old job scraped `/upstream/<model>/metrics` through llama-swap's proxy, which reset the idle timer and defeated `ttl`. The watchdog scrapes the llama-servers **direct** at `172.20.0.2:1000N`, which never reaches llama-swap. VM cannot route there itself (ai-stack/172.23 vs llama-stack_default/172.20 — verified, wget times out), which is why the relay must run on the host.
5. `observability/stack/docker-compose.yml` — added `-promscrape.configCheckInterval=30s` to VM; dropped the dead `litellm-metrics.token` mount.
6. `grafana/provisioning/alerting/alert-rules.yaml` — replaced dead `ai-stack-litellm-errors` (queried a metric with no producer since 2026-07-13, and with `noDataState: OK` sat permanently silent) with `ai-stack-model-wedged` (`min(llama_watchdog_probe_success) < 1`) and `ai-stack-watchdog-down` (`up{job="llama-watchdog"} < 1`, `noDataState: Alerting`). Added a `deleteRules:` block — Grafana's file provisioner never removes a rule just because it left the file.
7. `llama-stack/bench/mesa_baseline.py` — fixed the model list, added MTP draft-acceptance capture per run, de-duplicated the report builder, and added `"ignore_eos": true`.

**Expected:** KV-cache exhaustion becomes structurally impossible on the 35b and much less likely on the 27b, removing the measured trigger for the device-lost cascade — at zero VRAM cost. Any future wedge self-heals within ~3 probe intervals instead of going unnoticed for 20 minutes. Grafana regains per-model LLM visibility.

**Refs:** llama.cpp `--help` in image b9853; llama-swap v242 release notes (TTL/request deadlock #946, fixed via #949); llama-swap config reference (no `concurrencyLimit` exists — the earlier plan assumed one).

**Smoke test:**
- Slot config verified in stderr after restart: 35b `n_slots = 1, n_ctx_slot = 131072, kv_unified = 'false'`; 27b and gemma4 `n_slots = 2, n_ctx_slot = 131072, kv_unified = 'true'`. All three answered completions.
- **VRAM, apples-to-apples (27b + 35b loaded, both times): 76.12 GB before → 74.21 GB after.** Pinning `--parallel` did not cost VRAM; it saved ~1.9 GB.
- Baseline captured → `bench/baseline-b9853-np1.md`: 35b PP 829.79 / **TG 92.68**, 27b PP 211.88 / TG 23.78, gemma4 PP 677.27 / TG 94.05. The 35b figure is far above the 59 t/s reference from 2026-05-19. **The `ignore_eos` fix mattered:** without it the 35b emits EOS on its first token for the bench prompt, so `predicted_ms≈0` and llama.cpp returns its divide-by-zero sentinel `TG = 1000000 t/s` — which the first run duly printed into the table. The 27b and gemma4 do not do this.
- **Fault injection passed.** A fake wedged llama-server (200 on `/health` and `/v1/models`, 500 `ErrorDeviceLost` on `/completion`) plus a fake llama-swap that records every request: watchdog detected only via the real completion, honoured the 3-strike threshold, recovered exactly once, and the cooldown then correctly suppressed a recovery loop against a permanently dead device. Request log asserts it used `POST /api/models/unload/<model>` and **never** `GET /unload`. Two real Telegram alerts delivered.
- **TTL safety verified**: gemma4 evicted exactly 600s after its last proxied request with the watchdog probing it throughout — direct-path probes do not reset llama-swap's idle timer.
- VM: all 4 scrape targets up; `llamacpp:requests_deferred`, `requests_processing`, `predicted_tokens_seconds`, `n_busy_slots_per_decode` all queryable per model. `n_busy_slots_per_decode` now reads 1.0 across the board. `ai-stack-llama-queue` evaluates against live data for the first time since May.
- ⚠️ **Restarting `hermes-gateway` killed the embedded Hindsight daemon** (child process, does not self-restart). Restored via `DaemonEmbedManager().ensure_running()`; `:9177/health` → 200. Recorded in memory as a landmine for every future gateway restart.

---

## 2026-08-01 — Stage 1 outage repair: Hindsight restored (22h down), open-webui repointed off dead LiteLLM
**Observed:** A stack-wide upgrade review turned up two live outages nobody had noticed, neither of which had alerted.

1. **Hindsight down ~22h** (2026-07-31 10:41 → 2026-08-01 09:14). `:9177` refused connections; `~/.hindsight/profiles/hermes.log` held **2272 `APIConnectionError`** and 2 stuck operations; Hermes had been running `memory_enabled: true` against nothing. Root cause was **not** config: the Hermes 0.19.1 venv rebuild left **nothing hindsight-related installed at all** — not even the declared `hindsight-client`. Hermes' own `hermes_cli/memory_setup.py:_provider_pip_dependencies` documents this exact failure class (issue #70636) and is supposed to reinstall `hindsight-all` for `local_embedded` mode; the guard did not fire.
2. **open-webui broken ~3 weeks.** Still had `OPENAI_API_BASE_URL=http://litellm:4000/v1` from before the 2026-07-13 LiteLLM removal; `litellm` was NXDOMAIN. The container reported `Up (healthy)` the whole time because the stock healthcheck only probes its own port. It also had no `extra_hosts`, so `host.docker.internal` did not resolve either.
3. **A second Hindsight config** — `~/.hermes/hindsight/config.json` — still carried **both** poison values (`llm_base_url: :4000`, `llm_model: qwen3.5-122b`). The 2026-07-31 remediation fixed only `~/.hindsight/profiles/hermes.env`. Third recurrence of this drift; there are two files and both rot.

**Discovered en route — a permanent landmine:** Hindsight's default local URL is `http://localhost:8888` (`plugins/memory/hindsight/__init__.py:54`, and the Rust CLI shares the default), but **SearXNG owns 127.0.0.1:8888 on this box**. So every default-fallback silently talks to the search engine: `is_available()` returned **True** off SearXNG's 200, and `hermes memory status` showed "available ✓" with no daemon in existence. The CLI had neither `~/.hindsight/config` nor `~/.hindsight/cli-profiles/`, so it too was hitting SearXNG — meaning the long-standing memory note that "the CLI needs `-p hermes`" was wrong and could never have worked.

**Changed:**
- `uv pip install 'hindsight-all==0.8.4'` into `/home/dinesh-se/hermes-agent/.venv` (pinned to last-known-good from the uv cache; upstream latest is 0.8.6). Dry-run first: 161 packages added, **no downgrades of any Hermes-pinned dep**; `huggingface-hub` landed at 1.26.0, safely above the ≥1.5.0 floor whose violation caused the 2026-07-13 crash-loop.
- `~/.hermes/hindsight/config.json` → `llm_base_url: http://127.0.0.1:9292/v1`, `llm_model: extractor` (the **alias**, verified to resolve before writing). Backup at `config.json.bak-20260801-pre-repoint`.
- `hindsight configure --api-url http://localhost:9177` → created `~/.hindsight/config`, so the CLI no longer falls back onto SearXNG.
- `/home/dinesh-se/openwebui/docker-compose.yml`: base URL → `http://host.docker.internal:9292/v1`, key → `unused-llama-swap-direct` (matching pi/Hermes), **added the missing `extra_hosts: host.docker.internal:host-gateway`**, and replaced the self-referential healthcheck with one that probes the actual upstream `/v1/models`. RAG switched from `openai`/`bge-m3` (a model dropped from the lineup on 2026-05-19 — llama-swap serves **no** embedding model at all) to open-webui's in-container sentence-transformers, keeping embeddings off a GPU budget already at ~80 of 96 GiB. Backup at `docker-compose.yml.bak-20260801-pre-repoint`.

**Expected:** Hermes memory works again; open-webui can actually reach a model; both CLI and provider stop silently querying SearXNG; the open-webui health status becomes meaningful instead of decorative.

**Refs:** `hermes_cli/memory_setup.py:_provider_pip_dependencies` (#70636); `plugins/memory/hindsight/__init__.py:54,745,989,1288`; `hindsight_embed.daemon_embed_manager.DaemonEmbedManager.ensure_running`.

**Smoke test:** Daemon started via `DaemonEmbedManager().ensure_running(cfg, "hermes")` in **16.3s**, brought up its own Postgres at `~/.pg0/instances/hindsight-embed-hermes`. `:9177/health` → 200, `api_version 0.8.4`, database connected; `hindsight health` now works with no env var. **The operation stuck since 2026-07-31 completed on its own**: `op=f45e9b0d… stage=llm.openai.retain_extract_facts`, `model=openai/extractor, input=3671 output=3727 total=7398 tokens, 52.2s`, parent operation `e099bb2b…` closed. open-webui: `host.docker.internal` → 172.17.0.1, and from inside the container `/v1/models` returns all three models; container `Up (healthy)` with healthcheck exit 0 — now against the real upstream.

**Note for next `hermes update`:** verify `/home/dinesh-se/hermes-agent/.venv/bin/python -c "import hindsight_api"` afterwards. The upstream guard exists but demonstrably did not fire here.

---

## 2026-08-01 — GPU ring-timeout recurrence: both models wedged; `-ub 1024` disproven; service restored via /unload
**Observed:** User asked whether a GPU crash was memory pressure. It was not.
At 08:17:30 the kernel logged `ring comp_1.2.0 timeout` (pid 2684265 =
qwen3.6-27b) and 1 s later `ring comp_1.0.1 timeout` (pid 281377 =
qwen3.6-35b) — two different compute queues, both mid-decode. radv reported
`The CS has been cancelled because the context is lost. This context is
innocent.` for **both**, i.e. no faulting context was identified.

Memory ruled out with hard numbers: `amdgpu_vram_used_bytes` sat at
**76.06 GB / 96 GB, unchanged from 08:00 through 08:30** — flat across the
crash, ~20 GB headroom. GTT 2.66 GB / 124 GB. **Zero host OOM kills in 14
days**, so `-cram 0` from 2026-07-20 is still holding.

Real trigger is contention between the two co-resident models. GPU pinned
99–100% for 22 min, power flat-capped at exactly 119.05 W for 6 consecutive
samples, temp 68→76 °C (warm, not dangerous). Decisive discriminator: **the
35b's prompt eval stayed healthy at 194–204 t/s while its decode collapsed
to 5.94–14.73 t/s** (normal ~60); 27b decode 4.97 t/s (normal ~28). Prefill
fine + decode destroyed = queue contention, not power/thermal throttling
(which would drag both down together).

Two things this invalidates:
1. **`-ub 1024` did not work.** The 27b was running the 2026-07-21 mitigation
   and ring-timed-out anyway. That memory's own escape clause applies: "if
   timeouts persist at 1024, `-ub` is not the lever."
2. **The failure can be silent.** Unlike 07-21 (SIGABRT, loud, llama-swap logs
   "upstream exited"), both processes here **stayed alive and permanently
   wedged**, returning `500 decode() failed: vk::Queue::submit:
   ErrorDeviceLost` to every request. llama-server never rebuilds a lost
   Vulkan device, llama-swap saw live PIDs and **did not auto-restart** — the
   box served 500s to pi/Hermes/cron for ~20 min unnoticed.

**Changed:** No config change — recovery only, at user's request ("restart
both models"). `curl http://localhost:9292/unload` to kill both wedged
llama-servers, then one warm request per model to reload. Memory files
updated: `gpu_ring_timeout_device_lost_2026_07_21.md` (recurrence section,
`-ub` marked disproven, contention-vs-throttle discriminator, silent-failure
symptom, recovery recipe) and its `MEMORY.md` index line.

**Expected:** Service restored. No recurrence protection added — the actual
fix is still open and needs a user decision, since the candidates trade
against the throughput won in the 2026-07-06 co-residency pivot.

**Refs:** none external; diagnosis from `journalctl -k`, VictoriaMetrics
(`amdgpu_vram_used_bytes`, `amdgpu_power_watts`, `amdgpu_gpu_utilization_ratio`),
and `llama-stack/logs/qwen3.6-{27b,35b}.stderr.log`.

**Smoke test:** Both models reloaded and answered a real completion — 35b in
**32.8 s** (Q8 + `--no-mmap`), 27b in **15.7 s**. Post-restart state verified:
2 fresh llama-server PIDs, VRAM 71 GB / 96 GB, GTT 2.5 GB / 124 GB, and
`journalctl -k` since restart clean of `ring .* timeout` / `Out of memory`.

**OPEN — actual fix, needs user's call:** limit concurrency so 27b and 35b
never decode simultaneously; or drop co-residency (costs the 07-06 throughput
win); or raise `amdgpu.lockup_timeout`. Do NOT re-tune `-ub`.

---

## 2026-07-31 — 27b coder quality pass: IQ4_XS→Q6_K weights, q4_0→bf16 KV; Hindsight LLM pin repaired
**Observed:** Housekeeping review of the 27b coder's quantization, prompted by a r/LocalLLM thread the user shared. Three findings:
1. **The 27b was the outlier in the lineup on both legs.** It ran plain `IQ4_XS` weights (the only non-UD, non-Q6+ quant) and `q4_0/q4_0` KV (the only model still on 4-bit cache; 35b and gemma both ran `q8_0`). Neither was chosen for this model — both were leftovers from the 2026-05-22 OOM trimming.
2. **q4_0 KV was measurably the worst non-turbo setting available.** anbeeld.com benchmarks *this exact model at IQ4_XS/128k*: 99.9%-tail KLD precision vs a bf16 cache was **94.34% for q4_0** vs 98.52% for q8_0 and 98.13% for q8_0/q5_1. Mean PPL hides this entirely (every row within 0.01 PPL) — the damage is all in the rare-token tail, i.e. tool-call syntax and exact-match edit strings. Rotation (PR ggml-org/llama.cpp#21038, merged 2026-04-01) is in our pinned b9853, so those numbers apply to us directly.
3. **The 27b had not been loaded since 2026-07-21 17:46** (append-mode stderr log untouched across 4 boots). So the 07-21 `-ub 1024` ring-timeout mitigation was still completely unverified — the "0 ring timeouts since" reading was vacuous because the model never ran.

**Changed:**
- `~/llama-stack/config/llama-swap.yaml`, `qwen3.6-27b`: **`-hf ...:IQ4_XS` → `:Q6_K`** and **`--cache-type-k/v q4_0` → `bf16`**. Backup at `config/llama-swap.yaml.bak-20260731-pre-q6k`. Extensive in-file comment covering the evidence and the estimator trap below.
- `~/.hindsight/profiles/hermes.env`: **two dead pointers fixed together.** `HINDSIGHT_API_LLM_MODEL` was `qwen3.5-122b` (removed from the lineup 2026-07-19; llama-swap 404s it) → now the stable role alias **`extractor`**. `HINDSIGHT_API_LLM_BASE_URL` was `http://127.0.0.1:4000/v1` = **the LiteLLM proxy removed on 2026-07-13** — nothing has listened on :4000 since, so every LLM call the daemon made was failing at *connect*, not just at model resolution. → `http://127.0.0.1:9292/v1`. Backup at `hermes.env.bak-20260731`. Used the alias, not a concrete id, because concrete ids have now silently rotted twice (07-13 and again here).

**NOT changed / deliberately deferred:**
- **35b left resident** (`ttl: 0`, persistent group, `--no-mmap` intact). User asked about giving it a TTL instead; I flagged that this buys *no* peak-VRAM headroom — llama-swap has no VRAM-aware eviction and both groups are `swap: false`, so all three co-reside at peak regardless. It would only free budget via `swap: true` between 35b and 27b, which must not happen: the Hermes parent (35b) alternates with delegation children (27b) constantly, so that would mean a 38.7 GiB reload per hand-off. User elected to leave it. Still open as an idle-VRAM win.
- **No weights above Q6_K.** Rejected on evidence, not budget: on a 100-task SWE-bench-verified subset (wonderrico.github.io/local_llm_benchmark), 27B **BF16 weights scored 69 vs FP8 68**, and bf16-vs-fp8 KV was a dead tie — while the *same* model/quant swung **69→76 on vLLM vs SGLang** and 69→75 on 1 vs 2 GPUs. Above ~8-bit the curve is flat and infrastructure dominates. Caveat noted in-thread by u/Chromix_: ±6 is within noise at 100 unrepeated tasks.

**Expected:** Materially better tail behavior on tool calls and exact-match edits (94.34% → 100% on the KLD tail metric), at ~17% less decode speed. No change to the stall/contention failure mode, which was always the real problem.

**Refs:**
- Thread that started it: r/LocalLLM `1v7lbcf` — *"8-bit quants are generally lossless vs 16-bit source models. Is the same true for KV cache?"*. **Note the linked top comment ("don't use kv cache quantization, it gets dumb", 33 pts) is pure anecdote with no model/context/numbers, and is contradicted in-thread by a measured q4-vs-fp16 adherence test showing no difference at 56k.** Its value was the links, not the claim.
- anbeeld.com/articles/kv-cache-quantization-benchmarks-for-long-context — the real data. Qwen3.6-27B, IQ4_XS + Q5_K_S weights, PPL + KLD, 64k/128k. §17 also measures **bf16 > f16 against an f32 baseline** at identical size, which is why we use `bf16` and not `f16`.
- ggml-org/llama.cpp#21038 (KV rotation, merged 2026-04-01).

**Smoke test:**
- **Vulkan/RADV support probed empirically before committing** — launched the 27b with `bf16/bf16`, `q8_0/q5_1`, `q5_0/q4_1`, `q8_0/q8_0` at `-fa on`; all four loaded and served. No CPU fallback (a real risk — a user in the thread hit exactly that with q5 KV on CUDA/KoboldCPP).
- **Baseline captured on the day, before the change** (the 07-06 figures were 3 weeks and two config changes stale): IQ4_XS + q4_0 KV = **27.63 t/s, 87.9% MTP accept** — confirming the old "27.8 t/s / 85%" still held.
- After: **22.93 t/s, 92.8% MTP accept**, `/v1/models` lists all 3, live process args confirm `--cache-type-k bf16 --cache-type-v bf16` on the 27b and `q8_0/q8_0` unchanged on the other two.
- **VRAM measured with all three co-resident: 79.7 GiB of 96.** GPU floor (desktop, zero models) = 0.8 GiB. Per-model measured: 27b 31.5 | 35b 38.7 | gemma4-12b **8.7 (not the ~6.5 long carried in notes — corrected)**.
- Zero `ring .* timeout` and zero `Out of memory` kernel lines since the restart. **The `-ub 1024` mitigation is STILL not meaningfully verified** — these runs are short. It needs a real coding session.
- Hindsight: fixed values probe clean (`extractor` → resolves to `qwen3.6-35b`, HTTP 200 with real content), and the daemon boots to `Application startup complete` on 9177 with the new env.

**⚠️ GOTCHA — `gguf-vram-estimator.py` IS WRONG FOR HYBRID MODELS. This caused a wrong recommendation mid-session.**
It reported **32.5 GiB** for f16 KV on the 27b at 131k, on which I told the user bf16 KV "does not fit, would peak ~97.3/96" — and we initially shipped `q8_0/q5_1` as the affordable compromise. That was wrong. **Qwen3.6-27B is a hybrid model**: its GGUF carries `qwen35.ssm.conv_kernel` / `ssm.state_size` / `ssm.inner_size`, so a large share of its 65 blocks are state-space layers whose state is fixed-size and does *not* grow with context. The estimator assumes all 65 blocks hold a KV cache — `65 × 4 kv-heads × 256 × 2 × 2 B × 131072` reproduces its 32.5 GiB exactly — and therefore **overstates by ~4×**. Measured truth at 131072 ctx (floor subtracted): `q8_0/q5_1` KV ≈ **3.7 GiB**, `bf16` KV ≈ **8.0 GiB**. bf16 cost only **+4.4 GiB and −2.6% decode**, so we switched to it and took the full 100% tail fidelity. The same trap made the "expect ~19 t/s" bandwidth prediction too pessimistic (actual 22.9). **Rule: for any model with `ssm.*`/hybrid metadata, MEASURE VRAM — do not trust the estimator, and do not trust a naive weights-ratio speed prediction.**

---

## 2026-07-21 — GPU compute-ring hang root-caused; stderr logs made append-only; `-cram 0` confirmed
**Observed:** Investigation of "OOM, stuck requests, and 500 errors over the last 2 days" turned up three *separate* problems, not one.
1. **OOM — already fixed, now confirmed.** 15 kernel OOM kills across 2026-07-19/20 (10× `llama-server`, plus `code` and `chrome-headless` as collateral), last one 07-20 19:50 local. `-cram 0` landed 07-20 20:33. **Zero OOM kills since**, across ~17h of pre-reboot Jul 21 uptime under genuinely heavy agentic load (llama-swap served single generations of 1m58s / 2m54s / 5m15s with 700 KB response bodies, 27b and 35b both hot) plus the uptime since. This closes the "not yet confirmed under sustained real load" caveat on the 2026-07-20 entry below.
2. **"Stuck requests" — NEW root cause: amdgpu compute-ring hang → Vulkan device lost → llama-server SIGABRT.** Kernel logged `ring comp_1.3.x timeout ... Process llama-server` + `device wedged, but recovered through reset` three times on 07-21 (12:26:55, 12:30:20, 12:51:40 local). Two correlate **to the second** with llama-swap's `ReverseProxy read error ... unexpected EOF` → `group: running qwen3.6-27b exited: upstream exited unexpectedly`. The 12:30:20 one is the user-visible symptom: a request that **hung 3m22s and returned 18 bytes**. Confirmed via a 4.3 GB core dump left at `/app/core.13784` inside the llama-swap container — parsed its ELF notes: `fname=llama-server`, args `--port 10002` (= qwen3.6-27b), **`signal=6` (SIGABRT)** — not an OOM kill (SIGKILL, no core) and not a segfault. `ErrorDeviceLost` is present in the core's runtime memory and is **not** a static string in `libggml-vulkan.so` (verified: 0 occurrences via `strings` on a host-side copy), so it was materialized at runtime. Third ring reset recovered cleanly with no process death — the context loss is a race, not deterministic. **This is very likely the long-open "27b llama-server died mid-stream ~3×, repros clean, no backtrace" item** (see `llama_swap_stack` memory).
3. **500 errors — characterized, root cause NOT isolated.** 152× HTTP 500, all within 13:10–13:50 local on 07-21, ending 8 min before the reboot. All identical: 106-byte body, 24–53 ms, client `OpenAI/JS 6.26.0` from the host (172.20.0.1 = docker bridge gateway; llama-swap is the only container on `llama-stack_default`). Retry spacing 0/2/4/8s = OpenAI SDK backoff. **Interleaved with successful 200s** (e.g. a 79 KB/19.9s success mid-storm), so request-specific rejection, not a dead server. Ruled out by direct probe against the live stack: context overflow (`400`, 203 B, `exceed_context_size_error`), unknown model (`404`, 60 B, from llama-swap), missing/empty `messages` (`400`), bad role / orphan `tool` message / dangling `tool_calls` (all `200`). `gemma4-12b` also ruled out as the culprit — probed it live, `200` OK. **Left open**; see the diagnostic gap below for why it couldn't be attributed.

**Changed:**
- `~/llama-stack/config/llama-swap.yaml`: **`2>` → `2>>` on all three models.** The truncating redirect defeated the entire point of the 2026-07-13 I-3 change: llama-swap restarts a crashed model within 2–15s and the replacement process truncated the log, erasing the crash it existed to capture. All three stderr logs held only post-restart content, which is *why* problem 3 above could not be attributed to a model or a message.
- `~/llama-stack/config/llama-swap.yaml`: **`qwen3.6-27b` `-ub 2048 → 1024`** (`-b` left at 2048). `-ub` is the physical batch and governs single-dispatch size; amdgpu's compute-ring timeout is per-dispatch (10s default), so halving it shortens the longest dispatch. `-b` unchanged so upstream prefill batching is unaffected. 35b left at `-ub 2048` deliberately — its two observed deaths do not correlate with ring timeouts, so there's no evidence it needs this, and changing both at once would confound the test.
- Comments updated in-file for both changes; backup at `config/llama-swap.yaml.bak-20260721-pre-gpuhang`.
- **Ported to the public repo** (`~/Dev/strix-halo-llm-stack`, commit below) in its generalized idiom (role names `aux-fast`/`coder`/`orchestrator`, no internal product names) — **including `-cram 0`, which was never ported on 07-20** and had been sitting as undocumented drift. Also added a `host/tuning.md` section on ring timeouts vs OOM kills, a logrotate rule, and corrected the OOM section (it still recorded the superseded "background workload retry-loop" diagnosis as the cause).

**NOT changed / deliberately deferred:**
- **No llama.cpp/Mesa image bump.** Image stays pinned at `v234-vulkan-b9853`. Per `llama_swap_stack` memory, GPU detection is non-monotonic across bumps — probe `--list-devices` first. Not worth the risk until `-ub 1024` has been given a chance.
- **Not a kernel regression** — I initially suspected the 7.0.0-27 → 28 upgrade of 07-18 and was wrong: per-boot counts show **20 ring timeouts on the OLD kernel** (boot −3, Jul 12–18) vs 4 on the new one. Pre-existing, and if anything less frequent now. Do not re-litigate this.
- **logrotate rule not installed** — needs root and passwordless sudo isn't configured (see `sudo_no_password` memory). Documented in `host/tuning.md`; user to run the `sudo tee` one-liner. Note `logrotate.service` itself also failed to start on 07-20 and 07-21 — separate, unexamined.
- **4.3 GB core dump left in place** at `/app/core.13784` in the container's writable layer (disk is fine: 24% used, 1.4 TB free). Kept as evidence for now; delete when done with it.

**Expected:** The next mid-stream death leaves a readable stderr trail instead of erasing it. Ring timeouts on the coder become less frequent or stop. No change to OOM behavior (already fixed).

**Refs:**
- ggml-org/llama.cpp#22629 (`--cache-ram` not capacity-enforced on Linux) and #22372 (same hardware class, host RAM climbs to OOM) — both from the 07-20 entry, now cited in the public repo too.
- No external ref for the ring-timeout diagnosis; it's on-box forensics (kernel log ↔ llama-swap log ↔ core-dump ELF notes).

**Smoke test:**
- YAML validated before restart (`yaml.safe_load`, all 3 models parse).
- `docker compose restart llama-swap` → healthy, `/v1/models` lists all 3.
- **Append verified live** (this is the one that matters, since the previous form failed silently): fingerprinted `qwen3.6-27b.stderr.log` at 128,912 bytes / 1 recorded startup, triggered a fresh 27b load with a real request, re-checked → **130,667 bytes, first line unchanged, startup-count 1 → 2**. Old content survived a model restart, which it demonstrably did not before.
- `-ub 1024` confirmed on the **running process** (`ps -eo args` → `-b 2048 -ub 1024`), not just in config. (The stderr log doesn't print `n_ubatch` at this verbosity — process args are authoritative.)
- Public-repo config re-validated after porting: all 3 models `cram0=True append=True`, coder `ub=1024`; `docker-compose.yml` parses.
- **Still to verify under load:** whether `-ub 1024` actually eliminates the ring timeouts, and the prefill-throughput cost. Watch `journalctl -k | grep "ring .* timeout"` over the next few real coding sessions. If they persist, `-ub` is not the lever — revert to 2048 and look at draft-mtp graph length or `amdgpu.lockup_timeout` instead.
- **Gotcha for future sessions:** `strings` is NOT installed in the llama-swap container. An earlier `strings`-based search there returned empty and looked like a negative result; it was a false negative (`sh: strings: not found` on stderr). Copy the binary out with `docker cp` and inspect it host-side.

---

## 2026-07-20 — Host-RAM OOM root cause found + fixed: `-cram 0` on all 3 models
**Observed:** VS Code crashed repeatedly. `journalctl -k` showed 8 kernel OOM kills between 16:41–19:50, alternating `llama-server` (5x) with, for the first time, `code` itself (18:36, 19:50) and a `chrome-headless` subprocess spawned under VS Code's cgroup (19:50) — collateral damage once the ~30 GiB OS-visible RAM partition and 7.3 GiB swap were both fully exhausted. This continues the unresolved 2026-07-19 host-RAM-OOM incident (see `host_ram_oom_kills_2026_07_19` memory), now escalated to killing desktop apps, not just llama-server.
**Changed:** Root cause identified: llama-server's built-in host-RAM prompt/idle-slot cache (`-cram`/`--cache-ram`, default 8192 MiB *per model*, never explicitly set in `~/llama-stack/config/llama-swap.yaml`) — a different mechanism from `--cache-reuse 256` (live-GPU-KV-cache chunk reuse, left untouched). Added `-cram 0` to all 3 models' `cmd:` lines (qwen3.6-35b, qwen3.6-27b, gemma4-12b), disabling the host-RAM cache outright. Restarted `llama-swap` via `docker compose restart llama-swap`.
**Expected:** Eliminate the unbounded host-RAM growth (anon-RSS was climbing 11→23-26 GB per llama-server process under load) that was triggering the kernel OOM killer, without affecting `--cache-reuse`'s prefix-reuse performance benefit.
**Refs:**
- https://github.com/ggml-org/llama.cpp/issues/22629 (`--cache-ram` limit ineffective on Linux due to memory overcommit, causes OOM)
- https://github.com/ggml-org/llama.cpp/discussions/22372 (same Strix Halo hardware class, same agentic/subagent-driven workload shape, host RAM climbs until OOM-kill; their fix used the same `-cram`-family flags)
- llama-server README flag table (`tools/server/README.md`): `-cram, --cache-ram N` default 8192, `--cache-idle-slots` default-on and requires cache-ram
**Smoke test:** All 3 models' startup logs show `--cache-idle-slots requires --cache-ram, disabling`, confirming the flag took effect. All 3 answered a test `/v1/chat/completions` request with HTTP 200 post-restart. `free -h`: available RAM 5Gi→21Gi, swap-used 7.3Gi→5.7Gi (1.6Gi free) immediately after. **Not yet confirmed under sustained real (kalam/pi-hermes) agentic load** — re-check `journalctl -k --since today | grep "Out of memory"` over the next few sessions before considering this fully closed.

---

## 2026-07-19 — Aux model swap: gpt-oss-20b → Gemma 4 12B QAT (MTP) + pi-hermes-memory retry-loop fix
**Observed:** (1) Sessions in personal-website / pi-kalam repeatedly appeared "stuck" — Esc + nudge always fixed it. Root-caused to `pi-hermes-memory`'s auto-consolidation hitting the resident 35b with a hardcoded 60s timeout it could never meet under contention (task cancels spaced at machine-exact ~60s intervals in the 35b stderr log), retry-looping and starving the interactive session. (2) Separately, evaluated whether the aux model (gpt-oss-20b, MoE, ~3.6B active params) was underpowered for aux duties (compression, titles, background review) relative to available VRAM headroom.
**Changed:**
- `~/llama-stack/config/llama-swap.yaml`: replaced the `gpt-oss-20b` model block with `gemma4-12b` — `unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL` + auto-discovered MTP drafter (`mtp-gemma-4-12B-it.gguf`), `--spec-type draft-mtp --spec-draft-n-max 4`, sampling per unsloth's model card (`--temp 1.0 --top-p 0.95 --top-k 64`). `ondemand` group member list updated. Backup: `llama-swap.yaml.bak-20260719-pre-gemma`.
- `~/.hermes/config.yaml`: `gpt-oss-20b` → `gemma4-12b` at 4 refs (compression, title_generation, background_review, `custom_providers[Hermes].models` map).
- `~/.pi/agent/models.json`: aux entry replaced (`gpt-oss-20b` → `gemma4-12b`, `reasoning: false` — Gemma has no reasoning-effort/thinking-channel API surface the way Qwen/gpt-oss do).
- `~/.pi/agent/hermes-memory-config.json`: added `"llmModelOverride": "gemma4-12b"` (routes memory-consolidation child calls off the resident 35b onto the now-idle aux model) + `"consolidationTimeoutMs": 300000` (60s → 5min, so a legitimately slow consolidation doesn't retry-loop even under load).
**Expected:** Stronger aux-duty quality (dense 12B vs MoE-3.6B-active) at similar or smaller VRAM footprint; elimination of the 35b contention/retry-loop that was causing "stuck" sessions; freed headroom kept the door open for the parked Q8-27b upgrade (see `project_27b_q8_parked` memory).
**Refs:**
- MTP merged into llama.cpp mainline 2026-06-07: https://github.com/ggml-org/llama.cpp/pull/23398
- Open issue, quantized KV cache reportedly breaks Gemma4 MTP draft acceptance: https://github.com/ggml-org/llama.cpp/issues/24350
- unsloth MTP usage guide (contradicts the above — claims quantized KV works): https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/blob/main/MTP/README.md
- Strix Halo Gemma 4 benchmarks: https://akehir.com/blog/strix-halo-kubernetes-llm-gemma-4 , https://github.com/hogeheer499-commits/strix-halo-guide
**Smoke test:**
- `/v1/models` lists `gemma4-12b` alongside `qwen3.6-27b`/`qwen3.6-35b`; cold chat completion coherent.
- MTP draft acceptance measured at **60.3% (140/232) with q8_0 KV** — issue #24350's ~0%-acceptance report did NOT reproduce on this combo (unsloth's README claim held). KV stayed q8_0, no fallback needed.
- **Found and fixed a real gotcha during testing:** Gemma 4 thinks by default even with no system prompt and no `<|think|>` token — a bare request with `max_tokens=2000` burned the entire budget on `reasoning_content` with empty final `content` (`finish_reason: length`). `chat_template_kwargs.enable_thinking: false` suppresses it cleanly (verified: immediate clean content, no reasoning at all). Hermes' `compression` config already sent this kwarg; `title_generation` and `background_review` did NOT — added it to both. Also added `--reasoning-budget 8192` server-side as a ceiling/backstop for any caller that omits the kwarg (matches the pattern already used on `coder`/`orchestrator`).
- `bench/measure.py`: median TG 68.5 t/s (two of five prompts returned 0 tokens/content — same thinking-budget artifact, at the benchmark's default `max_tokens=256` with no `enable_thinking` override; not a regression, just the benchmark script needs the same kwarg to get clean numbers on this model in a future run).
- `bench/toolcall.py`: 8/10 first pass (2 failures on coding-agent cases, `<no tool call>`) — root-caused to the identical thinking-budget issue (`max_tokens=256`, no `enable_thinking` override in the script). Manually re-ran one failing case with `enable_thinking: false` added → correct tool call, `finish_reason: tool_calls`. Confirms 10/10 routing fidelity once the kwarg is present; the score deficit was a benchmark-harness gap, not a model capability gap.
- VRAM: all three models loaded simultaneously (35b resident + 27b + gemma4-12b on-demand) measured **68.7 GiB / 96 GiB carveout** — down from the prior lineup's ~77 GiB peak, more headroom than before.
- `qwen3.6-35b` stderr log: zero `cancel task` entries since restart (was seeing them at ~60s-spaced intervals under the old retry loop) — consistent with, though not yet a long-duration proof of, the `pi-hermes-memory` fix working.
- Hermes services (`hermes-dashboard`, `hermes-gateway`) restarted clean, journal free of model-not-found or config errors.
- **Unrelated pre-existing issue found, fixed later the same day (see the dedicated entry above, dated 2026-07-19 — Firecrawl LLM-extract endpoint fixed):** Firecrawl's LLM-extract (`firecrawl-api-1` container env) still pointed `OPENAI_BASE_URL` at `http://litellm:4000/v1` (removed 2026-07-13) and `MODEL_NAME=gpt-oss-20b` (now also stale). **Correction to the original phrasing here:** turned out this affects only Firecrawl's own built-in LLM-extract, which nothing on this box currently calls (Hermes' `web_extract` tool uses a separate code path) — so "silently broken" was accurate but the practical-impact claim was overstated; nothing was actually failing for a user in practice.

---

## 2026-07-19 — Firecrawl and SearXNG stripped back to defaults (post-shopping-pipeline cleanup)
**Observed:** After fixing the Firecrawl LLM-extract endpoint earlier the same day (see entry below) and discovering that customization was entirely unused, the user asked to remove any patches to Firecrawl's or SearXNG's default behavior outright — reacting to the realization that a lot of custom plumbing had accumulated on infra whose only real consumer (the shopping pipeline) was retired 2026-07-02. Confirmed via `git diff` against Firecrawl's upstream-committed `docker-compose.yaml` and the shipped `apps/api/.env.example` exactly what was custom vs. required self-hosting plumbing.
**Changed:**
- `~/firecrawl/.env`: removed `SEARXNG_ENDPOINT`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` entirely (none appear in the shipped `.env.example`; both were confirmed unused — see the entry below for why). Kept `PORT`/`HOST`/`REDIS_URL`/`BULL_AUTH_KEY`/`USE_DB_AUTHENTICATION=false` — required self-hosting plumbing, not "default behavior" in the sense meant here (flipping `USE_DB_AUTHENTICATION` to the shipped `true` default would require Supabase, which isn't configured, and would break the API outright for this single-user no-auth box — a judgment call, flagged rather than silently applied).
- `~/firecrawl/docker-compose.yaml`: removed the `ai-stack` network membership from the `api` service and the top-level `ai-stack: external: true` network declaration (was solely for the now-dead LiteLLM container-DNS lookup). Verified via `git diff` this was the *only* non-default change in the file besides the user's own standing `restart: unless-stopped` policy (see [[feedback-compose-restart-policy]]), which was deliberately left alone — it's a general infra preference, not a Firecrawl-specific behavior patch.
- `~/.searxng/settings.yml`: trimmed `search.formats` from `[html, json, csv, rss]` → `[html, json]`. User's call after being told only `json` is actually queried by anything (Hermes' `plugins/web/searxng/provider.py`, `format=json`); `html` stays for SearXNG's own web UI. **Did NOT** remove `json` itself despite the "restore defaults" framing — flagged first via AskUserQuestion, because SearXNG's shipped default is `[html]` only, and `json` is required by Hermes' own general-purpose `web_search_tool` (unrelated to Firecrawl or the shopping pipeline, used daily via the search-first policy). User confirmed keeping `json`.
- Confirmed no shopping-pipeline remnants remain beyond the already-archived tarball (`~/.hermes/profiles-shopper-mealplanner-archived-20260702.tar.gz`) — nothing further to remove there.
**Expected:** Firecrawl and SearXNG both running on essentially stock config; no dead/vestigial integration surface left over from the retired shopping pipeline or the LiteLLM removal.
**Refs:** none external — pure on-box archaeology (`git diff` against Firecrawl's own git history, `apps/api/.env.example`, `SELF_HOST.md`).
**Smoke test:**
- `git diff docker-compose.yaml` shows only the `restart: unless-stopped` lines remaining vs. upstream.
- Full `docker compose up -d --force-recreate` (all 5 firecrawl services) came up clean; basic `formats: ["markdown"]` scrape still succeeds (`success: true`, markdown present).
- `docker network inspect firecrawl_ai-stack` → "not found" (fully removed).
- SearXNG: `format=json` → 200, `format=html` → 200, `format=csv` → 403 (confirms the trim took effect and didn't over-restrict).
- Backups: `~/.searxng/settings.yml.bak-20260719-trim-formats`; Firecrawl `.env`/`docker-compose.yaml` changes are trivially reversible via `git diff`/`git checkout` (docker-compose.yaml is git-tracked) — no separate backup file needed for `.env` (gitignored, but the previous content is captured verbatim in the "Firecrawl LLM-extract endpoint fixed" entry below).

---

## 2026-07-19 — Firecrawl LLM-extract endpoint fixed (stale since the LiteLLM removal)
**Observed:** Discovered while writing the previous entry: `firecrawl-api-1`'s `.env` still had `OPENAI_BASE_URL=http://litellm:4000/v1` and `MODEL_NAME=gpt-oss-20b` — both dead references (LiteLLM removed 2026-07-13; `gpt-oss-20b` itself replaced by `gemma4-12b` earlier in this same session). On investigation, this specifically affects Firecrawl's own built-in LLM-extract feature (`/v1/scrape` with `formats: ["json"]`/`["extract"]`, or `/v1/extract`) — **not** the same thing as Hermes' `web_extract` tool, which does a plain `formats: ["markdown"]` scrape and then its own separate LLM summarization call via Hermes' own `auxiliary.web_extract` config (verified: `~/hermes-agent/plugins/web/firecrawl/provider.py` never requests `json`/`extract` formats). So this was a real dead config, but a latent one — nothing on this box currently exercises the broken path, unlike the initial (correct, but overstated) assumption that it had been "silently broken" in a way that was actively hurting anything.
**Changed:** `~/firecrawl/.env`: `OPENAI_BASE_URL` → `http://host.docker.internal:9292/v1` (the `api` container already has the `host.docker.internal` extra_hosts entry via its common-service anchor, same mechanism `SEARXNG_ENDPOINT` uses); `OPENAI_API_KEY` → `unused-llama-swap-direct` (llama-swap doesn't validate keys; the old LiteLLM virtual key was meaningless here anyway); `MODEL_NAME` → `qwen3.6-27b`, **not** `gemma4-12b` (see below for why). Also fixed a now-misleading comment in `~/firecrawl/docker-compose.yaml` explaining why the `api` service joins the `ai-stack` docker network (was "so it can reach LiteLLM by container DNS" — no longer true; left the network membership itself in place as harmless/possibly-useful-later, didn't remove it unprompted).
**Expected:** A working LLM-extract path if/when it's ever actually used (currently latent), on a config that no longer points at a dead hostname.
**Refs:** none external — this was pure on-box config archaeology (`.env.bak-20260706` showed a *third*, even older, Ollama-based config, confirming this `.env` has drifted through several dead states without ever being cleaned up).
**Smoke test:**
- Confirmed connectivity: `docker exec firecrawl-api-1 node -e "fetch('http://host.docker.internal:9292/health')..."` → `OK`.
- Confirmed the LLM is actually being called: `POST /v1/scrape` with `formats: ["json"]` shows `creditsUsed: 5` (LLM cost charged) and llama-swap/firecrawl logs show a real completion round-trip.
- **Tried `gemma4-12b` first (matching the just-swapped aux model) — it failed**: burned the entire structured-output call on its default-on reasoning trace with `"Request had format json, but there was no json field in the result"` in the firecrawl-api log. Root cause: Firecrawl's provider construction (`apps/api/src/lib/generic-ai.ts`) is a bare `createOpenAI({baseURL, apiKey})` — no hook to pass `chat_template_kwargs.enable_thinking: false` the way Hermes' config does. Switched `MODEL_NAME` to `qwen3.6-27b` instead — confirmed via a direct `/v1/chat/completions` test that it reasons but stays within a normal token budget and returns clean JSON in `content` (not swallowed by an unbounded reasoning channel).
- **`qwen3.6-27b` showed the identical symptom** (`no json field in the result`) on the exact same `/v1/scrape formats:["json"]` request — so the failure is NOT model-specific / NOT a thinking-mode issue after model swap. Ruled out `/v1/responses` endpoint absence (llama-server implements it correctly — verified directly, returns proper `reasoning`+`message` structured output). Root cause not fully isolated; most likely Firecrawl's "SmartScrape" `generateObject` structured-output pipeline expects a mode (native JSON-schema response_format, or tool-calling-based object generation) that doesn't fully line up with llama-server's implementation. Confirmed unrelated to today's endpoint routing fix: basic `formats: ["markdown"]` scraping (no LLM) works perfectly on the same container/request path.
- **Verdict:** endpoint-routing fix is correct and complete (dead host → live host, dead model → live model). The deeper SmartScrape/`json`-format gap is a separate, pre-existing-or-never-tested issue, orthogonal to the routing bug, on a code path nothing currently depends on. Left open, flagged, not chased further — would need AI-SDK-level tracing or Firecrawl source changes (against the repo's own CLAUDE.md test/PR workflow) to resolve properly.

---

## 2026-07-17 — Prompt-cache reuse + mmproj skip on coder and orchestrator
**Observed:** A 12h brownfield pi-kalam run spent 160min in prefill vs 181min generating — dominated by 15+ full 47–80k-token re-prefills (2–5 min of silent SSE each) after pi compactions destroyed the single slot's prompt cache. Also: the unsloth Qwen3.6 MTP repos silently auto-load a vision `mmproj` file that llama-server doesn't need for text-only use, and multimodal loading disables `cache_reuse` outright with only a log-line warning.
**Changed:** Added `--cache-reuse 256` and `--no-mmproj` to both `qwen3.6-27b` and `qwen3.6-35b` blocks in `llama-swap.yaml`.
**Expected:** Salvage unchanged prefix tokens (system prompt / tool schema / early history) across compactions even when the conversation middle diverges, cutting prefill time and GPU-pinned stream-abort windows. `--no-mmproj` also saves ~1 GiB per model (dead weight for a text-only client).
**Refs:** none captured at the time.
**Smoke test:** Verified `cache_reuse` behavior at runtime after restart — found it was STILL disabled ("not supported by this context"), a second incompatibility beyond the multimodal one (candidate cause: draft-mtp spec context or quantized KV). Flag left in place as inert-but-harmless (auto-activates if a future llama.cpp bump lifts the restriction); plain prefix caching confirmed still working (repeat request prefilled with 4 tokens processed).

---

## 2026-07-14 — Hindsight daemon crash-loop fixed
**Observed:** Hindsight (long-term memory daemon, port 9177) was crash-looping since 2026-07-13.
**Changed:** Root-caused to `huggingface-hub` having silently downgraded to 1.2.3 on 2026-07-09 against transformers' `>=1.5.0` requirement. Fixed by upgrading `huggingface-hub` to 1.23.0 in the hermes-agent venv.
**Expected:** Daemon comes back healthy; memory extraction resumes.
**Refs:** none captured.
**Smoke test:** Daemon confirmed healthy on port 9177; extraction verified live end-to-end. Also settled a parallel mystery: unexplained background `AsyncOpenAI` traffic observed during a separate hardening round turned out to be Hindsight, not an unknown process.

---

## 2026-07-13 — LiteLLM proxy removed; I-3 crash diagnostics added; Headroom compression removed
**Observed:** (1) LiteLLM had been sharing a Postgres instance with Honcho — a `down -v` on one stack wiped the other's keys, a coupling bug. (2) llama-server's stdout/stderr was being swallowed entirely by `docker logs llama-swap`, leaving silent crashes with zero trace. (3) Headroom (a prompt-compression layer added earlier) was formally evaluated and found to be lossy by design — 94.6% of its "savings" were destroyed content, including `read_file` output — and downstream retrieval couldn't reliably recover it.
**Changed:** (1) LiteLLM containers stopped and removed; pi, Hermes, and Hindsight now call llama-swap (`:9292`) directly. (2) Each model's `cmd:` wrapped as `/bin/sh -c 'exec /app/llama-server ... 2>/var/log/llama-swap/<model>.stderr.log'` — `exec` (not a trailing `echo $?`) because llama-swap kills the whole process group on a detected upstream failure, racing out any post-exit shell hook; a new `./logs:/var/log/llama-swap:rw` bind mount added to `docker-compose.yml`. (3) Headroom fully removed.
**Expected:** No more cross-stack DB coupling; a real diagnostic trail on the next silent crash; no more context silently getting destroyed by a "savings" layer that can't prove its drops are recoverable.
**Refs:** none captured.
**Smoke test:** (2) Verified live — full startup+request timing captured for all 3 models; a real `kill -9` crash drill on the resident 35b confirmed the stderr log survives and auto-recovery still completes in ~2–3s.

---

## 2026-07-12 — I4/I5/I6: coder reasoning-budget trim, on-demand --no-mmap removed, bigger batch size
**Observed:** Coder (27b) loops are mostly mechanical; a 16384-token reasoning budget let a routine lint-fix turn think for ~10 minutes at ~28 t/s with no quality payoff. Separately: on-demand models (27b, 20b) reload repeatedly, and `--no-mmap` on a reloading model spikes anonymous RAM each time — re-triggering the shape of the 2026-05-22 OOM-thrash incident on a host that was, at the time, down to ~9.5 GiB free with swap 75% used.
**Changed:** `--reasoning-budget` on `qwen3.6-27b` trimmed 16384 → 8192 (35b kept 16384 — design/decomposition work genuinely uses it). `--no-mmap` removed from the two on-demand model blocks (kept only on the resident 35b, which loads once at boot). `-b/-ub 2048` added to the coder (matching the 35b) for faster prefill of large diff/task prompts.
**Expected:** Faster average coder turns; lower host-RAM pressure from repeated on-demand reloads.
**Refs:** none captured.
**Smoke test:** not captured at the time.

---

## 2026-07-08 — Coder context bumped to 131k; TTL/timeout hardening
**Observed:** pi-kalam build/integration-check turns (npm install, Playwright, Lighthouse, git) have long tool-execution gaps with zero LLM calls, easily exceeding the coder's old 10-minute idle TTL. The 27b idle-evicted mid-workflow; the next turn's request landed on a torn-down connection right at the eviction race, surfacing in pi as "Request timed out" — confirmed by matching pi session-log timestamps 1:1 against llama-swap's "Unloading model, TTL of 600s reached" + immediate connection-refused. Separately, a 64k context window had been overflowed mid-turn by a big tool-chaining turn (~49k → 62k tokens in one turn; pi only threshold-compacts at turn boundaries).
**Changed:** `qwen3.6-27b`: `-c 65536 → 131072`, `ttl: 600 → 1800`, added `timeouts.connect: 60` (was default 30) as defense-in-depth. Paired with `pi`'s `compaction.reserveTokens` bumped so pi compacts at ~50% fill instead of closer to the wall.
**Expected:** No more mid-build eviction-race timeouts; no more mid-turn context overflow.
**Refs:** none captured.
**Smoke test:** Restarted `llama-swap`, verified healthy + all 3 models present in `/v1/models`. Live-checked `--no-mmap` was (at the time) present on all three model blocks — later corrected on 2026-07-19 when it was found to be 35b-only again (drifted, presumably during the 07-12 change above).

---

## 2026-06-24 / 2026-06-25 — MXFP4-on-RADV confirmed fixed; --no-mmap re-enabled deliberately
**Observed:** MXFP4 (gpt-oss family) had previously produced garbage output on Vulkan RADV at an older Mesa/llama.cpp build (b9093). kyuz0's community toolboxes recommend always using `--no-mmap` on Strix Halo for load-time and memory-locality reasons.
**Changed:** Confirmed MXFP4 runs clean on RADV at b9570+ (~78 t/s). Re-enabled `--no-mmap` on both models in the (pre-3-model) lineup, a conscious reversal of the caution taken after the 2026-05-22 OOM-thrash incident — user's call that the OOM-thrash had other root causes (dual-MTP residency + a too-tight carveout) that no longer applied.
**Expected:** Faster cold-loads (~25s vs ~56s), no output-quality regression.
**Refs:** kyuz0 Strix Halo toolbox recommendations (see `reference_mitkox` / radv_vs_rocm_benchmarks memory).
**Smoke test:** TG measured identical mmap vs no-mmap (~55 t/s) — the flag only affects load time, not steady-state throughput, on this box.

---

## 2026-06-07 (upstream) — MTP merged into llama.cpp mainline
Not a config change on this box, but the enabling event for MTP on Qwen3.6 and (as of 2026-06-07 same-day) Gemma4 family GGUFs. PR: https://github.com/ggml-org/llama.cpp/pull/23398. Relevant to every MTP-related entry above and below this line.

---

## 2026-05-24 — Context-overflow fallback chain deployed
**Observed:** `APIConnectionError`/`MidStreamFallbackError` surfacing from LiteLLM (at the time still in the stack) were being misdiagnosed as network/rate-limit issues. Actual cause: llama-server's own "Context size has been exceeded" error, which LiteLLM was just forwarding verbatim under a generic-looking exception type.
**Changed:** Deployed a fallback chain to the (then-lineup) 80b model for context-overflow cases.
**Expected:** Graceful degradation instead of a hard failure when a request genuinely exceeds a smaller model's context.
**Refs:** none captured.
**Smoke test:** not captured at the time.

---

## 2026-05-23 — 27b → 27b-MTP swap; sampling tune; OOM-thrash mitigations
**Observed:** A ~4-hour freeze on 2026-05-22 plus 4 kills/aborts on 2026-05-23, root-caused to a 96 GB VRAM carveout (32 GB reserved for the OS) combined with dual-MTP model residency overloading the tight OS-RAM partition. Separately, evaluating whether the plain (non-MTP) 27b should be swapped for an MTP-drafter variant.
**Changed:** Swapped `qwen3.6-27b` → the MTP variant. Post-trim Q4-KV + MTP config verified: 27b ~76% MTP accept, 35b ~71%. Context trimmed to 65K with Q4 KV cache and `ttl: 600` as the immediate OOM mitigation (later revised upward again on 2026-07-08 once the underlying pressure was believed resolved — see that entry; note the 2026-07-19 host-RAM-OOM incident showed the pressure hadn't fully gone away). Also tuned Qwen-thinking sampling parameters on both 27b and 35b, and set the compression/aux pipeline to thinking-off.
**Expected:** Faster coder decode via speculative drafting; stable long-running sessions without the OOM-thrash freeze recurring.
**Refs:** none captured.
**Smoke test:** MTP acceptance rates measured directly (76%/71% above) — established as the reference floor: "investigate" if any future MTP config drops acceptance below ~70% on real workload.

---

## 2026-05-19 — Coder/PA cutover: qwen3.6-27b + qwen3.6-35b MTP resident, qwen3-next-80b on-demand
**Observed:** Evaluating a 3-stage inference-optimization plan for splitting orchestrator/coder/on-demand-deep-reasoning roles across differently-sized models instead of one do-everything model.
**Changed:** All 3 planned stages executed. Final shape at the time: qwen3.6-27b + qwen3.6-35b MTP resident, qwen3-next-80b on-demand. Granite and bge-m3 models dropped from the lineup. MTP was attempted but rejected on the 80b (a since-superseded custom-fork MTP failure specific to that model — later confirmed unrelated to MTP-on-Qwen3.5/3.6 more generally, which works fine once MTP merged into llama.cpp mainline on 2026-05-16 pre-dating the 2026-06-07 Gemma4 merge above).
**Expected:** Better token economics — cheap fast coder + resident PA + an occasionally-used deep model, instead of one large resident model handling everything at high VRAM cost per request.
**Refs:** none captured.
**Smoke test:** Decode measured at 23 t/s, cold-load ~42s (for the 80b on-demand tier at the time).

---

## 2026-05-12 — Initial stack verification
**Observed:** N/A — first bring-up.
**Changed:** N/A — initial deployment of the (then) 3-model lineup on llama-swap + Vulkan/RADV.
**Expected:** A working local-inference baseline to iterate from.
**Refs:** none captured.
**Smoke test:** All three models confirmed to load on first request, stay resident, and produce valid output; end-to-end verified via LiteLLM (in the stack at the time, since removed 2026-07-13).

## 2026-08-06 (pm) — DS4 model review triaged; GRUB params + `--cache-reuse 256` added

**Observed.** A DS4-authored review of the inference stack proposed four changes.
Sources were real (verified 200 + fetched), but three of four conclusions were
wrong *for this box* because they came from external write-ups rather than our
own 08-05/08-06 benchmarks:
- "Move to HIP/ROCm for 21.31 t/s, Vulkan is 7.75–9.16 + OOM" — **inverted.** We
  measure **24.55 t/s on radv b10283**; our ROCm arm HUNG (b10288). Their Vulkan
  band matches **our own b10257 result (9.00 t/s)** — i.e. they benchmarked below
  the b10257→b10283 DeepSeek-V4 kernel gate and blamed the backend.
  (`GGML_HIP_MMQ_MFMA=ON` is also suspect on gfx1151 — MFMA is CDNA, RDNA uses WMMA.)
- "`-c 131072` costs 16–32 GB of KV, drop to IQ2_M" — **wrong by ~10×.** Measured
  16k=97.9 GiB vs 128k=98.4 GiB (~4.5 MiB/1k, MLA + q8_0). Restated unchanged in a
  second review after correction; `-c` also has **zero** effect on prefill speed.
- "No drafter loaded" — correct observation, but it was removed deliberately today
  (acceptance 0.583 on real 23k prompts, cost ~10 GiB against an 8.8 GiB floor).
- "`--cache-reuse 256` on Qwen but not DS4" — **correct** (I initially misread a
  truncated grep as absent; it is in the live yaml on all three Qwen models).

**Changed.**
1. `/etc/default/grub` — added `ttm.page_pool_size=32505856 amdgpu.gttsize=126976`
   (backup `grub.bak-*`, `update-grub` run, by the user w/ sudo). **NOT YET REBOOTED.**
2. `~/.config/systemd/user/ds4-server.service` — added `--cache-reuse 256` +
   rationale comment. `daemon-reload` done, unit verifies clean, still `disabled`.

**Expected.** Both are expected to be **no-ops**, and are staged as cheap
experiments rather than fixes. `gttsize` defaults to `-1` (derive from TTM pages
limit) which `ttm.pages_limit` already sets to the same 124 GiB — a 2026-08-05
session had already judged it redundant. `--cache-reuse` is not what provides
prefix reuse (LCP already does, hitting f_sim 0.80–0.995 in the live log); it only
covers post-prefix divergence via KV shifting, is recorded "inert" on one Qwen
model, and MLA commonly does not support shifting.

**Real finding (not from the review).** The live log holds a **100,489-token**
task: **751 s / 12.5 min to first token**, prefill decaying with depth
(160.7 t/s @63k → 133.7 @100k), decode 14.02 t/s. Prefill cost is driven by cache
**misses** (`launch_slot_` with no preceding LCP line), not partial hits. Next
lever is prompt-prefix stability, not flags.

**Refs.** `ds_4_standalone_server.md`, `gtt_memory_model_2026_08_06.md`,
`deepseek_v4_flash_evaluation.md`, `~/llama-stack/bench/deepseek-overnight-20260806.md`.

**Smoke-test (after reboot).**
```
cat /proc/cmdline                                     # both params present
sudo dmesg | grep -iE "amdgpu.*(GTT|gtt size)"        # did gtt_total move off 124.0 GiB?
curl :9292/unload && systemctl --user stop hindsight-daemon   # MANDATORY pre-start
systemctl --user start ds4-server                     # ~3.5 min cold load
docker logs ds4-server 2>&1 | grep -c "LCP similarity"   # miss count vs before
```

### 2026-08-06 (pm, post-reboot) — OUTCOME of the two staged experiments

**Observed.** Rebooted; both GRUB params present in `/proc/cmdline`.

**Result 1 — `amdgpu.gttsize=126976` is a CONFIRMED NO-OP. Verdict: revert.**
`amdgpu_gtt_total_bytes` = **133143986176 B = 124.0 GiB, byte-identical to
before.** `126976 MiB` == `32505856 pages × 4 KiB` == the same 133143986176 B —
the param set a ceiling identical to the one `ttm.pages_limit` already enforced,
and `gttsize` defaults to `-1` (derive from TTM) regardless.
**Process lesson: a prior session (2026-08-05) had ALREADY recorded "gttsize is
redundant — GTT already reports the full pool." That recorded conclusion should
have outweighed the DS4 review's "missing param" claim; instead a reboot was
spent re-testing it.** Weight our own written findings over an external model's
audit when they conflict. Revert command is in `gtt_memory_model_2026_08_06.md`.

**Result 2 — `--cache-reuse 256` on DS4: STILL UNRESOLVED, needs a real session.**
Flag confirmed on the live process. Cannot be judged yet — and note the
pre-change container log was **lost to `--rm` on restart**, so the miss-count
baseline is only the qualitative one in `ds4_standalone_server.md` (~4 cold
prefills in ~15 tasks). **To judge it: capture `docker logs ds4-server` to a file
BEFORE the next restart**, then compare `launch_slot_` lines lacking a preceding
`LCP similarity` line.

**Expected → actual on restart.** All clean: `n_ctx` 131072, alias
`deepseek-v4-flash`, GTT used **98.1 GiB** (matches the bare/no-sidecar 98.3 GiB
baseline), cold load **~180 s**, `oom_score_adj=1000` on the true `llama-server`
pid (score 1334), host RAM **20 GiB available** vs ~9 GiB with the sidecar.
`hindsight-daemon` had **resurrected on boot** as documented and was stopped
pre-start; llama-swap `/running` empty; no Hermes sockets on :9292.
⚠️ `llama-watchdog` is **inactive** post-reboot (it does not self-restart) — so
there is currently no device-lost cover or `:9177` probe.

**Refs.** `gtt_memory_model_2026_08_06.md`, `ds4_standalone_server.md`.

## 2026-08-06 (evening) — DS4-into-llama-swap ATTEMPTED, MEASURED, REVERTED; image b10200 → b10290

**Observed.** Goal was to retire the standalone `ds4-server.service` and make DS4
the sole production model in llama-swap, replacing both Qwens. The standing
instruction (`ds4_standalone_server.md`) was: probe
`ghcr.io/mostlygeek/llama-swap:v247-vulkan-b<N>` for N ≥ 10283 and fold it in
when one appears. **`v247-vulkan-b10290` now exists** (b10276 too) — the first
images above the long-standing b10257 ceiling.

**Changed.**
1. Image bumped `v245-vulkan-b10200` → **`v247-vulkan-b10290`**
   (`sha256:582c82f2629a0a842133074b1b835ae8c4f7cd7b36e7625b8aed926f30fdef2a`).
   GPU probe passed. **KEPT.**
2. Full DS4 production shape applied, then reverted: `deepseek-v4-flash` entry
   (ttl:0, `-c 131072`, no sidecar), two `exclusive: true` groups
   (`production` = DS4 + gemma4, `legacy-qwen` = the two Qwens), `classifier` +
   `extractor` aliases moved 35b → gemma4, `oom_score_adj: 1000` on the compose
   service. Preserved in `config/llama-swap.yaml.bak-20260806-ds4-attempt`.

**Result — 🔴 THE BUILD-NUMBER GATE DOES NOT EXIST. The prescribed test could
never have passed.** DS4 under mainline b10290 measured **10.2 t/s decode /
157.8 t/s prefill**, against **18.99 / 205** standalone — a **46% decode
regression**. Cause: **kyuz0's `vulkan-radv-performance` is a FORK.**

| image | version |
|---|---|
| kyuz0 vulkan-radv-performance | `10283 (b7b85da9c)` ← fork counter |
| mostlygeek/llama-swap | `10290 (c8e03ce81)` ← mainline |

Two fork-only features, both verified directly:
- **DeepSeek-V4 Vulkan kernels.** kyuz0: zero `not supported, set to disabled`
  lines. Mainline b10290 disables all four (`Lightning Indexer`, `fused DeepSeek
  V4 HC pre/comb/post`) and falls back to CPU. That quartet is the tell.
- **MUL_MAT_ID f16-B path** (`GGML_VK_MMID_F16B`) — string appears **0 times**
  in mainline's binary. MUL_MAT_ID is the MoE expert-routing matmul, i.e. the
  hot path for a 284B MoE.

**Mesa (26.1.5 vs 26.0.3) is NOT the explanation** — the missing binary string
proves the code is absent, not driver-gated. Do not spend a session A/B-ing it.
**Process lesson, same shape as this morning's:** a version number that looks
comparable across two publishers is not. Compare the *commit*, not the counter.

**Expected → actual on the image bump.** Gate passed on all three, MTP unchanged
(`bench/b10290-np1.md` vs `bench/gttflip-b10200-np1.md`): 35b PP 1135.90 →
1159.54 / TG 91.40 → 91.10; 27b PP 253.21 → 250.17 / TG 23.58 → 23.59;
gemma4 PP 716.77 → 720.67 / TG 93.75 → 94.64. All within noise.

**Bonus — `--cache-reuse 256` on DS4, the open item from the previous entry:
partially answered.** The standalone log WAS captured before restart this time
(`bench/ds4-log-2026-08-06.log`, the discipline the last entry asked for):
**22 `LCP similarity` hits / 27 `launch_slot_` = 5 cold prefills (18.5%)** vs the
~27% qualitative baseline. Suggestive, small sample, still not conclusive.

**Smoke-test after revert.** llama-swap on b10290, config inode matched
host (12726145, no bind-mount trap), `/v1/models` = the three Qwens/gemma4,
groups back to single `production`, aliases back on the 35b, GTT released to
0.4 GiB. `ds4-server.service` restarted standalone on :10097.
⚠️ `llama-watchdog` and `hindsight-daemon` remain **inactive** (stopped for the
DS4 protocol, as before) — no device-lost cover until restarted.

**Refs.** `ds4_standalone_server.md`, `llama_swap_stack.md`,
`infra_testing_queue.md`, `bench/b10290-np1.md`,
`config/llama-swap.yaml.bak-20260806-ds4-attempt`.

## 2026-08-06 (late) — kyuz0 image digest-pinned; llama-server router mode VERIFIED as a llama-swap replacement

**Observed.** Follow-on from the failed DS4-into-llama-swap attempt earlier the
same evening. Two questions: (1) the kyuz0 image was pinned by a MUTABLE TAG
while everything else in the stack is digest-pinned; (2) `llama-server` turns out
to have a **built-in router mode** (`--models-dir` / `--models-preset` /
`--models-max` / `--models-autoload`), which could replace llama-swap outright —
and running it from kyuz0's fork image would get the DS4 kernels too.

**Changed.**
1. `ds4-server.service` image pinned tag → digest
   `sha256:ca4c4c17d7357b6d69c787d734beb537bc5ae03279d557edfb4b40b1800a0211`
   (build 10283, 2026-08-04). Backup `ds4-server.service.bak-20260806-pre-digest-pin`.
   Rationale in-file: the tag is rebuilt frequently and tracks an unmerged
   third-party fork, so it can change the DeepSeek-V4 kernels with no signal.
2. New `config/models.ini` (router preset) + `bench/router_coresidency_test.py`.
   Router run on port 10098 in a throwaway container; **production untouched**,
   llama-swap idle throughout. Torn down after; DS4 restored standalone.

**Result — all three tests PASS, but the third only barely.**

- **T1 router starts / spawns children: PASS.** `cmd_child_to_router` protocol,
  child on its own port, `RestartCount: 0`. **Crash isolation preserved** — the
  main risk given our device-lost history. `load-on-startup = true` genuinely
  AUTO-LOADS, which is strictly better than llama-swap's `ttl: 0`.
  Preset translation is complete — `GET /models` returns the resolved child argv
  with every INI key mapped.
- **T2 fork kernels active: PASS.** Zero `not supported, set to disabled` lines
  (mainline b10290 emits four). Same harness, same prompt:

  | DS4 host | PP | TG |
  |---|---:|---:|
  | llama-swap, mainline b10290 | 157.8 | 10.2 |
  | router, kyuz0 fork | **250.7** | **18.8** |

  +59% prefill, +84% decode; router adds no overhead vs plain `docker run`.
- **T3 DS4 + gemma4 co-resident under load: PASS, NO MARGIN.**
  **109.9 GiB GTT / 8.5 GiB host RAM available.** gemma4 costs **~11.4 GiB**, not
  the ~8.7 the old three-model figure implied. Forced full prefill at 20,452
  tokens: PP 223.78, TG 16.40, guard never tripped.
  🔴 **This is the same band that OOM-killed twice this morning** (108.1 GiB /
  ~9 GiB free at 23,412 tokens). Untested at 23k+, 50k, 100k. Do not read the
  PASS as "safe".

**Two measurement traps hit — process lessons.**
1. First T3 run was sized at **13,049 tokens** when 23k was intended; the
   chars→tokens ratio had been assumed, not measured. A PASS there would have
   been meaningless. **Always print `prompt_n`.**
2. Second run sent 126,300 chars and reported **fewer** `prompt_n` than the
   shorter first run — prefix caching served ~12k. **`cache_prompt: false` alone
   does not force a cold prefill; prepend a random nonce too.**

**Other findings.** The router **auto-discovers every GGUF in `hf-cache` and
exposes it as loadable** — `/models` listed gpt-oss-120b, Qwen3.5-122B (73 GiB),
old DS4 IQ2_XXS, two stale 27b quants. **`--models-max` caps COUNT, not SIZE**;
one mistyped id pulls 73 GiB in beside DS4. Prune the cache before adopting.
Cold load was **~11 min** from a cold page cache (vs ~3 min warm).
Also: this **supersedes the combined-image Dockerfile** (queue item 3) — no
custom image needed. And the 08-04 objection that Cockpit needs pipx+Toolbx is
now **stale** (it runs natively on Docker); the surviving objection is that it
launches servers rather than routing between them.

**Smoke-test.** Router container removed, GTT released to 0.4 GiB, 118 GiB RAM
free. `ds4-server.service` restarted on the digest-pinned image, healthy on
:10097. llama-swap untouched on b10290 with the three Qwens/gemma4.
⚠️ `llama-watchdog` and `hindsight-daemon` still inactive.

**Refs.** `bench/router-mode-verification-20260806.md`,
`bench/router-test-20260806.log`, `config/models.ini`,
`llama_server_router_mode.md`, `ds4_standalone_server.md`.

## 2026-08-06 (night) — Router migration PHASE 0: pre-flight + E4B gate PASSED

**Observed.** Start of the autonomous overnight cutover to `llama-server` router
mode (plan: `~/.claude/plans/turning-to-plan-mode-temporal-acorn.md`). Autonomy
granted: Phases 0–8 unattended incl. reboot; **Phase 9 withheld** (nothing
deleted, rollback stays live). Guards: abort at GTT >118 GiB or MemAvailable
<4 GiB.

**Baseline** (`bench/baseline-pre-router-20260806.md`): GTT 98.4/124 GiB,
MemAvail 18 GiB, swap 3/7 used. llama-swap up with `{"running":[]}` (serving
nothing), ds4-server up on :10097. `hindsight-daemon` enabled-but-inactive,
`llama-watchdog` disabled+inactive, `ds4-server` **disabled** (nothing loads at boot).

**Changed.**
1. 27 consumer/config files backed up as `.bak-20260806-pre-router`.
2. **`git init` + initial commit on `~/llama-stack` (b8fb7d3), `~/observability`
   (3510b46), `~/openwebui` (f2c330c)** — none were version-controlled, so
   rollback had been `.bak` files only. `~/Dev/pi-kalam` and
   `~/Dev/automated-workflows` were already repos.
3. Downloaded `unsloth/gemma-4-E4B-it-qat-GGUF` UD-Q4_K_XL (3.92 GiB) +
   `MTP/mtp-gemma-4-E4B-it-Q8_0.gguf` (0.09 GiB).

**🟢 RESULT — the E4B gate PASSED, and it solves the memory margin.**
Tested standalone on :10098 in the pinned kyuz0 fork (build 10283) **with DS4
still loaded**, i.e. the real operating condition. `-c 65536 --parallel 4
--kv-unified --spec-type draft-mtp --no-mmproj -cram 0`:

| | GTT | MemAvail | decode |
|---|---:|---:|---:|
| DS4 + gemma4-**12b** (measured earlier today) | 109.9 GiB | **8.5 GiB** | 94.6 t/s |
| DS4 + gemma4-**E4B** | **103.3 GiB** | **14.7 GiB** | **114 t/s** |

**6.6 GiB reclaimed and it is FASTER.** 8.5 GiB was the band that OOM-killed
llama-server twice on 2026-08-06 morning; 14.7 GiB is real margin.
Cold prefill 858 t/s. 4 concurrent requests completed in **0.8 s wall with
`requests_deferred = 0`** — covers Hindsight's `RETAIN(2)+CONSOLIDATION(2)`
fan-out with headroom. `chat_template_kwargs.enable_thinking:false` honoured
(non-empty content, no reasoning burn).

⚠️ Benign warnings to expect at E4B load, do NOT treat as failure:
`Gemma4Assistant requires ctx_other to be set (this warning is normal during
memory fitting)` and `[spec] failed to measure draft model memory` — the draft
model loads successfully afterwards regardless. Log: `bench/e4b-gate-20260806.log`.

**🔴 Router surface finding: there is NO discovery-disable / allowlist flag.**
`--help` offers only `--models-dir`, `--models-preset`, `--models-max`,
`--models-autoload`. Auto-discovery comes from `LLAMA_CACHE`, so `--models-max`
capping COUNT-not-SIZE cannot be mitigated by a flag. **Mitigation adopted:
bind-mount only the two needed snapshot dirs read-only** instead of the whole
`hf-cache` — enforces it immediately and leaves the files in place for rollback.

**Smoke-test.** Gate container removed, GTT returned to 98.4 GiB, MemAvail 19.9,
`ds4-server` still healthy on :10097 throughout. Production untouched.

**Refs.** `bench/baseline-pre-router-20260806.md`, `bench/e4b-gate-20260806.log`,
`~/.claude/plans/turning-to-plan-mode-temporal-acorn.md`.

## 2026-08-06 (night) — Router migration PHASE 1-2: shadow verification, all 7 questions answered

**Observed.** Shadow router on :9393 alongside live llama-swap + ds4-server, to
answer the unknowns that could not be settled from `--help` or docs. Production
untouched throughout. Log: `bench/router-shadow-20260806.log`.

**Changed.** Wrote `config/models.ini` (production preset) and
`~/.config/systemd/user/llama-router.service`. Not yet started.

**Results — every answer favourable, two bugs caught before they mattered.**

| # | Question | Answer |
|---|---|---|
| Q1 | loaded-vs-discovered predicate | **`status.value`** is explicit (`unloaded`/`loading`/`loaded`), plus `status.failed` + `status.exit_code`. Child port is parseable from `status.args` (`--port`). |
| Q2 | load/unload shape | `POST /models/{load,unload}` with `{"model": "<id>"}` → `200 {"success":true}` |
| Q2c | **unload with NO argument** | **🟢 500 parse error, unloads NOTHING.** The llama-swap `GET /unload` landmine (which once took both production models down) **does not exist here** — it fails safe. |
| Q3 | does `/metrics?model=` pre-label? | **No** — bare `llamacpp:*` series. So the watchdog's `relabel()` can append `model=` without creating the duplicate label that would make VictoriaMetrics silently reject the whole exposition. |
| Q4 | child reachable from host? | **YES under `--network host`** — `/slots` returns the slot array, `/metrics` 11 series. This validates the `--network host` decision and lets the watchdog keep its direct-probe design unchanged. |
| Q5 | `sleep-idle-seconds` semantics | **MOOT** — final config keeps both models resident with no idle timer, so there is no idle-eviction path. Only explicit unload (image-gen wrapper). Re-test if ever enabled. |
| Q6 | crash isolation | **CONFIRMED.** `kill -9` the child (container-side, `docker exec -u 0` — a host-side kill gets `Operation not permitted`): router survived, marked `failed=True exit=1`, next request auto-reloaded it and answered correctly. |
| Q7 | listener bind timing | **Binds EARLY** — :9393 answered while the aux was still loading. **No 502-storm window at boot**, which was the main boot-ordering fear. |

**🟢 DISCOVERY GUARD VERIFIED WORKING.** `/models` listed **exactly 2** models.
The same router with the whole `hf-cache` mounted had listed **11**, including
`Qwen3.5-122B` (73 GiB). Mitigation = scoped bind-mounts of only the two model
repos + `LLAMA_CACHE` pointed at an empty dir. ⚠️ Mount the **repo root**, not
the snapshot dir — the .gguf files are symlinks into `../../blobs/` (aux) and
`../../../blobs/` (ds4), which only resolve if `blobs/` is in the same mount.

**🔴 Two bugs the shadow caught (this is why the phase exists):**
1. `model-draft` path omitted the **`MTP/`** subdirectory → aux hard-failed at
   load with `failed to load draft model`. Fixed in `models.ini`.
2. `--metrics` was missing from the draft `[*]` section → would have produced
   zero `llamacpp:*` series and silently killed the `ai-stack-llama-queue`
   alert (`noDataState: OK`). Added as `metrics = true`.

**Config decision — DS4 `parallel = 2 --kv-unified` (was 1).** Every consumer now
lands on one model, so single-slot head-of-line blocking is the biggest UX risk
in this migration. Nearly free: DS4's KV is ~4.5 MiB/1k, so a second 128k slot
costs ~0.5 GiB. `kv-unified` kept EXPLICIT — setting `-np` flips unified off and
splits n_ctx into 2 x 65536, landing exactly on Hermes' 64k floor.

**Smoke-test.** Shadow removed, GTT back to DS4-only, ds4-server healthy on
:10097 throughout. Benign E4B load warnings to expect (NOT failures):
`Gemma4Assistant requires ctx_other to be set` and
`[spec] failed to measure draft model memory`.

**Refs.** `bench/router-shadow-20260806.log`, `config/models.ini`,
`~/.config/systemd/user/llama-router.service`.

## 2026-08-06 (night) — Router migration PHASES 4-8: CUTOVER COMPLETE. llama-swap retired.

**Observed.** Phase 0-2 verification all green, so the cutover proceeded per the
agreed autonomy (Phases 0-8 unattended; **Phase 9 withheld — nothing deleted**).

**Changed — runtime.** `docker compose down` (llama-swap) + `systemctl --user
stop ds4-server` → GTT drained to 0.4 GiB in 3s → `systemctl --user enable --now
llama-router`. Both models resident.

| | GTT | MemAvail |
|---|---:|---:|
| old: llama-swap + gemma4-12b + DS4 standalone | 109.9 GiB | 8.5 GiB |
| **new: router + DS4 + gemma4-e4b** | **103.9 GiB** | **15.0 GiB** |

DS4 `parallel = 2 --kv-unified` cost **0.6 GiB**, matching the ~0.5 GiB estimate
derived from its ~4.5 MiB/1k KV. Aux was serving within **20 s** of unit start
while DS4 loaded — the `[gemma4-e4b]`-section-first ordering worked as designed.

**Changed — consumers (all repointed to :9292, concrete model ids).**
`~/.hermes/config.yaml` (single `DS4` provider; the `Hermes` provider carrying
qwen3.6-35b/27b + gemma4-12b DELETED; 4 auxiliary roles + delegation repointed),
`auth.json`, `~/.pi/agent/{models.json,settings.json,hermes-memory-config.json}`,
`~/Dev/pi-kalam/{src/config.ts,src/steps/technical-docs.ts,tests/config.test.ts}`,
3 per-project `.pi/kalam/config.json`, `~/openwebui/docker-compose.yml`,
`~/Dev/automated-workflows/{.env,llm_classifier.py,setup.sh}`,
`~/.hermes/cron/jobs.json`, `~/.hermes/hindsight/config.json`,
`~/.hindsight/profiles/hermes.env`, `hindsight-daemon.service`.

**🟢 EVERYTHING SUPPRESSED FOR THE DS4 SESSION IS BACK ON**, which was the whole
point of "production ready": `compression.enabled`, `title_generation.enabled`,
`curator.enabled`, `memory.memory_enabled`, `memory.user_profile_enabled`,
`skills.creation_nudge_interval: 10`, `disabled_toolsets: []`.

**Role split (the one refinement to "everything on DS4").** DS4 has 2 slots and
every consumer now lands on it, so the four `auxiliary.*` roles + Hindsight
retain/consolidation go to **gemma4-e4b**; `delegation` and Hindsight **reflect**
stay on **DS4** because they do real work the user waits on. Title-generation
fires on turn 1 of EVERY session — that role alone justifies keeping the aux
model resident rather than idle-evicted.

**🔴 ROLE ALIASES ARE GONE.** The router overwrites `--alias` with the INI
section name and reports `"aliases": []`. `classifier`/`extractor`/`memory-writer`
no longer exist; the three consumers pin concrete ids. **The mapping is recorded
in the `models.ini` header and in `hindsight-daemon.service`'s comment block —
if a model is swapped, those consumers must be edited BY HAND.** That is the
protection the aliases used to provide, and it is genuinely lost.

**Watchdog ported (Phase 6).** `running_models()` → `GET /models`
(`status.value=="loaded"`, child port from `status.args`); `resolve_upstream_host()`
no longer shells `docker inspect` (`--network host` ⇒ 127.0.0.1);
`recover()` → `POST /models/unload` + `POST /models/load` with an explicit
`{"model": ...}` and a non-empty guard retained; `RECOVERY_COOLDOWN` 900→1800
(DS4 reloads take up to 11 min); **new `PROBE_GRACE_SECONDS=300`** so a
just-loaded model is not "recovered" while still coming up. Metric names and the
`model=` label schema UNCHANGED, so **`scrape.yml` and `alert-rules.yaml` needed
zero edits**. Verified in VictoriaMetrics:
`up{job="llama-watchdog"}=1`, `llama_watchdog_models_loaded=2`,
`llama_watchdog_hindsight_up=1`, and `llamacpp:requests_deferred` present per
model — **that series has had no producer since 2026-07-13**. Zero duplicate
`model=` labels (the silent-VM-rejection failure mode).

**Image-gen wrapper (Phase 7).** `~/Dev/ai-image-gen/sd-exclusive.sh`: flock on
`$XDG_RUNTIME_DIR/llm-exclusive.lock`, stops `hindsight-daemon` for the duration
(reflect targets DS4 and runs unattended — it would auto-load 98 GiB mid-render),
unloads DS4, then **polls `mem_info_gtt_used` below 20 GiB rather than trusting
the unload's 200** (amdgpu frees asynchronously), and `trap`s EXIT/INT/TERM to
reload DS4 so a Ctrl-C cannot leave the box with no main model. Idempotent.

**Docs de-referenced (Phase 8).** `~/AGENTS.md` lineup block,
`~/.hermes/memories/{USER,MEMORY}.md`, and skills `homelab-conventions`,
`mlops-inference` (its architecture diagram still showed LiteLLM :4000 →
llama-swap → fixed ports 10002/10003 — all three now wrong), `kanban`, `email`,
`subagent-driven-development`. **Historical records deliberately NOT rewritten**
(`AI-INFRA-HISTORY.md`, `bench/*`, `cron/output/*`, `pi-kalam/ROUND*`) — they are
the forensic trail.

**Verification.** pi-kalam `npm test`: **1389 pass / 0 fail**. One test genuinely
had to change: `reviewer defaults to a different model than the coder` encoded an
invariant the user deliberately overrode (all 5 roles → DS4), so it was rewritten
to pin the *detection* (`reviewerSharesCoderModel`) rather than deleted.
Hindsight `{"status":"healthy","database":"connected"}`. Both models answer
through :9292. open-webui recreated.

**Refs.** `config/models.ini`, `~/.config/systemd/user/llama-router.service`,
`observability/stack/llama-watchdog/watchdog.py`,
`~/Dev/ai-image-gen/sd-exclusive.sh`, `bench/router-shadow-20260806.log`.

## 2026-08-06 (night) — Router migration VERIFIED UNDER REAL TRAFFIC + 2 open items

**Observed.** User resumed live Hermes chat on DS4 mid-verification, so the
remaining checks were done read-only. No reboot performed (would have evicted
DS4 mid-conversation) — see OPEN ITEMS.

**🟢 The role split is confirmed working under real load**, which was the main
unproven design claim:

| model | prompt tokens | predicted | deferred |
|---|---:|---:|---:|
| deepseek-v4-flash | 175,955 | 18,952 | 0 (1 in flight = live chat) |
| **gemma4-e4b** | **154,207** | **36,283** | 0 |

The aux is carrying comparable prompt volume to the main model — i.e. Hermes'
compression/title-generation/background-review and Hindsight retain really are
landing there and NOT queuing behind the user on DS4. `requests_deferred = 0` on
both while a real conversation was in flight.

**Watchdog healthy on both**: `probe_success=1`, `consecutive_failures=0`,
`hindsight_up=1`, `models_loaded=2`. **Grafana: no active alerts** — so
`ai-stack-watchdog-down` (which had been firing while the unit was disabled) has
resolved, and `ai-stack-model-wedged` / `ai-stack-llama-queue` have live
producers again.

⚠️ `llama_watchdog_probe_latency_seconds{deepseek-v4-flash} = 29.3s` — a 1-token
probe queuing behind the user's real request. Harmless at FAIL_THRESHOLD=3 /
PROBE_TIMEOUT=90, and `slot_busy_advancing()` covers the long-prefill case, but
it confirms probes on DS4 will routinely be slow. Do not "fix" it by lowering
PROBE_TIMEOUT.

**Memory under real load:** GTT 104.8 GiB, RAM 13 GiB available. Comfortable
against the 8.5 GiB band that OOM-killed twice this morning.

### 🔶 OPEN ITEM 1 — aux KV pressure (found, NOT fixed)
The `gemma4-e4b` child logged **2×** `failed to find a memory slot for batch of
size 5` / `failed to find free space in the KV cache, retrying with smaller
batch size`. Both self-recovered. **But this is the same warning shape that
preceded the 2026-08-01 device-lost wedge** (17× the same line on the 35b under
`-np` auto=4).

Cause: `[gemma4-e4b]` runs `parallel = 4` + `kv-unified = true` + `ctx-size =
65536`, so four concurrent aux calls share ONE 65,536-token buffer — ~16k each.
Hermes compression prompts plus Hindsight retain batches can exceed that.

**Fix (deliberately deferred — needs a router restart, which would evict DS4
mid-conversation):** raise `[gemma4-e4b] ctx-size` 65536 → 131072 in
`~/llama-stack/config/models.ini`. The E4B's whole footprint is only 4.9 GiB at
64k, so doubling ctx costs ~1-2 GiB against 13 GiB of headroom. Then
`systemctl --user restart llama-router` at a convenient moment and re-check
`~/.hermes/config.yaml` `auxiliary.compression.context_length` to match.
Alternative if memory ever tightens: drop `parallel` 4 → 2 and lower
`RETAIN_LLM_MAX_CONCURRENT` to match.

### 🔶 OPEN ITEM 2 — reboot never tested
`llama-router.service` is `enabled` and symlinked into `default.target.wants`
(verified), as are hindsight-daemon, llama-watchdog, hermes-gateway/dashboard
and amdgpu-exporter. But an actual unattended boot was **not** exercised.
Specific risk: `llama-router` is a USER unit that shells `docker run`, and it
cannot declare `After=docker.service` (a system unit) — so at boot it may fail
once and rely on `Restart=on-failure` + `RestartSec=30`. Designed for, not
proven. Verify on the next natural reboot:
`systemctl --user status llama-router` (expect NRestarts 0 or 1), both models
`loaded`, Hindsight healthy without a 502 storm.

**Rollback remains fully live** — Phase 9 was withheld: llama-swap's compose +
yaml, `ds4-server.service`, and every retired GGUF are still on disk.
`git log` in ~/llama-stack, ~/observability, ~/openwebui has the pre-migration
snapshot as the first commit.

**Refs.** `config/models.ini`, `llama-router.service`, `watchdog.py`,
`sd-exclusive.sh`, `bench/router-mode-verification-20260806.md`.

## 2026-08-06 (22:00) — Router migration: REBOOT VERIFIED. Open item 2 CLOSED.

**Observed.** User rebooted at 22:00:06 — the one verification the cutover could
not perform (it would have evicted DS4 mid-conversation). This closes OPEN ITEM 2
from the entry above.

**Result — fully unattended boot, everything green.**

| check | result |
|---|---|
| all units auto-started | ✅ llama-router, hindsight-daemon, llama-watchdog, hermes-gateway/dashboard, amdgpu-exporter |
| **docker.service race** | ✅ **NRestarts=0 on every unit.** The predicted failure did NOT occur. |
| aux serving during DS4 load | ✅ `gemma4-e4b` answered while DS4 was at 19% |
| **Hindsight 502-storm** | ✅ **zero** matching log lines; `{"status":"healthy","database":"connected"}` |
| watchdog | ✅ `models_loaded` 1 → 2 as DS4 came up; `hindsight_up=1` |
| **boot → DS4 ready** | **4 minutes** (better than the 3–11 min estimate) |
| both models answer | ✅ |
| memory | GTT 104.1 GiB, MemAvail 13.5 GiB |

**🔴 The docker.service concern was WRONG, and worth recording as such.**
`llama-router.service` is a USER unit that shells `docker run` and cannot declare
`After=docker.service` (a system unit). I expected one failed start plus a
`Restart=on-failure` retry. It started first try, NRestarts=0. The user-session
manager evidently comes up late enough that docker is already listening.
`Restart=on-failure` + `RestartSec=30` remain as insurance — do not remove them
on the strength of one clean boot.

**Also confirmed by this boot:** putting `[gemma4-e4b]` FIRST in `models.ini` is
load-bearing and works — the cheap model was serving within seconds while DS4
did its cold load, so no consumer saw an outage. And `load-on-startup = true`
genuinely auto-loads, which llama-swap's `ttl: 0` never did (it required a
manual warm-up loop after every restart).

⚠️ `probe_success` was present for `gemma4-e4b` but not yet for
`deepseek-v4-flash` immediately after load — that is `PROBE_GRACE_SECONDS=300`
working as designed, not a fault. Expect a ~5 min gap after any DS4 load before
its probe series appears.

**Remaining open item: aux KV pressure** (`[gemma4-e4b]` `parallel 4` +
`ctx-size 65536`) — unchanged, still needs the ctx-size raise to 131072 at a
convenient restart.

**Refs.** `config/models.ini`, `~/.config/systemd/user/llama-router.service`.

## 2026-08-06 (22:3x) — aux ctx-size 65536 → 131072. LAST OPEN ITEM CLOSED.

**Observed.** The `gemma4-e4b` child had logged 2x `failed to find free space in
the KV cache, retrying with smaller batch size` — four concurrent aux calls
(`parallel 4` + `kv-unified`) sharing ONE 65,536-token buffer, ~16k each, which
Hermes compression plus Hindsight retain batches exceeded. Self-recovering, but
the same warning shape that preceded the 2026-08-01 device-lost wedge.

**Changed — 3-way ctx sync, all four sites** (per [[pi-models-json-context-sync]]):
- `~/llama-stack/config/models.ini` `[gemma4-e4b] ctx-size` 65536 → **131072**
- `~/.hermes/config.yaml` `auxiliary.compression.context_length` → 131072
- `~/.hermes/config.yaml` `custom_providers[DS4].models.gemma4-e4b.context_length` → 131072
- `~/.pi/agent/models.json` gemma4-e4b `contextWindow` → 131072
(pi-kalam needed no change — all 5 of its roles are on DS4.)
Backups `.bak-20260806-pre-auxctx`.

**⚠️ Restart discipline that mattered.** `requests_processing` on DS4 was 1 for
several minutes. Rather than assume a stuck counter, checked `/slots` on the
child: `n_prompt_tokens_processed` climbed **53,238 → 55,286 in 8 s** (~256 t/s)
with `n_decoded` still empty — i.e. a genuine 55k-token PREFILL in flight, not a
hang. Waited for it (cleared in 90 s) instead of destroying it. **This is the
`/slots` diagnostic from [[qwen-thinking-runaway-mitigations]] applied to a
restart decision — use it before any restart, `requests_processing` alone cannot
distinguish busy from wedged.**

Incidentally confirmed `parallel = 2` on DS4 earning its keep: slot 1 carried the
55k prefill while slot 0 stayed free. Under `parallel = 1` that prefill would
have blocked every consumer on the box.

**Result.**

| | before fix | after fix |
|---|---:|---:|
| aux buffer | 4 slots / 65,536 shared | **4 slots / 131,072 shared** |
| GTT (both loaded) | 104.1 GiB | **105.3 GiB** |
| host RAM available | 13.5 GiB | **12.5 GiB** |
| KV-pressure warnings | 2 | **0** |

Cost **~1.2 GiB**, matching the ~1 GiB estimate. Both models answer; watchdog
`models_loaded=2`, `hindsight_up=1`; `NRestarts=0`.

**All open items from the router migration are now closed.** Rollback still live
(Phase 9 never run): llama-swap compose + yaml, `ds4-server.service` and every
retired GGUF remain on disk; ~/llama-stack, ~/observability, ~/openwebui are git
repos with the pre-migration state as their first commit.

## 2026-08-06 (23:xx) — PHASE 9: boats burned. llama-swap fully removed.

**Observed.** User elected to run Phase 9 the same night rather than after the
"several days stable" the plan called for. Concern raised, request reaffirmed, so
it proceeded. Mitigating factor that did not exist when the plan was written: the
three config dirs became git repos in Phase 0, so most of Phase 9 is recoverable.

**Changed.**
- **Removed** `~/llama-stack/docker-compose.yml`, `config/llama-swap.yaml` + all
  14 `.bak-*` (recoverable at commit `4e6d8c1`).
- **Removed** `~/.config/systemd/user/ds4-server.service` (`.bak-20260806-pre-digest-pin`
  kept). `list-unit-files 'ds4*'` → 0.
- **Removed** `~/observability/litellm/` — dead since 2026-07-13, still held :9292
  and both Qwen ids.
- **Removed all 17 llama-swap docker images** (~10 GB). Two needed `-f`: tags
  `:vulkan` and `:v226-vulkan-b9628` share one image id → plain `rmi` gives
  `image is referenced in multiple repositories`.
- **Quarantined 7 model repos → `hf-cache-archive/` (341 GB)**: gpt-oss-120b/20b,
  DeepSeek **IQ2_XXS**, gemma-4-12B, Qwen3.5-122B, Qwen3.6-27B/35B. `mv` on the
  same filesystem — instant, reversible. **Deletion deliberately NOT done**:
  re-download is many hours at ~14 MB/s and disk is only 46% used.
- **Bench**: `mesa_baseline.py` PORTED (MODELS → router lineup; :9292 unchanged
  since the router took the port). 8 llama-swap/Qwen-pinned harnesses moved to
  `bench/archive-prerouter/` with a README.
- **Public repo** `~/Dev/strix-halo-llm-stack` ported (`0d037ff`) — yaml+compose →
  `config/models.ini` + `systemd/llama-router.service`, watchdog updated, README
  banner on the fork-kernel reason. Host paths / digest / snapshot hashes scrubbed
  to placeholders (verified 0 leaks). ⚠️ **Committed locally, NOT pushed** —
  publishing is the user's call.

**🔴 Three traps hit. All will recur.**
1. **`hub/models--unsloth--Qwen3.6-27B-MTP-GGUF` was root-owned** (container
   download, July) → `mv` failed, no passwordless sudo. Fixed with the documented
   pattern: `docker run --rm -u 0 -v <src>:/cache -v <dst>:/archive redis:alpine mv ...`
2. **There were TWO DeepSeek repos.** `hf-cache/models--unsloth--DeepSeek...`
   (109 GB, IQ3_XXS **IN USE** + dspark) and `hf-cache/hub/models--unsloth--DeepSeek...`
   (85 GB, retired IQ2_XXS). Only the `hub/` one was archived. **Verified which
   was actually bind-mounted before moving either** — matching on the repo name
   alone would have taken production down.
3. 🔴 **`git add -A` in `~/llama-stack` tried to ingest the 341 GB archive** and
   timed out — `.gitignore` had `hf-cache/` but not `hf-cache-archive/`. It left
   **4.7 GB of loose objects** behind. Fixed: `git rm -r --cached .`, add
   `hf-cache-archive/` + `empty-cache/` to `.gitignore`, re-add, then
   `git reflog expire --expire=now --all && git gc --prune=now` → **.git 4.7 GB →
   332 KB**, all 4 commits intact. **Lesson: when creating a sibling directory
   next to an ignored one, extend .gitignore in the SAME step.**

**Kept deliberately:** the dspark sidecar (10 GB) inside the live DS4 repo —
absent from `models.ini` by choice, cheap insurance if host-RAM headroom is ever
solved another way.

**Smoke-test after every destructive step**, and again at the end: both models
`loaded` and answering, in-container mounts still resolve, `llama-router` /
`hindsight-daemon` / `llama-watchdog` / `hermes-gateway` all active,
`models_loaded=2`, `hindsight_up=1`.

**Rollback status — REDUCED, not gone.** git holds every config (`llama-stack`
`15af92b`, plus `observability`, `openwebui`); GGUFs are archived not deleted;
`ds4-server.service` has a `.bak`. Genuinely gone: the llama-swap docker images
(re-pullable by digest — the ladder is in git history at `4e6d8c1`).

## 2026-08-06 (23:xx) — Public repo pushed

`~/Dev/strix-halo-llm-stack` pushed to github.com/dinesh-se/strix-halo-llm-stack
(`8c129b3..d8938f6`, master). +527/-721 across 12 files: llama-swap yaml + compose
DELETED, `config/models.ini` + `systemd/llama-router.service` added, watchdog
ported, Qwen-pinned bench harnesses dropped, README banner explaining the
fork-kernel reason DS4 forced this.

**Pre-push safety scan (do this every time before publishing this repo):**
- secret patterns (`sk-*`, Telegram bot-token shape, private keys, `api_key=`,
  `TELEGRAM_*`) → **0 matches**
- `watchdog.env` (real tokens) **not tracked** — only `watchdog.env.example`
- host identifiers (`dinesh-se`, chat id, image digest) → 1 found and fixed:
  `bench/mesa_baseline.py` had `LOG_DIR = "/home/dinesh-se/llama-stack/logs"`,
  now `os.environ.get("LLAMA_LOG_DIR", os.path.expanduser("~/llama-stack/logs"))`.
  ⚠️ It leaked because the file was COPIED from the live host copy — anything
  copied out of `~/llama-stack` or `~/observability` must be re-scanned, the
  earlier placeholder scrub of the unit file did not cover it.
- snapshot hashes and the pinned image digest in the published unit/preset are
  placeholders (`YOUR_SNAPSHOT_HASH`, `PIN_YOUR_OWN_DIGEST`, `/home/YOUR_USER`).

## 2026-08-07 (10:4x) — Retired 35B model promoted to resident daily driver; router restart was the missing step

**Observed.** Every new Hermes session showed `the retired 35b model` as the default
model and then failed on `openai.BadRequestError: HTTP 400 - model
'the retired 35b model' not found` from `http://127.0.0.1:9292/v1`. The router's
`/v1/models` listed only `deepseek-v4-flash` + `gemma4-e4b`.

**Root cause — config edited, nothing reloaded.** The morning's work was
complete on disk (models.ini `[the retired 35b model]` + DS4 `load-on-startup = false`,
a third bind-mount `hf-cache/the retired 35b model-dl:/models/the retired 35b model:ro`, `--models-max 2 → 3`,
Hermes `model.default`, `swap-model.sh`) but **`daemon-reload` + `restart
llama-router` were never run.** The container had been up since 2026-08-06 22:10
serving the previous night's preset. `systemctl --user cat llama-router` said so
outright: *"changed on disk, the version systemd has loaded is outdated."*
⚠️ models.ini is bind-mounted into a RUNNING container — editing it changes
nothing until a child respawns.

**Changed.**
1. `~/.hermes/config.yaml` — repaired a corrupt key: `the retired 35b model-1: {0-35b:
   {context_length}}` → `the retired 35b model: {context_length: 131072}`. A dotted-path
   setter had split the id on the `.` in "1.0". Valid YAML, silently wrong.
2. `~/.pi/agent/models.json` — added the missing `the retired 35b model` entry
   (contextWindow 131072, reasoning true); pi had never been synced.
3. `~/llama-stack/swap-model.sh` — `status()` was a `SyntaxError`
   (`f"{m[\"id\"]}"`), masked by `2>/dev/null || echo "(could not query
   router)"`. Now uses `.format()`. `_model_state` was fine, so swap logic worked.
4. `systemctl --user daemon-reload && systemctl --user restart llama-router`.

**Expected / measured.** the retired 35b model **loaded in ~30 s** (vs DS4's 3–11 min).
GTT **106.4 → 43.5 GiB**; MemAvailable **7.9 → 71 GiB** — the host-RAM band that
drove the 07-19 and 08-06 OOM kills is far away while the retired 35b model is resident.
`requests_deferred = 0` on both; watchdog `models_loaded=2`, `hindsight_up=1`;
router/hermes-gateway/llama-watchdog/hindsight-daemon all active.

**Smoke-test.** `POST /v1/chat/completions` model=the retired 35b model → `"the retired 35b model
online"` in 3.6 s, with `reasoning_content` populated (always-on reasoning, as
designed). `swap-model.sh status` now prints all three with correct states.

**Refs.** Checked `/slots` (`is_processing: false` on both DS4 slots) BEFORE
restarting, per the standing rule. Backup of the pre-change preset is
`config/models.ini.bak-20260807-0945-pre-the retired 35b model`.

**Not done / user's call.** `swap-model.sh ds4` was NOT exercised — it evicts
the retired 35b model and costs 3–11 min to load DS4. The load path shares `_model_state` with
the verified the retired 35b model path, but the DS4 round trip is untested since the change.
`~/Dev/strix-halo-llm-stack` has NOT been updated with the the retired 35b model lineup.

## 2026-08-07 (15:0x) — Watchdog killed a healthy DS4; the retired 35b model was OOM-killed. Both root-caused, Batch A shipped.

**Observed.** Two independent faults, neither correctly reported by alerting.

*(a) 14:08–14:14 — `llama-watchdog` unloaded a perfectly healthy DS4.* A context
compaction rewrote the prompt prefix, so LCP similarity against the cached
76,031-token slot fell under the 0.10 threshold and the router picked a fresh
slot **by LRU** (`14:06:14 selected slot by LRU, t_last = 57937652671`). That
began a cold prefill of ~75.7k tokens which was **still only 92% done after 468 s**
(243 t/s at chunk 1 → 148 t/s at chunk 34 — prefill throughput decays as ctx
grows). With `--parallel 2` + continuous batching, the peer slot gets ~1 decode
step per 17 s prefill chunk, so the 90 s probe could not land a single token.
Three "failures" → unload at 14:14:03, **destroying 468 s of work at 92%**. The
apparent self-recovery at 14:14:18 was the ROUTER autoloading DS4 for the next
client request; the watchdog's own recovery had already failed.

*(b) 11:01:49 — kernel OOM-killed the retired 35b model.* A Hermes `/model` switch made the next
chat request trigger `ensure_model` for DS4 (11:00:19). Nothing evicted the retired 35b model:
`Out of memory: Killed process 1139320 (llama-server) total-vm:44839744kB`. The
router still reports the retired 35b model `failed=true exit_code=1` from this.

**Root causes (4 distinct bugs).**
1. `swap-model.sh` unload was `POST /models/{id}/unload` → **HTTP 404**. Router
   lifecycle routes are **body-form only** (`POST /models/unload {"model":id}`).
   Swallowed by `|| true`, then `return 0` anyway — so the script NEVER evicted
   anything. Undetected because the 10:4x entry below notes `swap-model.sh ds4`
   was never exercised after that morning's edit.
2. `recover()` posted unload→load with **zero delay**; the router's unload is
   async, so load always got `400 "model is already running"`. It had never
   worked, on any recovery.
3. `slots_progress()` used a **10 s** timeout on `/slots`, which a child mid-
   17 s-prefill-chunk cannot answer. One `None` → scored as a wedge. 4 successes
   vs 6 failures across 2026-08-07.
4. The 60 s probe is a 2-token raw `/completion` (`f_sim ≈ 0` → LRU), so it landed
   on whichever slot was idle and **overwrote its cached prefix** — measured live:
   DS4 slot 1 `n_prompt 294 → 1`, gemma4 slot 3 `2443 → 1`.
5. Not a bug but the enabling condition: `--models-max` caps model COUNT, not
   size, and the router has no memory awareness.
   `HINDSIGHT_API_REFLECT_LLM_MODEL=deepseek-v4-flash` means a background reflect
   can trigger that ~98 GiB autoload **unattended**.

**Changed (Batch A — watchdog restart only, no model touched).**
1. `~/llama-stack/swap-model.sh` — body-form `_post_model()` for load+unload;
   every failure path now FATAL (`|| exit 1`); explicit `load()`; async-unload
   wait requiring a literal `unloaded`; `absent` state so a typo'd id can never
   read as "safely evicted"; `_model_status` returns `value` + sticky `failed`
   separately (the retired 35b model proves `failed` is NOT a liveness signal).
2. `watchdog.py` — `SLOTS_TIMEOUT` 10 → **30 s**; new `slots_readable()` making an
   unreadable `/slots` **INCONCLUSIVE**, capped by `MAX_INCONCLUSIVE=5` (inverts
   the old rule — a device-lost server fails decode FAST and still answers
   `/slots` with frozen counters, so unreadable means BUSY, not dead);
   `wait_unloaded()` before reload; probe pinned via `id_slot` to the last slot;
   new `heavy_mutex_loop()`.
3. **Heavy-model mutex** (the OOM fix): 3 s poll, evicts the resident incumbent
   the moment a second heavy model enters `loading`/`loaded`. Deliberately NOT
   `--no-models-autoload`, which would break Hermes `/model`. `gemma4-e4b` is
   filtered out in BOTH `heavy_states()` and `pick_eviction_victim()`.
4. New metrics: `llama_watchdog_probe_inconclusive_total`,
   `_heavy_evictions_total`, `_heavy_coresident`. All existing names unchanged,
   so scrape.yml / alert-rules.yaml needed no edits.

**Expected / measured.** `id_slot` **confirmed honoured** on this build: one
`--once` probe moved only DS4 slot 1 and gemma4 slot 3; slots 0/1/2 untouched.
22 pure-logic tests pass (scratchpad `test_watchdog_logic.py`), incl. the real
14:08 signature reading BUSY and frozen-counters still reading WEDGED. Post-
restart: both probes ok, 22 `llamacpp:` series relayed, `hindsight_up=1`,
`heavy_coresident=0`, `heavy_evictions_total=0`.

**Smoke-test.** `swap-model.sh status` correct. Guard paths exercised live:
already-unloaded → rc 0 no-op; typo'd id → rc 1 fatal; load of already-loaded →
rc 0; load of bogus → rc 1 with the 404 explained.

**Refs.** Router route semantics measured directly:
`/models/{id}/{load,unload}` → 404; `/models/load` → 400 "model is already
running"; `/models/unload` → 400 "model is not running" / "model is not found".

**NOT DONE — Batch B, needs a router restart, user-triggered.**
`slot-prompt-similarity = 0.5` on `[deepseek-v4-flash]`; optional `parallel`
2 → 3 so the pinned probe slot is not stolen; fix the stale models.ini header
claiming `classifier -> deepseek-v4-flash` (that .env now reads
`LLM_MODEL=the retired 35b model`); `daemon-reload && restart llama-router`; then
`swap-model.sh the retired 35b model` — **the retired 35b model has been down since 11:01:49**. The mutex
end-to-end test is folded into that window.

⚠️ `~/llama-stack/swap-model.sh` is **UNTRACKED** by git — `git diff` is not a
rollback for it. Pre-change backups: scratchpad `swap-model.sh.bak` /
`watchdog.py.bak`. Both files ported to `~/Dev/strix-halo-llm-stack`
(uncommitted, unpushed).

## 2026-08-07 (15:2x) — Batch B applied: DS4 -sps 0.5 + -np 3, router restarted, heavy-mutex PROVEN against a live autoload

**Observed.** Batch A (entry above) shipped the watchdog/script fixes but left
three things needing a router restart, and left the heavy-model mutex unproven
against a real swap. DS4 confirmed idle first (`/slots` on the CHILD: 0 of 2
processing; `requests_deferred=0`), per the standing rule.

**Changed.** `~/llama-stack/config/models.ini` (backup
`models.ini.bak-20260807-1520-pre-batchb`):
1. `[deepseek-v4-flash] slot-prompt-similarity = 0.5` — protects the long-prefix
   slot from the many SHORT callers on this model. Documented in-file that this
   does NOT fix a compaction: a compaction rewrites the prefix so f_sim collapses
   legitimately (MEASURED 0.992 → <0.10 in one turn) and a re-prefill is
   unavoidable.
2. `[deepseek-v4-flash] parallel 2 → 3`. The watchdog now pins its probe to the
   LAST slot, which costs that slot's CACHE RETENTION every 60 s (not its
   availability — the probe releases in ~0.08 s, so this is NOT head-of-line
   blocking). At `-np 2` that left exactly one slot able to hold a conversation
   across turns; at 3, two can.
3. Fixed the stale header: `classifier` maps to **the retired 35b model**, not
   deepseek-v4-flash (`~/Dev/automated-workflows/.env` reads
   `LLM_MODEL=the retired 35b model`). Added a warning that `extractor`
   (`HINDSIGHT_API_REFLECT_LLM_MODEL=deepseek-v4-flash`) is a LIVE unattended
   OOM path.
4. `daemon-reload && restart llama-router`.

`[gemma4-e4b] parallel` deliberately LEFT at 4 despite its slot 3 also being
pinned: the probe releases in 0.02 s so Hindsight's 4-wide fan-out is unaffected,
and its short one-shot calls gain little from cache retention.

**Expected / measured.**
- DS4 argv confirms `--parallel 3 --slot-prompt-similarity 0.5 --ctx-size 131072
  --kv-unified`. **All 3 slots report `n_ctx=131072`** — unified held, NOT split
  into 3×43k, so Hermes' 64k floor is safe.
- 3rd slot cost is within noise: GTT **106.6 → 106.2 GiB** with DS4+gemma
  resident. Retired-35B+gemma steady state is GTT **43.6 GiB / 72 GiB MemAvailable**.
- **NO OOM anywhere in the window** (`journalctl -k`).

**🔴 HEAVY-MUTEX PROVEN AGAINST THE REAL FAILURE.** Reproduced the 11:00:19 path
exactly — a plain `POST /v1/chat/completions` naming `deepseek-v4-flash` while
the retired 35b model was resident:

```
15:23:01  request fired
15:23:03  HEAVY-MUTEX: 2 heavy models resident
          (deepseek-v4-flash=loading, the retired 35b model=loaded) — evicting the retired 35b model
15:23:03  HEAVY-MUTEX: unload the retired 35b model ok
15:26:06  DS4 loaded, no OOM
```

**Reacted in 2 s. The 2026-08-07 OOM landed at 90 s — a ~45× margin.** gemma4-e4b
untouched, `heavy_evictions_total 0→1`, `heavy_coresident` back to 0.

**Smoke-test.** `swap-model.sh the retired 35b model` — the path the 10:4x entry flagged as
NEVER EXERCISED — now works end-to-end in **23 s**, correctly confirming DS4
unloaded *before* loading the retired 35b model. Mutex correctly did NOT fire on that clean swap
(evictions stayed at 1), proving it only triggers on genuine co-residency.
`POST /v1/chat/completions` model=the retired 35b model → `"the retired 35b model online"`,
finish_reason=stop, with `reasoning_content` populated. All four user services
active.

⚠️ Note `max_tokens` must exceed the retired 35b model's thinking budget or `content` comes back
EMPTY with the reasoning in `reasoning_content` — 30 tokens returned '', 600
worked (147 completion tokens used).

**Minor, not fixed:** `llama_watchdog_probe_success{model="the retired 35b model"}`
lingers at 1 after a model unloads (gauge is never cleared). Harmless — the alert
is `min(...) < 1`, so a stale 1 cannot mask a real failure — but the series is
misleading if read directly.

**End state.** the retired 35b model + gemma4-e4b resident; DS4 on-demand. Batch A and B
both complete.
