# CH01 — Smoke Tests

Baseline health checks that verify the **@@APP_NAME@@** dev stack is running correctly.
These tests must always pass before any feature tests are run.

All three smoke tests are implemented in `tests/smoke.spec.ts` and tagged `@smoke`.
Run them with: `./run-audit.sh --scope "@smoke"`.

---

### TC-SMOKE-001 — Backend health endpoint returns 200

| Field      | Value |
|------------|-------|
| Priority   | P0 |
| Scope      | API |
| Tags       | smoke |
| Automation | `tests/smoke.spec.ts` |

#### Preconditions
- Backend server is running at `BACKEND_URL` (default `http://localhost:@@BACKEND_PORT@@`).

#### Steps
1. Send `GET @@HEALTH_PATH@@` to the backend.

#### Expected Result
- Response status is 200.

```gherkin
Feature: Smoke — Stack health

  @TC-SMOKE-001 @smoke
  Scenario: Backend health endpoint returns 200
    Given the backend is running
    When  I request GET @@HEALTH_PATH@@
    Then  the response status should be 200
```

---

### TC-SMOKE-002 — Frontend SPA loads

| Field      | Value |
|------------|-------|
| Priority   | P0 |
| Scope      | UI |
| Tags       | smoke |
| Automation | `tests/smoke.spec.ts` |

#### Preconditions
- Frontend server is running at `FRONTEND_URL` (default `http://localhost:@@FRONTEND_PORT@@`).

#### Steps
1. Navigate to `/` in the browser.

#### Expected Result
- Page has a title, or a non-empty body.
- No unhandled JavaScript errors on load.

```gherkin
Feature: Smoke — Stack health

  @TC-SMOKE-002 @smoke
  Scenario: Frontend SPA loads
    Given the frontend is running
    When  I navigate to "/"
    Then  the page should have a title or a non-empty body
```

---

### TC-SMOKE-003 — Key API endpoint accessible

| Field      | Value |
|------------|-------|
| Priority   | P0 |
| Scope      | API |
| Tags       | smoke |
| Automation | `tests/smoke.spec.ts` |

#### Preconditions
- Backend and its dependencies (DB, cache, etc.) are running.

#### Steps
1. Send a `GET` request to the liveness endpoint `@@HEALTH_PATH@@`.

#### Expected Result
- Response status is 200 or 401 — never 500.
- 200 means the service is up and unauthenticated access is permitted.
- 401 means auth is required but the service is healthy.
- A 500 indicates a server or DB connection error.

```gherkin
Feature: Smoke — Stack health

  @TC-SMOKE-003 @smoke
  Scenario: Key API endpoint accessible
    Given the backend is running with its dependencies
    When  I request the liveness endpoint @@HEALTH_PATH@@
    Then  the response status should be 200 or 401
    And   the response should not be 500
```
