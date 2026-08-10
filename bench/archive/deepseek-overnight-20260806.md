# DeepSeek V4 Flash + GTT memory flip — overnight run 2026-08-05 → 08-06

**Verdict: the 08-04 "not a daily driver" ruling is overturned.** DS4 Flash
0731 at **UD-IQ3_XXS with the dspark sidecar decodes at 26.33 t/s** on this
box — faster than the resident 27b coder (23.58 t/s) and 2× the 13.21 t/s that
got it shelved. The quant that was physically impossible 24 hours ago now runs
with ~15 GiB of headroom.

**And the production stack got faster, not slower.** Prefill improved 19–94%
across all three daily models; decode is flat within 1.3%.

---

## 1. What actually changed on the box

| | before | after |
|---|---|---|
| BIOS UMA carveout | 96 GiB | **512 MB** |
| `mem_info_vram_total` | 96 GiB | 0.5 GiB |
| `mem_info_gtt_total` | — | **124.0 GiB** |
| `RADV_PERFTEST=nogttspill` | set | **removed** |
| usable model memory | 96 GiB | **~124 GiB** |

`ttm.pages_limit=32505856`, `amd_iommu=off`, `amdgpu.dcdebugmask=0x12` and
`amdgpu.lockup_timeout=10000,60000,10000,10000` were **not touched**. No GRUB
edit was made — `amdgpu.gttsize` was judged redundant because GTT already
reported the full pool.

**The "96 GiB ceiling" was a Windows limit, not hardware.** This is now proven
on this box, not merely sourced from r/StrixHalo.

### `nogttspill` — why removing it was mandatory
It forbids RADV from placing buffers in GTT when VRAM is exhausted. Under a
96 GiB carveout that was correct: a spill landed in the ~30 GiB the OS had
left, which is the 2026-05-22 four-hour thrash. With a 512 MB carveout, GTT is
not the overflow path — **it is the memory model**, and blocking it blocks
everything. Found in **5** places, not the 2 the plan predicted: compose plus
`radv_perf_ab.py`, `rollback_ab.py`, `gemma4_prefill_hunt.py`,
`server_config_ab.py`.

---

## 2. Production under the GTT model (Phase 0 abort gate)

`bench/gttflip-b10200-np1.md` vs `bench/baseline-b10200-np1.md`, identical
harness (`mesa_baseline.py`, n_predict 128, 3 iters + discarded warmup,
cache_prompt false).

| Model | PP before → after | TG before → after | MTP accept |
|---|---|---|---|
| qwen3.6-35b | 831.05 → **1135.90** (+36.7%) | 92.60 → 91.40 (−1.3%) | 1.000 |
| qwen3.6-27b | 213.17 → **253.21** (+18.8%) | 23.72 → 23.58 (−0.6%) | 1.000 |
| gemma4-12b | 369.22 → **716.77** (+94.1%) | 90.71 → **93.75** (+3.4%) | 0.971 |

Gate was "any model >10% below baseline → abort". Worst delta −1.3%.
All three co-resident in **69.6 GiB of GTT**.

⚠️ **Not attributable to GTT alone.** The 08-01 baseline predates the 08-04
`-ub 256` (27b) and `-sps 0.5` (gemma4) changes, the 08-05 residency swap, and
a 7.0.0-28 → 7.0.0-29 kernel bump. **gemma4's +94% prefill is the single
largest jump in the run and lands squarely on the parked "gemma4 prefill hunt"
question — worth isolating, not proven here.**

---

## 3. DS4 results

All on the **radv** arm (`vulkan-radv-performance`, build 10283) unless noted.
`llama-bench`: `-p 512 -n 64 -b 2048`, q8_0 KV, `-fa 1`, `-ngl 999`, `-r 2`.

### Quant + ubatch

| config | PP | TG | GTT |
|---|---:|---:|---:|
| IQ2_XXS — **08-04 reference**, 96 GiB carveout, b10257 | 142.06 | 13.21 | 87.0 (VRAM) |
| IQ2_XXS — tonight | **218.31** ±2.42 | **20.39** ±0.08 | 85.1 |
| IQ3_XXS `-ub 512` | 205.62 ±3.19 | 19.03 ±0.22 | 97.0 |
| IQ3_XXS `-ub 1024` | 205.87 | **19.34** | 97.5 |
| IQ3_XXS `-ub 2048` | 205.15 | 19.32 | 97.5 |

**`-ub` is a non-lever for DS4** — 0.4% spread on PP, 1.6% on TG. This settles
the three-way dispute (512 / 1024 / 2048) by showing the question doesn't
matter. ⚠️ **Scoped to depth 0.** The 27b's `-ub` effect only appeared at
depth 32768, so this says nothing about long context.

