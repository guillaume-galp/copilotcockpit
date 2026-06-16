# US1 — `templates/e2e/`: Playwright infrastructure + governed runners

| Field | Value |
|-------|-------|
| Epic | E3 — E2E scaffold |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** developer scaffolding a project, **I want** the Playwright config and the
governed test runners in the template, **so that** the scaffolded `e2e/` runs a green
smoke test immediately against a generic localhost stack (VP1 §5b, ADR-004).

## Acceptance Criteria
- [ ] AC1: These template files exist under `templates/e2e/`:
  `package.json.tmpl`, `playwright.config.ts`, `global-setup.ts`,
  `global-teardown.ts`, `run-audit.sh`, `run-playwright.sh`.
- [ ] AC2: `package.json.tmpl` contains the `@@APP_NAME@@` token and, once substituted,
  is valid JSON (verifiable with `python3 -c 'import json,sys; json.load(...)'`).
- [ ] AC3: `run-audit.sh` and `run-playwright.sh` pass `bash -n`, are executable, carry
  a `--help`, and use only the portable constructs from the E6-US1 cheat-sheet
  (no GNU-only `sed -i`/`date -d`/`readlink -f`).
- [ ] AC4: `run-audit.sh` is the single governed entry point: it accepts `--scope`
  (e.g. `--scope "@smoke"`), snapshots component SHAs via an `E2E_COMPONENTS` mechanism,
  and writes an audit row (`runs/INDEX.md` + a `RUN-*.yaml`) — mirroring the reference
  harnesses (`ulysses-portal/e2e`, `ulysses-index/e2e`).
- [ ] AC5: `run-playwright.sh` invokes Playwright in Docker (the Ubuntu-26-safe runner)
  and is callable by `run-audit.sh`.
- [ ] AC6: `playwright.config.ts`, `global-setup.ts`, `global-teardown.ts` are valid
  TypeScript shape with `# ── CONFIGURE ──` / `// CONFIGURE:` annotated placeholders for
  topology-specific values (ports, base URL) defaulting to `@@BACKEND_PORT@@` /
  `@@FRONTEND_PORT@@` conventions.

## BDD Scenarios

```gherkin
Feature: Template Playwright infra and runners

  Scenario: package.json template substitutes to valid JSON
    Given package.json.tmpl with @@APP_NAME@@
    When  the token is substituted with "acme-portal"
    Then  the result parses as valid JSON

  Scenario: Runners are syntactically valid and portable
    When  I run "bash -n run-audit.sh" and "bash -n run-playwright.sh"
    Then  both exit 0
    And   neither uses a GNU-only flag flagged by the portability spike

  Scenario: run-audit.sh writes an audit row
    Given a scaffolded e2e/ with the smoke spec
    When  "./run-audit.sh --scope @smoke" runs
    Then  a new row appears in runs/INDEX.md
    And   a RUN-*.yaml is written
```

## Notes
- Reference: VP1 §5b, ADR-004 (tokens + CONFIGURE blocks), architecture §5/§6.
  Source the runner shape from the reference repos `ulysses-portal/e2e` and
  `ulysses-index/e2e`.
- Depends on E6-US1 (portability) — `run-audit.sh`/`run-playwright.sh` must be portable.
- These files are classified `framework` in `MANIFEST.toml` (E3-US4) except
  `package.json` which is `seed`.
- Paths under `templates/e2e/`.
