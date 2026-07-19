# Mesa/RADV baseline — 2026-05-19 15:52 UTC

## System
- **mesa**: 25.2.8-0ubuntu0.24.04.1
- **kernel**: 6.17.0-29-generic
- **llama_cpp**: version: 9209 (0caf2a1d4)
- **llama_swap**: version: 216 (2982dd3d40c79272335da961b519ba6170c750f6), built at 2026-05-17T18:46:44Z
- **radv_perftest**: RADV_PERFTEST=nogttspill
- **gpu**: Radeon 8060S Graphics (RADV GFX1151)
- **n_predict**: 128
- **iters**: 3 measured (+1 warmup discarded)
- **cache_prompt**: false (fresh prefill each run)

## Summary

| Model | prompt_n | PP median (t/s) | PP min–max | TG median (t/s) | TG min–max |
|---|---:|---:|---:|---:|---:|
| orchestrator (qwen3.6-35b) | 913 | 1092.03 | 1090.04–1096.66 | 59.00 | 58.72–59.16 |
| granite-4.1-8b (earlier lineup, since replaced) | 985 | 1079.44 | 1077.99–1080.79 | 37.16 | 37.13–37.31 |
