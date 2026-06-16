/**
 * Smoke tests — @smoke
 *
 * Baseline checks that the @@APP_NAME@@ dev stack is up and responding. Each test
 * is cross-linked to the test-book chapter test-book/CH01-smoke.md via its
 * @TC-SMOKE-NNN title tag (the audit parser maps JUnit results back to the book
 * through these tags — keep them identical in both places).
 *
 *   TC-SMOKE-001 — Backend health endpoint (@@HEALTH_PATH@@) returns 200.
 *   TC-SMOKE-002 — Frontend SPA loads (page has meaningful content).
 *   TC-SMOKE-003 — A key API endpoint returns 200 or 401 (auth required), never 500.
 *
 * Run with:  ./run-audit.sh --scope "@smoke"
 *
 * CONFIGURE:
 *   - Update API_PROBE_PATH to a representative API endpoint in your app.
 *   - Update TITLE_KEYWORD to a word that appears in your app's page title/body.
 */

import { test, expect } from '@playwright/test';
import { backendGet, HEALTH_PATH, BACKEND_URL } from './helpers';

// CONFIGURE: a word that should appear in your app's page title or body.
const TITLE_KEYWORD = '@@APP_NAME@@';

// CONFIGURE: a representative API path that returns 200 or 401 (proves the service is up).
const API_PROBE_PATH = '@@HEALTH_PATH@@';

// ---------------------------------------------------------------------------

test('backend health @TC-SMOKE-001 @smoke', async ({ request }) => {
  const response = await backendGet(request, HEALTH_PATH);
  expect(
    response.status(),
    `Expected 200 from ${BACKEND_URL}${HEALTH_PATH}, but got ${response.status()}`,
  ).toBe(200);
});

test('frontend SPA loads @TC-SMOKE-002 @smoke', async ({ page }) => {
  await page.goto('/');

  const title    = await page.title();
  const bodyText = await page.locator('body').innerText();

  const titleMatches  = title.toLowerCase().includes(TITLE_KEYWORD.toLowerCase());
  const bodyIsPresent = bodyText.trim().length > 0;

  expect(
    titleMatches || bodyIsPresent,
    `Expected page title to contain "${TITLE_KEYWORD}" or body to be non-empty. Got title="${title}"`,
  ).toBe(true);
});

test('key API endpoint accessible @TC-SMOKE-003 @smoke', async ({ request }) => {
  // Accepts 200 (unauthenticated access) or 401 (auth required) — never 500.
  // Either proves the backend is connected and the service is running.
  const response = await backendGet(request, API_PROBE_PATH);
  const status   = response.status();

  expect(
    [200, 401],
    `Expected 200 or 401 from ${API_PROBE_PATH}, but got ${status}`,
  ).toContain(status);
});
