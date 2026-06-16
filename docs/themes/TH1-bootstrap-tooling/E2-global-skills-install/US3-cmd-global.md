# US3 — `lib/cmd-global.sh`: install/update 8 skills + `cockpit-wake`

| Field | Value |
|-------|-------|
| Epic | E2 — Global skills install |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer, **I want** `bootstrap.sh global` to install (and idempotently
update) all skills and `cockpit-wake` onto my machine, **so that** one command makes me
able to operate a cockpit (VP1 §5a, ADR-001, ADR-005, NFR-1, NFR-4).

## Acceptance Criteria
- [ ] AC1: `lib/cmd-global.sh` exists, passes `bash -n`, and is invoked by
  `bootstrap.sh global`.
- [ ] AC2: It copies each `skills/<role>/SKILL.md` → `~/.copilot/skills/<role>/SKILL.md`
  for all **8** roles (the 7 harness skills + `copilotcockpit-dev`), creating parent
  dirs as needed, using `cc_install_file` (backup-before-overwrite, `already current`
  on identical).
- [ ] AC3: It copies `bin/cockpit-wake` → `~/.local/bin/cockpit-wake` and `chmod +x`,
  in the same idempotent pass.
- [ ] AC4: It **never** touches unrelated skills already in `~/.copilot/skills/`
  (e.g. `the-copilot-build-method`, `architecture-decisions`).
- [ ] AC5: If `~/.local/bin` is not on `PATH`, it prints the one-line `export PATH=…`
  guidance (and shell-rc snippet) but does **not** edit dotfiles.
- [ ] AC6: `--link` mode symlinks each installed `SKILL.md` and `cockpit-wake` back to
  the repo instead of copying.
- [ ] AC7: `--dry-run` prints "would copy/link" for every target and changes nothing.
- [ ] AC8: A second consecutive run reports every file `already current` and makes no
  destructive change (idempotent — VP1 §10.2).
- [ ] AC9: If a skill source file is missing it errors clearly and exits non-zero
  (does not partially install silently).

## BDD Scenarios

```gherkin
Feature: global skills install

  Scenario: Fresh install copies all eight skills plus cockpit-wake
    Given ~/.copilot/skills is empty of the managed roles
    When  the user runs "./bootstrap.sh global"
    Then  all 8 SKILL.md files are installed
    And   ~/.local/bin/cockpit-wake exists and is executable

  Scenario: Re-running is idempotent
    Given global was already run successfully
    When  the user runs "./bootstrap.sh global" again
    Then  every file is reported "already current"
    And   no backup file is created

  Scenario: Updating a changed skill backs up first
    Given ~/.copilot/skills/worker-dev/SKILL.md differs from the repo
    When  the user runs "./bootstrap.sh global"
    Then  a "SKILL.md.bak-<ts>" backup is written
    And   the repo version replaces it

  Scenario: Unrelated skills are left alone
    Given ~/.copilot/skills/the-copilot-build-method exists
    When  the user runs "./bootstrap.sh global"
    Then  that skill is untouched

  Scenario: Dev mode symlinks
    When  the user runs "./bootstrap.sh global --link"
    Then  ~/.copilot/skills/worker-dev/SKILL.md is a symlink into the repo
```

## Notes
- Reference: ADR-001 (copy + `--link`), ADR-005 (cockpit-wake copy + PATH advice),
  architecture §4. Installs the 8-skill set (§4 loop).
- Depends on: the dispatcher (E1-US2), vendored skills (E2-US1), vendored cockpit-wake
  (E2-US2). The 8th skill source (`copilotcockpit-dev`) is delivered by E5-US3; until
  then AC2 may iterate the 7 present roles — but the loop must enumerate all 8 role
  names so it installs `copilotcockpit-dev` once its source lands.
- Reuse `cc_install_file` / `cc_run` from `common.sh`.
- Path: `lib/cmd-global.sh`. Covered by `tests/unit/cmd-global.bats` (E5-US1).
