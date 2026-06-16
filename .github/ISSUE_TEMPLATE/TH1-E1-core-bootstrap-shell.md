---
name: "TH1-E1: Core bootstrap shell"
about: "Stories for Core bootstrap shell — dispatcher, shared lib, doctor"
labels: ["TH1", "E1"]
---

## Epic: TH1-E1 — Core bootstrap shell

The single discoverable entry point (`bootstrap.sh`), the shared `lib/common.sh`
helpers (logging, OS detection, idempotency, dry-run), and the `doctor` diagnostics.

### Stories

- [ ] US1 — `lib/common.sh` shared helpers: logging, portable OS shims, backup-before-overwrite, dry-run.
- [ ] US2 — `bootstrap.sh` dispatcher: self-documenting `case` router to `global`/`e2e`/`doctor`.
- [ ] US3 — `lib/cmd-doctor.sh`: prerequisites check + skill/cockpit-wake drift detection.

Full stories: `docs/themes/TH1-bootstrap-tooling/E1-core-bootstrap-shell/`
