# Plan: Planner/Executor Flip — cloud DS4 brain + local Qwen3.6-35B-A3B resident executor

**Date:** 2026-09-02 (brainstormed with Dinesh)
**Status:** PENDING — observation window in progress. Revisit 2026-09-09 (reminder set).
**Owner:** Dinesh / Epa

## 1. The idea

Today the Epa Q executor runs as a long agentic loop **on local DS4** (deepseek-v4-flash,
pinned via `workers/epaq/executor.py` + `ds4_lease`), while Hermes main chat and
`delegation.model` already run on **opencode-zen / deepseek-v4-flash** (cloud).

Flip proposal:
- **Brain (planner/reader):** stays **deepseek-v4-flash via OpenCode Zen** — unchanged for
  Hermes main session AND Epa Q executor brain. Delegation rules decide fan-out.
- **Hands (worker/executor):** **Qwen3.6-35B-A3B** becomes the **local resident** model
  (replaces DS4 in the residency slot). It executes concrete, single-scope subtasks
  (terminal, file edits, tests) at ~50–60 t/s and reports back condensed results.

DS4 leaves the local residency slot. The planner never loses its brain because the
planner already lives on Zen today.

## 2. The math (why residency is forced)

Resident today: DS4 98.4 GiB + gemma4-e4b 4.9 GiB = **103.3 GiB of 120 GiB cap**
→ ~16.7 GiB headroom. Qwen3.6-35B-A3B Q4_K_M (acesabar NSC-MTP, 21 GB file; unsloth
UD-Q4_K_M also cached) needs ~21–23 GiB resident. **It cannot coexist with resident
DS4.** The flip is therefore binary: DS4 resident → 35B resident. No partial state.

## 3. Theoretical token math (the question asked)

**Baseline today (local DS4 monolith executor):** input-dominated and re-prefill-heavy.
Measured over 38 sessions/7 days: mean per-call input **65,835**, p90 **119,394** tokens
(DS4 ctx 262144). A single agentic loop re-prefills its *entire* history every turn
(~200 t/s prefill mid-depth → ~5–6 min/turn just prefill at 65k; decode 18.8 t/s).
For a 50–100-turn task (the observed 2–4h tasks):
**≈ 3.5–7M input tokens + ~0.2M output ≈ 3.6–7.2M tokens/task. Cost $0 (local).**

**Flipped (cloud DS4 planner + local 35B executor):** contexts stay **bounded**.
- Planner: ~25–40 decision/review turns × 15–40k context (stateless per subtask —
  only condensed diffs/test tails come back) ≈ **0.4–1.6M input** + ~60–80k output.
  Paid on Zen: ≈ **$0.13–0.45/task** at $0.22/M in / $0.66/M out (off-peak).
  ⚠️ **Note (2026-09-02):** `deepseek-v4-flash-free` is RETIRED on Zen — the planner
  leg is all-paid; there is no free DS4 tier anymore.
- Executor: ~25–40 fresh contexts × 10–30k ≈ **0.25–1.2M input** + ~50k output.
  Local = **$0** at 50–60 t/s.

