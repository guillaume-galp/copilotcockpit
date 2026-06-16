# US2 — Categories 2–4: template integrity, skills lint & integration smoke

| Field | Value |
|-------|-------|
| Epic | E5 — Developer skill & test infra |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** contributor, **I want** automated checks for template integrity, skill-file
lint, and dry-run integration smoke, **so that** the scaffold, skills, and bootstrap
commands stay correct on every PR (ADR-008, architecture §9).

## Acceptance Criteria
- [ ] AC1: **Category 2** — `tests/template/check-template.sh` exists and asserts:
  every `*.tmpl` has no unresolved tokens after a test substitution;
  `package.json.tmpl` is valid JSON post-substitution; `MANIFEST.toml` accounts for
  **every** file in `templates/e2e/` (the E3-US4 coverage check);
  `run-audit.sh` & `run-playwright.sh` pass `bash -n`.
- [ ] AC2: **Category 3** — `tests/skills/lint-skills.sh` exists and asserts every
  `skills/*/SKILL.md` is valid Markdown with parseable frontmatter (`python3 -c "import
  yaml"`) and non-empty `name:`/`description:`.
- [ ] AC3: **Category 4** — `tests/integration/smoke.bats` exists and asserts:
  `global --dry-run` lists all **8** skills + `cockpit-wake`; `e2e <tmp> --dry-run`
  prints the expected scaffold file list; `doctor` exits 0 and reports each prerequisite
  found/missing.
- [ ] AC4: `./run-tests.sh template`, `./run-tests.sh skills`, and
  `./run-tests.sh integration` each invoke the matching script/suite and propagate its
  exit code.
- [ ] AC5: All three categories exit 0 against the implemented repo.

## BDD Scenarios

```gherkin
Feature: Categories 2-4

  Scenario: Template integrity catches an unresolved token
    Given a *.tmpl file with an unsanctioned @@TOKEN@@
    When  check-template.sh runs
    Then  it fails and names the offending file

  Scenario: Manifest coverage is enforced
    Given a template file missing from MANIFEST.toml
    When  check-template.sh runs
    Then  it fails reporting the unclassified path

  Scenario: Skills lint rejects empty frontmatter
    Given a SKILL.md whose description is empty
    When  lint-skills.sh runs
    Then  it fails naming that skill

  Scenario: Integration smoke lists eight skills
    When  "global --dry-run" runs under the smoke suite
    Then  all 8 skills plus cockpit-wake are listed
```

## Notes
- Reference: ADR-008 (Categories 2/3/4), architecture §9.
- Depends on: the template + manifest (E3-US1/US2/US3/US4), vendored skills (E2-US1),
  and the bootstrap commands (E2-US3, E3-US5). Hooks into `run-tests.sh` (E5-US1).
- Paths: `tests/template/check-template.sh`, `tests/skills/lint-skills.sh`,
  `tests/integration/smoke.bats`.
