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

## FIFO request queue

Product ideas arrive in bursts, but cockpit workers must stay focused on one
delivery at a time. Without a queue, the human operator either interrupts the
current mission or leaves ideas buried in chat history.

`copilotcockpit` solves that with a FIFO overseer request queue. The overseer can
accept build-method ideas, reject non-build chatter, inspect queue depth, start
the next item only when workers are ready, and clear an item only after the
cockpit has local delivery evidence plus the queue-scoped E2E operator runbook
result. Set `COCKPIT_QUEUE_ROOT` to the intended `docs/cockpit-queue` directory before
running queue commands so independent cockpits never share or corrupt each
other's FIFO state.

Benefits:

- **No orphan ideas**: accepted build requests persist until cleared or rejected.
- **Lower overseer overhead**: queue commands replace repeated prompt boilerplate.
- **Worker focus**: one active queue item prevents mission bleed across workers.
- **Auditable delivery**: queue events link the idea, worker trace, and E2E run
  evidence.
- **Operator calm**: product owners can submit ideas at thought speed while the
  cockpit delivers FIFO.

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
hosts the workers while `cockpit-wake` schedules bounded controller ticks — even
while detached. A wake reconciles durable mission evidence; it does not paste
its message into a pane or approve a worker's interactive prompt.

`cockpit-overseer` also writes an append-only communication archive under
`~/.config/cockpit-overseer/archive/` so you can replay a mission's prompts,
worker snapshots, and AIC signals after the fact.

Use `cockpit-trace show <trace-id>` or `cockpit-trace tree <trace-id>` to stitch
the dialog back together.

---

## The squad

| Role | Skill | Does |
|------|-------|------|
| **Overseer** | `e2e-cockpit` | Orchestrates the workers. Reads results. Uses `cockpit-overseer` for compact loop/status checks and append-only trace archives. |
| **worker-test** | `e2e-operator` | Runs governed E2E suites via `run-audit.sh`. Reads the audit trail. Triages failures. |
| **worker-dev** | `worker-dev` | Implements features, fixes, and new Playwright specs. |
| **worker-fix** | `worker-fix` | Deep-dives bugs. Traces API calls. Root-causes flakiness. |
| **Setup agents** | `setup-e2e-cockpit` · `setup-e2e-runbook` | One-shot AI agents that discover your app's topology and generate the cockpit scripts + Gherkin test-book. Run once per project. |
| **cockpit-wake** | CLI | Schedules one-off or recurring controller ticks (`at` / `cron`) with stored mission, queue, owner, intent, stop condition, control root, and target identity. |
| **cockpit-protocol** | CLI | Durable receipts, lifecycle, correlated questions/replies, hold, cooperative cancel/replace, acknowledgements, safe reservation recovery, and separate pane diagnostics. |

Every skill ships as a plain Markdown `SKILL.md`. The global playbook lives in
`~/.copilot/skills/<role>/`. Each project adds a thin overlay in
`.github/skills/<role>/` with its own ports, paths, and start commands.

## The tools

| Tool | Purpose | Managed here? |
|------|---------|---------------|
| `cockpit-protocol` | Durable mission protocol plus discovery, status, tail/watch and report extraction. `dispatch --bootstrap` is setup-only; raw `send`/`nudge` are diagnostic or explicitly human-requested exceptions. | yes |
| `cockpit-overseer` | Overseer controller: control-store initialization, the deterministic one-action `tick` reconciler, plus root-validated delta polling, dispatch, reset, and append-only trace archive. | yes |
| `cockpit-control` | Own the explicit versioned control store: atomic `init` / `bind-roots`, read-only `preflight`, durable command/lifecycle/dialog APIs, `accept-dispatch`, `recover-dispatch`, and derived `replay-ledger`. | yes |
| `cockpit-trace` | Replay and stitch archived comms by UUID trace / trace family. | yes |
| `cockpit-queue` | FIFO request queue operator for intake, list, inspect, pause/resume, reject, start-next, and clear-current. | yes |
| `cockpit-wake` | Schedule controller ticks; inspect with `list`, stop with `stop` / `cancel`, diagnose legacy state with `migrate`. | yes |
| `aic-tracker` | Measure token/AIC spend and compare comms efficiency across sessions. | no (companion tool) |
| `graphify` | Optional local code graph used by skills/workers before broad text search when `graphify-out/graph.json` exists. | no (companion tool) |
| `run-tests.sh` | Repo test dispatcher (unit/template/skills/integration/all). | yes |
| `bootstrap.sh` | Install/scaffold entry point. | yes |

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

