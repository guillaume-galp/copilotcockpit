# copilotcockpit

**One-command bootstrap for the Copilot end-to-end (E2E) testing harness.**

`copilotcockpit` provisions everything a machine needs to operate a *cockpit*: the
managed Copilot **skills**, the `cockpit-wake` launcher, and a ready-to-use `e2e/`
scaffold for any project. It is intentionally dependency-light — a fresh machine
needs only `bash`, `curl`, and `tar` to cold-install.

## Cold install (no clone required)

Run the minimal, checksum-verified wrapper straight from the latest GitHub Release:

```bash
bash <(curl -fsSL https://github.com/copilotcockpit/copilotcockpit/releases/latest/download/install.sh)
```

The wrapper downloads `copilotcockpit.tar.gz` plus its `.sha256`, **verifies the
checksum**, extracts, and runs `bootstrap.sh global`. The explicit form it performs
under the hood:

```bash
curl -fsSL https://github.com/copilotcockpit/copilotcockpit/releases/latest/download/copilotcockpit.tar.gz | tar -xz
./copilotcockpit/bootstrap.sh global
```

## Usage

`bootstrap.sh` is the single self-documenting entry point:

```bash
# Install/update the managed skills + cockpit-wake into your home
bootstrap.sh global [--link] [--dry-run] [--from-release <ref>]

# Scaffold or refresh an e2e/ sub-repo in <dir>
bootstrap.sh e2e <dir> [--update] [--no-git] [--yes] [--dry-run]

# Verify prerequisites and report install/drift state
bootstrap.sh doctor
```

- **`global`** installs (and idempotently updates) the managed `SKILL.md` files into
  `~/.copilot/skills/` and `cockpit-wake` into `~/.local/bin/`. Use `--dry-run` to
  preview every action without changing anything, `--link` to symlink back to a local
  clone, or `--from-release <latest|vX.Y.Z>` to cold-install from a GitHub Release.
- **`e2e <dir>`** scaffolds a project's `e2e/` harness from the bundled templates.
- **`doctor`** checks prerequisites and reports the current install / drift state.

## From a local clone

```bash
git clone https://github.com/copilotcockpit/copilotcockpit.git
cd copilotcockpit
./bootstrap.sh global
```

When run inside a clone, `bootstrap.sh global` uses the local files and makes no
network call — cold install is the additive fallback, not the default.

## Documentation

Design rationale, architecture, and ADRs live under [`docs/`](./docs/):

- `docs/architecture/overview.md` — system architecture and the CI/CD & release flow.
- `docs/ADRs/` — Architecture Decision Records (e.g. ADR-007 release distribution).

## License

See the repository for license details.
