---
name: "TH1-E2: Global skills install"
about: "Stories for Global skills install — vendor skills + cockpit-wake, cmd-global, cold install"
labels: ["TH1", "E2"]
---

## Epic: TH1-E2 — Global skills install

Make a machine able to operate a cockpit: vendor the canonical skills + `cockpit-wake`,
install/update them idempotently, and support the cold-install one-liner.

### Stories

- [ ] US1 — Vendor the 7 harness skill source files into `skills/` (single source of truth).
- [ ] US2 — Vendor `cockpit-wake` (single-file stdlib Python) into `bin/`.
- [ ] US3 — `lib/cmd-global.sh`: install/update 8 skills + cockpit-wake (copy, `--link`, backups, idempotent).
- [ ] US4 — Cold install: `install.sh` wrapper + `global --from-release <ref>` (fetch, verify, extract, run).

Full stories: `docs/themes/TH1-bootstrap-tooling/E2-global-skills-install/`
