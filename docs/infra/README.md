# AI Infra — Single Source of Truth

This directory is the **single source of truth** for the current AI infrastructure
on this machine (Beelink GTR9 Pro / Strix Halo / Ubuntu). It lives in the
`llama-stack` git repo (versioned) and is maintained by **both** Claude Code and
Hermes. Both agents MUST read `current.md` before any infra change, and MUST
update these files in the same session as any infra change.

## Files

- **`current.md`** — LIVING SNAPSHOT of the current state. Overwritten on every
  change (it describes what is true *now*, not history). This is the file to
  read first.
- **`changelog.md`** — APPEND-ONLY change log. One entry per change, newest
  first. Was formerly `~/AI-INFRA-HISTORY.md` (moved 2026-08-10). Keep the
  existing entry format (`Observed / Changed / Expected / Refs / Smoke test`).
- **`README.md`** — this protocol.

Canonical absolute path: **`~/llama-stack/docs/infra/`**

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
   config files (e.g. `~/llama-stack/config/models.ini`, `~/.hermes/config.yaml`)
   are the ground truth. When they disagree with `current.md`, trust the config
   files and update `current.md`.
5. **Known gotchas** live in `current.md` under `## Known gotchas` — read them.
6. **Version line:** `current.md` carries a `Last verified` timestamp. Update it
   whenever you touch the file. When both agents keep this honest, drift is
   detectable.

## Claude Code

- `~/AI-INFRA-HISTORY.md` is now a pointer to this directory — the old path
  still resolves. Claude's memory note `feedback_update_memory_after_infra_change`
  should now reference `~/llama-stack/docs/infra/current.md` + `changelog.md`.

## Hermes

- Hermes loads the `ai-infra-state` skill (see `~/.hermes/skills/`) before any
  infra work. Its MEMORY.md points here.
