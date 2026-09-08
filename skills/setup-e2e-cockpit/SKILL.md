---
name: setup-e2e-cockpit
description: "Guide the user to explore a target app and generate tmux-cockpit.sh and tmux-cockpit-local.sh from the e2e-template. USE FOR: setting up the E2E cockpit for a new app, configuring cockpit scripts, discovering app topology (ports, health endpoint, k8s namespace), and configuring worker overlays."
---

# Setup E2E Cockpit

You are the **E2E Cockpit Setup Agent**. Your job is to explore the target
application and generate correctly configured `tmux-cockpit.sh` and
`tmux-cockpit-local.sh` scripts — and to create the worker skill overlays
so workers know their role in this project.

---

## What You Produce

| File | Purpose |
|------|---------|
| `e2e/tmux-cockpit.sh` | k8s / remote cockpit launcher |
| `e2e/tmux-cockpit-local.sh` | local dev cockpit launcher |
| `e2e/.env.example` | updated with correct URL defaults |
| `.github/skills/e2e-cockpit/SKILL.md` | Copilot repo topology overlay (thin) |
| `.github/skills/e2e-operator/SKILL.md` | Copilot operator project overlay (thin) |
| `.github/skills/worker-dev/SKILL.md` | Copilot developer overlay (thin) |
| `.github/skills/worker-fix/SKILL.md` | Copilot troubleshooter overlay (thin) |
| `.github/skills/worker-test/SKILL.md` | Copilot test operator overlay (thin) |
| `.codex/config.toml` | Codex project configuration |
| `.agents/skills/e2e-cockpit/SKILL.md` | Codex Cockpit project overlay (thin) |
| `.agents/skills/e2e-operator/SKILL.md` | Codex operator project overlay (thin) |
| `.agents/skills/worker-dev/SKILL.md` | Codex developer overlay (thin) |
| `.agents/skills/worker-fix/SKILL.md` | Codex troubleshooter overlay (thin) |
| `.agents/skills/worker-test/SKILL.md` | Codex test operator overlay (thin) |

The scripts must prime each worker pane for the selected runtime:

- Copilot: global from `$HOME/.copilot/skills/<role>/SKILL.md`, project extension from `.github/skills/<role>/SKILL.md`
- Codex: global from `$HOME/.agents/skills/<role>/SKILL.md`, project extension from `.agents/skills/<role>/SKILL.md`

---

## Phase 1 — Explore the target app

Discover the following by reading files, checking running processes, and asking
user for anything you cannot determine automatically:

If the target repo has `graphify-out/graph.json` and `graphify` is available,
start topology discovery with focused graph queries before broad text search:

```bash
graphify query "application entrypoints, backend and frontend services, health endpoints, start commands, and deployment topology" --graph "$REPO/graphify-out/graph.json"
```

If a project-specific code intelligence system has higher priority, use that
first; otherwise prefer Graphify over grep-style search. Corroborate graph
answers with source/config files before writing cockpit scripts.

| Property | How to discover |
|----------|----------------|
| App name | Check `package.json`, `pyproject.toml`, `pom.xml`, or ask |
| Backend tech | Look for `fastapi`, `django`, `express`, `rails`, `spring` in deps |
| Backend port | Check `uvicorn`/`gunicorn`/`nodemon` config, `Makefile`, `docker-compose.yml` |
| Backend start command | Check `Makefile`, `package.json scripts`, `README.md` |
| Frontend port | Check `vite.config.ts`, `webpack.config.js`, `next.config.js`, `package.json` |
| Frontend start command | Usually `npm run dev`, but check `package.json scripts` |
| Health endpoint | Look for `/health`, `/healthz`, `/api/health`, `/ping` in route files |
| Deployment topology | Does it run on k8s? docker-compose? bare metal? |
| K8s context | `kubectl config current-context` or `~/.kube/config` |
| K8s namespace | Check Helm values, `k8s/` or `deploy/` dirs, or ask |
| Backend service name | Check `k8s/services/`, Helm templates, or ask |
| Backend pod label | Check `k8s/deployments/`, Helm templates |
| Frontend pod label | Same |
| DB connectivity check | Find DB host/port from `.env.example` or `docker-compose.yml` |

**Ask the user** for any value you cannot determine from files. Be specific.

---

## Phase 2 — Generate the cockpit scripts

For a fresh scaffold, adapt the shipped `# ── CONFIGURE ──` blocks. Existing
installed launchers and overlays are **project-owned** under
`templates/e2e/MANIFEST.toml`: do not copy over them or regenerate them during an
update. Inspect local customizations and present explicit, reviewed changes for
the project owner to apply. Toolkit bootstrap installs tools/skills; it does
not authorize overwriting project files.

### `e2e/tmux-cockpit.sh` — k8s cockpit

Required `# ── CONFIGURE ──` variables:

```bash
APP_NAME="<app-name>"
K8S_CONTEXT="<kubectl-context>"
NAMESPACE="<k8s-namespace>"
BACKEND_SVC="<svc/backend-service>"
BACKEND_PORT="<local-port>"
BACKEND_LABEL="<app=pod-label>"
FRONTEND_LABEL="<app=frontend-pod-label>"
CHROMIUM_PORT="3000"   # or 9002 — check docker-compose
```

