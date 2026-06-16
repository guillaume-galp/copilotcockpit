# copilotcockpit

> *"As an agentic builder, I want to talk to an overseer agent and have it instantly
> hand off requests to specialist workers — all living in a single cockpit where I can
> watch every microservice log stream, drive a real browser, run governed E2E tests, and
> fix bugs — without ever leaving my terminal."*

**`copilotcockpit` makes that real in under 10 minutes.**

It is a **bootstrap toolkit**: one command wires up the AI skills, the tmux cockpit
layout, and the full E2E test harness for any project — from a cold machine to a
green smoke run, with a squad of agents ready to take orders.

---

## What is the Copilot Cockpit?

Imagine a mission-control room for your codebase — built entirely inside
[tmux](https://github.com/tmux/tmux), powered by
[GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli).

```
┌─────────────────────────────────────────────────────────────────────┐
│  overseer      │  k8s-logs (live pod tails — 6 panes)               │
│  ──────────    │  ────────────────────────────────────────────────── │
│  Your command  │  backend [be] │ frontend [fe] │ worker │ db │ …    │
│  centre.       ├─────────────────────────────────────────────────────│
│  Dispatches    │  chromium / Playwright browser (CDP)                │
│  to workers.   ├───────────────┬─────────────────┬───────────────── │
│                │  worker-test  │  worker-dev     │  worker-fix      │
│                │  E2E operator │  Implements     │  Debugs &        │
│                │  Runs & reads │  features &     │  root-causes     │
│                │  audit trail  │  new specs      │  failures        │
└─────────────────────────────────────────────────────────────────────┘
```

Every pane is a **GitHub Copilot CLI** session pre-loaded with its role's **skill** —
a `SKILL.md` playbook that tells the agent exactly who it is, what tools to use, and
what protocol to follow. The overseer dispatches; workers execute; results flow back.
No copy-paste. No context-switching. No tribal knowledge.

tmux is the glue: it keeps every pane alive, lets you attach/detach freely, and
enables the `cockpit-wake` scheduler to fire messages into any pane — even while you
sleep.

---

## The squad

| Role | Skill | Does |
|------|-------|------|
| **Overseer** | `e2e-cockpit` | Orchestrates the workers. Reads results. Never gets buried in code. |
| **worker-test** | `e2e-operator` | Runs governed E2E suites via `run-audit.sh`. Reads the audit trail. Triages failures. |
| **worker-dev** | `worker-dev` | Implements features, fixes, and new Playwright specs. |
| **worker-fix** | `worker-fix` | Deep-dives bugs. Traces API calls. Root-causes flakiness. |
| **Setup agents** | `setup-e2e-cockpit` · `setup-e2e-runbook` | One-shot AI agents that discover your app's topology and generate the cockpit scripts + Gherkin test-book. Run once per project. |

Every skill ships as a plain Markdown `SKILL.md`. The global playbook lives in
`~/.copilot/skills/<role>/`. Each project adds a thin overlay in
`.github/skills/<role>/` with its own ports, paths, and start commands.

---

## The E2E harness

Each project's `e2e/` directory (its own git repo) gives you:

| File / dir | Purpose |
|------------|---------|
| `run-audit.sh` | **The only way to run tests.** Wraps Playwright, captures git SHAs, writes a 3-tier audit trail. |
| `run-playwright.sh` | Docker wrapper — works on any Linux without browser headaches. |
| `tmux-cockpit.sh` / `tmux-cockpit-local.sh` | One command to launch the full cockpit against k8s or local dev. |
| `test-book/CH*.md` | Gherkin test-book: one chapter per feature domain, TC-IDs cross-linked to spec files. |
| `tests/*.spec.ts` | Playwright specs, every test tagged `@TC-XXX-NNN` for audit mapping. |
| `governance/GOVERNANCE.md` | Run cadences, gate definitions, failure triage protocol. |
| `runs/INDEX.md` | Permanent, append-only audit index. One row per run, forever. |

---

## Installation

> *"As an agentic builder, I want to set up the cockpit and the skills for my project
> in under 10 minutes, so that I can open a terminal, say 'run the smoke suite', and
> have a worker do it while I watch the logs."*

```
Given  I have a terminal with bash, git, node, docker, and tmux
When   I run the one-liner below
Then   my global skills are installed in ~/.copilot/skills/
And    my project has a wired e2e/ harness
And    ./e2e/run-audit.sh --scope "@smoke" passes green
```

### Step 1 — Install global skills (once per machine)

No clone needed. The checksum-verified one-liner pulls the latest release:

```bash
bash <(curl -fsSL https://github.com/guillaume-galp/copilotcockpit/releases/latest/download/install.sh)
```

This installs into `~/.copilot/skills/`:
`e2e-cockpit` · `e2e-operator` · `setup-e2e-cockpit` · `setup-e2e-runbook` ·
`worker-dev` · `worker-fix` · `worker-test` · `copilotcockpit-dev`

…and `cockpit-wake` into `~/.local/bin/`.

Or from a clone (no network call):

```bash
git clone https://github.com/guillaume-galp/copilotcockpit.git
cd copilotcockpit && ./bootstrap.sh global
```

### Step 2 — Scaffold your project's `e2e/` (once per project)

```bash
./bootstrap.sh e2e ~/git/my-project
```

This creates `my-project/e2e/` as its own git repo, pre-populated with the full
harness skeleton: governed runner, test-book stub, Playwright config, cockpit scripts
with `# ── CONFIGURE ──` blocks, and all `.github/skills/` overlay stubs.

### Step 3 — Let AI complete the topology (2 Copilot prompts)

```
/setup-e2e-cockpit   → discovers ports, start commands, k8s context
                       → fills tmux-cockpit*.sh + writes local skill overlays

/setup-e2e-runbook   → discovers feature domains from routes/nav/API
                       → writes CH*.md chapters + Playwright spec stubs
```

### Step 4 — Launch and verify

```bash
./e2e/tmux-cockpit-local.sh          # spins up the full cockpit in tmux
./e2e/run-audit.sh --scope "@smoke"  # green smoke = you're live ✓
```

---

## Staying up to date

Re-run `bootstrap.sh global` at any time — it is fully idempotent. Already-current
skills are skipped; changed files are backed up before overwrite.

```bash
# Update skills + cockpit-wake from latest release
./bootstrap.sh global --from-release latest

# Refresh framework files in an existing e2e/ (project content is never touched)
./bootstrap.sh e2e ~/git/my-project --update

# Check what's installed vs what the repo ships
./bootstrap.sh doctor
```

---

## Works great with `copilotautopilot`

`copilotcockpit` pairs naturally with its sibling toolkit
**[copilotautopilot](https://github.com/guillaume-galp/copilotautopilot)** — which
installs and bootstraps the **`the-copilot-build-method`** skill: a 4-phase
autonomous product development lifecycle (Vision → Architecture → Planning →
Autopilot) that drives a squad of agents from idea to merged code.

```
copilotautopilot                         copilotcockpit
── the-copilot-build-method skill ──     ── e2e harness skills ──────
/kickstart-vision  →  product brief      bootstrap.sh global
/plan-product      →  epics + stories    bootstrap.sh e2e <dir>
/run-autopilot     →  developer builds   /setup-e2e-cockpit
                      the feature        /setup-e2e-runbook
                          │                       │
                          └──── PR merged ──────► worker-test runs
                                                   audit trail
                                                   worker-fix triages
```

The `the-copilot-build-method` squad **writes the code**; the cockpit squad
**verifies it lives**. Use them together: let `copilotautopilot` drive your sprints
and `copilotcockpit` provide the continuous test signal that keeps every story honest.

---

## Documentation

Design rationale and all architectural decisions live under [`docs/`](./docs/):

| Doc | What it covers |
|-----|---------------|
| [`docs/architecture/overview.md`](./docs/architecture/overview.md) | Repo layout, bootstrap phases, CI/CD & release flow |
| [`docs/ADRs/`](./docs/ADRs/) | 8 Architecture Decision Records (skills strategy, sub-repo model, release distribution, GitOps runbook, …) |
| [`docs/vision_of_product/VP1-e2e-bootstrap/VP1.md`](./docs/vision_of_product/VP1-e2e-bootstrap/VP1.md) | Product vision and the problem this solves |

---

## License

See the repository for license details.
