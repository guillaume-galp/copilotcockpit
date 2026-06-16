import path from 'path';
import fs from 'fs';

/**
 * Global setup — runs once before all tests.
 *
 * When SKIP_DB_RESET=true (recommended for UI tests that use page.route() mocks,
 * and mandatory against shared/production databases) this is a no-op, so the
 * scaffolded smoke test goes green against a generic localhost stack with zero
 * topology-specific wiring.
 *
 * ── CONFIGURE ────────────────────────────────────────────────────────────────
 * Replace the reset block below with your own migration/seed command. Examples:
 *   - Prisma:   execSync('npx prisma migrate reset --force', { cwd: backendDir })
 *   - Django:   execSync('python manage.py flush --no-input', { cwd: backendDir })
 *   - Alembic:  execSync('.venv/bin/alembic downgrade base && .venv/bin/alembic upgrade heads', ...)
 *   - Custom:   execSync('npm run db:reset', { cwd: backendDir })
 */
async function globalSetup() {
  if (process.env.SKIP_DB_RESET === 'true') {
    console.log('[global-setup] SKIP_DB_RESET=true — skipping database reset.');
    return;
  }

  console.log('[global-setup] Resetting test database...');

  // CONFIGURE: point this at the backend/service directory holding your reset command.
  const backendDir = path.resolve(__dirname, '../backend');

  if (!fs.existsSync(backendDir)) {
    throw new Error(`[global-setup] backend directory not found: ${backendDir}`);
  }

  // CONFIGURE: replace the throw below with your stack's reset command, e.g.
  //   import { execSync } from 'child_process';
  //   execSync('npm run db:reset', { cwd: backendDir, stdio: 'inherit' });
  throw new Error(
    '[global-setup] DB reset not configured. ' +
    'Set SKIP_DB_RESET=true or implement the reset command in global-setup.ts.',
  );
}

export default globalSetup;
