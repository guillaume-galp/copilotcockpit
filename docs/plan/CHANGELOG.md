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

---

## Epic TH3.E2 — Worker lifecycle and idempotent mission protocol

**Stories Completed:** TH3.E2.US1 (structured worker lifecycle and freshness, `d493be6`),
TH3.E2.US2 (idempotent command envelopes and acknowledgements, `5ba0b71`),
TH3.E2.US3 (managed questions, cancellation, and replacement, `2e2bede`).
All three reviewer-APPROVED; each needed exactly one bounded rework iteration, and every
rework closed a security or test-integrity defect rather than a functional one.

**Key Changes:**
- **Structured worker lifecycle and freshness (US1, ADR-013).** Workers emit versioned
  `worker-lifecycle-<state>` events for `accepted`, `running`, `blocked`, `completed`,
  `failed`, `cancelled`, and `replaced`, published through the existing immutable
  committed-event mechanism with a closed, fully validated field set carrying mission,
  queue-item, worker, trace, and parent-trace identifiers. `fold_worker_missions`
  materializes `ledger.worker_missions` only for events that correlate, target a
  non-terminal slot, carry a strictly greater sequence, and match the architecture §9
  transition table verbatim; every other event — late, duplicate, uncorrelated, or
  post-terminal — is still committed as durable audit evidence and classified
  `retained-*` without ever regressing state. Lock-free, read-only `lifecycle-status`
  derives freshness from committed events rather than the projection, and reports an
  expired `fresh_until` as `stale` with reason `heartbeat-expired` and recovery
  `awaiting-bounded-recovery`; `stale` is deliberately not a lifecycle state, so it can
  never be read as a failure.
- **Idempotent command envelopes and acknowledgements (US2, ADR-013/ADR-016).** Every
  state-changing worker operation is a versioned, closed-field command envelope carrying
  command ID, mission and queue-item IDs, target kind and ID, trace and parent-trace IDs,
  a canonical `sha256` payload digest, the declared mission boundaries, and timestamps.
  The payload body itself is never persisted — only its digest — keeping canonical events
  metadata-only. Acknowledgements record `accepted`, `applied`, `rejected`, and
  `duplicate` outcomes as committed events folded into `ledger.commands` under a closed
  `registered → accepted → applied` and `rejected` order in which `duplicate` never
  advances and `applied` is reachable exactly once. Redelivering the same ID with the same
  digest returns the stored result without re-applying; the same ID with a different
  digest or envelope is refused and its conflict durably recorded. Ordering is taken from
  the committed revision alone, so concurrent deliveries are resolved by the deterministic
  fold rather than by a pre-read.
- **Managed questions, cancellation, and replacement (US3, ADR-011/ADR-013/ADR-016).**
  Mission questions, replies, and access-prompt responses are first-class commands whose
  correlated `mission-dialog` record is committed inside the `command-registered` event of
  its own envelope; both halves of that contract are enforced in each direction, response
  kinds are derived from the prompt so an access prompt can only be closed by an
  access-prompt response, admission refuses any interaction that does not name a
  materialized, correlated, still-active mission, and the generic `register-command`
  surface refuses the managed command types — so unscoped temporary files and pane
  keystrokes have no protocol-recognised equivalent. Question and answer bodies are never
  written anywhere in the control root; the envelope digest binds the command to the exact
  body while typed references name where it lives. Cooperative cancellation carries an
  always-explicit acknowledgement deadline whose outcome is a byte-inert evaluation over
  committed evidence and one explicit `--as-of`, distinguishing `acknowledged`,
  `awaiting-acknowledgement`, and `timed-out` with a recoverable reason that never claims
  failure and runs no timer or daemon. Replacement terminates the prior mission with the
  existing `replaced` state and `superseded_by_mission_id`, reserves exactly one new
  mission ID per worker in the new replayable `mission_slots` projection, and durably
  records `second-active-slot`, `unmatched-mission-slot`, and `reused-mission-id`
  conflicts instead of ever creating a second active slot.
- **Fail-closed and anti-forgery hardening (all three stories).** Worker-controlled
  identifiers are constrained to printable non-whitespace ASCII before any lock or
  candidate exists, so no identity string can forge a record boundary in the line-oriented
  status payloads, and free text is kept out of every parseable stdout position.
  Unrepresentable freshness deadlines, oversized sequences, JSON integer literals beyond
  the CPython digit limit, and deep recursion now raise `cockpit-control:` diagnostics
  instead of tracebacks that leaked absolute internal paths, with the same guard extended
  to every parse site reachable from the new read paths. Ten `set -e`-vacuous `!`-negated
  bats assertions (SC2314) were replaced with errexit-honouring helpers after a surviving
  mutation proved they enforced nothing.
