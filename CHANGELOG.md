# Changelog

All notable changes to **copilotcockpit** are recorded here.

This file is the human-readable release-notes record that complements the
[`VERSION`](./VERSION) file (the single source of semver truth — see ADR-008 and
architecture §9).

> **Maintenance convention.** New sections are **prepended** (newest first) by the
> `copilotcockpit-dev` agent, which derives each entry from the **squash-commit body**
> of a merged change. On release, the matching `## vX.Y.Z — YYYY-MM-DD` section is
> **sourced into the GitHub Release body** by `release.yml`. Keep the heading format
> `## vX.Y.Z — YYYY-MM-DD` (newest first) and stage unreleased notes under
> `## Unreleased`.

## Unreleased

_Nothing yet._

## v0.9.2 — 2026-09-08

### Fixed
- Resolved BUG-001: bootstrap now binds distinct control, queue, planning and
  implementation roots through supported commands; preflight fails closed when
  operational boundaries or lifecycle capabilities are missing.
- Connected controller dispatch to durable pending mission intent before pane
  delivery, atomic worker acceptance, idempotent receipts, and immutable bounded
  acceptance deadlines.
- Made approval prompts, holds, responses, heartbeats, cancellation and replacement
  durable and correlated. Accepted-worker cancellation and replacement require
  cooperative acknowledgements; unaccepted reservations have an explicitly
  inspected recovery path.
- Made worker status authoritative, including awaiting-approval, held, blocked
  and unreachable states; lost journal authority cannot appear as availability.
- Preserved structured control-root and target identity in scheduled wakes,
  fenced cancellation against new execution, and retained the `stop` alias.
- Closed concurrent dispatch/acceptance races, foreign acknowledgement
  correlation gaps, and new legacy-form replacement bypasses.

### Operator adoption
- Retired direct mission-dispatch bypasses in favor of queue/controller delivery;
  explicit bootstrap transport remains for worker role priming only.
- Updated managed Copilot/Codex skills, templates and public CLI guidance.
  Existing project-owned launchers and legacy schedules require operator-led
  adoption; immutable history is diagnosed without destructive rewriting.
- Native approval prompts require explicit durable reporting. Worker
  acknowledgements are cooperative evidence, not proof of process termination.

## v0.9.1 — 2026-09-07

### Changed
- Modularized the cockpit control plane behind the stable `cockpit_control`
  compatibility facade, extracting control-root schemas, locks, immutable
  journal publication, ledger projection, worker lifecycle, command
  acknowledgements, mission control, controller reconciliation, wake
  scheduling, and CLI adapter seams.
- Preserved existing public imports, commands, output and exit-code contracts,
  control-store schemas, event and ledger formats, dry-run behavior, and
  fail-closed diagnostics throughout the extraction.
- Added the cockpit control-plane ontology and dependency-direction rules so
  implementation modules align with explicit bounded contexts and shared
  vocabulary.

### Distribution
- Added an explicit managed-runtime-module inventory and wired extracted Python
  modules into global copy/link installation, doctor drift detection,
  uninstall, release archives, and isolated cold-install verification.

### Tests
- Added modularity conformance coverage for import compatibility, dependency
  cycles, vocabulary, CLI behavior, schemas, replay, locking, controller
  reconciliation, wake behavior, and installed-wrapper execution.

## v0.9.0 — 2026-09-07

### Added
- Delivered the TH3/VP3 holistic cockpit control plane: versioned control store,
  immutable event journal, replayable ledger projections, structured worker
  lifecycle state, idempotent command envelopes, deterministic controller ticks,
  bounded stale-worker recovery, queue-linked escalation, intent-aware wakes,
  durable wake leases, evidence-boundary enforcement, additive migration, and a
  deterministic end-to-end resilience proof.
- Added `cockpit-control` and the Python control-plane runtime with preflight,
  guarded repair, replay, lifecycle, command, mission, controller, and boundary
  capabilities.
- Added integration resilience scenarios covering overseer reset recovery,
  stalled-worker replacement or escalation, governed evidence clearance, and
  wake termination/suspension.

### Changed
- Scheduled VP3 wake jobs now call the managed controller through wake leases
  instead of directly pasting autonomous prompts.
- Doctor/readiness output now surfaces control-plane preflight and migration
  guidance for legacy capability.

### Security
- Hardened generated wake job scripts by shell-quoting user and persisted wake
  metadata, including notification labels, with regression tests for malicious
  owner and label inputs.

### Tests
- Expanded the repository gate to cover the full TH3 control-plane contract,
  including schemas, lifecycle transitions, command replay, reconciliation
  precedence, wake stop/lease behavior, migration, interrupted writes, malformed
  state, lock contention, stale workers, overlapping wakes, and trace/evidence
  boundary reconstruction.

## v0.8.2 — 2026-07-31

### Fixed
- Made `cockpit-wake` press Enter explicitly after pasting a scheduled wake
  message into the target tmux pane, with a `C-m` fallback for compatibility.

### Tests
- Added fake-tmux regression coverage proving `cockpit-wake fire` pastes the
  wake message and submits it in the target pane.

## v0.8.1 — 2026-07-31

### Fixed
- Enforced the dual-queue contract so cockpit request queue operations require an explicit, scoped queue root instead of falling back to shared state.
- Isolated queue roots per cockpit tmux session so concurrent cockpit sessions keep independent overseer request queues.

## v0.8.0 — 2026-07-31

### Added
- Added `cockpit-queue`, a FIFO overseer request queue CLI for classifying,
  enqueueing, inspecting, pausing/resuming, rejecting, starting, and clearing
  build requests.