**IQ3 costs only 6.7% decode over IQ2** for ~50% more bits per weight.

### Speculative decoding — the headline

Same llama-server config both arms (`-c 16384 -ub 1024`, q8_0 KV, `--jinja`),
identical prompt, no other variable.

| config | TG | draft acceptance | peak GTT | RAM floor |
|---|---:|---:|---:|---:|
| `--spec-type none` | 18.81 | — | 97.9 | 19.7 GiB |
| **`--spec-type draft-dspark`** | **26.33** | **0.786** | 108.9 | 8.8 GiB |

**+40.0%**, acceptance well above the 0.70 abandon-floor. `draft-dspark` is
the correct mode — no fallback to `draft-mtp` was needed.

Resolved flags on this build: sidecar is `-md`; modes are
`none, draft-simple, draft-eagle3, draft-mtp, draft-dflash, draft-dspark,
ngram-*`; the draft-depth knob is `--spec-draft-n-max` (default 3, we used 2).
`--draft` / `--draft-n` / `--draft-max` do not exist.

**Memory budget as measured:** 97.05 (IQ3) + 10.15 (sidecar) + KV = **108.9 GiB
of 124**. Host RAM floor 8.8 GiB — the real constraint, and the reason not to
push context much past 16k with the sidecar loaded.

### Where DS4 now sits against the daily lineup

| model | decode t/s |
|---|---:|
| qwen3.6-35b | 91.40 |
| gemma4-12b | 93.75 |
| **DS4 IQ3_XXS + dspark** | **26.33** |
| qwen3.6-27b (resident coder) | 23.58 |

A 284B model decoding faster than the 27b coder. It still cannot co-reside
with anything (108.9 of 124 GiB), so using it means evicting the lineup.

---

## 4. Quality: IQ3_XXS vs IQ2_XXS

The 08-04 evaluation left this explicitly open. Both arms: identical request
bodies, temperature 0, `max_tokens` 2048, no sidecar, so quant is the only
variable. Outputs in `bench/ds4-quality-outputs.json`.

**Coding task** (`merge_intervals`, with empty / single / touching-boundary /
nested cases called out in the prompt). Both answers were extracted and
**executed against 8 edge cases**:

| quant | tests passed | completion tokens |
|---|---|---|
| IQ3_XXS | **8/8** | 1065 |
| IQ2_XXS | **8/8** | 1160 |

**Null result: no measurable quality difference at this difficulty.** IQ2 is
not visibly degraded. Temperature 0 reproduced bit-identically across runs.

**Reasoning task: inconclusive.** Both quants consumed the entire 2048-token
budget inside the reasoning channel and emitted **zero content**
(`finish_reason: length`, 7665 / 8020 reasoning chars). This is the runaway
the plan warned about, and it means the harder of the two probes produced no
comparable answer. **The bit-depth question is narrowed, not settled** — a
real verdict needs harder tasks and a much larger token budget.

---

## 5. The ROCm arm hung — unexplained

`kyuz0/amd-strix-halo-toolboxes:rocm-7.14_20260805T174643` (build 10288) on
IQ2_XXS: allocated all **84.8 GiB**, printed
`found 1 ROCm devices (Total VRAM: 126976 MiB) … gfx1151`, then **one thread
sat in `R` state at ~93% CPU with 0% GPU and zero disk I/O for 17 minutes**
(`read_bytes` delta 0 over 20 s). Not slow loading — hung. Killed with
`docker kill`; rc 137, all memory released cleanly.

Deliberately not chased ("no retrying into a degraded box"). Its decision value
had already collapsed: **radv's 20.39 t/s beats the 16.22 t/s ROCm figure that
motivated the arm in the first place.** The standing "radv wins/ties on
everything we run" rule now holds for DS4 too — the one documented exception is
retired.

---

## 5b. ROCm follow-up 2026-08-06 — researched the community config, still hangs

User asked to reproduce the community ROCm result properly. Three further
attempts, all on IQ3_XXS, all hung the same way (allocates, then GPU idle):

| attempt | image | flags | outcome |
|---|---|---|---|
| 1 | rocm-7.14 (b10288) | defaults | hang, 0% GPU, 17 min |
| 2 | rocm-7.14 | **`--no-warmup`** | hang, 0% GPU, 6 min |
| 3 | **rocm-7.2.4** | `--no-warmup --ipc=host` | hang — but briefly hit **10% GPU** before stalling |

