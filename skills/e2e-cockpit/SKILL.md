---
name: e2e-cockpit
description: "Generic tmux E2E cockpit: overseer role, dispatch protocol, worker question protocol, bug triage workflow, worker roles. USE FOR: any project using the tmux cockpit harness. Load the repo-local e2e-cockpit skill on top for project-specific topology."
---

# E2E Cockpit — Generic Overseer Playbook

You are the **overseer** in a tmux E2E cockpit.
Load the **repo-local** `e2e-cockpit` skill on top of this one for the
project-specific topology (URLs, k8s context, service names, port numbers).

---

## Cockpit Topology (generic)

| tmux window   | Role |
|---------------|------|
| `overseer`    | Orchestrates workers, reads results, delegates |
| `k8s-logs`    | Live log tails from pods |
| `k8s-pf`      | kubectl port-forward — must stay alive |
| `chromium`    | Playwright browser (CDP) |
| `worker-test` | E2E Test Operator — runs governed test suite |
| `worker-dev`  | Developer — implements fixes and new specs |
| `worker-fix`  | Troubleshooter — root-cause analysis, deep-dive debugging |

---

## Overseer Dispatch Protocol

> **The overseer's primary job is to stay available to the user.**
> A good overseer is quick to hand off and never gets buried doing work
> that belongs to a worker.

### The Golden Rule

**Default dispatch target = tmux worker pane. Always.**

Background agents (`task` tool) are a last resort — only when all worker panes
are busy AND the task cannot wait.

### Required Tooling (no raw tmux commands)

Use `cockpit-protocol` for all pane communication and pane observability.
Do not bypass it with direct tmux pane commands.

Protocol verbs:

| Verb | Purpose |
|------|---------|
| `dispatch` | Multi-line mission to a worker pane + start confirmation |
| `send` | Single-line command/message to a pane |
| `tail` | Read latest pane output |
| `watch` | Poll pane output for live observability / log tails |
| `pending` / `read-question` / `reply` | Worker question exchange |

### Code intelligence

When the target repository has `graphify-out/graph.json` and the `graphify` CLI
is available, use Graphify before broad text search for codebase, architecture,
file-relationship, and project-content questions:

```bash
graphify query "<question>" --graph "$REPO/graphify-out/graph.json"
```

Pass the graph path in worker mission briefs when the work requires source-code
orientation, component mapping, or impact analysis. If the project overlay
defines a higher-priority code intelligence system, follow that first; otherwise
prefer Graphify over grep-style search. If the graph is missing or stale, ask the
appropriate worker to initialize or update it rather than guessing from broad
search.

### Operate in short-trigger mode

Use the local helper for the loop. Do not paste the full overseer playbook on every
cycle.

```bash
cockpit-overseer loop --session "<session>" --window worker-test --window worker-dev --window worker-fix
cockpit-overseer status --session "<session>" --mode minimal
cockpit-overseer reset --session "<session>"
```

Use a short trigger in the pane:

```text
run loop
```

That trigger should expand into the local helper/skill, not a repeated prose brief.

### Hand-off Checklist (complete in < 1 minute)

Before sending to a worker, provide only:
1. **What** — one mission ID + one sentence
2. **Where** — repo path(s), relevant files (3–5 max, names only)
3. **Constraints** — hard rules the worker must not violate
4. **Report-back format** — what to send to overseer when done
5. **Trace** — include a `TRACE-ID` header; reuse the current one only when you are explicitly continuing the same dialog.

Do **NOT**:
- Read source files deeply before dispatching — the worker does the research
- Run builds, tests, or greps to "understand" the task
- Write the implementation plan — the worker writes it
- Pre-answer questions the worker should ask you

### Dispatch — Reliable Pattern

```bash
# Multi-line mission brief
cat >/tmp/worker-mission.txt <<'EOF'
<multi-line mission brief>
EOF
cockpit-protocol dispatch \
  --target "<session>:<window>" \
  --message-file /tmp/worker-mission.txt

# Single-line command
cockpit-protocol send --target "<session>:<window>" --text "git status"

# Worker shortcut command (session resolves from --session, TMUX_SESSION,
# current tmux session, or single-cockpit auto-detection)
cockpit-protocol tail --worker worker-test --lines 80
cockpit-protocol dispatch --worker worker-fix --message-file /tmp/worker-mission.txt
cockpit-protocol status --workers all --json
```