- **Cross-story integrity closed at the epic gate.** Three defects that existed only
  between the layers were found and fixed by the integration gate: a lifecycle event from
  another worker could claim a replacement-reserved mission ID, producing two claimed slots
  naming one mission with zero recorded conflicts and a permanently unreleasable
  reservation; a reserved slot's queue item could be silently re-pointed against the
  committed replacement record; and a dialog could outlive its mission while
  `mission-status` still advertised it as answerable even though `answer-question` fails
  closed. The first two are closed by a `_mission_slot_claim_fault` guard at the head of
  the lifecycle branch of `fold_mission_state`, placed in the deterministic fold so no
  surface — including the raw `publish-event` primitive — can bypass it, recording the
  refusal as a durable conflict on the emitting worker. The third is closed by dialog
  observations (`answerable`, `orphaned`, `settled`) that follow the established "an
  observation is not a state" pattern: no committed record is rewritten and replay bytes
  are unchanged.

**Files Modified:** `bin/cockpit_control.py`, `bin/cockpit-control`, `README.md`,
`tests/unit/cmd-control-lifecycle.bats` (new), `tests/unit/cmd-control-command.bats` (new),
`tests/unit/cmd-control-mission.bats` (new), `tests/unit/cmd-control.bats`,
`docs/plan/backlog.yaml`, `docs/plan/session-log.md`, `docs/plan/CHANGELOG.md`.

**Epic ceremony (3 stories):** the `epic-integration` session ran the full gate
(`./run-tests.sh all`: 161/161 unit, 25-check template integrity, 8/8 skills, 5/5
integration, 14-check codex — 166 ok, 0 failures) plus `bootstrap.sh doctor`,
`global --dry-run`, `codex-repo --dry-run`, `codex-global --dry-run`, and a
side-effect-free `e2e <tmpdir> --yes --dry-run`, and exercised the three stories together
on scratch control roots rather than in isolation: a complete dispatch → accept →
acknowledge → run → heartbeat → blocked → question → answer → running → complete mission
with all status surfaces cross-checked; the obsolescence path through cancellation,
acknowledgement, replacement, and re-acceptance; twelve adversarial cross-layer
interleavings; total derived-state loss with byte-deterministic re-derivation and
idempotent replay; and fifteen-process concurrency mixing lifecycle events, deliveries,
acknowledgements, and replacements. It closed the three cross-story defects above with two
new deterministic regressions in `tests/unit/cmd-control-mission.bats`, each verified to
fail against the pre-fix build. The epic quality check re-verified all nine acceptance
criteria against the final tree, independently reproduced each defect on an isolated copy
of the pre-fix module, killed seven of eight mutations of the fixes, ran twelve
multi-process concurrency rounds and 320 randomized cross-layer fuzz steps against eight
hand-written invariants, verified the migration note (a pre-fix store that hit the pattern
is reported as advisory derived drift and healed non-destructively by `replay-ledger`), and
APPROVED. The epic completion gate is met: every active worker mission has structured
state, and every state-changing command is safely retryable and auditable.

**Recorded follow-ups (non-blocking, none epic-blocking):** `lifecycle-status` is silent
about slot ownership, so a mission refused the slot still appears as a normal fresh mission
(E3 precedence); a `reserved` slot has no deadline and no recovery path if the worker never
accepts (E3/E4 bounded recovery); preflight has structural but no semantic readiness
dimension for the new authoritative records, underlined by `ledger.active_mission_id` being
settable through the E1 `publish-event` primitive to a mission no slot holds while both
`validate` and `preflight` stay clean (E3); an unacknowledged cancellation of a mission that
has since completed is still counted as timed-out and should gain an obsolete observation
(E3); a module-level `_dialog_observation` mutation survives because `observation settled`
is unasserted on the answer path; a different-worker lifecycle event records a durable
conflict only when the mission is slot-held, not when it is materialized-but-slotless;
`record-lifecycle` exits 0 for a retained event while the command surfaces exit 1 for the
analogous case; the pre-existing `publish-event --actor`/`--type` newline injection into the
`list-events` renderer, reproduced at baseline; the pre-existing `publish-event` acceptance
of non-finite JSON numbers into the immutable log, which the US2 command path already
refuses; sixteen `set -e`-vacuous `!`-negated assertions remaining in sibling control
suites; the `commands/` directory and `validate_command` describing a second, incompatible
notion of "command" alongside `command-envelope`; and no `.gitignore` for
`bin/__pycache__/`.

## Epic TH3.E3 — Overseer reconciliation and bounded recovery

**Stories Completed:** TH3.E3.US1 (`c39873d`), TH3.E3.US2 (`675a743`), TH3.E3.US3 (`1a22229`). All three stories were reviewer-APPROVED under the active assurance envelope.

**Key Changes:**
- Added `cockpit-overseer tick` as a deterministic, one-action controller pass that validates declared roots, folds queue/control evidence from committed state, applies precedence without trusting pane text, and records no-action observations only once.
- Added bounded stale-worker recovery and conflict reconciliation: stale active missions proceed through finite recovery commands without being failed by timeout alone, duplicate live worker claims retain the earliest valid accepted mission and block dispatch with actionable repair, and derived ledger drift is repaired by replay while authoritative corruption refuses mutation.
- Added queue-linked bounded escalation: terminal queue state is not reopened by recovery, repeated unresolved blocking advances mission-scoped escalation evidence/counts through troubleshoot and human-decision suspension, and escalation records include evidence, impact, attempted recovery, options, and the pending decision.
- Removed the accidental tracked `bin/__pycache__` artifact introduced during partial US3 recovery; generated bytecode is not part of the delivery surface.

