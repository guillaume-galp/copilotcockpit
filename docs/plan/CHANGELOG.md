# Changelog — copilotcockpit (orchestration)

Per-epic delivery log produced by the Autopilot Orchestrator during TH1 execution.

---

## Epic E6 — Spikes (portability + manifest classification)

**Stories Completed:** TH1-E6-US1, TH1-E6-US2 (both XS spikes, self-review only).

**Key Changes:**
- Verified macOS/BSD ↔ GNU shell-tool portability hazards and chose portable
  constructs (avoid `sed -i`; use `python3` for date math; `grep -Eo` not `-P`;
  temp-file+`mv`; uutils-coreutils caveat noted). Gates E1/E3 implementation.
- Proved a pure-bash `[[ str == glob ]]` array-driven matcher for MANIFEST.toml
  ownership classification (seed→framework→project precedence; unclassified →
  `project`/never-touch). Verified macOS bash-3.2-safe (single `*` matches `/`
  inside `[[ ]]`, no globstar dependency). 11/11 fixture classifications correct.

**Files Modified:**
- `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/portability-cheatsheet.md` (new)
- `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/manifest-classification.md` (new)

**Ceremony:** Small epic (2 stories). No test suite exists yet — suite run deferred to E5. Working tree clean; no fixtures leaked into VCS.

---

## Epic E1 — Core bootstrap shell (dispatcher, shared lib, doctor)

**Stories Completed:** TH1-E1-US1, TH1-E1-US2, TH1-E1-US3 (all reviewer-APPROVED).

**Key Changes:**
- `lib/common.sh` — sourceable shared library: stderr logging (`log_info/warn/error/ok`),
  `detect_os`, portable shims (`cc_realpath`, `cc_timestamp`, `cc_files_identical`),
  `cc_install_file` (identical→no-op, differ→backup `.bak-<ts>`+overwrite, dry-run aware),
  `cc_run` + `DRY_RUN`, idempotency guard. Portable per E6-US1 (no `sed -i`/`readlink -f`/`date -d`), bash 3.2-safe.
- `bootstrap.sh` — thin ADR-003 dispatcher: `global`/`e2e`/`doctor` routing via `case`,
  portable self-dir resolution, usage text, global `--dry-run` → `DRY_RUN`.
- `lib/cmd-doctor.sh` — prerequisite probes (found/missing+version), PATH check with
  exact `export PATH=…` remediation, 4-state skills + cockpit-wake drift detection,
  correct hard-vs-optional exit-code logic. Graceful when `skills/`/`bin/` not yet vendored.

**Files Modified:** `bootstrap.sh`, `lib/common.sh`, `lib/cmd-doctor.sh`.

**Ceremony:** Small epic (3 stories). Epic smoke: `bash -n` passes on all 3 files;
`./bootstrap.sh doctor` exit 0; `./bootstrap.sh` usage exit 0. Full bats suite deferred to E5.

---

## Epic E2 — Global skills install (vendor skills + cockpit-wake, cmd-global, cold install)

**Stories Completed:** TH1-E2-US1, US2, US3, US4 (all reviewer-APPROVED). Large epic → epic-integration test PASS + cross-cutting quality review APPROVED.

**Key Changes:**
- Vendored the 7 canonical harness skills verbatim into `skills/<role>/SKILL.md`
  (e2e-cockpit, e2e-operator, setup-e2e-cockpit, setup-e2e-runbook, worker-dev,
  worker-fix, worker-test) and `cockpit-wake` (stdlib-only Python 3) into `bin/`.
- `lib/cmd-global.sh` — `bootstrap.sh global`: idempotent install/update of all 8
  managed skills (7 + pending `copilotcockpit-dev`) + cockpit-wake via `cc_install_file`
  (backup-before-overwrite, `already current`); `--link` (dev symlinks), `--dry-run`,
  PATH guidance (no dotfile edits), pre-flight required-source validation (atomic, no
  partial install), and `--from-release <ref>` (latest|vX.Y.Z) cold-install path with
  checksum-verified, atomic tarball fetch/extract.
- `install.sh` — tiny cold-install one-liner wrapper: fetch tarball + `.sha256` from
  `releases/latest/download/`, verify, extract, exec `bootstrap.sh global`.
- `lib/common.sh` — added portable `cc_sha256_file` / `cc_sha256_verify`
  (sha256sum→shasum auto-detect). Test seam: `CC_RELEASE_BASE_URL` / `CC_RELEASE_REPO`.

**Files Modified:** `skills/*/SKILL.md` (×7), `bin/cockpit-wake`, `lib/cmd-global.sh`,
`install.sh`, `lib/common.sh`.

