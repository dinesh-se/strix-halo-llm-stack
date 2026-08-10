# Bench Archive

One-off experiment harnesses and raw results from the 2026-08-05/06 router-migration
and DS4-tuning sprint. The **decisions and conclusions** from these experiments are
captured in `docs/infra/changelog.md` and the canonical baseline report
(`bench/baseline-mesa-25.2.8-llamacpp-b9209.md`). These scripts were single-use and
are retained only for reference — not part of the live stack.

## Canonical (keep in repo root, not archived)
- `tools/gguf-vram-estimator.py` — VRAM estimator (referenced in README + `host/tuning.md`)
- `tools/swap-model.sh` — model swap wrapper
- `bench/mesa_baseline.py` + `baseline-mesa-25.2.8-llamacpp-b9209.md` — mesa driver baseline

## Archived here (one-off experiments)
- `ds4_arm_ab.py`, `ds4_spec_test.py`, `ds4_quality.py` — DS4 draft/spec decoding + quality A/B
- `saber-exp-a.py`, `exp-a-report.py` — saber experiment
- `router_coresidency_test.py` — router co-residency check
- `ds4-bench.py`, `ds4-test-handoff.md` — DS4 realistic benchmark + handoff
- plus raw `.md`/`.txt`/`.json`/`.log` results

Archived 2026-08-10 during the repo consolidation (`~/llama-stack` absorbed into this repo).
