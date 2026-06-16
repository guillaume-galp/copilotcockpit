---
name: setup-e2e-cockpit
description: "Guide the user to explore a target app and generate tmux-cockpit.sh and tmux-cockpit-local.sh from the e2e-template. USE FOR: setting up the E2E cockpit for a new app, configuring cockpit scripts, discovering app topology (ports, health endpoint, k8s namespace, backend start commands), installing cockpit-wake."
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
| `.github/skills/e2e-cockpit/SKILL.md` | repo topology overlay (thin) |
| `.github/skills/e2e-operator/SKILL.md` | operator project overlay (thin) |
| `.github/skills/worker-dev/SKILL.md` | developer role overlay (thin) |
| `.github/skills/worker-fix/SKILL.md` | troubleshooter role overlay (thin) |
| `.github/skills/worker-test/SKILL.md` | test operator role overlay (thin) |

The `tmux-cockpit.sh` / `tmux-cockpit-local.sh` scripts must prime each worker
pane with both the global skill (from `~/.copilot/skills/<role>/`) and the
repo overlay (from `.github/skills/<role>/`).

---

## Phase 1 — Explore the target app

Discover the following by reading files, checking running processes, and asking
the user for anything you cannot determine automatically:

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

If a reference implementation exists (e.g. another project's `tmux-cockpit.sh`),
copy it and adapt the `# ── CONFIGURE ──` block. Otherwise generate from scratch.

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

### `prime_worker()` function (required in both scripts)

```bash
prime_worker() {
  local pane="$1"   # e.g. "1:worker-dev"
  local role="$2"   # e.g. "worker-dev"
  local global_skill="$HOME/.copilot/skills/${role}/SKILL.md"
  local repo_skill=".github/skills/${role}/SKILL.md"

  # Wait for Copilot CLI to be ready
  for i in $(seq 1 10); do
    local output
    output=$(tmux capture-pane -t "$pane" -p 2>/dev/null)
    if echo "$output" | grep -qE "Claude|commands|help"; then break; fi
    sleep 2
  done

  # Build priming message
  cat > /tmp/prime_${role}.txt << PRIME
You are the ${role} in the $(basename "$(pwd)") E2E cockpit.

Load your skills in order:
1. Global role: ${global_skill}
2. Project extension: ${repo_skill}

After reading both skills, confirm: "I am ${role} for $(basename "$(pwd)"), ready."
PRIME

  tmux load-buffer /tmp/prime_${role}.txt
  tmux paste-buffer -t "$pane"
  sleep 1
  tmux send-keys -t "$pane" "" Enter
  rm /tmp/prime_${role}.txt
}
```

---

## Phase 3 — Write worker skill overlays

Create thin project overlays in `.github/skills/<role>/SKILL.md` for each worker.
Each overlay should contain only what is project-specific:

```markdown
---
name: <role>
description: "Project extension for <role> in <AppName>. Load after global ~./copilot/skills/<role>/SKILL.md."
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
- Worker question inbox: /tmp/worker-<role>-question.txt
```

---

## Phase 4 — Update `.env.example`

```dotenv
FRONTEND_URL=http://localhost:{FRONTEND_PORT}
BACKEND_URL=http://localhost:{BACKEND_PORT}
SKIP_DB_RESET=true
# Add app-specific tokens/flags here
```

---

## Phase 5 — Verify + Install cockpit-wake

```bash
chmod +x e2e/tmux-cockpit.sh e2e/tmux-cockpit-local.sh
bash -n e2e/tmux-cockpit.sh && echo "k8s syntax OK"
bash -n e2e/tmux-cockpit-local.sh && echo "local syntax OK"
```

Install `cockpit-wake` from the bundled tool if not already on PATH:
```bash
if ! command -v cockpit-wake &>/dev/null; then
  cp e2e/tools/cockpit-wake ~/.local/bin/cockpit-wake
  chmod +x ~/.local/bin/cockpit-wake
  echo "cockpit-wake installed → ~/.local/bin/cockpit-wake"
else
  echo "cockpit-wake already installed ($(which cockpit-wake))"
fi
```

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

Worker skills:   .github/skills/{worker-dev,worker-fix,worker-test}/SKILL.md ✓
Cockpit overlay: .github/skills/e2e-cockpit/SKILL.md ✓
Operator overlay: .github/skills/e2e-operator/SKILL.md ✓

Next step: run /setup-e2e-runbook to generate the Gherkin test-book and spec stubs.
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
