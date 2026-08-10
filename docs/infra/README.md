# AI Infra — Single Source of Truth

This directory is the **single source of truth** for the current AI infrastructure
on this machine (Beelink GTR9 Pro / Strix Halo / Ubuntu). It lives in the
`strix-halo-llm-stack` git repo (versioned, pushed to GitHub) and is maintained by
**both** Claude Code and Hermes. Both agents MUST read `current.md` before any
infra change, and MUST update these files in the same session as any infra change.

## Files

- **`current.md`** — LIVING SNAPSHOT of the current state. Overwritten on every
  change (it describes what is true *now*, not history). This is the file to
  read first.
- **`changelog.md`** — APPEND-ONLY change log. One entry per change, newest
  first. Was formerly `~/.AI-INFRA-HISTORY.md` (moved 2026-08-10). Keep the
  existing entry format (`Observed / Changed / Expected / Refs / Smoke test`).
- **`README.md`** — this protocol.

Canonical absolute path: **`~/Dev/strix-halo-llm-stack/docs/infra/`**

---

## ⚠️ CRITICAL DISTINCTION — Source vs. Runtime Data

The AI stack is split across **two locations**. Models MUST understand this split
or they will break the live system (this is what happened during repo consolidation
on 2026-08-10 — one agent nearly deleted the runtime model files).

### 1. `~/Dev/strix-halo-llm-stack/` — the Git repo (SOURCE OF TRUTH, on GitHub)

What goes here: **code, config templates, systemd units, docs, archived bench
experiments.** This is what you edit and commit. It is versioned and pushed to
`github.com/dinesh-se/strix-halo-llm-stack.git`.

- `config/` — config **templates** (e.g. `models.ini`)
- `systemd/` — service unit files
- `observability/` — watchdog etc.
- `tools/` — scripts (`swap-model.sh`, `gguf-vram-estimator.py`)
- `docs/infra/` — this SSoT
- `bench/` — canonical baselines + `bench/archive/` for one-off experiments

**NEVER** commit model weights, caches, or logs here.

### 2. `~/llama-stack/` — runtime data (NOT in git, NOT to be deleted)

What lives here: **the actual model weights + the live config the router mounts.**
These are gitignored runtime data. The running stack depends on them by absolute
path. **Do NOT delete, move, or "clean up" this directory** — you will kill the
router.

Live `llama-router.service` mounts (read-only) from `~/llama-stack/`:
- `~/llama-stack/hf-cache/models--unsloth--DeepSeek-V4-Flash-0731-GGUF` → `/models/ds4`
- `~/llama-stack/hf-cache/hub/models--unsloth--gemma-4-E4B-it-qat-GGUF` → `/models/aux`
- `~/llama-stack/hf-cache-archive/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF` → `/models/qwen`
- `~/llama-stack/empty-cache` → `/models/empty` (eviction buffer)
- `~/llama-stack/config/models.ini` → `/models.ini` (the router's config!)

**Rule:** if it's a `.gguf`, a cache dir, or a log — it belongs in `~/llama-stack`
and is runtime data. If it's code/config/docs/units — it belongs in the repo.

---

## Rules (both agents)

1. **Read first:** before changing any infra (models, router, services, config,
   docker, ports, memory), read `current.md`.
2. **Update in the same session:** after an infra change, do BOTH:
   - Append an entry to `changelog.md` (newest first), AND
   - Rewrite the affected sections of `current.md` so it reflects the new state.
   Never update only one.
3. **Don't let the two drift:** if you find `current.md` does not match reality,
   fix `current.md` (that is a change — log it in `changelog.md` too).
4. **Config files, not prose, are truth:** `current.md` summarizes; the actual
   config files (e.g. `~/Dev/strix-halo-llm-stack/config/models.ini`,
   `~/.hermes/config.yaml`) are the ground truth. When they disagree with
   `current.md`, trust the config files and update `current.md`.
5. **Known gotchas** live in `current.md` under `## Known gotchas` — read them.
6. **Version line:** `current.md` carries a `Last verified` timestamp. Update it
   whenever you touch the file. When both agents keep this honest, drift is
   detectable.

## Claude Code

- `~/AI-INFRA-HISTORY.md` is now a pointer to this directory — the old path
  still resolves. Claude's memory note `feedback_update_memory_after_infra_change`
  should now reference `~/Dev/strix-halo-llm-stack/docs/infra/current.md` +
  `changelog.md`.
- **Claude must also know the Source vs. Runtime Data split above** — it must
  never edit or delete `~/llama-stack/` model/cache files.

## Hermes

- Hermes loads the `ai-infra-state` skill (see `~/.hermes/skills/`) before any
  infra work. Its MEMORY.md points here.
- Hermes must also respect the Source vs. Runtime Data split — never touch
  `~/llama-stack/` weights/caches/logs.
