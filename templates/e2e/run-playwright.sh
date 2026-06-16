#!/usr/bin/env bash
# run-playwright.sh — run Playwright tests inside Docker (Ubuntu-26-safe runner).
#
# Playwright's bundled browsers cannot run natively on Ubuntu 26.04, so tests run
# inside the pinned mcr.microsoft.com/playwright image (Ubuntu 24.04 "noble").
# This script is the low-level executor; the governed entry point is run-audit.sh,
# which captures component SHAs and writes the audit trail before calling this.
#
# Requires:
#   - e2e/.env.local with FRONTEND_URL / BACKEND_URL (and SKIP_DB_RESET=true for
#     UI-mock smoke tests)
#   - the backend reachable at BACKEND_URL before tests start
#   - Docker available on the host
#
# Usage:
#   ./run-playwright.sh                        # full suite
#   ./run-playwright.sh --grep @smoke          # smoke tests only
#   ./run-playwright.sh --project chromium     # one project only
#   ./run-playwright.sh tests/smoke.spec.ts    # a single spec file
#   ./run-playwright.sh --help                 # this help
#
# Outputs (on the host):
#   - HTML report → playwright-report/
#   - JUnit XML   → test-results/junit.xml   (consumed by run-audit.sh)

set -euo pipefail

print_usage() {
  sed -n '2,21p' "$0" | sed -E 's/^# ?//'
}

# Intercept --help before anything else; all other args pass through to Playwright.
for arg in "$@"; do
  case "$arg" in
    --help|-h) print_usage; exit 0 ;;
  esac
done

# Portable script dir (no readlink -f; pure-POSIX cd/pwd per portability cheat-sheet).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# ── CONFIGURE ────────────────────────────────────────────────────────────────
# Pin the Playwright Docker image to match the @playwright/test version in
# package.json. Keep this on a non-26 Ubuntu base ("noble" = 24.04).
PLAYWRIGHT_IMAGE="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.60.0-noble}"

# Pass env vars from the env file into the container. E2E_ENV_FILE is set by
# run-audit.sh when --env <target> is given; otherwise default to .env.local.
ENV_FILE="${E2E_ENV_FILE:-$SCRIPT_DIR/.env.local}"
ENV_ARGS=()
if [ -f "$ENV_FILE" ]; then
  ENV_ARGS=(--env-file "$ENV_FILE")
fi

printf 'Running Playwright tests via Docker (%s)...\n' "$PLAYWRIGHT_IMAGE"
printf 'Args: %s\n\n' "$*"

# Mount the host CA bundle when present so tests can reach TLS endpoints signed
# by corporate CAs. Optional — skipped silently if absent.
CA_BUNDLE_ARGS=()
if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
  CA_BUNDLE_ARGS=(-v /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro)
fi

docker run --rm \
  --network=host \
  "${ENV_ARGS[@]}" \
  -e PLAYWRIGHT_JUNIT_OUTPUT_FILE=/e2e/test-results/junit.xml \
  -v "$SCRIPT_DIR":/e2e \
  -v "$SCRIPT_DIR/node_modules":/e2e/node_modules:ro \
  "${CA_BUNDLE_ARGS[@]}" \
  -w /e2e \
  "$PLAYWRIGHT_IMAGE" \
  npx playwright test "$@"
