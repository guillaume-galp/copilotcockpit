# Session Log — copilotcockpit

A running log of lifecycle ceremonies and significant decisions.

---

## 2026-06-16 — Phase 2: Architecture (Architect Agent)

**Input:** VP1 vision brief for `copilotcockpit` — the canonical one-command
bootstrap for the Copilot CLI E2E testing harness (global skills install +
per-project `e2e/` scaffold).

**Reference study:** read the two working harnesses at `ulysses-portal/e2e/` and
`ulysses-index/e2e/` (governed `run-audit.sh` + 3-tier audit trail, Dockerised
Playwright runner, Gherkin test-book, tmux cockpit, local skill overlays), the seven
global skills in `~/.copilot/skills/`, and the `cockpit-wake` script
(`~/.local/bin/cockpit-wake`, a 344-line stdlib Python file).

**Produced:**
- `docs/vision_of_product/VP1-e2e-bootstrap/VP1.md` — product brief.
- `docs/architecture/overview.md` — repo structure, two-phase bootstrap, component
  diagram, parameterisation model, idempotency/update model, tech summary, risks/spikes.
- `docs/ADRs/ADR-001` … `ADR-006` — all Proposed:
  - ADR-001 Skills source-of-truth → vendor skills in `skills/`, copy on install (`--link` for authors).
  - ADR-002 `e2e/` sub-repo → `git init` inside `e2e/` (matches references); `--no-git` opt-out.
  - ADR-003 Entry point → single `bootstrap.sh` dispatcher + `lib/cmd-*.sh` (no CLI framework).
  - ADR-004 Parameterisation → two-tier: scaffold-time tokens (config→prompt→default) + AI skills for topology.
  - ADR-005 `cockpit-wake` → vendor single-file Python script in `bin/`, copy to `~/.local/bin`.
  - ADR-006 Update strategy → `e2e --update` driven by `MANIFEST.toml` (framework/seed/project ownership).
  - ADR-007 Cold install → versioned GitHub Releases tarball + `install.sh` one-liner; `--from-release latest|vX.Y.Z`.
- `docs/plan/backlog.yaml` — VP1 + placeholder TH1 (`not_started`, `locked: false`).

**Open items for Phase 3 (Planning):**
- Decompose TH1 into epics/stories.
- Two spikes flagged: macOS/BSD bash portability; `MANIFEST.toml` glob classification correctness.

**Status:** Architecture complete; ADRs Proposed (none locked). Ready for `/plan-product` step 2.

---

## 2026-06-16 — Architecture addendum: cold install + release CI (Architect Agent)

Added per request:
- `docs/ADRs/ADR-007-github-release-distribution.md` (Proposed) — cold install via a
  versioned GitHub Releases tarball; `install.sh` one-liner; checksum-verified.
- `docs/architecture/overview.md` §8 "CI/CD & releases" — `.github/workflows/release.yml`
  triggers on `v[0-9]+.[0-9]+.[0-9]+` tags, assembles `copilotcockpit-${TAG}.tar.gz`
  (+ `.sha256`), and runs `gh release create --latest`. Sections renumbered (Technology
  choices → 9, Risks → 10, Out of scope → 11); repo tree gains `install.sh`,
  `CHANGELOG.md`, `.github/workflows/release.yml`.
- `docs/plan/backlog.yaml` — ADR-007 registered; TH1 scope notes gain cold-install +
  release-CI items.

**Boundary clarified:** the release CI is for *copilotcockpit itself* and is in scope;
generating CI for a *scaffolded project's* `e2e/` remains out of scope (VP1 §9, arch §11).

---

## 2026-06-16 — Architecture addendum: dev workflow, test strategy & dev skill (Architect Agent)