**Root cause candidate: `VMM: no`.** Both kyuz0 ROCm images report
`Device 0: … gfx1151, VMM: no`. tinycomputers.io builds llama.cpp with
**`-DGGML_HIP_NO_VMM=OFF`** and calls VMM *critical*: "With VMM enabled, the
HIP backend can use the GTT memory pool." That is a **compile-time** flag — it
cannot be passed at runtime, so no flag combination fixes the kyuz0 images.
`rocm-7.2.4` does expose a newer UMA path
(`ggml_backend_cuda_get_available_uma_memory: 123716680 kB` = 118 GiB) and got
marginally further, but still wedged.

**`--no-warmup` is real but was not our bug.** tinycomputers documents
`HipVMM Failure: invalid argument` without it; our hang reproduces with it.

**The honest answer to "if it worked for them, why not here":** the people
running DS4 well on Strix Halo are largely **not running llama.cpp on ROCm**.
AMD's own playbook for DS4 on this hardware points at **ds4 ("DwarfStar")** —
antirez's DeepSeek-V4-specific engine — via `kyuz0/strix-halo-ds4-toolbox` and
`ds4-cockpit`, not llama.cpp. ⚠️ This softens the 08-04 note that dismissed the
antirez build: it is still not llama.cpp, but it is now the *officially
recommended* path, and needs its own weights (~90 GiB, separate quant repo).

**AMD's playbook independently validates tonight's flip:** it requires "BIOS
dedicated VRAM set to **minimum**" plus a shared pool of **≥110 GB**
(`amd-ttm --set 110`). We are at 512 MB / 124 GiB. It recommends **IQ2_XXS**
for a single 128 GB node and mentions an `--mtp` speculative flag.

**Perspective on whether ROCm is even worth chasing:** the tinycomputers
llama.cpp-on-ROCm write-up reports only "single-digit tokens per second"
(1–2 t/s on IQ1_S-XL). **Our radv result is 26.33 t/s on a *larger* quant.**
Nothing found suggests llama.cpp-on-ROCm beats what we already have.

Sources: tinycomputers.io "Running DeepSeek V4 Flash on AMD Strix Halo";
developer.amd.com playbook "Running DeepSeek V4 Flash with ds4";
github.com/kyuz0/strix-halo-ds4-toolbox.

## 5c. ds4 (DwarfStar) validated as-is — it WORKS on ROCm, and reproduces the community number exactly

Ran antirez's ds4 engine unmodified via Donato's toolbox, per the AMD playbook.
**Image:** `kyuz0/strix-halo-ds4-toolbox:rocm-7.14`.
**Weights:** `antirez/deepseek-v4-gguf` (⚠️ NOT the `schlaflos/…` repo recorded
in memory) — `…IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf`
**80.76 GiB** + MTP head `…MTP-Q4K-Q8_0-F32.gguf` **3.55 GiB**.
**Command:** documented run flags exactly (`--device /dev/kfd --device /dev/dri
--group-add video --group-add render --ipc=host --cap-add=SYS_PTRACE`), plus
`ds4-server --rocm --ctx 16384 --mtp … --host 0.0.0.0`.

**It loaded and served in ~60 s** — 80.76 GiB of tensor spans in **19.3 s**,
MTP head in 0.86 s, 92.7 GiB GTT, `81.45 GiB planned`. **No hang.** This is the
sharpest possible contrast with llama.cpp-on-ROCm, which wedged three times on
the same box, same driver, same GPU.

