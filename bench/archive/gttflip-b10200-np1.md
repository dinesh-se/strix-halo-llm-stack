# Mesa/RADV baseline — 2026-08-05 22:10 UTC

## qwen3.6-35b
  warmup: prompt_n=913 PP=741.77 t/s  TG=81.78 t/s  (40.11s wall)
  run 1: prompt_n=913 PP=1135.90 t/s  TG=91.40 t/s  (2.21s wall)
  run 2: prompt_n=913 PP=1131.88 t/s  TG=91.20 t/s  (2.22s wall)
  run 3: prompt_n=913 PP=1136.10 t/s  TG=91.56 t/s  (2.21s wall)
  MTP draft acceptance: median=1.000 mean=1.000 range=1.000-1.000 (n=4)

## qwen3.6-27b
  warmup: prompt_n=913 PP=243.85 t/s  TG=23.41 t/s  (28.51s wall)
  run 1: prompt_n=913 PP=255.07 t/s  TG=23.65 t/s  (9.00s wall)
  run 2: prompt_n=913 PP=252.93 t/s  TG=23.58 t/s  (9.05s wall)
  run 3: prompt_n=913 PP=253.21 t/s  TG=23.56 t/s  (9.05s wall)
  MTP draft acceptance: median=1.000 mean=1.000 range=1.000-1.000 (n=4)

## gemma4-12b
  warmup: prompt_n=986 PP=686.17 t/s  TG=92.08 t/s  (13.13s wall)
  run 1: prompt_n=986 PP=716.77 t/s  TG=93.75 t/s  (2.75s wall)
  run 2: prompt_n=986 PP=716.24 t/s  TG=94.18 t/s  (2.75s wall)
  run 3: prompt_n=986 PP=717.62 t/s  TG=93.44 t/s  (2.75s wall)
  MTP draft acceptance: median=0.971 mean=0.971 range=0.971-0.971 (n=4)

# Mesa/RADV baseline — 2026-08-05 22:10 UTC

## System
- **mesa**: 26.0.3-1ubuntu1
- **kernel**: 7.0.0-29-generic
- **llama_cpp**: version: 10200 (5f55650a7)
- **llama_swap**: version: v245 (30470a4), built at 2026-07-31T04:56:21Z
- **radv_perftest**: 
- **gpu**: Radeon 8060S Graphics (RADV STRIX_HALO)
- **n_predict**: 128
- **iters**: 3 measured (+1 warmup discarded)
- **cache_prompt**: false (fresh prefill each run)

## Summary

| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |
|---|---:|---:|---:|---:|---:|
| qwen3.6-35b | 913 | 1135.90 | 1131.88–1136.10 | 91.40 | 91.20–91.56 |
| qwen3.6-27b | 913 | 253.21 | 252.93–255.07 | 23.58 | 23.56–23.65 |
| gemma4-12b | 986 | 716.77 | 716.24–717.62 | 93.75 | 93.44–94.18 |

## MTP draft acceptance (this run only)

| Model | median | mean | min–max | n tasks |
|---|---:|---:|---:|---:|
| qwen3.6-35b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| qwen3.6-27b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| gemma4-12b | 0.971 | 0.971 | 0.971–0.971 | 4 |

