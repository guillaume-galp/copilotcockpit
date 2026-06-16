/**
 * Global teardown — runs once after all tests.
 *
 * ── CONFIGURE ────────────────────────────────────────────────────────────────
 * Add any post-run cleanup here (stop test containers, delete seeded test data,
 * revoke tokens, etc.). The default is a no-op so the scaffolded harness runs
 * green without topology-specific wiring.
 */
async function globalTeardown() {
  // CONFIGURE: add cleanup steps for your stack here.
  console.log('[global-teardown] No-op teardown complete.');
}

export default globalTeardown;
