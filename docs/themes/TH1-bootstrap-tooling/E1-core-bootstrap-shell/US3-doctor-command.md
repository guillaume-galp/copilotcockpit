# US3 — `lib/cmd-doctor.sh` prerequisites + drift detection

| Field | Value |
|-------|-------|
| Epic | E1 — Core bootstrap shell |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer setting up or troubleshooting the harness, **I want**
`bootstrap.sh doctor` to report every prerequisite and any install drift, **so that**
I can fix gaps before they fail silently (NFR-3, ADR-005).

## Acceptance Criteria
- [ ] AC1: `lib/cmd-doctor.sh` exists, passes `bash -n`, and is invoked by
  `bootstrap.sh doctor`.
- [ ] AC2: It checks for each prerequisite and prints `found`/`missing` with the
  resolved version where applicable: `bash`, `git`, `node`/`npm`, `docker`,
  `python3`, `tmux`, plus the `cockpit-wake` runtime deps `at` and `cron`, and
  (contributor-only, reported as optional) `bats`.
- [ ] AC3: It verifies `~/.local/bin` is on `PATH` and reports the exact
  `export PATH=…` remediation line when it is not.
- [ ] AC4: It reports install state / **drift**: for each of the 8 skills it compares
  `skills/<role>/SKILL.md` against `~/.copilot/skills/<role>/SKILL.md`
  (`installed & current` / `drifted` / `not installed`), and the same for
  `bin/cockpit-wake` vs `~/.local/bin/cockpit-wake`.
- [ ] AC5: Exit code is **0** when all *hard* prerequisites (`bash`, `git`, `node`,
  `python3`) are present (missing optional deps like `at`/`cron`/`docker` are warnings,
  not failures); non-zero only if a hard prerequisite is missing.
- [ ] AC6: Output is grouped and human-readable (e.g. `Prerequisites`, `PATH`,
  `Skills`, `cockpit-wake` sections).

## BDD Scenarios

```gherkin
Feature: doctor command

  Scenario: All prerequisites present
    Given bash, git, node, python3 are installed
    When  the user runs "./bootstrap.sh doctor"
    Then  each is reported "found" with a version
    And   the exit code is 0

  Scenario: Missing optional dependency is a warning
    Given "at" is not installed
    When  the user runs doctor
    Then  "at" is reported "missing" as a warning
    And   the exit code is still 0

  Scenario: PATH guidance when ~/.local/bin is absent
    Given ~/.local/bin is not on PATH
    When  the user runs doctor
    Then  the exact "export PATH=..." remediation line is printed

  Scenario: Skill drift is surfaced
    Given ~/.copilot/skills/worker-dev/SKILL.md differs from the repo copy
    When  the user runs doctor
    Then  worker-dev is reported "drifted"
```

## Notes
- Reference: ADR-005 (doctor checks python3/at/cron/tmux + PATH), ADR-001 (drift),
  architecture §4, §11.
- Depends on the dispatcher (E1-US2) and `common.sh` (transitively). It reads the
  `skills/` and `bin/` sources but does not require `cmd-global.sh`.
- Path: `lib/cmd-doctor.sh`. Drift comparison reuses `cc_install_file`'s identity check
  helper from `common.sh`.
