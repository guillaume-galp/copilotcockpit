/**
 * Shared helpers for the @@APP_NAME@@ E2E suite.
 *
 * This module is intentionally lean: it exposes the resolved stack URLs and a
 * couple of generic helpers so the scaffolded smoke spec runs green against a
 * plain localhost stack with zero topology-specific wiring.
 *
 * ── CONFIGURE ────────────────────────────────────────────────────────────────
 * As you add feature tests, grow this file with app-specific helpers:
 *   - `loginAs(page, role)` to seed auth tokens / mock the auth API,
 *   - fixture factories for your domain entities,
 *   - data-reset utilities used by global-setup.ts.
 * Keep helpers framework-agnostic where possible so chapters stay portable.
 */

import { type APIRequestContext } from '@playwright/test';

// Resolved at runtime from e2e/.env.local (override) → scaffold-token defaults.
export const BACKEND_URL  = process.env.BACKEND_URL  ?? 'http://localhost:@@BACKEND_PORT@@';
export const FRONTEND_URL = process.env.FRONTEND_URL ?? 'http://localhost:@@FRONTEND_PORT@@';

// Backend liveness path used by the smoke gate (scaffold token, default /health).
export const HEALTH_PATH = process.env.HEALTH_PATH ?? '@@HEALTH_PATH@@';

// Optional bearer token for hitting authenticated endpoints in API smoke checks.
const BYPASS_AUTH_TOKEN = process.env.BYPASS_AUTH_TOKEN;

/**
 * Auth headers for backend API requests. Empty unless BYPASS_AUTH_TOKEN is set.
 * CONFIGURE: replace with your real auth-token plumbing as the suite grows.
 */
export function authHeaders(): Record<string, string> {
  return BYPASS_AUTH_TOKEN ? { Authorization: `Bearer ${BYPASS_AUTH_TOKEN}` } : {};
}

/**
 * GET a backend path relative to BACKEND_URL, carrying any auth headers.
 */
export async function backendGet(request: APIRequestContext, path: string) {
  return request.get(`${BACKEND_URL}${path}`, { headers: authHeaders() });
}
