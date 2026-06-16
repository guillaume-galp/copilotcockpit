# E2E Test Governance

This document defines the **discipline** around running, recording, and reviewing the
end-to-end tests for **@@APP_NAME@@**. It answers three questions:

1. **How do I run tests?** — Use `run-audit.sh`, not `run-playwright.sh` directly.
2. **How do I know what was tested?** — Read `runs/INDEX.md`, then drill into the relevant files.
3. **How do I keep the harness honest?** — Follow the maneuver guide at the bottom.

This is the **framework** layer of a self-auditing harness. It is generic and
topology-agnostic — the only project-specific facts live in `.env.local`
(URLs/tokens) and the test-book chapters under `test-book/`.

---

## Audit File Structure (3-tier, scales to thousands of runs)

```
e2e/runs/
├── INDEX.md              ← one row per run, forever (~80 bytes/row; always fast to read)
├── AUDIT-2026-06.md      ← full per-TC table, current month (~1–5 KB/run)
├── AUDIT-2026-05.md      ← archived month (open only when investigating past failures)
└── RUN-YYYYMMDD-*.yaml   ← per-run YAML: git SHAs, TC-level status, error snippets
```

**Reading order for agents:**
1. Start with `INDEX.md` — find the run(s) you care about by date, label, or result icon.
2. For full per-TC detail of a specific run: open `RUN-{id}.yaml`.
3. For a human-readable table of a month's runs: open `AUDIT-YYYY-MM.md`.
4. For cross-run failure analysis: `grep "status: failed" runs/RUN-*.yaml`.

**Do not** read the monthly digest just to check the latest status — `INDEX.md`
is enough and will remain fast forever.

---

## The Audit-Run Wrapper

`run-audit.sh` is the single entry point for all test execution. It wraps
`run-playwright.sh` and adds:

- **Metadata capture** — git SHAs, branches, dirty-tree flags, env URLs, Playwright version.
- **Scope declaration** — which TC tags or spec files you are exercising.
- **Result mapping** — JUnit XML → TC-ID resolution via `@TC-XXX-NNN` tags in test titles.
- **Persistent YAML run record** — one file per run, committed to `runs/`.
- **Compact index row** — appended to `runs/INDEX.md` (newest directly under the header).
- **Monthly per-TC digest** — prepended to `runs/AUDIT-YYYY-MM.md` (auto-rotates monthly).

### Usage

```bash
# Full suite against local dev stack
./run-audit.sh

# Smoke gate only  (maps to P0 in the test-book)
./run-audit.sh --scope "@smoke"

# Smoke + major gate
./run-audit.sh --scope "@smoke or @major"

# One Playwright project only
./run-audit.sh --project chromium

# Named release run — label appears in the audit log
./run-audit.sh --label "pre-release" --scope "@smoke"

# Force environment tag  (auto-detected from .env.local by default)
./run-audit.sh --env staging --scope "@smoke"
```

### Tracking split repos

`run-audit.sh` snapshots a git SHA per component listed in `E2E_COMPONENTS`:

```bash
E2E_COMPONENTS="app:."                              # monorepo (default — single "app")
E2E_COMPONENTS="frontend:frontend backend:backend"  # split repo
```

Each entry becomes one block under `code:` in the run record.

---

## TC Annotation Convention

Every `test()` call that maps to a test-book entry **must** include the TC-ID in its
title. The audit parser uses this to map JUnit results back to the book.

```typescript
test('Backend health endpoint returns 200 @TC-SMOKE-001 @smoke', async ({ request }) => { ... });
test('Frontend SPA loads @TC-SMOKE-002 @smoke', async ({ page }) => { ... });
```

Rules:
- Tag format: `@TC-{CHAPTER}-{NNN}` — exactly as it appears in `test-book/SUMMARY.md`.
- One test ↔ one TC-ID (1:1 mapping — split tests if one test covers two TCs).
- Tests without a `@TC-` tag still run; they are recorded under `id: unmapped`.
- Tags are cumulative: `@TC-SMOKE-001 @smoke` — all are valid Playwright `--grep` values.

---

## Run Record Schema

Each run writes `runs/RUN-{YYYYMMDD}-{HHMMSS}-{env}.yaml`.
Full schema is documented in `governance/run-schema.yaml`. Key fields:

| Field | Description |
|-------|-------------|
| `run_id` | Unique identifier: `RUN-20260616-120000-local` |
| `timestamp` | ISO-8601 (UTC) |
| `label` | Free-text human label (optional) |
| `env` | `local`, `remote`, or a forced `--env` value |
| `scope` | `--grep` value passed to Playwright, or `"all"` |
| `project` | Playwright project: `chromium`, `all`, … |
| `urls.frontend` / `urls.backend` | Endpoints used at run time |
| `code.<component>.sha` | Git SHA of each tracked component at run time |
| `summary.total/passed/failed/skipped/flaky` | Aggregate counts |
| `cases[]` | Per-test result with TC-ID, status, duration, error snippet |

---

## Maneuver Guide

### Before a feature branch merge

```bash
./run-audit.sh --scope "@smoke" --label "pre-merge {branch-name}"
```

Gate: all P0 (@smoke) TCs must pass.

### Before a sprint release

```bash
./run-audit.sh --scope "@smoke or @major" --label "sprint-{N} release gate"
```

Gate: all P0+P1 TCs must pass (0 failures).

### Full regression (weekly / before major releases)

```bash
./run-audit.sh --label "full regression {date}"
```

Gate: P0+P1 pass; P2 failures are triaged; P3 failures are logged, not gating.

### Continuous Cadence

| Cadence | Command | Gate |
|---------|---------|------|
| Every PR (CI) | `--scope "@smoke"` | 100% P0 pass |
| Weekly regression | full suite | 0 P0/P1 failures |
| Sprint boundary | full suite + label | sign-off in `INDEX.md` + `AUDIT-YYYY-MM.md` |

---

## Adding New TCs

1. Add the TC entry in the relevant `test-book/CH*.md` chapter (per `TC-FORMAT.md`).
2. Add a row to `test-book/SUMMARY.md` master table.
3. Write or extend the Playwright spec file listed in the `Automation` field.
4. Tag the `test()` title with `@TC-XXX-NNN` and the priority tag.
5. Run `./run-audit.sh --scope "@TC-XXX-NNN"` to verify it passes before committing.

---

## Resolving Failures

| Outcome | Action |
|---------|--------|
| TC fails on CI, passes locally | Check env config, port-forward, inspect the trace. |
| TC fails on both | File an issue tagged `e2e-failure`, link to the run YAML. |
| TC consistently flaky | Add `test.slow()` or raise the timeout; if still flaky, add to `governance/flaky-known.md`. |
| TC maps to a real bug | Fix the app, re-run the TC, ensure the run record is green before closing. |