The script must:
1. Create the tmux session with windows: `overseer`, `k8s-logs`, `k8s-pf`, `chromium`, `worker-test`, `worker-dev`, `worker-fix`
2. Set up log tail panes in `k8s-logs` (7-pane grid: 3×2 left columns + 1 full-height right)
3. Start `kubectl port-forward` in `k8s-pf`
4. Start chromium/browserless in `chromium`
5. Prime each worker pane with `prime_worker()` (see below)
6. Set session-local FIFO and VP3 control roots:
   `COCKPIT_QUEUE_ROOT="${COCKPIT_QUEUE_ROOT:-$PROJECT_DIR/docs/cockpit-queue}"`,
   `COCKPIT_CONTROL_ROOT="${COCKPIT_CONTROL_ROOT:-$PROJECT_DIR/docs/cockpit-control}"`,
   export both, then set both through `tmux set-environment -t "$SESSION"`.

### `e2e/tmux-cockpit-local.sh` — local dev cockpit

Required `# ── CONFIGURE ──` variables:

```bash
APP_NAME="<app-name>"
BACKEND_PORT="<port>"
FRONTEND_PORT="<port>"
BACKEND_RELATIVE="<path-from-repo-root>"
FRONTEND_RELATIVE="<path-from-repo-root>"
BACKEND_START_CMD="<exact command>"
FRONTEND_START_CMD="npm run dev"
PREFLIGHT_HOST="<db-host-or-empty>"
PREFLIGHT_PORT="<db-port-or-empty>"
```

The script must:
1. Create windows: `overseer`, `backend`, `frontend`, `worker-test`, `worker-dev`, `worker-fix`
2. Start backend and frontend in their respective windows
3. Prime each worker pane with `prime_worker()`
4. Set session-local FIFO and VP3 control roots:
   `COCKPIT_QUEUE_ROOT="${COCKPIT_QUEUE_ROOT:-$PROJECT_DIR/docs/cockpit-queue}"`,
   `COCKPIT_CONTROL_ROOT="${COCKPIT_CONTROL_ROOT:-$PROJECT_DIR/docs/cockpit-control}"`,
   export both, then set both through `tmux set-environment -t "$SESSION"`.

### Bootstrap boundaries before worker priming

Confirm absolute, distinct queue/control paths and the actual planning and
implementation repositories with the user. For a fresh store:

```bash
export COCKPIT_CONTROL_ROOT="/absolute/project/docs/cockpit-control"
export COCKPIT_QUEUE_ROOT="/absolute/project/docs/cockpit-queue"
cockpit-control init --queue-root "$COCKPIT_QUEUE_ROOT" \
  --planning-root "/absolute/project/docs/plan" \
  --implementation-root "/absolute/project"
cockpit-control preflight
```

For an existing unbound store, use `cockpit-control bind-roots` with those same
root arguments; `--implementation-root` is repeatable and `bind-roots --dry-run`
previews the operation. No manual metadata/event editing. Bare `init` /
`cockpit-overseer start` creates structural state only; preflight must report
`operationally-blocked` for absent work boundaries or required capability.
The launchers export roots but do not infer or bind work authority for you.

In each launcher, resolve `PROJECT_DIR` to an absolute path, export both roots
before starting agents, and inject both into the selected tmux session:

```bash
tmux set-environment -t "$SESSION" COCKPIT_CONTROL_ROOT "$COCKPIT_CONTROL_ROOT"
tmux set-environment -t "$SESSION" COCKPIT_QUEUE_ROOT "$COCKPIT_QUEUE_ROOT"
```

This is setup-time environment wiring, not permission for agents to use raw tmux
for normal communication. Retain the shipped `prime_worker()` pattern: select
the intended runtime, load its global role then matching project overlay, and
use `cockpit-protocol dispatch --bootstrap --target ...` for role priming only.
Never prime with product work, raw `send`/`nudge`, or automatic permission approval.

---

## Phase 3 — Write worker skill overlays

Create thin project overlays for each worker role in both Copilot and Codex trees:

- Copilot: `.github/skills/<role>/SKILL.md`
- Codex: `.agents/skills/<role>/SKILL.md`

Each overlay should contain only what is project-specific:

```markdown
---
name: <role>
description: "Project extension for <role> in <AppName>."
---

# <Role> — <AppName> Project Extension

## Repo layout
- Root: /path/to/repo
- E2E tests: e2e/tests/
- Backend: <backend-dir>/
- Frontend: <frontend-dir>/

## Start commands
- Backend: <cmd>
- Frontend: <cmd>

## Test runner
./e2e/run-audit.sh --scope "@smoke" --label "reason"

## Key paths
- Test book: e2e/test-book/
- Audit trail: e2e/runs/

## Durable mission contract
- Follow global e2e-cockpit for receipt, lifecycle, status, questions and ACKs.
- Run the exact accept-dispatch receipt; only accepted + start_work=true starts.
- Dispatch is sequence 0 without heartbeat; accepted is 1; heartbeat running is 2.
- pending / read-question return mission-status JSON, not an answer-file inbox.
- ask / access-prompt use correlated IDs and private typed body references.
- Only explicit human replies; hold --answers never approves a pending prompt.
- Inspect status.pending_commands and command-status; accepted-worker cancel/replace
  requires accepted then applied ACK with the matching digest and typed --result.
- Pane diagnosis is not durable lifecycle; reuse IDs/digests and journal no secrets.
```

