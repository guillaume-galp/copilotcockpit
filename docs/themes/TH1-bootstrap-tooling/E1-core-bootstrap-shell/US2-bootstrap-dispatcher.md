# US2 — `bootstrap.sh` dispatcher

| Field | Value |
|-------|-------|
| Epic | E1 — Core bootstrap shell |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | S |

## As a…
**As a** developer who just found the repo, **I want** one self-documenting entry point
that routes to the right subcommand, **so that** I can succeed without reading the wiki
(NFR-8, ADR-003).

## Acceptance Criteria
- [ ] AC1: `bootstrap.sh` exists, is executable (`chmod +x`), and passes `bash -n`.
- [ ] AC2: Run with **no args** (or `--help`/`-h`) it prints usage listing the three
  subcommands and their flags, and exits 0:
  `global [--link] [--dry-run] [--from-release <ref>]`,
  `e2e <dir> [--update] [--no-git] [--yes] [--dry-run]`,
  `doctor`.
- [ ] AC3: It sources `lib/common.sh` and resolves its own directory portably (works
  when invoked via relative path, absolute path, or symlink — per E6-US1 findings).
- [ ] AC4: It dispatches `global` → `lib/cmd-global.sh`, `e2e` → `lib/cmd-e2e.sh`,
  `doctor` → `lib/cmd-doctor.sh` via a `case "$1"` and forwards remaining args.
- [ ] AC5: An unknown subcommand prints an error naming the bad verb plus the usage,
  and exits with a non-zero code (e.g. 2).
- [ ] AC6: A global `--dry-run` parsed before/after the subcommand sets the shared
  `DRY_RUN` flag from `common.sh`.

## BDD Scenarios

```gherkin
Feature: Bootstrap dispatcher

  Scenario: No arguments prints usage
    Given the repo is cloned
    When  the user runs "./bootstrap.sh"
    Then  usage text listing global, e2e and doctor is printed
    And   the exit code is 0

  Scenario: Unknown subcommand is rejected
    When  the user runs "./bootstrap.sh frobnicate"
    Then  an error naming "frobnicate" is printed
    And   the usage is shown
    And   the exit code is non-zero

  Scenario: Subcommand delegation
    Given lib/cmd-doctor.sh exists
    When  the user runs "./bootstrap.sh doctor"
    Then  bootstrap.sh sources common.sh and invokes lib/cmd-doctor.sh
```

## Notes
- Reference: ADR-003 (single dispatcher, `case "$1"`, no CLI framework, P1).
- Depends on `lib/common.sh` (E1-US1) being present to source.
- Keep the dispatcher ~30 lines — pure routing; all logic lives in `cmd-*.sh`.
- Path: `bootstrap.sh`. Smoke-tested later by E5-US2 (integration).
