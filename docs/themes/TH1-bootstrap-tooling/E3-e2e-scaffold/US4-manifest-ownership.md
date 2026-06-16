# US4 — `templates/e2e/MANIFEST.toml` ownership classification

| Field | Value |
|-------|-------|
| Epic | E3 — E2E scaffold |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | S |

## As a…
**As a** developer upgrading a harness, **I want** every template path classified as
`framework` / `seed` / `project`, **so that** `e2e --update` refreshes the right files
and never clobbers my work (ADR-006, NFR-5).

## Acceptance Criteria
- [ ] AC1: `templates/e2e/MANIFEST.toml` exists with `[framework]`, `[seed]`, and
  `[project]` sections, each a `paths = [...]` array, and is valid TOML.
- [ ] AC2: `[framework]` includes (at least): `run-audit.sh`, `run-playwright.sh`,
  `playwright.config.ts`, `global-setup.ts`, `global-teardown.ts`,
  `governance/GOVERNANCE.md`, `governance/run-schema.yaml`, `test-book/TC-FORMAT.md`,
  `.github/copilot-instructions.md`.
- [ ] AC3: `[seed]` includes: `package.json`, `.env.example`, `.gitignore`,
  `test-book/CH01-smoke.md`, `tests/smoke.spec.ts`, `tests/helpers.ts`.
- [ ] AC4: `[project]` includes: `tests/**`, `test-book/CH0[2-9]*.md`,
  `test-book/CH1*.md`, `test-book/SUMMARY.md`, `runs/**`, `tmux-cockpit.sh`,
  `tmux-cockpit-local.sh`, `.github/skills/**`, `.env.local`, `.env.*`.
- [ ] AC5: **Every** file produced by E3-US1/US2/US3 is accounted for by exactly one
  class (no template path is unclassified) — verifiable by a coverage check that lists
  the template tree and matches each path against the manifest globs using the
  E6-US2-validated matcher.
- [ ] AC6: The classification matcher matches the technique proven in E6-US2, including
  the safe default (unlisted ⇒ `project`).

## BDD Scenarios

```gherkin
Feature: MANIFEST.toml ownership

  Scenario: Manifest is valid and three-classed
    When  I parse templates/e2e/MANIFEST.toml
    Then  it has [framework], [seed] and [project] path arrays

  Scenario: Every template path is classified exactly once
    Given the templates/e2e tree from E3-US1..US3
    When  each path is matched against the manifest globs
    Then  each path matches exactly one class
    And   no path is left unclassified

  Scenario: A project-owned file is never framework
    When  I classify "tests/smoke_extra.spec.ts"
    Then  it resolves to "project"
```

## Notes
- Reference: ADR-006 (the canonical `MANIFEST.toml` content is reproduced in
  architecture §7), NFR-5.
- Depends on E6-US2 (matcher technique) and on E3-US1/US2/US3 (the files to classify).
- The coverage check (AC5) is also enforced later by the Category-2 template-integrity
  test (E5-US2).
- Path: `templates/e2e/MANIFEST.toml`.