Added per request:
- `docs/ADRs/ADR-008-copilotcockpit-dev-skill.md` (Proposed) — a new **8th** repo-managed
  skill `copilotcockpit-dev` (in `skills/` + installed globally) encoding the autonomous
  GitOps feature→release flow: branch → local test gate → PR → CI → squash-merge →
  conventional-commit-driven version bump (`VERSION`) → annotated tag → `release.yml`
  (ADR-007). Defines 5 test categories (script unit/bats, template integrity, skills lint,
  integration smoke, release asset validation), the commit→semver mapping, a bounded
  3-attempt escalation policy, and the `gh` CLI commands per step.
- `docs/architecture/overview.md` §9 "Developer workflow & test strategy" — `copilotcockpit-dev`
  skill, `tests/` tree + `run-tests.sh` dispatcher, 5-category test table, ASCII GitOps
  sequence diagram, `VERSION`+`CHANGELOG.md` ownership. Sections renumbered (Technology
  choices → 10, Risks → 11, Out of scope → 12). §2 repo tree gains `tests/`, `run-tests.sh`,
  `VERSION`, `CHANGELOG.md`, `skills/copilotcockpit-dev/SKILL.md`, `.github/workflows/ci.yml`.
  §4 global-install list extended to 8 skills.
- `docs/ADRs/ADR-001-skills-source-of-truth.md` — added a forward-reference note that
  ADR-008 introduces the 8th (contributor) skill; the source-of-truth/install mechanism is
  unchanged. (ADR-001 is Proposed/unlocked — edit permitted.)
- `docs/plan/backlog.yaml` — ADR-008 registered; TH1 scope notes gain dev-skill, test-infra,
  `ci.yml`, and `VERSION`/`CHANGELOG` items.

**Boundary clarified:** ADR-008 owns idea→tag (development discipline of copilotcockpit
itself); ADR-007 owns tag→release (artefact publishing). Contributor deps (`bats`, `gh`)
do not affect end users — `bootstrap.sh` stays dependency-light (P5).

---

## 2026-06-16 — Phase 3: Planning (Product Owner Agent)

**Input:** VP1 vision + architecture overview (§2–§12) + ADR-001…ADR-008 + the TH1
placeholder backlog. Decomposed TH1 — Bootstrap Tooling into epics and hybrid-BDD
user stories.

**Produced:**
- `docs/themes/TH1-bootstrap-tooling/README.md` — theme overview + epic table + sequencing.
- **6 epics / 21 stories** (all `pending`), every story with an "As a…", concrete
  acceptance criteria (exact paths/flags/exit codes), Gherkin BDD scenarios incl.
  edge/error cases, and `## Notes` with ADR references:
  - **E6 — Spikes** (scheduled first): US1 macOS/BSD portability cheat-sheet; US2 `MANIFEST.toml`
    glob-classification matcher. Both XS, output-defined.
  - **E1 — Core bootstrap shell**: US1 `lib/common.sh`; US2 `bootstrap.sh` dispatcher; US3 `cmd-doctor.sh`.
  - **E2 — Global skills install**: US1 vendor 7 harness skills; US2 vendor `cockpit-wake`;
    US3 `cmd-global.sh` (8 skills + cockpit-wake, copy/`--link`/idempotent); US4 cold install
    (`install.sh` + `--from-release`).
  - **E3 — E2E scaffold**: US1–US3 `templates/e2e/` skeleton (runners / governance+test-book /
    overlays+context); US4 `MANIFEST.toml`; US5 `cmd-e2e.sh` scaffold; US6 `--update`.
  - **E4 — CI/CD & releases**: US1 `VERSION`+`CHANGELOG.md`; US2 `release.yml`; US3 `ci.yml`.
  - **E5 — Dev skill & test infra**: US1 `run-tests.sh`+cat-1 bats; US2 cat-2/3/4 suites;
    US3 `copilotcockpit-dev/SKILL.md`.
- **6 GitHub issue templates** at `.github/ISSUE_TEMPLATE/TH1-E<m>-<slug>.md`
  (frontmatter `labels: ["TH1","E<m>"]`, one checkbox per story + link to full stories).
