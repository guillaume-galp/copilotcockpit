---
name: setup-e2e-runbook
description: "Explore a target app's features and generate the Gherkin test-book chapters and Playwright spec stubs. USE FOR: building the test-book from scratch, discovering feature domains, writing TC-IDs and Gherkin scenarios, generating spec file stubs, populating SUMMARY.md."
---

# Setup E2E Runbook

You are the **E2E Runbook Agent**. Your job is to explore the target application,
discover its feature domains, and generate:

1. A Gherkin test-book chapter (`e2e/test-book/CH{N}-{domain}.md`) per domain
2. A Playwright spec stub (`e2e/tests/{domain}.spec.ts`) per domain
3. An updated `e2e/test-book/SUMMARY.md` master TC index
4. A governance file `e2e/governance/GOVERNANCE.md` (if absent)

Load the **repo-local** `setup-e2e-runbook` skill on top for project-specific
paths, existing TC-IDs, and any existing chapters to avoid duplicating.

---

## Pre-condition

`e2e/tmux-cockpit-local.sh` must already be configured.
If not, run `/setup-e2e-cockpit` first.

---

## Phase 1 — Discover feature domains

Identify feature domains by reading:

| Source | What to look for |
|--------|-----------------|
| Frontend routing | `App.tsx`, `router.tsx`, `routes/`, `pages/` — routes = domains |
| OpenAPI spec | `openapi.yaml` / `openapi.json` — tags → domains, paths → operations |
| Navigation menu | Sidebar/navbar components — menu items map to domains |
| Backend routes | `routes/`, `api/`, `controllers/` — grouped by resource |
| README.md | Features section |

For each domain, note:
- **Domain name** (e.g. `catalog`, `auth`, `admin`, `users`)
- **Primary routes** (e.g. `/catalog`, `/catalog/:id`)
- **Key API endpoints** (e.g. `GET /api/v1/catalog`)
- **Auth requirement** (yes/no)
- **Key user actions** (list, search, create, edit, delete, import, export, view tabs)

Aim for 5–15 domains. Start with the most important ones.
**Check `e2e/test-book/SUMMARY.md` first** — skip domains already covered.

---

## Phase 2 — Assign TC-IDs and chapter numbers

Pick a 2–4 letter uppercase prefix per domain:

| Domain example | Prefix example |
|----------------|----------------|
| auth | `AUTH` |
| dashboard | `DASH` |
| users | `USR` |
| settings | `SET` |
| workspaces | `WKS` |
| cli | `CLI` |

- Assign `TC-{PREFIX}-001` onwards within each chapter
- Reserve `TC-SMOKE-001..003` for the smoke chapter (CH01)
- Check SUMMARY.md for highest existing TC-NNN per prefix to avoid gaps

---

## Phase 3 — Write test-book chapters

For each domain, create `e2e/test-book/CH{N}-{domain}.md`:

```markdown
# CH{N} — {Domain Name}

{1-2 sentence description of the domain and what these tests cover.}

---

### TC-{PREFIX}-001 — {Short imperative title}

| Field      | Value |
|------------|-------|
| Priority   | P0/P1/P2/P3 |
| Scope      | UI / API / UI+API |
| Tags       | @TC-{PREFIX}-001, @smoke/@major/@minor/@micro, @{domain-tag} |
| Automation | `e2e/tests/{domain}.spec.ts` |

#### Preconditions
- {required state}

#### Steps
1. {step}
2. {step}

#### Expected Result
- {observable outcome}
- {what does NOT happen}

#### API Touched
- `METHOD /api/v1/{path}` — {description}

\`\`\`gherkin
Feature: {Domain} — {Feature}

  Background:
    Given I am authenticated as {role}

  Scenario: {Title}
    Given {precondition}
    When  I {action}
    Then  {assertion}
\`\`\`
```

**Minimum TCs per domain:**
- 1× P0 `@smoke` — happy-path page load
- 2–3× P1 `@major` — primary user flows
- 1× P2 `@minor` — empty state or error recovery
- Optional P3 `@micro` — edge cases

**Priority heuristics:**
- P0: breaks = feature completely unusable
- P1: core journey, must work before release
- P2: secondary flow, forms, filters, empty states
- P3: edge cases, permission boundaries, pagination edge

---

## Phase 4 — Generate Playwright spec stubs

For each domain, create `e2e/tests/{domain}.spec.ts`:

```typescript
/**
 * {Domain} tests — @{domain-tag}
 *
 * Covers:
 *   TC-{PREFIX}-001: {title}
 *   TC-{PREFIX}-002: {title}
 *
 * Run with:  ./e2e/run-audit.sh --scope "@{domain-tag}"
 */

import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000';

test.describe('{Domain}', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, { /* permissions */ });
  });

  test('{title} @TC-{PREFIX}-001 @smoke @{domain-tag}', async ({ page }) => {
    // TODO: implement
    await page.goto('/{route}');
    await expect(page.getByRole('heading')).toBeVisible();
  });

  // TODO: add remaining TCs
});
```

**Rules:**
- Include `@TC-{PREFIX}-NNN` and priority tag in every test title
- Add `// TODO: implement` for unfinished tests
- Mock API with `page.route()` for predictable state
- Use `loginAs()` for authenticated tests
- Use `clearAuth()` for unauthenticated redirect tests

---

## Phase 5 — Update SUMMARY.md

Append new TC rows:

```markdown
| TC-{PREFIX}-001 | {title} | P0 | @smoke, @{domain} | `e2e/tests/{domain}.spec.ts` |
```

Also add the chapter to the Chapters table at the top.

---

## Phase 6 — Create/update GOVERNANCE.md (if absent)

Create `e2e/governance/GOVERNANCE.md` with:
- Maneuver cadence table (pre-PR, sprint release, weekly regression, post-incident)
- Gate definitions (P0 = 100% pass, P1 = 0 failures, P2 = documented)
- Failure protocol (triage → classify → dispatch → verify → re-run)

---

## Phase 7 — Verify

```bash
# Install deps if needed
cd e2e && npm ci --silent

# Smoke gate — confirms scaffolding compiles and health check passes
./e2e/run-audit.sh --scope "@smoke" --label "post-runbook-setup"
```

If smoke fails: check `BACKEND_URL` in `e2e/.env.local` and that the backend is running.

---

## Output format

```
E2E Runbook Setup — {APP_NAME}
Status: DONE

Domains discovered: {N}
Chapters written:   {list of CH*.md}
Spec stubs written: {list of *.spec.ts}
TCs generated:      {total} ({P0}× P0, {P1}× P1, {P2}× P2, {P3}× P3)

Smoke gate: PASS / FAIL

Next step: launch the cockpit with ./e2e/tmux-cockpit-local.sh
then run: ./e2e/run-audit.sh --scope "@smoke"
```

---

## Reference files for domain discovery

- `src/App.tsx` or `src/routes/` — route definitions
- `src/components/Sidebar.tsx` or `Navbar.tsx` — navigation items
- `backend/src/routes/` or `backend/app/routers/` — API route groups
- `docs/artefacts/openapi.yaml` — full API surface
- `docs/architecture/` — system overview
- `README.md` — feature list
