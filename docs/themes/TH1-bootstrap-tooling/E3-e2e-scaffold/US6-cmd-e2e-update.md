# US6 — `lib/cmd-e2e.sh --update`: content-preserving refresh via `MANIFEST.toml`

| Field | Value |
|-------|-------|
| Epic | E3 — E2E scaffold |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer with an existing harness, **I want** `e2e <dir> --update` to refresh
framework files while never touching my tests, chapters, runs or env, **so that**
upgrading is a no-brainer, not a merge nightmare (VP1 §6, ADR-006, NFR-5).

## Acceptance Criteria
- [ ] AC1: `bootstrap.sh e2e <dir> --update` requires `<dir>/e2e/` to already exist;
  otherwise it points the user at a plain scaffold and exits non-zero.
- [ ] AC2: For each template path it consults `MANIFEST.toml` (via the E6-US2 matcher)
  and applies: `framework` → overwrite (backup old to `<file>.bak-<ts>` first);
  `seed` → create only if missing, never overwrite; `project` → never touch.
- [ ] AC3: An unclassified path defaults to `project` (never touch) — the safe failure
  mode (ADR-006 Risks).
- [ ] AC4: It provably leaves untouched: `tests/**`, `test-book/CH0[2-9]*.md`,
  `runs/**`, `.env.local`, and the AI-generated `tmux-cockpit*.sh` and
  `.github/skills/**` overlays (VP1 §10.4).
- [ ] AC5: It prints a one-line per-changed-file diff summary and a final count of
  refreshed / skipped / preserved files.
- [ ] AC6: `--update --dry-run` lists exactly what *would* change, touches nothing, and
  exits 0.
- [ ] AC7: Running `--update` twice in a row with no upstream template change reports all
  framework files `already current` and writes no backups (idempotent — NFR-1).

## BDD Scenarios

```gherkin
Feature: e2e --update content-preserving refresh

  Scenario: Framework file is refreshed with a backup
    Given an existing e2e/ whose run-audit.sh is older than the template
    When  the user runs "./bootstrap.sh e2e <dir> --update"
    Then  run-audit.sh is overwritten with the current version
    And   a run-audit.sh.bak-<ts> backup is written

  Scenario: Project content is never touched
    Given e2e/tests/foo.spec.ts and e2e/test-book/CH02-orders.md and e2e/.env.local
    When  the user runs "--update"
    Then  all three files are byte-for-byte unchanged

  Scenario: Seed file is created only if missing
    Given e2e/package.json already exists and was edited
    When  the user runs "--update"
    Then  package.json is left untouched (not overwritten)

  Scenario: Dry-run previews without changing anything
    When  the user runs "--update --dry-run"
    Then  the would-change list is printed
    And   no file on disk is modified

  Scenario: Update on a non-existent e2e/ is refused
    Given <dir> has no e2e/
    When  the user runs "--update"
    Then  it points at a plain scaffold and exits non-zero
```

## Notes
- Reference: ADR-006 (class-driven refresh, backups, dry-run), architecture §7, NFR-5,
  VP1 §10.4.
- Depends on E3-US5 (scaffold logic to extend) and E3-US4 (`MANIFEST.toml`). Uses the
  E6-US2-validated matcher.
- Path: `--update` branch within `lib/cmd-e2e.sh`. Covered by `tests/unit/cmd-e2e.bats`
  (E5-US1).
