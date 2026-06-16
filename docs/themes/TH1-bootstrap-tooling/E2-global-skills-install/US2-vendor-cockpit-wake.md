# US2 — Vendor `cockpit-wake` into `bin/`

| Field | Value |
|-------|-------|
| Epic | E2 — Global skills install |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | S |

## As a…
**As a** harness maintainer, **I want** the `cockpit-wake` CLI committed verbatim in
`bin/`, **so that** `global` can install a working binary onto the user's PATH with a
simple file copy and no build step (ADR-005, NFR-3).

## Acceptance Criteria
- [ ] AC1: `bin/cockpit-wake` exists, is committed verbatim from the working
  `~/.local/bin/cockpit-wake`, and is executable (`chmod +x`).
- [ ] AC2: It is a single-file Python 3 script: first line is `#!/usr/bin/env python3`,
  it imports **stdlib only** (no third-party imports), and it passes
  `python3 -m py_compile bin/cockpit-wake`.
- [ ] AC3: `python3 bin/cockpit-wake --help` (or equivalent) runs and prints usage
  without a traceback.
- [ ] AC4: No build artefacts, no compiled binary, and no packaging metadata are added
  — the file *is* the deliverable.

## BDD Scenarios

```gherkin
Feature: Vendored cockpit-wake

  Scenario: The script compiles
    Given bin/cockpit-wake is committed
    When  I run "python3 -m py_compile bin/cockpit-wake"
    Then  it exits 0 with no error

  Scenario: It is stdlib-only Python 3
    When  I inspect the shebang and imports
    Then  the shebang is "#!/usr/bin/env python3"
    And   no third-party module is imported

  Scenario: Help runs cleanly
    When  I run "python3 bin/cockpit-wake --help"
    Then  usage is printed and no traceback occurs
```

## Notes
- Reference: ADR-005 (vendor single-file Python script; ~344 lines, stdlib only;
  shells out to `at`/`cron`/`tmux`).
- Source: copy the exact contents of `~/.local/bin/cockpit-wake`.
- Runtime deps (`at`/`cron`/`tmux`) are *not* this story's concern — they are checked
  by `doctor` (E1-US3). This story only vendors the file.
- Consumed by `cmd-global.sh` (E2-US3), which copies it to `~/.local/bin/cockpit-wake`.
