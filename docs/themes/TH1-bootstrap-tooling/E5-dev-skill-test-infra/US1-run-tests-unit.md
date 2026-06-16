# US1 — `run-tests.sh` dispatcher + Category-1 `bats` unit tests

| Field | Value |
|-------|-------|
| Epic | E5 — Developer skill & test infra |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** contributor, **I want** a `run-tests.sh` dispatcher and `bats` unit tests for
each `cmd-*.sh`, **so that** "tests pass" has a precise, runnable meaning before I push
(ADR-008, architecture §9).

## Acceptance Criteria
- [ ] AC1: `run-tests.sh` exists at the repo root, passes `bash -n`, and accepts
  `unit|template|skills|integration|all` — running the matching suite(s) and exiting
  non-zero on any failure.
- [ ] AC2: `tests/unit/cmd-global.bats`, `tests/unit/cmd-e2e.bats`, and
  `tests/unit/cmd-doctor.bats` exist and run under `bats`.
- [ ] AC3: Each bats suite proves, per its `cmd-*.sh`: **idempotency** (running the
  command twice yields the same state), **dry-run = no side-effects** (`--dry-run`
  writes nothing), and **error paths** (missing arg / wrong dir / refusing to clobber
  exit with the correct non-zero code and message).
- [ ] AC4: The unit tests operate against temp dirs / fake `HOME` (no mutation of the
  real `~/.copilot/skills/` or `~/.local/bin/`).
- [ ] AC5: `./run-tests.sh unit` exits 0 against the implemented commands.

## BDD Scenarios

```gherkin
Feature: Category-1 unit tests + dispatcher

  Scenario: The dispatcher runs the unit category
    When  I run "./run-tests.sh unit"
    Then  the bats suites in tests/unit/ execute
    And   the exit code reflects pass/fail

  Scenario: cmd-global idempotency is asserted
    Given a fake HOME
    When  the bats suite runs global twice
    Then  the second run reports "already current" and writes no backup

  Scenario: cmd-e2e refuses to clobber
    Given an existing e2e/ in a temp dir
    When  the bats suite runs e2e without --update
    Then  the test asserts a non-zero exit and the --update hint

  Scenario: Unknown category errors
    When  I run "./run-tests.sh bogus"
    Then  it prints usage and exits non-zero
```

## Notes
- Reference: ADR-008 (Category 1, `run-tests.sh`), architecture §9.
- Depends on the commands under test: `cmd-doctor.sh` (E1-US3), `cmd-global.sh`
  (E2-US3), `cmd-e2e.sh` incl. `--update` (E3-US6).
- Contributor-only deps (`bats`); end users never need these (P5).
- Paths: `run-tests.sh`, `tests/unit/*.bats`.
