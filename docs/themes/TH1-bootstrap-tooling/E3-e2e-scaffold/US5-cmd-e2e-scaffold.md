# US5 — `lib/cmd-e2e.sh` scaffold (copy + tokenise + git init + npm install + handoff)

| Field | Value |
|-------|-------|
| Epic | E3 — E2E scaffold |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer, **I want** `bootstrap.sh e2e <dir>` to drop a complete, runnable
`e2e/` sub-repo into my project, **so that** I get a self-auditing smoke harness in
minutes (VP1 §5b/§6, ADR-002, ADR-004, NFR-1, NFR-7).

## Acceptance Criteria
- [ ] AC1: `lib/cmd-e2e.sh` exists, passes `bash -n`, and is invoked by
  `bootstrap.sh e2e <dir>`.
- [ ] AC2: It validates `<dir>` is an existing directory; if `<dir>/e2e` already exists
  and `--update` was not given, it **refuses** and points the user at `--update`
  (exit non-zero, no clobber — NFR-1).
- [ ] AC3: It resolves the four Tier-1 tokens with first-hit-wins order:
  `.e2e-config.yaml` → interactive prompt (TTY only, unless `--yes`/no TTY) → defaults
  (`@@APP_NAME@@`=basename, ports `8000`/`5173`, health `/health`) — ADR-004.
- [ ] AC4: It copies `templates/e2e/` → `<dir>/e2e/`, copying verbatim files as-is and
  substituting tokens into `*.tmpl` files (stripping the `.tmpl` suffix); no
  unresolved `@@…@@` token remains.
- [ ] AC5: Scaffolding is atomic: it writes to a staging dir and moves into place, so an
  interruption leaves no partial `<dir>/e2e/` (architecture §7).
- [ ] AC6: It runs `git init` inside `<dir>/e2e/` and makes an initial commit of the
  skeleton, unless `--no-git` is given (ADR-002).
- [ ] AC7: It runs `npm install` inside `<dir>/e2e/` (Playwright deps), and on success
  prints the handoff: *"Smoke harness ready. Next: run /setup-e2e-cockpit then
  /setup-e2e-runbook"* and the suggested `/e2e/` parent-`.gitignore` line.
- [ ] AC8: `--dry-run` prints the full scaffold file list and the resolved tokens, but
  writes nothing, does not `git init`, and does not `npm install`.

## BDD Scenarios

```gherkin
Feature: e2e scaffold

  Scenario: Fresh scaffold produces a runnable, git-initialised e2e/
    Given a target project dir without an e2e/
    When  the user runs "./bootstrap.sh e2e ~/git/acme-portal"
    Then  ~/git/acme-portal/e2e/ is created with the template files
    And   tokens are substituted (basename "acme-portal" as APP_NAME)
    And   e2e/ has its own .git and an initial commit
    And   the handoff message is printed

  Scenario: Refuses to clobber an existing e2e/
    Given <dir>/e2e already exists
    When  the user runs "./bootstrap.sh e2e <dir>" without --update
    Then  it refuses, points at --update, and exits non-zero

  Scenario: Non-interactive run takes defaults
    Given no TTY and no .e2e-config.yaml
    When  e2e scaffolds with --yes
    Then  default tokens are used without prompting

  Scenario: Dry-run writes nothing
    When  the user runs "./bootstrap.sh e2e <tmp> --dry-run"
    Then  the scaffold file list is printed
    And   no e2e/ directory is created

  Scenario: --no-git skips repo init
    When  the user runs "./bootstrap.sh e2e <dir> --no-git"
    Then  e2e/ has no .git directory
```

## Notes
- Reference: VP1 §5b/§6, ADR-002 (git init), ADR-004 (token resolution), architecture
  §5/§6/§7 (atomic staging).
- Depends on the dispatcher (E1-US2) and the full template (E3-US1/US2/US3).
- Reuse `common.sh` helpers (`cc_run`, dry-run, portable copy). `--update` is a separate
  story (E3-US6).
- Path: `lib/cmd-e2e.sh`. Covered by `tests/unit/cmd-e2e.bats` (E5-US1).
