# ADR-009 — Codex compatibility layer

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-07-02 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

This repository already supports a GitHub Copilot runtime through:

- global skill install in `~/.copilot/skills/`,
- `.github/skills` project overlays,
- legacy e2e scaffold instructions.

Codex expects a different guidance contract:

- `AGENTS.md` for repo instructions,
- `.agents/skills` for repo-scoped skill exposure,
- `$HOME/.agents/skills` for user-global Codex skills,
- optional `.codex/config.toml` runtime configuration.

The toolkit needed to remain dual-runtime without breaking Copilot users.

## Decision

Keep `skills/` as the canonical source of all managed skill content and add a Codex
compatibility layer that:

- installs user-global Codex skills into `$HOME/.agents/skills/`,
- installs repo-scoped Codex overlays into `.agents/skills/`,
- preserves the legacy Copilot install/install behavior unchanged,
- adds `./bootstrap.sh codex-global` and `./bootstrap.sh codex-repo` commands,
- adds Codex checks and templates in the e2e scaffold.

Project-level compatibility files (`AGENTS.md`, `.codex/config.toml`, and
repo-scoped skill overlays) are added or updated only through the Codex-specific
commands and templates.

## Consequences

### Positive
- Users can run Copilot and/or Codex workflows from the same repository safely.
- Skill updates flow from a single canonical source (`skills/`).
- E2E scaffolds now include runtime-specific guidance for both Copilot and Codex.
- Dual-runtime behavior is explicit and auditable.

### Negative / Trade-offs
- More dispatch paths in `bootstrap.sh` and one more manifest surface to maintain.
- Additional test coverage is needed to keep Codex and Copilot states aligned.
- Generated project files must be classified with expanded manifest rules.

### Why dual-runtime was chosen

The repository already has stable Copilot behavior with documented users and workflows.
A migration-only change would force a risky split for existing consumers. Keeping both
runtimes allows incremental adoption while ensuring backward compatibility.

## Why `skills/` remains canonical

The repository already owns the source-of-truth model for skill versions and upgrades.
Centralizing on `skills/` avoids divergence, keeps review flow normal, and preserves the
existing offline + idempotent install/upgrade path.

## Why `.agents/skills` is an exposure layer

Codex reads repo-scoped instructions from `.agents/skills/`. Keeping this directory as
an exposure layer lets the repo ship overlays while still referencing canonical source
files in `skills/`.

## Why `.github/skills` remains for backwards compatibility

`~/.copilot/skills` and `.github/skills` are the current Copilot entry points. Removing
or changing them would break current users and existing cockpit flows. They remain
untouched and fully supported.

## History
- 2026-07-02: Proposed