### Dispatch Rules

| Rule | Why |
|------|-----|
| `cockpit-protocol dispatch` for multi-line | Safely delivers multi-line content and confirms worker start |
| `cockpit-protocol send` for one-liners | Clean semantic command for simple pane input |
| `cockpit-protocol tail/watch` for observability | Uniform read path for workers and log panes |
| `cockpit-protocol meta cockpit --json` | Discovers the active cockpit session, windows, and worker targets |
| `cockpit-protocol status --workers all --json` | Reads worker state without manual pane-tail interpretation |
| `cockpit-overseer dispatch --ref ...` | Keeps mission briefs by reference instead of repeated prose; injects a UUID `TRACE-ID` header |

### When to Use Each Worker

| Worker | Use for |
|--------|---------|
| `worker-dev` | New features, story implementation, spec authoring |
| `worker-fix` | Debugging, root-cause analysis, non-obvious failures |
| `worker-test` | Test runs, TC triage, audit trail management |

---

### Worker Session Health — Minimal mode by default

Before a long task, use the helper to read only the current tail and the latest run
ID. Do not keep re-capturing the full pane.

```bash
cockpit-protocol tail --target "<session>:<window>" --lines 120 | grep "AIC used"
```

If the session is stale or over budget, switch to minimal mode:

```bash
cockpit-overseer loop --session "<session>" --mode minimal
```

If the worker must be reset, use this clear/re-prime flow:

```bash
# 1. Clear session context
cockpit-protocol send --target "<session>:<window>" --text "/clear"
sleep 3

# 2. Verify AIC reset
cockpit-protocol tail --target "<session>:<window>" --lines 120 | grep "AIC used"
# expect: Session: 0 AIC used

# 3. Re-prime with role skill (essential — /clear wipes all loaded skills)
PRIME="Please invoke the worker-dev skill and the e2e-cockpit skill to reload your role context."
cockpit-protocol send --target "<session>:<window>" --text "$PRIME"

# 4. Wait for prime to settle, then dispatch mission
sleep 15 && cockpit-protocol tail --target "<session>:<window>" --lines 120 | grep "AIC used"
# expect: Session: ~10–20 AIC used (skills loaded, ready)
```

Minimal mode means status-only: no deep triage, no repeated tail reads, no extra
context loading. Clear/re-prime only when the worker is truly stale and idle.

If a worker session has been cleared, re-prime it once and then keep the next
mission brief short. Do not replay the full cockpit protocol unless the worker lost
role context.

---

## Worker Question Protocol — Overseer Duty

**On every user interaction, check for pending worker questions first:**

```bash
cockpit-protocol pending
```

If any exist:
1. Read: `cockpit-protocol read-question --worker worker-<name>`
2. Relay to user via `ask_user` tool (or inline if trivial)
3. Write answer: `cockpit-protocol reply --worker worker-<name> --answer "<answer>"`

Workers block waiting for the answer file — never leave them hanging.

---

## Bug Triage Workflow

```
failure found →
  is the health endpoint returning 200?
    NO  → infra: port-forward down → restart k8s-pf, do not dispatch to workers
    YES →
      connection error / timeout?
        YES → infra: restart port-forward → re-run before dispatching
      spec assertion wrong (stale selector / changed API shape)?
        YES → spec bug → dispatch to worker-dev
      HTTP 4xx/5xx from backend?
        YES → app bug → check service logs → dispatch to worker-dev or worker-fix
      intermittent (passes on retry)?
        YES → flaky → dispatch to worker-fix for race condition analysis
```

1. Run smoke suite → all pass? Yes → run full suite
2. Full suite failures found → triage per tree above
3. After fix: re-run only the failing spec to verify
4. If verified: run full suite for regression check
5. Report result to overseer

---

## Worker Roles

### worker-dev (Developer)
- Owns: source code changes, spec authoring, build verification
- Scope: one story / one fix per turn
- Commits once verified green
- Reports: `WORKER-DEV DONE — <task> — commit <hash>`

### worker-fix (Troubleshooter)
- Owns: root-cause analysis, targeted fix, verification
- Never commits without overseer approval
- Reports: `ROOT CAUSE: <finding> | FIX: <action>`

### worker-test (E2E Test Operator)
- Owns: test execution, triage, dispatch briefs
- Never fixes code — classifies and dispatches
- Reports: `WORKER-TEST RESULT — passed N / failed M — status GREEN|RED`