| setup | decode t/s |
|---|---:|
| kyuz0's **published ROCm** figure | 16.22 |
| **ds4 IQ2XXS + MTP `draft=1`** (ds4's own counter) | **16.31** |
| ds4 IQ2XXS + MTP `--mtp-draft 2` | 15.32 (worse — draft=1 is optimal here) |
| radv llama.cpp IQ2_XXS, no sidecar | 20.39 |
| **radv llama.cpp IQ3_XXS + dspark** | **26.33** |

**This is the validation.** The community's ROCm number is **reproducible on
this box to within 0.6%** (16.31 vs 16.22) — but only through ds4, not through
llama.cpp-on-ROCm. And **our radv llama.cpp stack is 25% faster than ds4 at a
comparable quant, and 61% faster at IQ3 with dspark.**

So the answer to "if it worked for them, why not here" is: **it does work here,
it produces exactly their number, and their number is slower than what we
already have.** The 24%-faster-ROCm premise was measured against a *Vulkan
baseline of 13.08* that this box no longer has — we are at 20.39–26.33.

Caveats kept honest: ds4's quant is its own IQ2XXS recipe (80.76 GiB) and is
not byte-identical to unsloth's UD-IQ2_XXS (84.62 GiB); ds4 was in THINKING
mode (t/s is unaffected — tokens are tokens); ds4 was not tuned beyond
`--mtp-draft`, and it has `--dspark`, `--power`, `--prefill-chunk`, and a
separate `DeepSeek-V4-Flash-DSpark-support.gguf` in the same repo that were
**not** tested. A tuned ds4 could close some of the gap; it would have to close
61% to matter.

## 6. Corrections to the plan and to memory

| Believed | Actual |
|---|---|
| 96 GiB is the ceiling | **Windows limit**; Linux reaches 124 GiB via GTT |
| `nogttspill` in 2 files | **5** |
| download ~6.2 MB/s → 4 h 27 m | **~38 MB/s → ~25 min** (xet, ~44 sockets) |
| ROCm ~24% faster on DS4 | **radv faster; ROCm hangs at this size** |
| `-ub` contested, matters | **non-lever** at depth 0 |
| sidecar 20.7 GiB | **10.15 GiB** (root Q8_0; `dspark/` holds only BF16) |

**Two new operational traps, both cost real time tonight:**

1. **`pgrep -f 'hf download…'` self-matches the waiting shell.** Two "wait for
   the download, then continue" loops matched their own command lines and
   waited forever; ~40 minutes lost and the sidecar retry never fired. Same
   class as the documented `pkill -f` trap. **Don't poll by command-line
   pattern — use the harness's own completion notification, or `kill -0 $PID`.**
2. **`du` on `blobs/` is not a progress signal for xet downloads.** Chunks
   stage elsewhere; `blobs/` and `*.incomplete` look idle while GBs land. Use
   `write_bytes` from `/proc/<pid>/io`.

**Plus a cache-layout trap:** `hf download --cache-dir hf-cache` writes to
`hf-cache/models--…`, while the pre-existing IQ2 cache is at
`hf-cache/hub/models--…`. Both layouts now coexist under the same bind mount —
**the IQ3 in-container path has no `hub/` segment.**

**And a harness bug:** `ds4_arm_ab.py`'s result parser used
`re.finditer(r"\{.*?\}(?=\s*(?:\{|$))")` against llama-bench's pretty-printed
JSON *array*; the comma between elements defeats that lookahead, so a fully
successful run reported `pp: null, tg: null` with `returncode 0`.
**rc=0 plus empty results ⇒ suspect the parser, not the benchmark.**

---

## 7. State left behind

**Production is restored and verified:** all three models re-warmed
(27b 26 s, 35b 39 s, gemma4 10 s — all normal GPU cold-load times, no silent
CPU fallback), correct answers from each, TTLs correct (27b `ttl=0` resident,
35b 1800, gemma4 600), **80.7 GiB of 124 GiB** with all three co-resident.

**`llama-watchdog` re-enabled** (`enable --now`): `models_loaded 3`,
`probe_success=1` on all three, `device_lost_total 0`, `recoveries_total 0`,
`hindsight_up 1`.

**Kept (do not revert without reverting the BIOS carveout):**
- `docker-compose.yml` — `nogttspill` removed, with a comment block explaining
  when to restore it. Backup: `docker-compose.yml.bak-20260805-pre-gtt-flip`.
- 4 bench harnesses — same removal.

**On disk:** IQ3_XXS 97.05 GiB + sidecar 10.15 GiB + IQ2_XXS 84.62 GiB
≈ 192 GiB in `hf-cache` (1.2 TB free). No DS4 model is loaded or configured in
llama-swap — it stays opt-in.

**Zero incidents:** no ring timeouts, no device-lost, no OOM kills, no GPU
resets. Peak GTT 108.9 of 124 GiB; the 118 GiB guard never tripped.

---

## 8. What I would do next

1. **Isolate gemma4's +94% prefill.** Biggest unexplained number of the night;
   candidates are GTT, `-sps 0.5`, and the kernel bump. Answers the parked
   "gemma4 prefill hunt" if it's the memory model.
2. **Separate build from memory model on DS4** — run b10257 against the new
   carveout. One run; makes the +54% attributable.
3. **Decide whether DS4 earns a llama-swap entry.** At 26.33 t/s it is a
   credible deep-reasoning tier, but it evicts the entire lineup, so it wants a
   dedicated group and an explicit invocation, never an alias.
4. **Re-run the quality probe with a 8k+ token budget** on harder tasks; the
   current null result only covers easy work.
5. **Consider raising context with the sidecar** cautiously — the 8.8 GiB host
   RAM floor at 16k is the binding constraint, not GTT.
