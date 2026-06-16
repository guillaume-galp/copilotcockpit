import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import dotenv from 'dotenv';

// Load e2e/.env.local if present (gitignored, holds per-developer URLs/tokens).
dotenv.config({ path: path.join(__dirname, '.env.local'), override: false });

// ── CONFIGURE ────────────────────────────────────────────────────────────────
// Topology-specific endpoints. Defaults use the token conventions filled in at
// scaffold time; override at runtime via e2e/.env.local (BACKEND_URL / FRONTEND_URL).
//   - @@BACKEND_PORT@@  : port your API/backend listens on
//   - @@FRONTEND_PORT@@ : port your web UI is served from
//   - @@HEALTH_PATH@@   : backend liveness path used by the webServer gate (e.g. /health)
const BACKEND_URL  = process.env.BACKEND_URL  ?? 'http://localhost:@@BACKEND_PORT@@';
const FRONTEND_URL = process.env.FRONTEND_URL ?? 'http://localhost:@@FRONTEND_PORT@@';

// CONFIGURE: set SANDBOX_BASE_URL if you add a live-API ("sandbox") test project.
const SANDBOX_BASE_URL = process.env.SANDBOX_BASE_URL ?? BACKEND_URL;

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ['list'],
    // JUnit goes to test-results/ (kept separate from HTML's playwright-report/,
    // which the HTML reporter wipes on each run — that would delete junit.xml).
    ['junit', { outputFile: 'test-results/junit.xml' }],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  use: {
    baseURL: FRONTEND_URL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // When PLAYWRIGHT_WS_ENDPOINT is set (e.g. ws://localhost:3000 for a
        // browserless/chrome container), Playwright connects to that remote
        // browser instead of launching a local Chromium. Required on Ubuntu 26+
        // where bundled Chromium cannot run natively (see run-playwright.sh).
        ...(process.env.PLAYWRIGHT_WS_ENDPOINT
          ? { connectOptions: { wsEndpoint: process.env.PLAYWRIGHT_WS_ENDPOINT } }
          : {}),
      },
      // CONFIGURE: if you add a sandbox/ sub-directory for live-API tests, ignore it here.
      testIgnore: /sandbox\//,
    },
    // CONFIGURE: uncomment and adapt this project for live-API / sandbox tests.
    // {
    //   name: 'sandbox',
    //   testMatch: /sandbox\/.*\.spec\.ts/,
    //   use: {
    //     baseURL: `${SANDBOX_BASE_URL}/api/v1`,
    //     extraHTTPHeaders: { 'Content-Type': 'application/json' },
    //   },
    // },
  ],
  globalSetup: './global-setup.ts',
  globalTeardown: './global-teardown.ts',
  webServer: {
    // CONFIGURE: this gate only waits for an already-running backend (it never
    // starts one). Point `url` at your liveness endpoint; @@HEALTH_PATH@@ is the
    // scaffold token for that path.
    command: process.env.CI
      ? 'echo "ERROR: backend must be running before E2E tests in CI" && exit 1'
      : `echo "Reusing existing dev stack — ensure backend is running at ${BACKEND_URL}"`,
    url: `${BACKEND_URL}@@HEALTH_PATH@@`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