- `docs/plan/backlog.yaml` — `epics: []` placeholder replaced with full epic/story entries
  (`id`, `title`, `status`, `size`, `path`, `depends_on`).

**Sequencing & dependencies:** E6 spikes first (feed E1 `common.sh` + E3 templates/manifest);
E1 (`common.sh`→dispatcher→doctor) gates E2/E3; E5 test suites follow the code they cover and
wire into E4 `ci.yml`; `release.yml` consumes the E3 template tarball contract; `copilotcockpit-dev`
skill follows `release.yml`. Validated: **YAML parses, all 21 story paths exist, dependency graph
is acyclic, no duplicate IDs.** Sizes: 3×XS, 5×S, 13×M (none > L).

**Notes:** No remote/`gh` auth in this environment, so repo labels could not be verified live;
issue templates use the conventional `["TH1","E<m>"]` labels per the planning spec. TH1 is the
first theme (no prior-theme issue templates to archive). TH1 remains `locked: false`.

**Status:** Phase 3 (Planning) complete for TH1. Backlog ready for orchestration/implementation.

---

## 2026-06-16 — Phase 4: Autopilot orchestration begins (Orchestrator)

Starting TH1 execution. Dependency-resolved order: E6 spikes → E1 → E2/E3 → E4/E5.

- **TH1-E6-US1** START — SPIKE macOS/BSD portability cheat-sheet.
- **TH1-E6-US1** DONE — portability-cheatsheet.md produced (commit 1ec7015). XS/spike: self-review only.
- **TH1-E6-US2** START — SPIKE MANIFEST.toml glob classification matcher.
- **TH1-E6-US2** DONE — manifest-classification.md produced; matcher run against throwaway fixture (outside repo), 11/11 paths classified per ADR-006. Mechanism: bash `[[ == glob ]]` array classifier, seed→framework→project→default-project precedence, macOS bash-3.2-safe (no globstar dependency). XS/spike: self-review only.
- **TH1-E6-US2** DONE — manifest-classification.md produced (commit 9a15b7f). Fixture kept out of VCS.
- **EPIC E6 DONE** — both spikes complete. Small-epic ceremony: test suite deferred (none built yet); changelog entry written. E6 unblocks E1-US1, E3-US1, E3-US4, E3-US6.
- **TH1-E1-US1** START — lib/common.sh shared helpers.
- **TH1-E1-US1** DONE — lib/common.sh (commit b2e0d58). Reviewer: APPROVED.
- **TH1-E1-US2** START — bootstrap.sh dispatcher.
- **TH1-E1-US2** DONE — bootstrap.sh dispatcher (commit 44f4507). Reviewer: APPROVED.
- **TH1-E1-US3** START — lib/cmd-doctor.sh prerequisites + drift detection.
- **TH1-E1-US3** DONE — lib/cmd-doctor.sh (commit 2667aba). Reviewer: APPROVED.
- **EPIC E1 DONE** — dispatcher + common.sh + doctor. Small-epic ceremony: bash -n all 3 files OK; doctor/usage smoke exit 0; changelog written. Unblocks E2-US3, E3-US5.
- **TH1-E2-US1** START — vendor 7 harness skills into skills/.
- **TH1-E2-US2** START — vendor cockpit-wake into bin/.
- **TH1-E2-US1** DONE — 7 skills vendored (commit ba7cce7). Verbatim verified.
- **TH1-E2-US2** DONE — cockpit-wake vendored (commit 97d26d5). Verbatim verified.
- **TH1-E2-US3** START — lib/cmd-global.sh install/update 8 skills + cockpit-wake.
- **TH1-E2-US3** DONE — lib/cmd-global.sh installs/updates 8 skills + cockpit-wake; all 9 ACs verified against scratch HOME (copy/link/dry-run/drift/idempotent/PATH/AC9-fatal).
- **TH1-E2-US3** DONE — lib/cmd-global.sh (commit 31c04a1). Reviewer: APPROVED.
- **TH1-E2-US4** START — install.sh wrapper + global --from-release.
- **TH1-E2-US4** DONE — install.sh + --from-release (commit 6be0446). Reviewer: APPROVED. Tech-debt note: reject `--link` with `--from-release` to avoid dangling symlinks (non-AC hardening, deferred).
- **EPIC E2** all stories done — running large-epic ceremony (integration + quality check).
- **EPIC E2 DONE** — integration PASS + quality review APPROVED. Changelog written. Unblocks E2-US4-dependents (E4-US2) and feeds E5 tests. Deferred tech-debt logged.

