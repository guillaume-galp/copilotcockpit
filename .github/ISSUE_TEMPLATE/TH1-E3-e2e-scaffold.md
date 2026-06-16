---
name: "TH1-E3: E2E scaffold"
about: "Stories for E2E scaffold — templates/e2e/, MANIFEST.toml, scaffold + --update"
labels: ["TH1", "E3"]
---

## Epic: TH1-E3 — E2E scaffold

Wire E2E into a project: the full parameterisable `templates/e2e/` skeleton, the
ownership manifest, and the `e2e <dir>` scaffold + content-preserving `--update`.

### Stories

- [ ] US1 — `templates/e2e/`: Playwright infra + governed runners (`run-audit.sh`, `run-playwright.sh`, config).
- [ ] US2 — `templates/e2e/`: governance, test-book, tests & runs skeleton (self-auditing trail).
- [ ] US3 — `templates/e2e/`: context, 7 skill overlays, tmux stubs & env files.
- [ ] US4 — `templates/e2e/MANIFEST.toml`: framework/seed/project ownership classification.
- [ ] US5 — `lib/cmd-e2e.sh` scaffold: copy + tokenise + `git init` + `npm install` + handoff.
- [ ] US6 — `lib/cmd-e2e.sh --update`: content-preserving refresh via `MANIFEST.toml`.

Full stories: `docs/themes/TH1-bootstrap-tooling/E3-e2e-scaffold/`
