# Release: TH1 — Bootstrap Tooling

**Version:** 0.1.0
**Theme:** TH1 — Bootstrap Tooling
**Vision:** VP1 — One-command bootstrap for the Copilot E2E testing harness
**Status:** Complete — 21/21 stories, 6/6 epics `done`, product-owner verdict **GO**
**Date:** 2026-06-16

## Summary

TH1 delivers `copilotcockpit`: a one-command bootstrap toolkit that installs the
Copilot CLI E2E testing harness (8 managed skills + `cockpit-wake`) globally and
scaffolds a structurally-complete, governed `e2e/` sub-repo into any project from
versioned templates — with idempotent updates, portable (macOS/BSD/bash-3.2-safe)
shell, checksum-verified GitHub-release distribution, a 5-category test suite, CI/PR
quality gates, and an in-repo GitOps delivery runbook. All choices are backed by
ADR-001…ADR-008. Every VP1 success criterion (SC-1…SC-6) and all eight NFRs are MET.

## Epics Delivered

| Epic | Title | Stories | Highlights |
|------|-------|---------|------------|
| E6 | Spikes | 2 | macOS/BSD portability cheat-sheet; MANIFEST.toml glob-classification design |
| E1 | Core bootstrap shell | 3 | `lib/common.sh` (portable shared lib), `bootstrap.sh` dispatcher, `lib/cmd-doctor.sh` diagnostics |
| E2 | Global skills install | 4 | Vendored 7 harness skills + `cockpit-wake`; `lib/cmd-global.sh` (install/`--link`/`--from-release`); cold `install.sh` with sha256 verify |
| E3 | E2E scaffold | 6 | `templates/e2e/**` (governed runners, Playwright infra, governance, test-book, overlays, tmux stubs); `MANIFEST.toml`; `lib/cmd-e2e.sh` scaffold + `--update` |
| E4 | CI/CD & releases | 3 | `VERSION` + `CHANGELOG.md`; tag-driven `release.yml` (tarball + dual sha256 + `gh release` + Cat-5 validate); PR-gate `ci.yml` |
| E5 | Dev skill & test infra | 3 | `run-tests.sh` dispatcher + 5 test categories (`tests/**`); `skills/copilotcockpit-dev/SKILL.md` GitOps runbook (8th skill) |

## Capabilities

- **One-command bootstrap** — `bootstrap.sh {global|e2e <dir>|doctor}` (ADR-003).
- **Cold install** — `install.sh` fetches the release tarball, verifies its sha256, extracts, and runs `global` (no `git clone` needed).
- **Doctor** — prerequisite probing (bash/git/node/npm/python3/docker/tmux/bats) + 4-state skills/cockpit-wake drift detection.
- **Global skills install** — 8 managed skills + `cockpit-wake`; idempotent re-run ("already current"); `--link` dev mode; `--from-release vX.Y.Z`.
- **E2E scaffold** — copies `templates/e2e/` with 4-token substitution (ADR-004), `git init`, `npm install`; `--update` refreshes only `framework` files (MANIFEST-driven), preserving `seed`/`project` content (ADR-006).
- **GitHub-release distribution** (ADR-007) — deterministic tarball, versioned + unversioned alias, sha256, `gh release create --latest`, Category-5 post-publish self-validation.
- **Quality gates** — `ci.yml` runs Categories 1–4 on every PR to `main`; `[skip ci]` honoured.
- **Test suite** — `run-tests.sh unit|template|skills|integration|all`: 14 bats unit tests, template integrity, skills lint, integration smoke (Category 5 lives in `release.yml`).
- **Portability** — bash 3.2-safe; no `sed -i`/`readlink -f`/`date -d`/`grep -P`; portable `cc_realpath`/`cc_timestamp`/`cc_sha256_file`.

## Verification at Release

- `./run-tests.sh all` → **EXIT 0** (14 unit + template integrity + 8-skill lint + 3 integration).
- `bash -n` clean across all shell sources (bootstrap/install/run-tests/lib/templates/tests).
- `release.yml` + `ci.yml` parse as valid YAML; packaging + Category-5 logic + the CI job-body validated locally.
- Install surface present: `bootstrap.sh README.md VERSION CHANGELOG.md install.sh lib/ skills/(8) bin/ templates/`.
- No `failed`/`pending`/`in_progress` stories.

## Breaking Changes

None — this is the initial `0.1.0` foundation release.

## Migration Notes

None — first release. Cold install (once a `v0.1.0` release is published):

```sh
curl -fsSL https://github.com/copilotcockpit/copilotcockpit/releases/latest/download/install.sh | bash
```

Or from a clone: `./bootstrap.sh global`.

## Deferred (non-blocking tech-debt)

- Live GitHub Actions execution of `ci.yml`/`release.yml` deferred until a real tag push / PR (locally validated; structurally mitigated by the in-workflow Category-5 validate job).
- Reject `--link` + `--from-release` combo; hoist canonical 8-role list into `common.sh`; sync architecture §7 embedded MANIFEST block; PyYAML install hardening for PEP-668; generic-ify the dead pending-skip message in `cmd-global.sh`.

## Recommendations for VP2 (from PO revalidation)

Live release validation (push `v0.1.0`, open a PR); multi-project harness inventory for fleet `--update`; scaffold customisation profiles; resolve the minor tech-debt above; optional published-package distribution (Homebrew/npm).