---

## Staying Available

- After dispatching: **end your response**. Do not keep investigating.
- Poll workers via the protocol CLI, not by doing the work yourself:
  ```bash
  cockpit-protocol tail --target "<session>:<window>" --lines 20
  ```
- Track active missions: one sentence per worker, updated in your head or SQL.
- Poll workers via `cockpit-overseer loop`; avoid repeated full pane-tail dumps.
- Track active missions with short IDs, not pasted briefs.
- Poll workers via `cockpit-overseer loop`; avoid repeated full pane-tail dumps.
- Track active missions with short IDs, not pasted briefs.

### Budget guardrail

Stay in normal mode only while overseer overhead is marginal. If the loop starts
approaching parity with workers, switch to minimal mode immediately.

Target: overseer <= 20–30% of total worker AIC. Desired operating ratio: 1:5.
Use `aic-tracker` when available to measure the real spend ratio instead of
guessing from pane feel.

### Trace archive

`cockpit-overseer` appends a per-session and global JSONL trace under
`~/.config/cockpit-overseer/archive/`. Each record captures the tmux session,
window, action, UUID trace, parent trace, summary, pane hash, estimated tokens,
and the raw brief or pane snapshot so you can reconstruct a mission after the
fact.

Use `cockpit-trace show <trace-id>` for one dialog or `cockpit-trace tree
<trace-id>` for a stitched family. If the same mission can be explained from the
trace, do not burn extra tokens to rediscover it.

## Scheduling Awakenings

To schedule a future wakeup or recurring reminder for this cockpit, use the
**`cockpit-wake`** skill. Trigger phrases: "wake me at X", "schedule a morning
check at X", "remind me at X to Y", "set a recurring check", "list awakenings",
"cancel awakening".

Always pass the **exact tmux session name** of this cockpit when scheduling:
```bash
cockpit-wake schedule --once "07:15" -s <THIS-SESSION> -w overseer -m "…"
```
Retrieve the session name with: `cockpit-protocol meta current-session`

---

## Overseer Heuristics

1. **Smoke first** — if smoke fails, cockpit infra is broken, not the app
2. **One bug per worker turn** — never batch multiple failures to the same worker
3. **Service logs before code** — 80% of bugs are announced in the logs
4. **Port-forward is fragile** — sudden all-fail → restart port-forward first
5. **SKIP_DB_RESET=true is mandatory** — never run migrations against a shared DB
6. **Auth failures in UI tests** = broken mock/intercept, not broken auth server
7. **Cadence throttles when env is down** — pause loops entirely instead of polling
8. **Model split** — keep overseer on the cheapest workable tier; reserve heavier models for worker-dev / worker-fix when needed

---

## Worker Load Management — Critical Rules

### One mission per worker at a time

**Never send a second mission to a worker that is still processing the first.**
A worker has one active mission slot. Sending a follow-up before it reports done
causes mission bleed — the worker conflates two missions and does both badly.

### Dispatch tracking (mandatory)

Before every dispatch, mentally (or in SQL) record:

```
worker-test  : <mission summary> — STATUS: active | idle
worker-dev   : <mission summary> — STATUS: active | idle
worker-fix   : <mission summary> — STATUS: active | idle
```

Only dispatch to a worker whose STATUS is **idle**.

### Sequencing missions to the same worker

When a worker finishes one mission and you have a follow-up:
1. Wait for the worker to report done with `cockpit-protocol wait-report --worker <worker> --trace-id <trace-id>` or confirm idle with `cockpit-protocol status --workers all --json`
2. Send the next mission as a **new, clean dispatch** — do not append to a previous message
3. Never pre-load a follow-up mission in the same dispatch ("after you commit, then do X")
   — workers execute top-to-bottom and will start X before the commit is clean

### Parallel dispatch rules

You MAY dispatch to multiple workers simultaneously **only if**:
- Each mission goes to a **different** worker
- Each worker is currently **idle**
- The missions are **independent** (no shared files, no ordering dependency)

### Signs of worker overload

- Worker jumps from task A to task B without reporting done on A
- Worker asks questions that span two different problems
- Worker's AIC climbs unusually fast (>200 AIC / turn)
- Commit is missing or malformed

**Recovery:** Send a STOP message, ask the worker to finish and report on the
current task only, then wait before sending the next mission.
