# US1 — `lib/common.sh` shared helpers (logging, OS detection, idempotency, dry-run)

| Field | Value |
|-------|-------|
| Epic | E1 — Core bootstrap shell |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** bootstrap subcommand author, **I want** a single sourced library of
cross-cutting helpers, **so that** logging, OS detection, backup-before-overwrite,
and `--dry-run` are implemented once and inherited by every `cmd-*.sh` (ADR-003, NFR-1).

## Acceptance Criteria
- [ ] AC1: `lib/common.sh` exists and is sourceable (`source lib/common.sh` exits 0,
  passes `bash -n lib/common.sh`).
- [ ] AC2: Provides logging helpers `log_info`, `log_warn`, `log_error`, `log_ok`
  (`already current` / success) writing to stderr with a consistent prefix.
- [ ] AC3: Provides `detect_os` returning `linux` or `macos`, and portable shims that
  follow the E6-US1 cheat-sheet — in particular `cc_realpath`, `cc_install_file`
  (copy + backup), and a date/timestamp helper `cc_timestamp` — with **no** GNU-only
  flags (no bare `sed -i`, no `readlink -f`, no `date -d`).
- [ ] AC4: Provides `cc_install_file <src> <dst>` that: skips identical files
  (logs `already current`, exit 0), backs up a differing existing file to
  `<dst>.bak-<ts>` before overwriting, and honours dry-run (prints "would copy …",
  changes nothing).
- [ ] AC5: Exposes a `DRY_RUN` flag (set from `--dry-run`); a `cc_run` wrapper executes
  or merely prints a command based on `DRY_RUN`.
- [ ] AC6: Re-sourcing `common.sh` twice in one shell is safe (idempotent guard, no
  redefinition errors).

## BDD Scenarios

```gherkin
Feature: Shared bootstrap library

  Scenario: Installing an identical file is a no-op
    Given a destination file identical to the source
    When  cc_install_file is called
    Then  it logs "already current"
    And   it exits 0 without writing a backup

  Scenario: Installing a differing file backs up first
    Given a destination file that differs from the source
    When  cc_install_file is called (not dry-run)
    Then  a "<dst>.bak-<timestamp>" backup is written
    And   the destination is overwritten with the source content

  Scenario: Dry-run changes nothing
    Given DRY_RUN is set
    When  cc_install_file is called on a differing file
    Then  it prints "would copy" guidance
    And   no file on disk is modified

  Scenario: OS detection is portable
    Given the script runs on macOS
    When  detect_os is called
    Then  it returns "macos" without invoking GNU-only flags
```

## Notes
- Reference: ADR-003 (dispatcher + `lib/common.sh`), NFR-1, NFR-2.
- Implement portable constructs per the E6-US1 findings cheat-sheet (this story
  depends on that spike): temp-file + `mv` instead of `sed -i`; `python3`/`cc_timestamp`
  for dates; a `cc_realpath` fallback for systems lacking `readlink -f`.
- Path: `lib/common.sh`. Covered later by `tests/unit/` (E5-US1).