- Added queue persistence under `docs/queue/items/<id>.yaml` with append-only
  transition history in `docs/queue/events.jsonl`.
- Added queue-scoped E2E clearance rules: a queue item can clear only after
  local delivery evidence plus governed runbook evidence or an explicit waiver.

### Changed
- Featured the FIFO queue use case in the README with problem, solution, and
  benefits.
- Wired `cockpit-queue` into global install, doctor, uninstall, and integration
  smoke checks.
- Updated `e2e-cockpit` and `e2e-operator` skills with queue loop and clearance
  report guidance.

### Tests
- Added unit coverage for queue intake, rejection, FIFO start behavior,
  pause/resume, and clearance evidence.

## v0.7.2 — 2026-07-29

### Fixed
- Made generated Copilot and Codex `e2e-cockpit` project overlays explicitly require `cockpit-protocol` / `cockpit-overseer` for cockpit status, discovery, pane observability, and worker communication.
- Made the managed-cockpit rule cover read-only status requests, including `tmux ls`, `tmux list-windows`, `tmux list-panes`, and `tmux capture-pane`, unless the user explicitly asks for raw tmux diagnostics.
- Added template regression coverage so future generated overlays cannot silently drop the cockpit protocol guardrail.

## v0.7.1 — 2026-07-29

### Fixed
- Replaced stale raw tmux worker communication guidance with
  `cockpit-protocol` and `cockpit-overseer` usage across the canonical E2E
  cockpit skills.
- Made `e2e-operator` explicitly load `e2e-cockpit` first so protocol rules are
  applied before operator-specific triage and dispatch guidance.
- Updated setup guidance for worker priming to use `cockpit-protocol dispatch`
  and `cockpit-protocol tail`.

## v0.7.0 — 2026-07-29

### Added
- Graphify-aware code intelligence guidance across the global cockpit,
  operator, setup, worker-dev, and worker-fix skills.
- Scaffolded Copilot and Codex project overlays now tell agents to prefer
  `graphify query` when the parent repository has `graphify-out/graph.json`,
  while respecting higher-priority project-specific code intelligence systems.

### Changed
- Worker and operator briefs can now carry the Graphify graph path so workers
  orient with the local code graph before broad text search.
- README documents `graphify` as an optional companion tool for cockpit skills
  and workers.

## v0.6.0 — 2026-07-28

### Added
- **cockpit-protocol meta**: added session and cockpit introspection commands for
  current-session, sessions, windows, worker target resolution, and full cockpit
  JSON discovery.
- **cockpit-protocol worker shortcuts**: `dispatch`, `send`, `tail`, and `watch`
  now accept `--worker` plus optional `--session`, while preserving existing
  `--target SESSION:WINDOW` syntax.
- **cockpit-protocol workflow wrappers**: added `status`, `mission`, `nudge`,
  `report`, and `wait-report` helpers for common overseer-to-worker flows.

### Changed
- Worker-addressed dispatch and mission commands now refuse panes that look busy
  unless `--force` is supplied.
- The E2E cockpit skill documents the new worker shortcut and status commands.
- **install.sh**: made cold-install reruns idempotent for version updates by
  extracting release tarballs into a temporary staging directory (instead of the
  caller's current directory) before running `bootstrap.sh global` and
  `bootstrap.sh codex-global`.
- **README**: documented that the cold installer one-liner is safe to re-run for
  updates and no longer leaves `./copilotcockpit` in the working directory.

## v0.5.1 — 2026-07-21

### Fixed
- **install.sh**: recover when the latest GitHub Release is missing the stable
  `copilotcockpit.tar.gz` asset by resolving the latest tag and falling back to
  GitHub's tagged source archive.
- **bootstrap.sh global --from-release latest**: apply the same source-archive
  fallback so release installs keep working even for legacy assetless releases.

## v0.1.3 — 2026-06-16

### Changed
- **README**: clarify `copilotautopilot` section — it bootstraps the `the-copilot-build-method` skill specifically (not just a generic "autopilot"); update compound flow diagram labels accordingly.

_Nothing yet._

## v0.1.2 — 2026-06-16

### Changed
- **README**: complete rewrite — builder-focused intro with Gherkin user stories,
  tmux cockpit ASCII layout, full squad/skills/tools survey, step-by-step
  installation guide, and a new *"Works great with `copilotautopilot`"* section
  explaining how the two sibling toolkits compound.

## v0.1.1 — 2026-06-16

### Fixed
- **cockpit-wake**: replaced `send-keys '{escaped_msg}' Enter` with `load-buffer + paste-buffer + separate Enter` — the previous approach broke on long messages and special characters, causing scheduled messages to never reach the overseer pane. The new approach writes the message to a temp file, loads it into the tmux paste buffer, pastes it, then sends a bare Enter to submit.

### Added
- **cockpit-wake**: persistent inbox (`~/.config/cockpit-wake/inbox.md`) — every fired awakening is appended so future Copilot sessions can catch up on missed messages.
- **cockpit-wake**: optional desktop notification via `notify-send` when a scheduled message fires.

### Changed
- `actions/checkout` bumped from `v4` → `v6` in both `ci.yml` and `release.yml` (Node 20 deprecation on GitHub Actions runners).

## v0.1.0 — 2026-06-16

Initial bootstrap release — **TH1: Bootstrap Tooling**.

- Global skills install flow (`install.sh` / `bootstrap.sh`) for provisioning the
  `copilotcockpit` skills and agent runbooks.
- End-to-end (e2e) scaffold and supporting `lib/` and `bin/` tooling.
- CI/CD & release groundwork: `VERSION` as the semver source of truth and this
  agent-maintained `CHANGELOG.md`.