**Files Modified:**
- `README.md`
- `bin/cockpit-overseer`
- `bin/cockpit_control.py`
- `skills/e2e-cockpit/SKILL.md`
- `tests/unit/cmd-overseer-tick.bats`
- `tests/unit/cmd-overseer-recovery.bats`
- `tests/unit/cmd-overseer-escalation.bats`
- `docs/plan/backlog.yaml`
- `docs/plan/session-log.md`

**Epic ceremony:** Full repository gate `./run-tests.sh all` completed with exit 0 (unit category `1..217` plus template, skills, integration, and codex categories). Proportionate epic quality review APPROVED; residual risk is limited to future stories adding new recovery/escalation observation kinds, which should carry explicit tests.

## Epic TH3.E4 — Intent-aware wakes, evidence, and boundaries

**Stories Completed:** TH3.E4.US1 (`9547607`), TH3.E4.US2 (`b07f6df`), TH3.E4.US3 (`9360a4a`). All three stories were reviewer-APPROVED under the reduced assurance envelope.

**Key Changes:**
- VP3 scheduled wake records now carry mission, queue item, owner, intent, stop condition, cadence, blocker threshold, and lifecycle metadata; generated jobs export that metadata and call managed controller ticks rather than pasting prompts.
- Wake jobs now guard execution through persisted wake state and short durable leases: malformed/legacy/terminal/human-suspended/fulfilled wakes skip or fail closed, overlapping invocations record `wake-duplicate-skipped`, and expired leases are recovered by evidence-preserving quarantine plus explicit recovery events before reacquisition.
- Evidence-boundary enforcement now uses committed command/mission boundaries and explicit architecture blocker categories so undeclared repository, image, CI/CD, IAM, or deployment needs block dispatch pending re-scope and emit a journal-backed controller observation with mission, queue, command, trace, and boundary references.

**Files Modified:**
- `bin/cockpit-wake`
- `bin/cockpit_control.py`
- `tests/unit/cmd-wake.bats`
- `tests/unit/cmd-evidence-boundary.bats`
- `docs/plan/backlog.yaml`
- `docs/plan/session-log.md`

**Epic ceremony:** Full repository gate `./run-tests.sh all` completed with exit 0 (unit category `1..223` plus template, skills, integration, and codex categories). Proportionate epic quality review APPROVED. Residual risks are accepted as follow-ups: the generated wake script has a redundant inert quoted owner/mission shell check guarded authoritatively by `_guard-fire`, and future blocker detail formats will need explicit boundary-category mapping.

## Epic TH3.E5 — Compatibility, integration, and resilience validation

**Stories Completed:** TH3.E5.US1 (`41b6933`), TH3.E5.US2 (`919e5b2`), TH3.E5.US3 (`33add40`). All three stories were reviewer-APPROVED; the epic gate required two focused security hardening reworks for generated wake-script quoting before final approval.

**Key Changes:**
- Added additive VP3 migration and doctor legacy guidance: existing wakes/queues/traces/project-owned files are preserved, current stores are idempotent, malformed metadata is backed up, future schema versions refuse mutation, and doctor surfaces control-plane readiness/legacy limitations.
- Confirmed the control-plane contract and fault-injection coverage across existing repository categories for schemas, lifecycle transitions, command replay, reconciliation precedence, wake stop/lease behavior, migration, interrupted writes, malformed state, lock contention, lost acknowledgements, stale workers, and overlapping wakes.
- Added deterministic integration resilience BDDs wired into `run-tests.sh integration`: queue-backed mission restart recovery without chat history, stalled-worker replacement or bounded human escalation, governed-evidence queue clearance, clearance refusal without evidence, and recurrent wake termination/suspension.
- Hardened generated wake job scripts by shell-quoting persisted/user-provided metadata and notification arguments with `shlex.quote`, with malicious owner and label regression tests.

**Files Modified:**
- `bin/cockpit-wake`
- `lib/cmd-doctor.sh`
- `run-tests.sh`
- `tests/unit/cmd-wake.bats`
- `tests/unit/cmd-wake-migrate.bats`
- `tests/integration/resilience.bats`
- `docs/plan/backlog.yaml`
- `docs/plan/session-log.md`

**Epic ceremony:** Full repository gate `./run-tests.sh all` completed with exit 0 after the final quoting fix (unit category `1..223`, template, skills, integration `1..8`, and codex categories all passed). Final proportionate epic re-review APPROVED. Residual risk: cron path quoting remains documented as a future robustness cleanup for unusual wake-directory paths; generated job metadata and notification injection vectors are covered.