**Ceremony (large epic):** Integration journey (install→idempotent→doctor→drift→link/dry-run→
release fetch+tamper-abort) PASS against scratch HOMEs; real `~/.copilot`/`~/.local` untouched.
Quality review APPROVED. Deferred tech-debt: (a) reject `--link` + `--from-release`
combination; (b) hoist canonical 8-role list into common.sh (duplicated in cmd-global/cmd-doctor).
Live network round-trip for `--from-release` to be E2E-validated once E4-US2 publishes a release.

---

## Epic E3 — E2E scaffold (templates/e2e/, MANIFEST.toml, scaffold + --update)

**Stories Completed:** TH1-E3-US1..US6 (all reviewer-APPROVED; US1 required one rework iteration). Large epic → epic-integration PASS (after one blocker fix) + cross-cutting quality review APPROVED.

**Key Changes:**
- `templates/e2e/` — complete, topology-agnostic harness template: governed `run-audit.sh`
  (3-tier audit trail: INDEX.md + RUN-*.yaml + monthly digest; portable; correct JUnit
  failure classification), Docker `run-playwright.sh`, `playwright.config.ts` +
  `global-setup/teardown.ts` with CONFIGURE blocks, governance (GOVERNANCE.md,
  run-schema.yaml, flaky-known.md), test-book (SUMMARY, TC-FORMAT, CH01-smoke), tests
  (helpers.ts, smoke.spec.ts tag-linked to CH01), runs/ skeleton, 7 thin skill overlays,
  copilot-instructions, tmux cockpit stubs, env files, `.gitignore`, and `MANIFEST.toml`
  (framework/seed/project ownership; E6-US2-validated matcher; safe project default).
- `lib/cmd-e2e.sh` — `bootstrap.sh e2e <dir>`: scaffold (copy + tokenise all 4 sanctioned
  tokens, `.tmpl` stripping, ADR-004 first-hit-wins resolution, atomic staging+move,
  git init+commit, tolerant npm install, handoff) and `--update` (MANIFEST-driven
  content-preserving refresh: framework overwrite+backup only on diff, seed create-if-missing,
  project never-touch, dry-run, idempotent). sed-injection-hardened token substitution.

**Files Modified:** `templates/e2e/**` (29 files), `lib/cmd-e2e.sh`.

**Ceremony (large epic):** Integration journey (scaffold→config-precedence→self-consistency→
audit-trail→update round-trip→idempotency) PASS against scratch dirs after fixing one blocker
(commit 4a09371: run-audit.sh INDEX-append aborted under pipefail on an empty index). Quality
review APPROVED. Deferred tech-debt: MANIFEST.toml ships into scaffold but is unread there
(drift risk) — classify framework or exclude; flaky-known.md relies on implicit project default.
Real Docker/Playwright run validated structurally; full E2E deferred to E5 integration.

## Epic E4 — CI/CD & releases

**Stories completed:** TH1-E4-US1 (VERSION + CHANGELOG.md), TH1-E4-US2 (release.yml), TH1-E4-US3 (ci.yml)

**Key changes:**
- Established semver source of truth: repo-root `VERSION` (0.1.0) + `CHANGELOG.md` with `## vX.Y.Z` sections consumed by the release workflow for notes.
- `.github/workflows/release.yml`: tag-driven (`v[0-9]+.[0-9]+.[0-9]+`, pre-release `-suffix` excluded by glob + guard) release pipeline. Assembles a deterministic `copilotcockpit-${TAG}.tar.gz` (top dir `copilotcockpit/`, exactly `bootstrap.sh lib/ skills/ bin/ templates/ README.md`; excludes docs/.git/tests/.github), computes sha256, and publishes via `gh release create --latest` attaching both the versioned tarball+sha256 and an unversioned `copilotcockpit.tar.gz`+sha256 alias plus `install.sh` — exactly the asset names `install.sh` and `lib/cmd-global.sh --from-release` pin. A Category-5 `validate` job downloads, verifies checksum, extracts and runs `bootstrap.sh global --dry-run` + `doctor` under a throwaway HOME, failing the release on a broken artefact.
- `.github/workflows/ci.yml`: PR merge-gate on `pull_request → main` (+ push to non-main); installs bats + PyYAML and runs categories 1–4 via `./run-tests.sh all`; honours `[skip ci]`; deliberately excludes Category 5.
- Added repo-root `README.md` (part of the install surface shipped in the tarball).

**Files modified:** `VERSION`, `CHANGELOG.md`, `README.md`, `.github/workflows/release.yml`, `.github/workflows/ci.yml`.

**Epic ceremony (small, 3 stories):** both workflows parse as valid YAML; VERSION + CHANGELOG present with a `## v0.1.0` section; release packaging + Category-5 logic and the ci dispatcher job-body were validated locally (live GitHub Actions execution deferred to a real tag push / PR). All 3 stories reviewer-APPROVED.

**Deferred (non-blocking):** sync architecture §7 embedded MANIFEST block; PyYAML install hardening for PEP-668; richer release notes mtime.
