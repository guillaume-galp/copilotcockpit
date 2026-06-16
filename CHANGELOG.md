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
