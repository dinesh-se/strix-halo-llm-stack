# Mesa/RADV baseline — 2026-08-01 13:29 UTC

## System
- **mesa**: 26.0.3-1ubuntu1
- **kernel**: 7.0.0-28-generic
- **llama_cpp**: version: 10200 (5f55650a7)
- **llama_swap**: version: v245 (30470a4), built at 2026-07-31T04:56:21Z
- **radv_perftest**: RADV_PERFTEST=nogttspill
- **gpu**: Radeon 8060S Graphics (RADV STRIX_HALO)
- **n_predict**: 128
- **iters**: 3 measured (+1 warmup discarded)
- **cache_prompt**: false (fresh prefill each run)

## Summary

| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |
|---|---:|---:|---:|---:|---:|
| qwen3.6-35b | 913 | 831.05 | 826.01–836.63 | 92.60 | 92.54–92.79 |
| qwen3.6-27b | 913 | 213.17 | 203.45–213.63 | 23.72 | 23.64–23.78 |
| gemma4-12b | 986 | 369.22 | 368.19–372.06 | 90.71 | 90.19–91.08 |

## MTP draft acceptance (this run only)

| Model | median | mean | min–max | n tasks |
|---|---:|---:|---:|---:|
| qwen3.6-35b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| qwen3.6-27b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| gemma4-12b | 0.943 | 0.943 | 0.943–0.943 | 4 |