```
Given  I have a terminal with bash, git, node, go, docker, and tmux
When   I run the install command below
Then   Copilot skills are installed in ~/.copilot/skills/
And    Codex skills are installed in ~/.agents/skills/
And    cockpit-wake + cockpit-protocol are installed in ~/.local/bin/
When   I run the scaffold command below for my project
And    my project has a wired e2e/ harness
And    ./e2e/run-audit.sh --scope "@smoke" passes green
```

### Step 1 — Install global skills (once per machine)

Install from the latest release:

```bash
bash <(curl -fsSL https://github.com/guillaume-galp/copilotcockpit/releases/latest/download/install.sh)
```

Installed user-scoped files:

- `~/.copilot/skills/<role>/SKILL.md` for GitHub Copilot
- `~/.agents/skills/<role>/SKILL.md` for Codex
- `~/.local/bin/cockpit-wake`
- `~/.local/bin/cockpit-protocol` + `~/.local/bin/cockpit-protocol.go`
- `~/.local/bin/cockpit-overseer`
- `~/.local/bin/cockpit-trace`
- `~/.local/bin/cockpit-control` (+ all modules listed in `MANAGED_RUNTIME_MODULES`)

Managed roles:
`e2e-cockpit` · `e2e-operator` · `setup-e2e-cockpit` · `setup-e2e-runbook` ·
`worker-dev` · `worker-fix` · `worker-test` · `copilotcockpit-dev`

…and `cockpit-protocol`, `cockpit-wake`, `cockpit-overseer`, `cockpit-trace`,
`cockpit-queue`, and `cockpit-control` into `~/.local/bin/`.

Or from a clone (no network call):

```bash
git clone https://github.com/guillaume-galp/copilotcockpit.git
cd copilotcockpit
./bootstrap.sh global
./bootstrap.sh codex-global
```

Remove managed user-scoped files:

```bash
./uninstall.sh
```

### Step 2 — Scaffold your project's `e2e/` (once per project)

```bash
./bootstrap.sh e2e ~/git/my-project
```

This creates `my-project/e2e/` as its own git repo, pre-populated with the full
harness skeleton: governed runner, test-book stub, Playwright config, cockpit scripts
with `# ── CONFIGURE ──` blocks, and `.github/skills/` / `.agents/skills/` overlays.
Fresh bootstrap also initializes `docs/cockpit-control` and
`docs/cockpit-queue`, declaring `docs/plan` as the planning root and the explicit
project target as the implementation root. It does not inherit another cockpit's
ambient roots.

### Step 3 — Let AI complete the topology (2 Copilot prompts)

```
/setup-e2e-cockpit   → discovers ports, start commands, k8s context
                       → fills tmux-cockpit*.sh + writes local skill overlays

/setup-e2e-runbook   → discovers feature domains from routes/nav/API
                       → writes CH*.md chapters + Playwright spec stubs
```

### Step 4 — Launch and verify

