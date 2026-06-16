# ADR-005 — `cockpit-wake` distribution

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`cockpit-wake` is a CLI that schedules one-off (`at`) and recurring (`cron`) messages
to tmux cockpit panes. Its skill (`cockpit-wake/SKILL.md`) documents the binary as
living at `~/.local/bin/cockpit-wake` and being on `PATH`. For the cockpit to work,
this tool must be **on the user's PATH** after a global install.

**Evidence:** the installed artefact at `~/.local/bin/cockpit-wake` is a
**single-file Python 3 script** (~344 lines, `#!/usr/bin/env python3`, stdlib only —
it shells out to `at`/`cron`/`tmux`). It is *not* a compiled binary and has no
third-party Python dependencies.

The question: how should `copilotcockpit` distribute it so a `global` install puts a
working `cockpit-wake` on PATH, idempotently and cross-platform (NFR-1, NFR-2, NFR-3)?

Candidate approaches: vendor the script in the repo and copy it; build from source;
`npm install` it; or document a manual copy.

## Decision

**Vendor `cockpit-wake` as a single-file Python script at `copilotcockpit/bin/cockpit-wake`,
and have `bootstrap.sh global` install it by copying to `~/.local/bin/cockpit-wake`
(`chmod +x`), then verifying `~/.local/bin` is on `PATH`.**

- The script is committed verbatim into `bin/` — it is the canonical source, exactly
  as the skills are ([ADR-001](ADR-001-skills-source-of-truth.md)).
- `global` copies it alongside the skills in the same idempotent pass (backup-before-
  overwrite; `already current` when identical).
- If `~/.local/bin` is not on `PATH`, `global` prints the one-line `export PATH=…`
  guidance (and the shell-rc snippet) rather than silently editing dotfiles.
- `bootstrap.sh doctor` checks: `python3` present, `~/.local/bin` on PATH, and the
  runtime dependencies `at` / `cron` / `tmux` available — surfacing any gap.

Because it is pure-Python stdlib, there is **no build step and no runtime install** —
copying the file *is* the installation (P1, P5).

## Consequences

### Positive
- Dead-simple, dependency-free install: a file copy (P1, NFR-3).
- Cross-platform: any machine with `python3` runs it (NFR-2).
- Versioned in-repo like everything else — one source of truth, reviewable diffs.
- Offline-friendly — no registry fetch (NFR-6).
- `doctor` makes missing runtime deps (`at`/`cron`) explicit instead of failing silently.

### Negative / Trade-offs
- We must keep the vendored copy in sync with upstream `cockpit-wake` development.
  Mitigation: as with the skills, this repo becomes the origin; `doctor` reports drift
  against `~/.local/bin/cockpit-wake`.
- We do not auto-edit the user's shell rc to add `~/.local/bin` to PATH (deliberately —
  editing dotfiles is intrusive). Mitigation: clear printed guidance + `doctor` check.

### Risks
- The script depends on system `at`/`cron`/`tmux`, which may be absent on a fresh
  machine. Mitigation: documented in the skill; verified by `doctor`; not a hard
  install failure (the copy still succeeds, the tool reports the missing dep at run time).
- A user on Python 2-only systems. Mitigation: shebang is `python3`; `doctor` checks it.

## Alternatives Considered

### A. Build from source / compile to a binary (e.g. Go, PyInstaller)
- Pros: a self-contained binary with no Python requirement.
- Cons: introduces a build toolchain and per-OS/arch artefacts; the tool is currently a
  simple stdlib Python script — compiling it is pure overhead.
- **Rejected** as disproportionate (P1) — there is nothing to build.

### B. `npm install` / publish to a registry
- Pros: familiar install UX; versioning via semver.
- Cons: requires publishing and network; wraps a Python script in a Node package for no
  reason; violates offline-install (NFR-6) and minimal-deps (P5).
- **Rejected** — wrong ecosystem, adds a network and packaging dependency.

### C. Document a manual copy ("copy this file to ~/.local/bin yourself")
- Pros: zero bootstrap code.
- Cons: not idempotent, easy to forget (the exact failure mode VP1 calls out:
  "forget the `cockpit-wake` binary entirely"), no PATH check.
- **Rejected** — defeats the one-command promise.

### D. Symlink from repo into `~/.local/bin`
- Pros: zero drift.
- Cons: breaks if the repo moves/deletes; same trade-off as skills.
- **Rejected as default**, but the shared `--link` mode from ADR-001 applies here too
  for authors who want it.

## History
- 2026-06-16: Proposed
