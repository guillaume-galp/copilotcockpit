# US2 — `.github/workflows/release.yml`: tag-driven tarball + sha256 + `gh release`

| Field | Value |
|-------|-------|
| Epic | E4 — CI/CD & releases |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** maintainer, **I want** a push of a `vX.Y.Z` tag to build and publish the
checksum-verified install tarball, **so that** the cold-install one-liner always
resolves to a deterministic, latest release (ADR-007, architecture §8).

## Acceptance Criteria
- [ ] AC1: `.github/workflows/release.yml` exists, is valid YAML, and triggers **only**
  on `push` of tags matching `v[0-9]+.[0-9]+.[0-9]+` (excludes branch pushes and
  pre-release `-suffix` tags like `v1.0.0-rc1`).
- [ ] AC2: It assembles `copilotcockpit-${TAG}.tar.gz` containing **exactly** the
  install surface — `bootstrap.sh lib/ skills/ bin/ templates/ README.md` — under a top
  `copilotcockpit/` dir, and **excludes** `docs/`, `.git/`, `tests/`, and CI files
  (ADR-007).
- [ ] AC3: It computes `copilotcockpit-${TAG}.tar.gz.sha256`.
- [ ] AC4: It creates the GitHub Release via `gh release create "${TAG}" --latest`,
  attaching the tarball, the `.sha256`, and `install.sh`, with `--title
  "copilotcockpit ${TAG}"` and `--notes-file` = the matching `CHANGELOG.md` section if
  present, else a default note.
- [ ] AC5: A stable unversioned alias is resolvable via
  `releases/latest/download/copilotcockpit.tar.gz` and `…/install.sh` (so the one-liner
  needs no API call).
- [ ] AC6: A post-publish **Category-5** job downloads the published tarball, verifies
  its `.sha256`, extracts it, and runs `./copilotcockpit/bootstrap.sh global --dry-run`
  + `doctor` from the extracted dir — failing the release if the artefact is broken.

## BDD Scenarios

```gherkin
Feature: release workflow

  Scenario: A release tag publishes the install surface tarball
    Given the maintainer pushes tag "v1.2.3"
    When  release.yml runs
    Then  copilotcockpit-v1.2.3.tar.gz and its .sha256 are attached to the release
    And   the tarball contains bootstrap.sh, lib/, skills/, bin/, templates/, README.md
    And   it does not contain docs/ or tests/

  Scenario: Pre-release tags do not trigger
    Given the maintainer pushes tag "v1.2.3-rc1"
    Then  release.yml does not run

  Scenario: Category-5 validates the published artefact
    Given the release is published
    When  the post-publish job downloads and verifies the tarball
    Then  global --dry-run and doctor succeed from the extracted dir
    And   a failure here fails the release
```

## Notes
- Reference: ADR-007 (release tarball, `--latest`, integrity), ADR-008 (Category 5),
  architecture §8/§9.
- Depends on the template (E3-US1/US2/US3 — defines the tarball contents), `VERSION`/
  `CHANGELOG.md` (E4-US1), and `install.sh` (E2-US4, shipped as a release asset here).
- Path: `.github/workflows/release.yml`.
