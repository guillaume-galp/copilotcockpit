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
