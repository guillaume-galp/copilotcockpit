# Codex review checklist

When reviewing changes, validate:

- Bash portability: Bash 3.2 compatibility, no `readlink -f`, `sed -i`, `date -d`,
  associative arrays, or other Bash 4+-only assumptions.
- Idempotency: repeated runs do not duplicate changes; diffs are only written when
  content changes.
- Codex compatibility:
  - Canonical `skills/` remains untouched by runtime outputs.
  - `.agents/skills` exposes repo-scoped Codex overlays.
  - `$HOME/.agents/skills` install path is preserved.
- Template ownership safety:
  - `templates/e2e/MANIFEST.toml` classifies scaffolded paths as
    framework/seed/project and protects project-owned content on `--update`.
- No hardcoded project references in canonical worker global skills:
  - `ulysses-index`, `/home/guillaume`, `/home/guillaume/git/ulysses-index`,
    `localhost:5002/healthcheck`, `@TC-CLI-007`, `@cli`.
- Test coverage:
  - `./run-tests.sh codex` includes all requested Codex checks.
  - Relevant category command updates still run in `./run-tests.sh all`.
- Safety and config hygiene:
  - No secrets are added.
  - No unsafe defaults or local-machine-only paths are introduced.
  - `AGENTS.md` and `.codex/config.toml` remain project-owned guidance, not optional runtime data.
