# Copilot Instructions for `copilotcockpit`

## Build, test, and lint commands

This repository is a Bash/bootstrap toolkit (no compile/build step). Use these checks:

```bash
# Run all PR-gated checks (categories 1-4)
./run-tests.sh all

# Run one category
./run-tests.sh unit
./run-tests.sh template
./run-tests.sh skills
./run-tests.sh integration

# Run a single test suite file
bats tests/unit/cmd-e2e.bats
bats tests/unit/cmd-overseer.bats

# Run one test by name
bats tests/unit/cmd-e2e.bats -f "<test name>"

# Run lint-like checks directly
tests/template/check-template.sh
tests/skills/lint-skills.sh
```

Useful command-level smoke checks while editing scripts:

```bash
./bootstrap.sh global --dry-run
./bootstrap.sh e2e /tmp/some-project --dry-run --yes
./bootstrap.sh doctor
```

## High-level architecture

`bootstrap.sh` is the single entry point and dispatches to `lib/cmd-global.sh`, `lib/cmd-e2e.sh`, and `lib/cmd-doctor.sh`. Shared behavior (logging, portability helpers, idempotent install helpers, sha256 verification, dry-run wiring) lives in `lib/common.sh`.

The product has two bootstrap phases:

1. `bootstrap.sh global` syncs the canonical global skills from `skills/*/SKILL.md` into `~/.copilot/skills/*` and installs `bin/cockpit-wake` plus `bin/cockpit-overseer` to `~/.local/bin`.
2. `bootstrap.sh e2e <dir>` scaffolds `templates/e2e/` into `<dir>/e2e`, resolves Tier-1 tokens, initializes `e2e/` as its own git repo, and prints AI handoff (`/setup-e2e-cockpit`, `/setup-e2e-runbook`).

`--update` behavior for existing `e2e/` is driven by `templates/e2e/MANIFEST.toml` ownership classes (`framework`, `seed`, `project`) to preserve project-owned content while refreshing framework-owned files.

CI/release split:

- `.github/workflows/ci.yml` runs categories 1-4 via `./run-tests.sh all`.
- `.github/workflows/release.yml` is tag-driven (`vX.Y.Z`), builds the install-surface tarball, publishes SHA256 assets and `install.sh`, then validates published assets.

## Key conventions specific to this codebase

- **Portability baseline is Bash 3.2/macOS-safe**: avoid GNU-only patterns (`readlink -f`, `sed -i`, `date -d`, associative arrays, `mapfile`); follow patterns already used in `lib/common.sh`.
- **Idempotent + non-destructive by default**: re-runs should converge; when overwriting managed files, create timestamped backups (`.bak-<ts>`).
- **Dry-run is a first-class contract**: `--dry-run` must describe actions and produce no side effects.
- **Use `lib/common.sh` helpers instead of duplicating behavior** (`cc_install_file`, `cc_run`, `cc_realpath`, checksum helpers, log helpers).
- **Logging contract**: operational logs go to stderr with `cc:` prefix; keep stdout for command payload/handoff text.
- **Skill set is explicit and managed**: global install manages exactly the enumerated roles in `cmd-global.sh` plus `cockpit-wake`.
- **Token model is fixed and ordered**: `@@APP_NAME@@`, `@@BACKEND_PORT@@`, `@@FRONTEND_PORT@@`, `@@HEALTH_PATH@@`, resolved via `.e2e-config.yaml -> prompt -> default`.
- **Safe update default**: unclassified files are treated as `project` (preserve, do not overwrite).