For the `e2e-cockpit` overlay specifically, include a visible
`## Cockpit communication protocol` section that makes the managed-cockpit rule
persistent in the generated project:

- Use `cockpit-queue` plus `cockpit-overseer tick` for product missions.
- Use `cockpit-protocol` for receipts, lifecycle, durable dialogs/ACKs, tail, watch,
  `meta cockpit --json`, and `status --workers all --json`.
- `send` / `nudge` are raw-input diagnostic or explicitly human-requested
  exceptions, never normal mission, approval, or cancel operations.
- Use `cockpit-protocol dispatch --bootstrap --target ...` only while priming a
  newly created worker pane; never use it for product work.
- Use `cockpit-overseer status` / `cockpit-overseer loop` for compact status
  checks and short-loop polling.
- Do not use ad-hoc raw `tmux` commands for cockpit status, discovery, pane
  reading, or pane control unless the user explicitly asks for raw tmux
  diagnostics.

Use `$skill-name` to refer to the runtime skill when writing project-specific prompts.

---

## Phase 4 — Update `.env.example`

```dotenv
FRONTEND_URL=http://localhost:{FRONTEND_PORT}
BACKEND_URL=http://localhost:{BACKEND_PORT}
SKIP_DB_RESET=true
# Add app-specific tokens/flags here
```

---

## Phase 5 — Verify + Install the managed runtime set

```bash
chmod +x e2e/tmux-cockpit.sh e2e/tmux-cockpit-local.sh
bash -n e2e/tmux-cockpit.sh && echo "k8s syntax OK"
bash -n e2e/tmux-cockpit-local.sh && echo "local syntax OK"
```

Install/update from the **copilotcockpit toolkit clone**, not the target
project's harness. `cockpit-wake` depends on the installed shared runtime
modules; never copy a standalone executable from a supposed harness tools folder.

```bash
./bootstrap.sh global          # Copilot skills + complete tools/module set
./bootstrap.sh codex-global    # Codex skills + complete tools/module set
./bootstrap.sh doctor
```

Use the [README operator walkthrough](../../README.md#durable-operator-walkthrough)
for the bootstrap → FIFO/tick → receipt → question/hold → worker ACK flow.
Keep all generated `.agents` and `.github` contracts aligned with the global
`e2e-cockpit` guidance, including pending ACK inspection and durable status.

For scheduling, require a ready control root and `--mission`, `--queue-item`,
`--owner`, `--intent`, `--stop-condition`, plus the exact session/window.
Schedules store structured control root/target and invoke controller ticks;
`-m` is an inbox intent note, not pane input. `--dry-run` is read-only.
`stop` aliases `cancel` even for fired recurring jobs; legacy list/cancel/stop
need no root. `migrate` is diagnosis-only, never corrupt-state renaming or
legacy-script rewriting. Explicitly stop old schedules, bootstrap/bind roots,
pass preflight and reschedule with complete metadata.

---

## Output format

```
E2E Cockpit Setup — {APP_NAME}
Status: DONE

Local cockpit:   e2e/tmux-cockpit-local.sh
  backend:   {BACKEND_START_CMD} → :{BACKEND_PORT}
  frontend:  {FRONTEND_START_CMD} → :{FRONTEND_PORT}
  preflight: {PREFLIGHT_HOST}:{PREFLIGHT_PORT} (or "none")

K8s cockpit:     e2e/tmux-cockpit.sh
  context:   {K8S_CONTEXT}
  namespace: {NAMESPACE}
  backend:   {BACKEND_SVC} → localhost:{BACKEND_PORT}

Worker skills (Copilot): .github/skills/{worker-dev,worker-fix,worker-test}/SKILL.md ✓
Worker skills (Codex):   .agents/skills/{worker-dev,worker-fix,worker-test}/SKILL.md ✓
Cockpit overlay: .github/skills/e2e-cockpit/SKILL.md ✓
Cockpit overlay: .agents/skills/e2e-cockpit/SKILL.md ✓
Operator overlay: .github/skills/e2e-operator/SKILL.md ✓
Operator overlay: .agents/skills/e2e-operator/SKILL.md ✓
Codex config: .codex/config.toml ✓

Next step: invoke the setup for runbook generation to create the Gherkin test-book and spec stubs.
```

---

## Reference files to read when exploring

- `README.md` — start instructions, feature list
- `Makefile` — `make dev`, `make run` targets
- `docker-compose.yml` — services, ports, env vars
- `package.json` → `scripts.dev`, `scripts.start`
- `pyproject.toml` or `setup.py` — Python entry points
- `k8s/` or `helm/` or `deploy/` — k8s resources
- `e2e/.env.example` — often has correct URLs already