## 2026-06-16 — Epic E3 (E2E scaffold) begins
- **TH1-E3-US1** START — templates/e2e/ Playwright infra + governed runners.
- **TH1-E3-US1** DONE — template runners (commit 4de48f0; rework fix 406a298). Reviewer: REQUEST_CHANGES→APPROVED (1 iter; JUnit failure-classification + digest header bugs fixed).
- **TH1-E3-US2** START — templates/e2e governance, test-book, tests & runs skeleton.
- **TH1-E3-US2** DONE — governance/test-book/tests skeleton (commit a4792e1). Reviewer: APPROVED.
- **TH1-E3-US3** START — templates/e2e context, skill overlays, tmux stubs & env files.
- **TH1-E3-US3** DONE — overlays/context/tmux/env (commit deddd6e). Reviewer: APPROVED.
- **TH1-E3-US4** START — templates/e2e/MANIFEST.toml ownership classification.
- **TH1-E3-US4** DONE — MANIFEST.toml (commit 640ac96). Reviewer: APPROVED (flaky-known.md safe-default = correct, non-blocker).
- **TH1-E3-US5** START — lib/cmd-e2e.sh scaffold.
- **TH1-E3-US5** DONE — lib/cmd-e2e.sh scaffold (commit 788e682). Reviewer: APPROVED (substitute-everywhere + atomic staging + sed-injection hardening validated).
- **TH1-E3-US6** START — lib/cmd-e2e.sh --update content-preserving refresh.
- **TH1-E3-US6** DONE — --update content-preserving refresh (commit 493f3be). Reviewer: APPROVED (never-touch + idempotency + scaffold-regression validated).
- **EPIC E3** all 6 stories done — running large-epic ceremony (integration + quality check).
- **EPIC E3 DONE** — integration PASS (1 blocker fixed: run-audit INDEX append, commit 4a09371; re-verified PASS) + quality review APPROVED. Changelog written. Unblocks E3-US5/US6-dependents (E4-US2, E5 tests).

## 2026-06-16 — Epics E4 (CI/CD) + E5 (test infra) — interleaved by dependency
- **TH1-E4-US1** START — VERSION + CHANGELOG.md (semver source of truth).
- **TH1-E4-US1** DONE — VERSION 0.1.0 + CHANGELOG.md (commit b8222e8). XS: self-review.
- **TH1-E4-US2** START — .github/workflows/release.yml (tag-driven tarball + sha256 + gh release).

### TH1-E4-US2 — DONE (reviewer APPROVED)
- `.github/workflows/release.yml` (tag-driven tarball + dual sha256 + gh release --latest + Category-5 validate job) + repo-root `README.md`. Commit a8db271. Local packaging dry-run + Category-5 extract/global-dry-run/doctor all PASS; actionlint clean.

### TH1-E5-US1 — START
- Dispatching developer: run-tests.sh dispatcher + Category-1 bats unit tests (cmd-global/cmd-e2e/cmd-doctor). bats not present on host — developer to install.

### TH1-E5-US1 — DONE
- `run-tests.sh` repo-root dispatcher (`unit|template|skills|integration|all`; absent categories auto-skip without false pass, auto-wire when E5-US2 files land) + `tests/unit/{cmd-global,cmd-e2e,cmd-doctor}.bats` + `tests/unit/helper.bash`. All 14 unit tests green via `./run-tests.sh unit`; bats installed under $HOME (not committed). Fake-HOME isolation enforced in helper (hard guard); real ~/.copilot untouched. `bash -n` clean.

