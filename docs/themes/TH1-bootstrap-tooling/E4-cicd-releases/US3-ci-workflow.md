# US3 — `.github/workflows/ci.yml`: PR checks (test categories 1–4)

| Field | Value |
|-------|-------|
| Epic | E4 — CI/CD & releases |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | S |

## As a…
**As a** contributor, **I want** every PR to run the four pre-release test categories,
**so that** nothing lands on `main` without a green quality gate (ADR-008, architecture
§9).

## Acceptance Criteria
- [ ] AC1: `.github/workflows/ci.yml` exists, is valid YAML, and triggers on
  `pull_request` against `main` (and `push` to PR branches).
- [ ] AC2: It installs the contributor deps (`bats`, `gh` as needed, `node`, `python3`)
  and runs all four categories via the dispatcher:
  `./run-tests.sh unit`, `template`, `skills`, `integration` (or `run-tests.sh all`
  excluding category 5).
- [ ] AC3: Any failing category fails the workflow (the merge gate).
- [ ] AC4: It honours `[skip ci]` on the version-bump commit so the `chore: bump
  version …[skip ci]` push to `main` does not trigger a redundant run.
- [ ] AC5: It does **not** run Category 5 (release-asset validation) — that lives in
  `release.yml` (E4-US2).

## BDD Scenarios

```gherkin
Feature: CI PR checks

  Scenario: A PR runs categories 1-4
    Given a pull request against main
    When  ci.yml runs
    Then  unit, template, skills and integration suites all execute

  Scenario: A failing category blocks merge
    Given the template-integrity suite fails
    When  ci.yml runs
    Then  the workflow fails and the PR cannot merge

  Scenario: The version-bump commit skips CI
    Given a commit "chore: bump version to v1.2.3 [skip ci]"
    When  it is pushed to main
    Then  ci.yml does not run
```

## Notes
- Reference: ADR-008 (Categories 1–4 on PR; `[skip ci]`), architecture §9.
- Depends on the test infra (E5-US1 provides `run-tests.sh` + category 1; E5-US2
  provides categories 2–4). Without those scripts there is nothing to run.
- Path: `.github/workflows/ci.yml`.