Confirm the roots created by fresh bootstrap with `cockpit-control preflight`,
using the exports in the [operator walkthrough](#durable-operator-walkthrough).
For legacy or separately configured cockpits, declare boundaries explicitly
first. Launchers export distinct queue/control roots into both shell and tmux;
they do not discover or authorize additional planning/implementation boundaries.

```bash
./e2e/tmux-cockpit-local.sh          # spins up the full cockpit in tmux
./e2e/run-audit.sh --scope "@smoke"  # green smoke = you're live ✓
```

---

## Durable operator walkthrough

### Bootstrap explicit work boundaries

Use reviewed **absolute** paths, not current-directory inference. The queue and
control directories must be distinct. Declare the actual planning directory and
each allowed implementation repository (`--implementation-root` is repeatable).

```bash
export COCKPIT_CONTROL_ROOT="/absolute/project/docs/cockpit-control"
export COCKPIT_QUEUE_ROOT="/absolute/project/docs/cockpit-queue"
cockpit-control init \
  --queue-root "$COCKPIT_QUEUE_ROOT" \
  --planning-root "/absolute/project/docs/plan" \
  --implementation-root "/absolute/project"
cockpit-control preflight
```

For an already initialized, unbound store, use `cockpit-control bind-roots` with
the same three root arguments (`--dry-run` previews the binding). This updates
metadata and its derived ledger atomically; never hand-edit `control.json`,
`ledger.json`, or committed events to declare roots. `init` without root
arguments (also `cockpit-overseer start`) creates only structural state, not
product-work authority. Missing boundaries/capability make read-only `preflight`
report `operationally-blocked` and exit nonzero.

Configure the launcher to export these same roots and inject both with
`tmux set-environment -t "$SESSION"` before worker priming. This is launcher
setup, not a normal agent communication path. Existing installed launchers and
overlays are **project-owned**: `bootstrap.sh e2e ... --update` does not overwrite
them. Ask the project owner to review and apply these root/bootstrap changes
explicitly; never silently replace local files. Install the complete tool/module
set with `bootstrap.sh global` and/or `bootstrap.sh codex-global` from the toolkit
clone, not by copying a standalone executable out of a project's harness.

### FIFO dispatch and worker receipt

With preflight ready and the configured cockpit running:

```bash
QI_ID="$(cockpit-queue enqueue --approved --title "One reviewed change" \
  --text "Implement the agreed single change in the declared repository; verify and report.")"
cockpit-queue list
# Only when no other item is active:
cockpit-queue start-next
cockpit-queue transition "$QI_ID" implementing --reason "approved brief ready"
cockpit-overseer tick --session "<session>" --dry-run
cockpit-overseer tick --session "<session>"
cockpit-protocol status --session "<session>" --workers all --json
```

FIFO plus `cockpit-overseer tick` is the **only normal mission path**. Each tick
takes at most one state-changing action. `cockpit-protocol mission` and
`cockpit-overseer dispatch` are retired bypasses. `dispatch --bootstrap` only
primes roles during setup. `send` and `nudge` are raw-input diagnostic or
explicitly human-requested exceptions, never normal mission, approval, or cancel
operations; a typed STOP or a pane reset cannot cancel durable work.

Before delivery, the controller commits a command, mission, trace, boundaries,
worker-slot reservation and `pending-dispatch` lifecycle **sequence 0, without a
heartbeat**. The brief contains the exact shell-quoted
`cockpit-control accept-dispatch` receipt (also exposed by `cockpit-protocol`).
The worker must execute it unchanged and start only on exit 0 with
`outcome=accepted` **and** `start_work=true`. It atomically records the accepted
command receipt and lifecycle sequence 1. Duplicate receipts return
`start_work=false`; after a lost response or context reset, observe and obtain
explicit recovery rather than executing the mission twice.

The following fragments use IDs from that accepted brief; angle-bracket values
are placeholders, not new dispatch IDs:

```bash
cockpit-protocol heartbeat --state running --worker worker-dev \
  --mission "<mission-uuid>" --queue-item "$QI_ID" --trace "<trace-uuid>" \
  --sequence 2 --fresh-for 300
```

`heartbeat` aliases `record-lifecycle`; renew freshness with strictly increasing
per-mission sequences. Publish a terminal lifecycle with correlated evidence
when work actually ends. A DONE marker or `wait-report` output alone is not
terminal authority and does not clear a queue item.

For a successfully finished mission (not a pending cancel/replace request), the
worker records completion with the **next** lifecycle sequence and actual result
evidence:

```bash
cockpit-protocol record-lifecycle --state completed --worker worker-dev \
  --mission "<mission-uuid>" --queue-item "$QI_ID" --trace "<trace-uuid>" \
  --sequence "<next-sequence>" --evidence "file:/private/mission/verified-result"
```

The overseer then inspects `mission-status`, gathers delivery and queue-scoped
governed E2E evidence, and only clears the item when those gates are satisfied
(`cockpit-queue clear-current --e2e-run RUN-<id> --e2e-result passed --reason ...`,
or an explicit human waiver). Completion alone is not a clearance waiver.

### Questions, explicit human decisions, and cooperative ACKs

Create each command UUID once and retain it, its digest and correlation on retry.
Store prompt/answer/result bodies in access-controlled local artifacts; journal
only typed references (`file:...`, for example) and digests, never full secrets.
Do not put secrets in CLI arguments, categories, reasons, traces or pane archives.
The safe metadata payloads below illustrate the digest input, not the body.

```bash
# Worker: report a real prompt explicitly (ask / raise-question for a question).
cockpit-protocol access-prompt --command-id "<prompt-uuid>" \
  --worker worker-dev --mission "<mission-uuid>" --queue-item "$QI_ID" \
  --trace "<trace-uuid>" --category permission \
  --body-ref "file:/private/mission/prompt" --payload '{"kind":"permission"}'
cockpit-protocol pending --worker worker-dev
cockpit-protocol read-question --mission "<mission-uuid>"

# Operator: only after the human explicitly chooses to hold this pending prompt.
cockpit-protocol hold --command-id "<hold-uuid>" --answers "<prompt-uuid>" \
  --by operator --trace "<hold-trace-uuid>" --category explicit-hold \
  --body-ref "file:/private/mission/hold" --payload '{"decision":"hold"}'

# Operator: only relay the human's explicit answer, never infer one.
cockpit-protocol reply --command-id "<reply-uuid>" --answers "<prompt-uuid>" \
  --by operator --trace "<reply-trace-uuid>" --category explicit-decision \
  --body-ref "file:/private/mission/answer" --payload '{"kind":"human-decision"}'
```

`pending` and `read-question` return durable **mission-status JSON**, including
dialogs and slots, not a temporary-file inbox or just a list of unanswered
questions. Inspect the dialog state and its command ID. `hold` requires
`--answers` correlated to a pending prompt; it neither answers nor approves it.
`reply` aliases `answer-question`. Only explicit human decisions may be relayed;
the `--by` label is attribution, not proof that human consent was obtained.
Uninstrumented interactive prompts remain diagnostic until explicitly reported
with `ask` / `access-prompt`. Never infer answers or auto-approve permissions.

Workers inspect `command-status` (and `status.pending_commands`) for replies,
holds and cancellation/replacement requests. Check target, mission, boundaries
and the stored envelope digest. Acknowledge receipt, perform the permitted
cooperative action, then acknowledge its actual application:

```bash
cockpit-protocol command-status --command-id "<control-command-uuid>"
cockpit-protocol acknowledge-command --command-id "<control-command-uuid>" \
  --digest "sha256:<stored-envelope-digest>" --by worker-dev \
  --outcome accepted --result "file:/private/mission/receipt"
# Only after applying the action / stopping old work safely:
cockpit-protocol acknowledge-command --command-id "<control-command-uuid>" \
  --digest "sha256:<stored-envelope-digest>" --by worker-dev \
  --outcome applied --result "file:/private/mission/result"
```

Use `accept-dispatch`, not this generic ACK sequence, to accept a dispatch.
To request cancellation of accepted work:

```bash
cockpit-protocol cancel-mission --command-id "<cancel-uuid>" \
  --worker worker-dev --mission "<mission-uuid>" --queue-item "$QI_ID" \
  --trace "<trace-uuid>" --reason "human requested stop" \
  --acknowledge-within 300 --payload '{}'
```

Alternatively use `replace-mission` with the same correlation/reason/payload
arguments, omit `--acknowledge-within`, and add
`--replacement-mission "<new-mission-uuid>"`. Both require the **owning worker's
accepted then applied ACK**, with a typed `--result`, before accepted old work
is cancelled/replaced and its slot released/switched. A request or timeout is
not evidence that a worker stopped. The next controller tick dispatches an
applied replacement with a new receipt; the replacement ACK is not that receipt.
Replacement is not permission to bypass FIFO.

### Status and inspected reservation recovery

`status --workers all --json` separates durable `lifecycle` from the operational
`status`: `available`, `working`, `awaiting-approval`, `held`, `blocked`, or
`unreachable`. Inspect `reachable`, pane diagnostics, freshness and
`pending_commands` separately. Pane text cannot erase durable prompts or advance
a mission; an idle-looking pane does not make an occupied slot available.

Unaccepted delivery retries reuse the command ID, payload digest and immutable
five-minute deadline. At expiry, delivery stops with
`dispatch-acceptance-expired`; the reservation stays blocked. A legacy dispatch
without a deadline is `dispatch-acceptance-unsupported`. For either, inspect
`command-status`, `mission-status`, and the worker. Only when inspection confirms
**no worker is executing that reservation**, an operator may explicitly release it:

```bash
cockpit-protocol recover-dispatch --dispatch-command "<dispatch-uuid>" \
  --command-id "<recovery-uuid>" --worker worker-dev --inspect-safe \
  --by operator --reason "inspection confirms no executing worker" \
  --evidence "file:/private/mission/inspection"
```

Recovery fences late receipts. It does **not** kill a worker, forge an ACK,
start replacement work, or settle the queue item. Reuse the recovery command ID
and unchanged inspection evidence on retry. Accepted work requires cooperative
cancel/replace instead; uncertain inspection means leave blocked and escalate.

### Scheduled controller ticks and legacy adoption

```bash
cockpit-wake schedule --once "07:15" -s "<session>" -w overseer \
  -m "Observe this mission" --mission "<mission-uuid>" --queue-item "$QI_ID" \
  --owner operator --intent "bounded oversight" --stop-condition "mission terminal"
# For recurrence replace --once "07:15" with --cron "*/5 * * * *".
cockpit-wake list
cockpit-wake stop "<wake-id>"      # alias of cancel; also stops fired recurring jobs
```

Scheduling requires all five intent fields (`--mission`, `--queue-item`,
`--owner`, `--intent`, `--stop-condition`) and a ready explicit control root.
The schedule stores structured `control_root` and `target.session/window`;
execution restores stored identity and calls `cockpit-overseer tick`. `-m` is an
inbox intent note, not injected pane input. `schedule --dry-run` is read-only:
no state, job, scheduler or control-store writes.

Legacy `list` / `cancel` / `stop` remain usable without a control root.
`cockpit-wake migrate` is **diagnosis-only**: it does not bootstrap/bind roots,
rewrite old jobs, or rename corrupt state. Inspect its diagnostic, stop old
schedules explicitly, bootstrap using `init` / `bind-roots`, pass `preflight`,
then reschedule with complete metadata. Unknown/corrupt authority needs operator
review, not guessed repair or event rewriting. This is current adoption guidance;
the earlier migration claims in historical TH3 release notes are not instructions
for the current CLI.

## Updates

Re-run install commands at any time to update. Managed files are idempotent:
already-current files are skipped and changed files are backed up before
overwrite.

You can also safely re-run the cold installer one-liner for updates; it now
stages extraction in a temporary directory and does not leave `./copilotcockpit`
behind in your current folder.

```bash
# Re-run the cold installer from latest release (safe to run repeatedly)
bash <(curl -fsSL https://github.com/guillaume-galp/copilotcockpit/releases/latest/download/install.sh)

# Update skills + cockpit tools from latest release
./bootstrap.sh global --from-release latest
./bootstrap.sh codex-global

# Refresh framework files in an existing e2e/ (project content is never touched)
./bootstrap.sh e2e ~/git/my-project --update

# Check what's installed vs what the repo ships
./bootstrap.sh doctor
```

## Codex

Codex support has two install targets:

- User-scoped skills: `~/.agents/skills/<role>/SKILL.md`
- Repo overlay skills: `.agents/skills/<role>/SKILL.md`

```bash
./bootstrap.sh codex-global
./bootstrap.sh codex-repo
./run-tests.sh codex
```

`skills/` remains the canonical source for all 8 managed skills.
`.agents/skills` exposes those skills to Codex in the repo.
`.github/skills` remains available for the legacy Copilot cockpit flow.

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
