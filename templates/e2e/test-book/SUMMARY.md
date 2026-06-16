# E2E Test Book Summary

This file is the **master TC index** for **@@APP_NAME@@** — one row per test case,
always current.

Use it as the quickest route from a TC identifier to its chapter, Gherkin scenario,
and Playwright spec file. Add a row here every time you add a TC to a chapter
(see `TC-FORMAT.md` and `../governance/GOVERNANCE.md`).

---

## Chapters

| Chapter | File | Scenarios |
|---------|------|-----------|
| CH01 | [CH01-smoke.md](./CH01-smoke.md) | 3 |

> Extend this table as `/setup-e2e-runbook` adds feature chapters
> (`CH02-*`, `CH03-*`, …).

---

## Master Test Case Index

| TC-ID | Title | Priority | Tags | Automation file |
|-------|-------|----------|------|-----------------|
| TC-SMOKE-001 | Backend health endpoint returns 200 | P0 | smoke | `tests/smoke.spec.ts` |
| TC-SMOKE-002 | Frontend SPA loads | P0 | smoke | `tests/smoke.spec.ts` |
| TC-SMOKE-003 | Key API endpoint accessible | P0 | smoke | `tests/smoke.spec.ts` |
