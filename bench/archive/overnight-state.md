# Overnight state — DS4 GTT flip (2026-08-05 → 08-06)

## ✅ RUN COMPLETE — all 8 phases done, production restored, nothing pending.

**Full report: `bench/deepseek-overnight-20260806.md`.**
History entry: `~/AI-INFRA-HISTORY.md` (2026-08-06).
Memory: [[gtt-memory-model-2026-08-06]] (new, authoritative) plus corrections to
`deepseek_v4_flash_evaluation`, `oom_thrash_incident_2026_05_22`,
`llama_swap_stack`, `radv_vs_rocm_benchmarks`, `infra_testing_queue`, `MEMORY.md`.

## Headline

- **BIOS carveout 96 GiB → 512 MB; usable GPU memory 96 → ~124 GiB.** The
  96 GiB ceiling was a **Windows** limit — now disproven on this box.
- **Production got FASTER:** prefill +36.7% (35b), +18.8% (27b), **+94.1%**
  (gemma4); decode flat within 1.3%.
- **DS4 verdict overturned:** IQ3_XXS + `--spec-type draft-dspark` sidecar =
  **26.33 t/s**, acceptance 0.786 — faster than the resident 27b coder.
- **Zero incidents:** no ring timeouts, device-lost, OOM, or GPU resets.
  Peak GTT 108.9/124; the 118 GiB guard never tripped.

## State left on the box

| | |
|---|---|
| Production lineup | re-warmed, verified answering, 80.7/124 GiB co-resident |
| TTLs | 27b `ttl=0` resident, 35b 1800, gemma4 600 |
| `llama-watchdog` | **re-enabled** — `models_loaded 3`, `probe_success=1` ×3, `device_lost_total 0`, `hindsight_up 1` |
| `nogttspill` | **removed** from compose + 4 harnesses — KEEP removed while the carveout is small |
| DS4 | on disk only, **not** in llama-swap; opt-in |
| Backup | `docker-compose.yml.bak-20260805-pre-gtt-flip` |

## Follow-ups (none urgent)

1. **Isolate gemma4's +94% prefill** — GTT vs `-sps 0.5` vs kernel bump. Biggest
   unexplained number; re-scopes the parked "gemma4 prefill hunt".
2. **Separate build from memory model on DS4** — one run of b10257 against the
   new carveout makes the +54% attributable.
3. **Quality probe needs harder tasks + ≥8k token budget** — current IQ3-vs-IQ2
   result is a null (8/8 both) and the reasoning probe was inconclusive.
4. **ROCm arm hang is unexplained** — allocated 84.8 GiB, 0% GPU, 0 I/O, 17 min.
5. If DS4 ever gets a llama-swap entry: dedicated group, explicit invocation,
   **never an alias** — it evicts the entire lineup.