**Verdict: ~2–5x FEWER total tokens** (the monolith's re-prefill is the waste), with a
**new small cost** on the planner leg only. Money spend: $0 → ~$0.2–0.5/task (no free
DS4 tier — retired 2026-09-02). **Wall-clock: ~2–3x faster (est. 45–90 min vs 2–4h)** — executor turns
are ~1.5–2 min (small prefill + 55 t/s decode + cloud round-trip) vs ~6–11 min today.

**Prefill — the Gemma question (2026-09-02):** no draft/small model can speed up a big
model's prefill — prefill is compute-bound batch work over the whole prompt. Speculative
decoding (Gemma-as-draft) accelerates DECODE only, and DS4's own MTP head was already
rejected (0.583 accept vs 0.70 floor, −10 GiB, two OOMs). The real prefill levers:
(1) **slot-KV reuse** — same conversation on a warm slot prefills only the delta; today's
65–120k per-turn re-prefill is worsened by slot contention (3 conversation slots, many
consumers) — a dedicated executor slot would help; (2) **llama.cpp prompt cache**
(`cache-ram`) — prefix-KV reuse across calls, but DISABLED (`cache-ram=0`) because it
caused the 2026-07-19 host-RAM OOM (not capacity-enforced on Linux, llama.cpp#22629);
revisitable only with guardrails or a disk-backed cache; (3) **shrink the prompt** —
gemma4-e4b (114 t/s) as history compressor (already the Hermes-compression model),
which is this flip's bounded-context design in miniature.

**⚠️ The lever that makes or breaks this:** orchestration must stay **stateless-bounded**.
Executor returns condensed results (diff + test tail), never full file dumps; planner
plans each subtask from summaries, not the accumulated agentic history. If the planner
context grows like today's monolith, the token win shrinks to ~1.5–2x AND the burn
relocates to paid Zen — the worst of both worlds.

## 4. Decision gates (Dinesh's own criteria)

- **G1 — 2026-09-04 (after ~2 days):** observe the free-cloud delegation setup
  (live since 2026-09-02, commit `c38cbc5`: `delegate_ok` opt-in, free chain
  nemotron-3.5-lightning-free → mimo-v2.5-free → laguna-s-2.1-free). Baseline pain:
  one morning task took **~4h**, typical tasks **2–3h**. If delegation makes Epa Q
  tasks fast enough → keep it, no flip.
- **G2:** if still slow → flip: DS4 leaves local residency, 35B-A3B resident executor.
- **External trigger:** **Qwen3.8-35B-A3B** release (Dinesh expects soon, unverified) —
  a refreshed 35B may add intelligence/resilience for longer sessions; re-evaluate
  the executor model choice at revisit.

## 5. Rollout staging (if G2 fires)

1. Preflight: serve 35B-A3B via existing `swap-model.sh` path; measure t/s + ctx;
   pick acesabar NSC-MTP vs unsloth UD-Q4_K_M (A/B).
2. Residency swap: DS4 `load-on-startup=false`, 35B `load-on-startup=true`
   (`~/llama-stack/config/models.ini`; update `docs/infra/current.md` + changelog;
   3-way ctx sync only if ctx-setting changes).
3. Retarget executor: `workers/epaq/executor.py` pin → local `qwen3.6-35b`;
   move `ds4_lease` → the 35B slot; crons unchanged.
4. **Sensitive-task policy decision (must be explicit):** finance/ingest/email stay
   pinned local with NO delegation — but local-strong is now the 35B, not DS4.
   Options: (a) accept 35B + human review, (b) cold-load DS4 (3–11 min) only for
   sensitive tasks, (c) defer sensitive tasks to the Hermes main leg. Decide before flip.
5. Bounded-orchestration discipline (section 3 lever) — executor returns condensed
   results; planner reads tails, not full files.
6. A/B one representative Epa Q task (same task, both regimes): wall-clock + tokens
   + error rate → record in changelog before committing to the flip.
7. Rollback: `swap-model.sh` back to DS4 resident; revert executor pin (git).

## 6. Risks & constraints

- **35B tool-call reliability** in long loops → mitigate with short single-scope tasks,
  `enable_thinking=false` on worker calls, bounded toolset (terminal + file edit).
- **Single-caller only.** qwen3.8-27b eval (2026-08-20) lesson: fast per token but
  lost on multi-caller work. The 35B must stay a worker — never promoted to general chat.
- **Network dependency:** every step needs a cloud planner round-trip; outage → executor idle.
- **Cost drift:** paid planner leg (no free DS4 tier since 2026-09-02); watch monthly Zen spend.
- **DS4 cold load 3–11 min** after eviction — affects any local-strong fallback need.

## 7. Open data to collect before revisit

- Per-task wall-clock + turn counts for current Epa Q tasks (free-delegation regime).
- Zen spend/day under free-chain delegation.
- Qwen3.8-35B-A3B release status.