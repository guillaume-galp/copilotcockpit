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

## v0.1.0 — 2026-06-16

Initial bootstrap release — **TH1: Bootstrap Tooling**.

- Global skills install flow (`install.sh` / `bootstrap.sh`) for provisioning the
  `copilotcockpit` skills and agent runbooks.
- End-to-end (e2e) scaffold and supporting `lib/` and `bin/` tooling.
- CI/CD & release groundwork: `VERSION` as the semver source of truth and this
  agent-maintained `CHANGELOG.md`.
