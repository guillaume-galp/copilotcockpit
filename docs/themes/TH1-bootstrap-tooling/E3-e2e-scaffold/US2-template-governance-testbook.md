# US2 — `templates/e2e/`: governance, test-book, tests & runs skeleton

| Field | Value |
|-------|-------|
| Epic | E3 — E2E scaffold |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer scaffolding a project, **I want** the governance docs, the test-book
skeleton, the smoke test files, and the audit-trail structure, **so that** the harness
is self-auditing and ready for `/setup-e2e-runbook` to extend (VP1 §5b, §7).

## Acceptance Criteria
- [ ] AC1: Governance files exist: `governance/GOVERNANCE.md`,
  `governance/run-schema.yaml`, `governance/flaky-known.md`.
- [ ] AC2: `governance/run-schema.yaml` is valid YAML and describes the `RUN-*.yaml`
  audit record schema written by `run-audit.sh`.
- [ ] AC3: Test-book skeleton exists: `test-book/SUMMARY.md`, `test-book/TC-FORMAT.md`,
  `test-book/CH01-smoke.md` — `CH01-smoke.md` contains Gherkin smoke scenarios tagged
  `@smoke` and `@TC-…-NNN` per the `TC-FORMAT.md` convention.
- [ ] AC4: Tests skeleton exists: `tests/helpers.ts`, `tests/smoke.spec.ts` —
  `smoke.spec.ts` is a runnable Playwright spec tagged `@smoke` that hits the generic
  `@@HEALTH_PATH@@` (default `/health`) and passes against a generic localhost stack.
- [ ] AC5: Audit-trail structure exists: `runs/INDEX.md` (header row only) and
  `runs/.gitkeep`.
- [ ] AC6: `tests/smoke.spec.ts` tag references match a `@TC-…` entry in
  `test-book/CH01-smoke.md` (cross-linking convention preserved).

## BDD Scenarios

```gherkin
Feature: Governance, test-book and tests skeleton

  Scenario: run-schema.yaml is valid YAML
    When  I parse governance/run-schema.yaml
    Then  it loads without error

  Scenario: Smoke spec is tag-linked to the test-book
    Given tests/smoke.spec.ts carries a @TC-XXX-NNN tag
    When  I search test-book/CH01-smoke.md for that tag
    Then  a matching scenario is found

  Scenario: Audit index is born empty but present
    Given a freshly scaffolded e2e/
    Then  runs/INDEX.md exists with a header
    And   runs/.gitkeep exists
```

## Notes
- Reference: VP1 §5b, §7 (3-tier audit trail), architecture §2/§5. Source the
  governance + test-book shape from the reference repos.
- `MANIFEST.toml` (E3-US4) classes: `governance/GOVERNANCE.md`,
  `governance/run-schema.yaml`, `test-book/TC-FORMAT.md` → `framework`;
  `test-book/SUMMARY.md`, `runs/**` → `project`; `test-book/CH01-smoke.md`,
  `tests/smoke.spec.ts`, `tests/helpers.ts` → `seed`.
- No portability dependency (no shell here) — independent of E6-US1.
- Paths under `templates/e2e/`.
