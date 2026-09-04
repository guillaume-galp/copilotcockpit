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

## Epic E5 — Developer skill & test infra

**Stories completed:** TH1-E5-US1 (run-tests.sh + Category-1 unit), TH1-E5-US2 (Categories 2–4), TH1-E5-US3 (copilotcockpit-dev skill)

**Key changes:**
- `run-tests.sh`: portable (bash 3.2-safe) test dispatcher — `unit|template|skills|integration|all`; aggregates exit codes (any failure → non-zero); detects missing `bats` with actionable guidance; existence-checks category scripts so they auto-wire.
- **Category 1** `tests/unit/{cmd-global,cmd-e2e,cmd-doctor}.bats` (+`helper.bash`): per-command idempotency, dry-run = no-side-effects, and error paths (missing arg / wrong dir / refuse-to-clobber). Hard-guarded fake-HOME isolation (HOME forced under `$BATS_TEST_TMPDIR`, aborts otherwise) so the real `~/.copilot/skills` / `~/.local/bin` are never mutated.
- **Category 2** `tests/template/check-template.sh`: unresolved-token scan on every `*.tmpl`, `package.json.tmpl` JSON validity post-substitution, MANIFEST.toml full-coverage of `templates/e2e/` (stricter than runtime — orphan = fail), `bash -n` of the runners. Closed two genuine MANIFEST coverage gaps (MANIFEST.toml→framework, governance/flaky-known.md→seed).
- **Category 3** `tests/skills/lint-skills.sh`: PyYAML frontmatter parse + non-empty name/description for every `skills/*/SKILL.md`.
- **Category 4** `tests/integration/smoke.bats`: fake-HOME dry-run smoke for `global` (8 skills + cockpit-wake), `e2e` (scaffold list), `doctor`.
- `skills/copilotcockpit-dev/SKILL.md`: the 8th managed skill — ADR-008 GitOps delivery runbook (branch/commit conventions, commit-type→bump table, five test categories, 12-step/4-phase flow with exact `gh` commands, 3-attempt bounded-autonomy escalation, VERSION/CHANGELOG ownership). Promoted from a pending source to a required harness role in `cmd-global.sh` so `bootstrap.sh global` installs all 8.

**Files modified:** `run-tests.sh`, `tests/unit/*.bats`, `tests/unit/helper.bash`, `tests/template/check-template.sh`, `tests/skills/lint-skills.sh`, `tests/integration/smoke.bats`, `tests/integration/helper.bash`, `skills/copilotcockpit-dev/SKILL.md`, `lib/cmd-global.sh`, `templates/e2e/MANIFEST.toml`.

**Epic ceremony (small, 3 stories):** `./run-tests.sh all` exits 0 — 14 unit + template integrity + 8-skill lint + 3 integration. All 3 stories reviewer-APPROVED.

## Epic TH3.E1 — Control store and preflight foundations

**Stories Completed:** TH3.E1.US1 (control root and versioned schemas, `677fc7e`),
TH3.E1.US2 (portable lock acquisition and exact release, `207f922`),
TH3.E1.US3 (recoverable guarded stale-lock repair, `8f88a21`),
TH3.E1.US4 (immutable authoritative event publication, `29a22e2`),
TH3.E1.US5 (materialized ledger projection and replay, `75bdce5`),
TH3.E1.US6 (deterministic crash-consistency and interleaving proof, `6642144`),
TH3.E1.US7 (control-plane preflight and guarded repair diagnostics, `e6e35e2`).
All seven reviewer-APPROVED; US2 and US7 each needed one bounded rework iteration.

**Key Changes:**
- **Versioned control root (US1).** `bin/cockpit_control.py` + the `cockpit-control`
  entry point establish a canonical, explicitly configured control root with versioned
  `control.json`, ledger, and event schemas. Roots are never inferred from `cwd`,
  relative roots are refused, and unknown future schema versions stop mutation and
  instruct the operator to upgrade. `cockpit-overseer`, `cockpit-protocol`, and
  `cockpit-wake` resolve and validate the root before any tmux mutation, and generated
  wake jobs fail closed on missing, malformed, or future-versioned stores.
- **Portable ownership protocol (US2/US3, ADR-018).** Every acquisition, release, and
  repair transition runs under a process-scoped advisory `fcntl.flock` on
  `locks/control.guard`, which the kernel releases on process death. Acquisition
  prepares a complete private candidate directory containing `owner.json` and publishes
  it by one atomic rename, so no creation-to-owner-publication window exists. Release
  and repair re-validate owner UUID and filesystem identity under the guard and claim by
  renaming to a unique quarantine. Repair proves same-host owner death or requires
  explicit exact-owner authorization, refuses live, remote, malformed, or unprovable
  owners, and publishes no shared repair marker: the rename is the entire claim.
