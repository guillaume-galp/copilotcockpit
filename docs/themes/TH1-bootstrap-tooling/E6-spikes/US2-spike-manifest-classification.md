# US2 — SPIKE: MANIFEST.toml glob classification correctness

| Field | Value |
|-------|-------|
| Epic | E6 — Spikes |
| Theme | TH1 — Bootstrap Tooling |
| Status | done |
| Size | XS |

## As a…
**As a** bootstrap toolkit author, **I want** a proven bash technique that classifies
every scaffolded path as `framework` / `seed` / `project` from the `MANIFEST.toml`
globs, **so that** `e2e --update` provably refreshes the right files and never clobbers
project-owned content (NFR-5, ADR-006).

## Acceptance Criteria
- [x] AC1: A findings note is produced at
  `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/manifest-classification.md`.
- [x] AC2: A throwaway fixture `e2e/` tree (including `tests/a.spec.ts`,
  `test-book/CH01-smoke.md`, `test-book/CH02-foo.md`, `runs/RUN-x.yaml`,
  `run-audit.sh`, `package.json`, `.env.local`, `.github/skills/worker-dev/SKILL.md`)
  is classified by the candidate technique and the result table is recorded.
- [x] AC3: The note confirms the chosen matcher correctly resolves the ADR-006 globs:
  `tests/**`, `test-book/CH0[2-9]*.md`, `test-book/CH1*.md`, `runs/**`,
  `.github/skills/**`, `.env.*` → `project`; CH01 + smoke spec + helpers → `seed`;
  `run-audit.sh` etc. → `framework`.
- [x] AC4: The note states the fallback rule for an **unclassified** path
  (default to `project` / never-touch — the safe failure mode, ADR-006 Risks).
- [x] AC5: The note names the exact bash mechanism chosen (e.g. `case`/`[[ == glob ]]`
  with `shopt -s globstar extglob`, or a `find`-based matcher) and notes any
  portability caveat surfaced by E6-US1.

## BDD Scenarios

```gherkin
Feature: Manifest classification spike

  Scenario: A project file is never classified as framework
    Given the fixture contains tests/a.spec.ts and runs/RUN-x.yaml
    When  the candidate matcher classifies the fixture
    Then  both paths resolve to "project"

  Scenario: A path absent from the manifest is treated safely
    Given the fixture contains an unlisted file foo/extra.txt
    When  the candidate matcher classifies it
    Then  it resolves to "project" (never overwritten) — the safe default

  Scenario: The smoke chapter is a seed, not framework
    Given test-book/CH01-smoke.md is listed under [seed]
    When  the matcher classifies it
    Then  it resolves to "seed" (created if missing, never overwritten)
```

## Notes
- Time-boxed — **no production `cmd-e2e.sh` code**; only the findings note + a fixture
  scratch dir used to validate the matcher (the fixture need not be committed).
- Reference: ADR-006, architecture §7 (`MANIFEST.toml`), §11.
- Must complete **before** E3-US4 (`MANIFEST.toml`) and E3-US6 (`--update`) — its
  matcher is their implementation foundation.
- Beware `globstar`/`extglob` are bash-only; confirm against the E6-US1 portability findings.