### TH1-E5-US1 — DONE (reviewer APPROVED)
- `run-tests.sh` dispatcher + `tests/unit/{cmd-global,cmd-e2e,cmd-doctor}.bats` (14 tests, all pass, exit 0). Fake-HOME isolation with hard guard. Commit d2aeddf.

### TH1-E5-US2 — START
- Dispatching developer: Category 2 (template integrity), Category 3 (skills lint), Category 4 (integration smoke); auto-wire into run-tests.sh.

### TH1-E5-US2 — DONE
- Categories 2-4 added and auto-wired into `run-tests.sh`: `tests/template/check-template.sh` (token resolution after test-substitution of the 4 sanctioned tokens, package.json.tmpl valid JSON, MANIFEST.toml explicit coverage of every templates/e2e/ path, `bash -n` of run-audit.sh/run-playwright.sh), `tests/skills/lint-skills.sh` (YAML frontmatter + non-empty name/description via PyYAML), `tests/integration/smoke.bats` (global/e2e/doctor dry-run smoke under fake HOME).
- Coverage check surfaced 2 genuine manifest gaps → classified in MANIFEST.toml: `MANIFEST.toml`→[framework], `governance/flaky-known.md`→[seed].
- All categories exit 0; `./run-tests.sh template|skills|integration|all` green (all incl. 14 Category-1 unit tests). Fake-HOME hard-guard isolation; real ~/.copilot/cockpit-wake untouched. `bash -n` clean.

### TH1-E5-US2 — DONE (reviewer APPROVED)
- Category 2 `tests/template/check-template.sh`, Category 3 `tests/skills/lint-skills.sh`, Category 4 `tests/integration/smoke.bats`. Closed 2 MANIFEST coverage gaps (MANIFEST.toml→framework, governance/flaky-known.md→seed, classifications verified correct). `run-tests.sh all` exits 0 (14 unit + cat2/3/4). Commit 047197d.
- DEFERRED (non-blocking): sync architecture §7 embedded MANIFEST block with the 2 new classifications; add min-count assert to lint-skills.

### TH1-E4-US3 — START
- Dispatching developer: `.github/workflows/ci.yml` PR checks running test categories 1-4.

### TH1-E4-US3 — DONE (reviewer APPROVED)
- `.github/workflows/ci.yml` PR merge-gate running categories 1-4 via `run-tests.sh all`; no Category 5; `[skip ci]` honoured. Local job-body simulation exit 0. Commit d160e5e.

### Epic E4 (CI/CD & releases) — CEREMONY (small epic, 3 stories)
- Epic smoke: release.yml + ci.yml valid YAML; VERSION=0.1.0; CHANGELOG.md has v0.1.0 section. All 3 stories APPROVED.
- Epic E4 status -> done. Changelog entry written.

### TH1-E5-US3 — START
- Dispatching developer: `skills/copilotcockpit-dev/SKILL.md` (GitOps delivery runbook, 8th managed skill).

### TH1-E5-US3 — DONE (reviewer APPROVED)
- `skills/copilotcockpit-dev/SKILL.md` (8th managed skill, ADR-008 GitOps runbook). Promoted copilotcockpit-dev from CC_PENDING_ROLES → CC_HARNESS_ROLES in cmd-global.sh (now required+installed); updated smoke.bats to assert pending-warning absent. Commit 216bc09.
- DEFERRED (non-blocking): generic-ify the dead pending-skip message in cmd-global.sh:333.

### Epic E5 (Dev skill & test infra) — CEREMONY (small epic, 3 stories)
- Epic gate `./run-tests.sh all` → EXIT 0 (14 unit + template + 8 skills + 3 integration). All 3 stories APPROVED.
- Epic E5 status -> done. Changelog entry written.