- **Immutable events and derived projection (US4/US5, ADR-019).** Each event is written,
  flushed, and byte-validated against its exact canonical serialization under `pending/`,
  then committed by one non-clobbering atomic rename into
  `events/<12-digit revision>-<event-id>.json`. Filenames, revisions, event IDs, and
  schemas must agree and form one contiguous unique sequence; gaps, duplicates,
  disagreements, and invalid entries fail closed naming the offender. `ledger.json` is a
  pure, byte-reproducible fold of committed events installed through a flushed
  `ledger.json.tmp` and `os.replace`, `events.jsonl` becomes a strictly derived view, and
  `replay-ledger` deterministically repairs missing, stale, corrupt, ahead, or
  interrupted projections exactly once without touching event authority.
- **Deterministic interruption proof (US6).** All nine architecture §8.4 interruption
  boundaries are reachable through named fault hooks and all six required interleavings
  (acquire/acquire, acquire/repair, release/acquire, repair/acquire, bounded timeout,
  replacement preservation) are proven by barrier-coordinated real processes. Every
  scenario asserts safety and subsequent liveness. `check-interruption-matrix.sh` parses
  §8.4 out of the architecture document, fails on drift in either direction, and rejects
  concurrency regressions that coordinate only by timing.
- **Preflight and guarded repair (US7).** `preflight` reports root, schema, transition
  guard, authoritative lock, quarantine, pending event, committed revision, ledger,
  queue, worker capability, and declared-path readiness without changing a byte, and
  classifies every finding as authoritative, derived, debris, or configuration with the
  exact repair command. `repair-store` only quarantines into
  `quarantine/<stamp>-<uuid>-<name>`, never deletes, is idempotent, and refuses live
  owners, future-versioned state, and unproven committed continuity, with the preview
  sharing the same read-only refusal gates while staying lock-free.

**Files Modified:** `bin/cockpit_control.py` (new, 4278 lines), `bin/cockpit-control`
(new), `bin/cockpit-overseer`, `bin/cockpit-protocol`, `bin/cockpit-protocol.go`,
`bin/cockpit-wake`, `lib/cmd-global.sh`, `lib/cmd-doctor.sh`, `uninstall.sh`, `README.md`,
`skills/e2e-cockpit/SKILL.md`, `skills/setup-e2e-cockpit/SKILL.md`,
`templates/e2e/.agents/skills/e2e-cockpit/SKILL.md.tmpl`,
`templates/e2e/.github/skills/e2e-cockpit/SKILL.md.tmpl`,
`templates/e2e/tmux-cockpit.sh`, `templates/e2e/tmux-cockpit-local.sh`,
`tests/unit/cmd-control.bats` (new), `tests/unit/cmd-control-interruption.bats` (new),
`tests/unit/cmd-control-preflight.bats` (new), `tests/unit/check-interruption-matrix.sh`
(new), `tests/unit/control-interruption-matrix.tsv` (new), `tests/unit/helper.bash`,
`tests/unit/cmd-doctor.bats`, `tests/unit/cmd-global.bats`, `tests/unit/cmd-overseer.bats`,
`tests/unit/cmd-protocol.bats`, `tests/unit/cmd-wake.bats`,
`tests/integration/smoke.bats`, `tests/template/check-template.sh`,
`docs/architecture/overseer-control-plane.md`, `docs/ADRs/ADR-012-control-store-persistence.md`,
`docs/ADRs/ADR-018-portable-lock-repair-protocol.md` (new),
`docs/ADRs/ADR-019-immutable-event-publication.md` (new),
`docs/plan/backlog.yaml`, `docs/plan/session-log.md`.

**Epic ceremony (large, 7 stories):** `epic-integration` session ran the full gate
(`./run-tests.sh all`: 116/116 unit, template integrity, 8/8 skills, 5/5 integration,
codex — 0 failures, about 2m50s) plus `bootstrap.sh doctor` and `global --dry-run`, and
exercised the epic end to end on a scratch store: init → validate → preflight ready →
event publication → ledger advance and deterministic rebuild → idempotent replay; crashed
writer → preflight naming both exact repairs → ordered `repair-lock` then `repair-store`
→ ready → new writer publishes; killed lock owner recovery; derived ledger and
`events.jsonl` corruption rebuilt by `replay-ledger`; and composed fail-closed refusals
for future-versioned state, committed gaps, and live owners. One genuine cross-story gap
was closed with a new deterministic integration regression in
`tests/unit/cmd-control-preflight.bats` proving the crashed-writer chain through the
installed CLI. The epic quality check reviewed all 21 acceptance criteria against final
HEAD, ran adversarial symlink, event-forgery, and schema-poisoning probes, and APPROVED.

**Recorded follow-ups (non-blocking, none epic-blocking):** document that `repair-lock`
is deliberately independent of root metadata and version-gates `owner.json` instead
(reproduced: it quarantines a provably dead owner even when `control.json` is
future-versioned or malformed, losslessly and recoverably); enumerate the non-authoritative
root-level `quarantine/` directory in architecture §7; close the `repair-store --dry-run`
parity gap when `quarantine` exists but is not a directory and soften the `_plan_only`
docstring accordingly; move required-directory creation after the continuity gate; harden
the anti-sleep gate against comment keyword stuffing; add a `.gitignore` so
`bin/__pycache__/` stops dirtying the tree.
