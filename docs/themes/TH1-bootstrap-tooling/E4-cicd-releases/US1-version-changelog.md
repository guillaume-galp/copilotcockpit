# US1 — `VERSION` + `CHANGELOG.md` (semver source of truth)

| Field | Value |
|-------|-------|
| Epic | E4 — CI/CD & releases |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | XS |

## As a…
**As a** release agent, **I want** a single `VERSION` file and a structured
`CHANGELOG.md`, **so that** there is one source of semver truth and an agent-maintained
release-notes record (ADR-008, architecture §9).

## Acceptance Criteria
- [ ] AC1: `VERSION` exists at the repo root and contains a single plain semver string
  with no `v` prefix and no trailing content (e.g. `0.1.0`), matching
  `^[0-9]+\.[0-9]+\.[0-9]+$`.
- [ ] AC2: There is **no** `package.json` version field in the repo root to keep in sync
  — `VERSION` is authoritative.
- [ ] AC3: `CHANGELOG.md` exists at the repo root with a top-of-file convention of
  `## vX.Y.Z — YYYY-MM-DD` sections (newest first), and at least an initial section or a
  documented "Unreleased" placeholder.
- [ ] AC4: `CHANGELOG.md` documents that sections are prepended by the
  `copilotcockpit-dev` agent from the squash-commit body and sourced into the GitHub
  Release body by `release.yml`.

## BDD Scenarios

```gherkin
Feature: VERSION and CHANGELOG

  Scenario: VERSION is a bare semver string
    When  I read VERSION
    Then  it matches ^[0-9]+\.[0-9]+\.[0-9]+$
    And   it has no "v" prefix

  Scenario: CHANGELOG uses the dated section convention
    When  I read CHANGELOG.md
    Then  it contains a "## v" dated section heading (or an Unreleased placeholder)
```

## Notes
- Reference: ADR-008 (VERSION single source of truth; agent-maintained CHANGELOG),
  architecture §9.
- Trivial config files — no dependency on other stories.
- Consumed by `release.yml` (E4-US2) and maintained per the `copilotcockpit-dev`
  runbook (E5-US3).
- Paths: `VERSION`, `CHANGELOG.md`.
