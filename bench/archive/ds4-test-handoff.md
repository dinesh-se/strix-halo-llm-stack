# DS4 (DeepSeek-V4-Flash-0731) Local Test — Session Handoff

Last updated: 2026-08-06 (post-reboot — steps 1-5 verified)
Source session: `20260806_103422_141201` (pre-reboot) → continued 2026-08-06

## Purpose
Testing DeepSeek-V4-Flash-0731 (UD-IQ3_XXS) on the Beelink GTR9 Pro (AMD Strix Halo,
gfx1151, 128 GB UMA) as the primary local model for: coding-heavy work, AI PA
workflows, general chat. Goal: efficient config, longer context, session stability.

## Changes made this session (BEFORE reboot)
1. **GRUB updated** — added `amdgpu.gttsize=126976` (+ `ttm.page_pool_size=32505856`)
   to lift GPU aperture to ~124 GiB. Boot params should now be:
   `amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856`
   - Verify after reboot: `cat /proc/cmdline` should show all four.
2. **cache-reuse** — added KV-cache-reuse for DS4 (per llama-swap config pattern `--cache-reuse 256`).

## Current live config (pre-reboot)
- **Container:** `ds4-server` = `kyuz0/amd-strix-halo-toolboxes:vulkan-radv-performance`
- **Model:** `unsloth/DeepSeek-V4-Flash-0731-GGUF` UD-IQ3_XXS, 4 shards, ~104 GB
- **Port:** 10097 (standalone, NOT behind llama-swap :9292)
- **Launch cmd:**
  ```
  -m .../DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00001-of-00004.gguf
  --alias deepseek-v4-flash --host 0.0.0.0 --port 10097 -c 131072 -ngl 999 -fa on
  --cache-type-k q8_0 --cache-type-v q8_0 -b 2048 -ub 1024 --parallel 1 -cram 0 --jinja --no-webui --metrics
  ```
- **Hermes config:** `model.default: deepseek-v4-flash`, `provider: custom:ds4` (config.yaml
  is annotated with `# DS4 SESSION 2026-08-06` markers — REVERT to qwen3.6-35b/custom:hermes when done)

## Measured performance (real, on this box)
| Scenario | Prefill tok/s | Decode tok/s |
|---|---|---|
| Small prompt (16 tok) | 26.45 | **19.64** |
| Realistic (19K prompt, watchdog.py review) | **217.43** | **16.7** |

Key finding: decode drops 19.6 → 16.7 under long prompts. **Prefill dominates** —
87.7s to ingest 19K tokens. Biggest bottleneck for coding/PA loops is prefill, not decode.

## Post-reboot verification (2026-08-06) — ALL STEPS VERIFIED
1. **Boot params:** confirmed all 4 present in `/proc/cmdline`
   (`amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856`)
2. **Container:** ds4-server up, health OK (`curl 127.0.0.1:10097/health`), UD-IQ3_XXS @ 131072 ctx
3. **cache-reuse:** `--cache-reuse 256` confirmed in launch cmd (docker inspect)
4. **Realistic benchmark re-run** (14,805-token prompt, watchdog.py + commentary):
   - prefill **224.79 tok/s** (was 217.43, +3.4%)
   - decode **17.09 tok/s** (was 16.7, +2.3%)
   - prompt_ms 65,462 → prefill still dominates (~65s per long prompt)
5. **KV-cache-reuse test** (same prompt twice, cache_prompt=True):
   - wall 73s → **7.6s** (~10x speedup)
   - prompt_ms 65,464 → **174ms** (only 4 tokens reprocessed — prefill effectively skipped)
   - decode unaffected (17.3)

**Key finding:** cache-reuse is the highest-value lever — turns the ~65s prefill tax
into ~0.2s for repeated prompts (the coding/PA loop pattern).

## Recommendation (step 6)
- **Keep Vulkan for now.** Reboot fixes (gttsize + cache-reuse) resolved the OOM/session-death
  failure mode. Prefill dominates but cache-reuse neutralizes it on repeated context.
- Remaining Vulkan OOM tail risk (amdgpu_amdkfd_restore_userptr_worker) → if 503s/session deaths
  recur after sustained use, switch to HIP for stability.
- **DSpark drafter** (21.31 tok/s, 67% accept on HIP = ~2.5x decode) is a decode win, but
  bottleneck is prefill (already fixed by cache-reuse). Lower priority — test only if decode
  latency becomes the pain point.

## Test protocol — to reproduce after reboot
1. Verify boot params: `cat /proc/cmdline` → expect all 4 params (esp. `amdgpu.gttsize=126976`)
2. Verify ds4-server container up: `docker ps | grep ds4` ; health: `curl -s http://127.0.0.1:10097/health`
3. Verify cache-reuse flag present in launch cmd (docker inspect ds4-server)
4. Re-run the realistic benchmark (see below), compare decode/prefill to the table above
5. Optional: test KV-cache-reuse by sending the SAME prompt twice — measure second-prefill drop

### Realistic benchmark (execute_code)
```python
# POST to http://127.0.0.1:10097/completion
# prompt = real watchdog.py code (~27KB) + task commentary, padded to ~19K tokens
# n_predict=128, temperature=0, cache_prompt=False
# read timings: prompt_per_second, predicted_per_second
```
Reference file: `/home/dinesh-se/observability/stack/llama-watchdog/watchdog.py`
(Full script was run via execute_code in the source session.)

## Key reference (verified deployment, same model+machine)
`darnoq99/deepseek-v4-flash-0731-strix-halo` (GitHub):
- HIP backend: 21.31 tok/s with DSpark drafter (`--draft-dspark`, n_max=3), 67.46% acceptance
- Non-spec HIP baseline: 6–14 tok/s
- **Vulkan OOMs under full offload after many generations** (amdgpu_amdkfd_restore_userptr_worker) —
  likely cause of the "session dies / 503s" failure mode → reason to consider HIP backend for STABILITY
- Boot params REQUIRED (default ~62 GB aperture insufficient)
- `--kv-unified` critical
- Quant↔context tradeoff: UD-IQ3_XXS fits at 8K (~114 GiB resident, ~10 GiB headroom);
  for 32–128K context drop to UD-IQ2_M (~90.9 GB) or UD-Q2_K_XL (~96.8 GB)

Sources (accessed 2026-08-06):
- https://github.com/darnoq99/deepseek-v4-flash-0731-strix-halo
- https://computingforgeeks.com/run-deepseek-v4-flash-locally/
- https://aifinitee.com/running-deepseek-v4-flash-q2-on-strix-halo-128gb/
- https://tinycomputers.io/posts/running-deepseek-v4-flash-on-amd-strix-halo.html

## Open threads / next steps
- [x] Confirm gttsize=126976 took effect after reboot
- [x] Verify cache-reuse landed on DS4 launch cmd
- [x] Re-run realistic benchmark → compared (prefill 224.8 / decode 17.1, both slightly better)
- [x] Test KV-cache-reuse: same prompt twice → 10x wall speedup (65.5s → 0.17s prefill)
- [x] Decided: keep Vulkan for now; HIP only if 503s/session deaths recur
- [ ] Optional: test DSpark drafter for 2x decode if decode latency becomes a pain point
- [ ] REMEMBER: revert Hermes config to qwen3.6-35b/custom:hermes when DS4 session done
  (config.yaml is annotated with `# DS4 SESSION 2026-08-06` markers)
