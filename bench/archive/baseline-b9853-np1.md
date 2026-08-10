# Mesa/RADV baseline — 2026-08-01 13:02 UTC

## System
- **mesa**: 26.0.3-1ubuntu1
- **kernel**: 7.0.0-28-generic
- **llama_cpp**: version: 9853 (7af4279f4)
- **llama_swap**: version: v234 (4a6b8a8), built at 2026-07-01T00:30:33Z
- **radv_perftest**: RADV_PERFTEST=nogttspill
- **gpu**: Radeon 8060S Graphics (RADV STRIX_HALO)
- **n_predict**: 128
- **iters**: 3 measured (+1 warmup discarded)
- **cache_prompt**: false (fresh prefill each run)

## Summary

| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |
|---|---:|---:|---:|---:|---:|
| qwen3.6-35b | 913 | 829.79 | 828.08–830.65 | 92.68 | 92.28–92.87 |
| qwen3.6-27b | 913 | 211.88 | 211.02–212.52 | 23.78 | 23.04–23.79 |
| gemma4-12b | 986 | 677.27 | 673.56–678.69 | 94.05 | 92.70–94.55 |

## MTP draft acceptance (this run only)

| Model | median | mean | min–max | n tasks |
|---|---:|---:|---:|---:|
| qwen3.6-35b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| qwen3.6-27b | 1.000 | 1.000 | 1.000–1.000 | 4 |
| gemma4-12b | 0.971 | 0.971 | 0.971–0.971 | 4 |
