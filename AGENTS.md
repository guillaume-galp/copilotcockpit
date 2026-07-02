# AGENTS.md (copilotcockpit)

This repository is a bootstrap toolkit for AI-assisted E2E workflow automation:

- Bash scripts in the repo root and `lib/`.
- A vendored Python CLI under `bin/cockpit-wake`.
- Canonical skill sources under `skills/`.
- Codex repo exposure under `.agents/skills/`.
- Codex user/global install target: `$HOME/.agents/skills/`.
- Legacy Copilot install target remains: `$HOME/.copilot/skills/`.

`skills/` is the canonical source of truth for the 8 managed skills:

- `e2e-cockpit`
- `e2e-operator`
- `setup-e2e-cockpit`
- `setup-e2e-runbook`
- `worker-dev`
- `worker-fix`
- `worker-test`
- `copilotcockpit-dev`

## Important commands

- `./run-tests.sh all`
- `./run-tests.sh codex`
- `./bootstrap.sh doctor`
- `./bootstrap.sh global --dry-run`
- `./bootstrap.sh codex-global --dry-run`
- `./bootstrap.sh codex-repo --dry-run`
- `./bootstrap.sh e2e <tmpdir> --yes --dry-run`

Before opening a PR, run the most relevant checks:
- `./run-tests.sh all` (or at minimum `./run-tests.sh codex` and `./run-tests.sh unit` on local changes).
- `./bootstrap.sh doctor`.
- `./bootstrap.sh codex-repo --dry-run` and `./bootstrap.sh codex-global --dry-run`.

For repository-generated e2e project files, treat `templates/e2e/MANIFEST.toml` as
ownership control. Do not edit project-owned files during `bootstrap.sh e2e <dir> --update`
unless the task explicitly asks for it.

Conventional commits are required for PRs.
