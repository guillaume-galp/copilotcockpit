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

**Product work enters FIFO and is delivered by `cockpit-overseer tick`.**
The controller targets a managed worker pane; a busy cockpit means queue and
wait, not a background-agent or direct-pane bypass.

### Required Tooling (no raw tmux commands)

Use `cockpit-protocol` for all pane communication and pane observability.
Do not bypass it with direct tmux commands, including read-only status or
discovery commands.

### Control-plane context packet

For VP3+ control-plane work, keep vocabulary and ownership aligned with the
[Cockpit Control-Plane Ontology](../../docs/architecture/cockpit-control-plane-ontology.md).
Use its canonical terms consistently across code, CLI output, ADRs, stories,
skills, and docs. New synonyms for queue item, mission, command,
acknowledgement, lifecycle event, wake lease, trace/evidence, authority, derived
state, and one-action tick are review defects unless documented as compatibility
aliases.

When a tmux cockpit is running, treat it as managed state. Do not improvise
`tmux ls`, `tmux list-windows`, `tmux list-panes`, `tmux capture-pane`,
`tmux load-buffer`, `tmux paste-buffer`, or `tmux send-keys` unless the user
explicitly asks for raw tmux diagnostics. Use `cockpit-protocol` and
`cockpit-overseer` instead.

Protocol verbs:

| Verb | Purpose |
|------|---------|
| `dispatch --bootstrap` | Setup-only role priming, never product work |
| `send` / `nudge` | Raw input: diagnostic or explicitly human-requested exception, not mission/approval/cancel |
| `tail` | Read latest pane output |
| `watch` | Poll pane output for live observability / log tails |
| `accept-dispatch` / `heartbeat` | Atomic receipt, then `record-lifecycle` freshness |
| `ask` / `raise-question` / `access-prompt` | Explicit durable prompt with correlated IDs and typed body refs |
| `pending` / `read-question` | Durable mission-status JSON: inspect dialog states, slots and command IDs |
| `reply` / `answer-question` / `hold` | Relay explicit human decisions correlated with `--answers`; hold never approves |
| `cancel-mission` / `replace-mission` / `acknowledge-command` | Cooperative control with accepted then applied worker ACK and typed result |
| `recover-dispatch` | Operator-inspected unaccepted reservation release, not a worker kill |
| `meta cockpit --json` | Discover active cockpit session, windows, and workers |
| `status --workers all --json` | Read worker state without manual pane tails |

Queue verb:

| Tool | Purpose |
|------|---------|
| `cockpit-queue` | FIFO request queue CLI: `enqueue`, `review-footprint`, `list`, `inspect`, `classify`, `pause`, `resume`, `reject`, `start-next`, `clear-current` |

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
# Persist the reviewed brief before delivery; keep secrets out of queue/trace text.
QI_ID="$(cockpit-queue enqueue \
  --approved \
  --title "<short mission title>" \
  --text "<one mission: scope, paths, constraints, verification and report format>")"
# Only when no other queue item is active:
cockpit-queue start-next
cockpit-queue transition "$QI_ID" implementing --reason "ready for worker-dev"
cockpit-overseer tick --session "<session>"

# Worker shortcut command (session resolves from --session, TMUX_SESSION,
# current tmux session, or single-cockpit auto-detection)
cockpit-protocol tail --worker worker-test --lines 80
cockpit-protocol status --workers all --json
```

### Dispatch Rules

| Rule | Why |
|------|-----|
| `cockpit-queue enqueue` + state transition + `cockpit-overseer tick` | Commits mission authority before the controller delivers the brief |
| Durable dialog/control commands | Never paste approvals, cancellation, or additional work as one-liners |
| `cockpit-protocol tail/watch` for observability | Uniform read path for workers and log panes |
| `cockpit-protocol meta cockpit --json` | Discovers the active cockpit session, windows, and worker targets |
| `cockpit-protocol status --workers all --json` | Reads worker state without manual pane-tail interpretation |

`cockpit-overseer dispatch` and `cockpit-protocol mission` are retired bypasses.
`cockpit-protocol dispatch --bootstrap --target ...` is reserved for setup-time
role priming and must never carry product work.

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

Minimal mode means status-only: no deep triage or repeated tail reads. Context
cost is not authority to clear a pane. Inspect `mission-status` and
`command-status`; accepted work must stop cooperatively through
`cancel-mission` or `replace-mission` and its worker ACKs before any
human-requested session reset. Unaccepted reservations use inspected recovery
below. A reset never proves cancellation or frees a slot. After context loss,
reload role guidance and observe durable state; a duplicate receipt does not
authorize starting again.

---

## Worker Question Protocol — Overseer Duty

**On every user interaction, check for pending worker questions first:**

```bash
cockpit-protocol pending
```

If any exist:
1. Read `cockpit-protocol read-question --worker worker-<name>`: this is
   mission-status JSON, not a temporary-file inbox. Inspect each dialog state.
2. Relay the referenced prompt to the human; never infer even a "trivial" answer.
3. Use `reply` / `answer-question` only for the explicit human answer, or `hold`
   for an explicit hold. Supply a new response `--command-id`, `--answers`
   naming the pending prompt command, `--by operator`, `--trace`, `--category`,
   `--body-ref TYPE:VALUE` and `--payload JSON` or `--digest sha256:...`.

Hold keeps the prompt unresolved and grants no permission. A reply is a durable
command, not automatic approval or keystrokes. Workers inspect pending commands
and acknowledge accepted then applied with the matching digest and a typed
`--result`. Keep command IDs/digests stable on retry. Prompt/answer/result bodies
stay in private artifacts, not full secrets in the journal or CLI arguments.
Uninstrumented pane prompts are diagnostic only: ask the worker/operator to
report them explicitly via `ask` / `access-prompt`, never answer from pane text.

See [README — Durable operator walkthrough](../../README.md#durable-operator-walkthrough)
for complete CLI examples and the worker ACK sequence.

---

## FIFO Request Queue — Overseer Duty

Use the queue for build-method requests and explicitly approved build work that
should not interrupt the current worker mission.

### Dual-state contract: SQL vs FIFO

The native Copilot CLI SQL tables (`todos`, `todo_deps`, `inbox_entries`, and
session scratch tables) are **session-local operational state**. Use SQL for raw
idea staging, worker mission tracking, ephemeral todos, and dependencies inside a
single overseer session.

`cockpit-queue` is the **durable product FIFO** for accepted build requests. Any
accepted request tagged with `/the-copilot-build-method`, or explicitly approved
for cockpit-framework build work, must be promoted into `cockpit-queue` before
workers start implementation.

Rules when both exist:

- FIFO item state is authoritative for product progress and clearance.
- SQL completion never clears a FIFO item; only `cockpit-queue clear-current`
  can do that.
- Every SQL todo derived from a FIFO item must include `source_item_id=<QI-ID>`
  in its description or equivalent task metadata.
- Every staged SQL idea promoted into FIFO must be marked
  `promoted_to=<QI-ID>`; keep the SQL row as audit/scratch state, not as the
  source of truth.
- Do not use SQL queues to skip FIFO ordering for durable product work.

Before any queue command, set the queue root explicitly. Never rely on the
current directory, because a machine may host multiple independent FIFO queues:

```bash
export COCKPIT_QUEUE_ROOT="/absolute/project/docs/cockpit-queue"
export COCKPIT_CONTROL_ROOT="/absolute/project/docs/cockpit-control"
cockpit-control init --queue-root "$COCKPIT_QUEUE_ROOT" \
  --planning-root "/absolute/project/docs/plan" \
  --implementation-root "/absolute/project"
cockpit-control preflight
```

Generated cockpit launchers should set this tmux session environment when the
session starts. `cockpit-queue` may read `COCKPIT_QUEUE_ROOT` from the shell or
from the current tmux session environment, but it must never infer it from `cwd`.
For an initialized unbound store, use `cockpit-control bind-roots` with those
same root arguments. Bare `cockpit-overseer start` / `init` establishes only
structure; missing work boundaries/capability make preflight
`operationally-blocked`. Never hand-edit metadata/events. Existing installed
launchers and overlays are project-owned: have their owner explicitly review
root exports and tmux injection, not overwrite them during toolkit updates.

### Controller tick

`cockpit-overseer tick` is the deterministic reconciler. One tick validates the
control and queue roots, replays the committed journal, checks the derived
ledger against that replay, reads queue and worker state, applies the ADR-014
precedence order, takes **at most one** state-changing action, persists it, and
exits.

```bash
cockpit-overseer tick --window worker-dev --window worker-test --window worker-fix
cockpit-overseer tick --dry-run          # report the decision, change nothing
```

Read the `precedence <n> <source> <role> ...` lines it prints: they are the
ADR-014 ladder it actually applied, in order. Rules 7 (`live-status`) and 8
(`pane-text`) always carry `advances-state no` — pane text is diagnostic only
and can never dispatch, complete, or advance a mission.

The controller first commits the command and reserves the worker's one mission
slot with `pending-dispatch` sequence 0 and no heartbeat, then delivers the
queue-owned `source_text` with `MISSION-ID`,
`COMMAND-ID`, `QUEUE-ITEM-ID`, and `TRACE-ID` headers, boundaries, digest,
acceptance deadline and the exact `cockpit-control accept-dispatch` command.
The worker must run that receipt before work: only JSON `outcome=accepted` and
`start_work=true` authorizes starting. It commits the command acknowledgement
and lifecycle sequence 1 in one event. A duplicate receipt returns
`start_work=false`, never permission to repeat work after a restart.
The worker then emits `heartbeat` / `record-lifecycle --state running` sequence 2
with matching mission/worker/queue/trace and renews freshness monotonically.

Only the tick that commits a new dispatch sends its brief: a private named tmux
buffer, bracketed paste with preserved newlines, a one-second processing interval,
then one Enter. `transport=enqueued acceptance=pending` is not proof of worker
start or acceptance. Later ticks never automatically paste or press Enter again,
even after a transport error or crash: uncertain input could be a permission
prompt. They retain the command ID, digest and immutable five-minute deadline.
At expiry,
ticks stop delivery and record `dispatch-acceptance-expired` once for unchanged
evidence; the slot remains reserved, not failed or automatically replaced.
Missing-deadline legacy reservations are `dispatch-acceptance-unsupported`.
Inspect `command-status`, `mission-status` and the worker. For unknown delivery,
expired or legacy missing-deadline dispatch, only after confirming no worker is executing
the reservation may the operator use:

```bash
cockpit-protocol recover-dispatch --dispatch-command "<dispatch-uuid>" \
  --command-id "<recovery-uuid>" --worker worker-dev --inspect-safe \
  --by operator --reason "inspection confirms no executing worker" \
  --evidence "file:/private/mission/inspection"
```

Reuse that recovery ID and inspection on retry. Recovery fences late receipts;
it does not kill a worker, forge acceptance/ACKs, start work or settle the queue.
Accepted work requires cooperative cancellation/replacement; uncertain
inspection stays blocked. Queue disposition alone does not release a reservation.

Exit codes: `0` when the tick enqueued input (not worker acceptance), recorded an
observation, retained an existing command without resending, or found nothing new.
It exits `1` in exactly two
controller cases plus explicit delivery failure, all of which print a named
diagnostic on stderr and never a traceback:

* the decision is **blocked** — the tick refuses to guess, names the exact
  repair, and records one conflict observation;
* the dispatch command was committed but **did not claim the worker's mission
  slot** — the fold, not the tick, decides who holds a slot, so the tick reports
  what the fold recorded and creates no second mission.
* tmux delivery failed after commit — the durable reservation remains and the
  next tick does not resend input. Inspect before explicit operator recovery;
  never use an extra Enter to repair unknown delivery.

Ticking again over the same evidence persists nothing, so a recurrent wake
terminates instead of re-investigating.

A concurrent writer is ordinary operation, not an incident: while another
process is between committing its event and replacing `ledger.json`, a reader
legitimately sees a projection one revision behind. The tick settles that
reading before deciding, then repairs any derived state the committed journal
can rebuild — a missing, stale, unreadable, or disagreeing `ledger.json`, or a
stale `events.jsonl` view — by replaying the journal. That repair commits no
event, so it is never the tick's one action. Read the `ledger repair <outcome>
from <classification>` line to see what it did.

Two things are never repaired automatically:

* a `ledger.json` that records revisions `events/` no longer holds — replay
  would rewind derived state and hide the disappearance of committed authority;
* malformed authoritative journal data under `events/`.

Both stop the tick before it derives one fact, commit nothing, change not one
byte, and print the diagnostic. Use `cockpit-control preflight` to name the file
and escalate for operator investigation/restoration from verified evidence.
Never hand-edit history to make a mission appear accepted or terminal:
cockpit-control never rewrites, reorders, or removes committed authority.

### Bounded recovery and conflicts

An expired heartbeat is an **observation**, never a mission state. A stale
worker is never marked `failed` by timeout; the tick walks a fixed, finite
ladder instead, one rung per tick and each rung at most once per staleness
episode:

```text
nudge -> troubleshoot -> cancel -> replace -> escalate
```

Each command rung declares the moment a response is due by, and the ladder does
not advance until that declared window has elapsed — so a worker is never
cancelled for missing a window it was never given. An episode ends when the
controller looks and records that the mission was fresh; the next staleness is
then a new episode and the ladder starts again at `nudge`. That observation
carries the freshness the worker itself declared, so a worker that goes quiet
and then answers is always a new situation and is always recorded: a responsive
worker that blips repeatedly is nudged each time and is never walked on to
cancel or replace. Lifecycle evidence that is
already expired by the time the controller looks does **not** end an episode,
however much of it arrives: a worker that declares a two-minute freshness
window but reports every ten minutes is walked to the end of the ladder rather
than nudged forever. When the queue item a mission implements leaves the active
set, the ladder is only `cancel -> escalate`: the controller asks the worker to
stop and **never** reopens the queue item. `escalate` blocks, names the decision
it needs from you, and stops for good.

Cancellation/replacement of accepted work is cooperative, including recovery
requests: the owning worker must acknowledge the registered command as
`accepted`, actually stop/apply it, then acknowledge `applied` with the matching
digest and a typed result reference. A timeout, pane marker or overseer request
does not prove the worker stopped. Do not manufacture lifecycle events to free
a slot. Contested slots block dispatch; inspect the named conflict and escalate
for an explicit disposition without disturbing the retained mission.

Keep durable `lifecycle` separate from operational `status` (`available`,
`working`, `awaiting-approval`, `held`, `blocked`, `unreachable`). Check
`reachable`, freshness, and `pending_commands` independently. Pane diagnostics
cannot override a durable prompt, completion or slot claim.

### Queue intake

```bash
# classify before enqueueing when the request is ambiguous
cockpit-queue classify --text "<request>"

# accepted automatically when build-method-tagged
cockpit-queue enqueue --text "<request containing /the-copilot-build-method>" --actor overseer

# accepted by explicit overseer approval
cockpit-queue enqueue --text "<request>" --approved --actor overseer
```

Queue pause blocks admission and controller dispatch/acceptance. Enqueueing remains free-flowing so the
human can keep adding ideas while delivery is paused.

Do not enqueue questions, pure diagnostics, reminders, or unrelated commands.
Use `cockpit-queue reject <id> --reason "<why>"` for an existing item that is
not buildable.

### Queue loop

Parallel missions require an explicit reviewed version-1 footprint; see the
[README schema and commands](../../README.md#fifo-dispatch-and-worker-receipt).
Use `enqueue --footprint <JSON-file>` for draft or reviewed intake and
`review-footprint <QI-ID> --footprint <JSON-file>` while the item is queued.
Do not infer independence from titles or ask the tooling to interpret prose.

Before marking `status: reviewed`, perform targeted scope analysis: inspect
repository/worktree and upstream identities, intended branches, writable paths,
planning artefacts, generated outputs, test fixtures, databases, ports,
deployment environments, external resources and dependencies on earlier work.
Record why the complete declarations are independent in `rationale`; if any
identity or side effect remains uncertain, leave it draft/unknown and serial.
This boundary inspection is required even though implementation research belongs
to the worker. Include all writable planning/output paths outside the exclusive
repository claims, not just source files. Resource keys must name the same
resource identically across missions.

Reviewed footprints narrow, never expand, canonical cockpit authority.
Repository and Git common-directory paths must be inside `implementation_roots`;
writable paths must be inside implementation roots or `planning_root`.
Do not claim the queue/control stores or directories enclosing them. A footprint
or resource key is not permission to add a repository outside those upper bounds.

Repository claims are exclusive: different branches, files, clones sharing an
upstream, or linked worktrees do not establish independence. Use distinct
explicit instance IDs such as `workers: {"worker-dev": "worker-dev-2"}` and
matching already-provisioned windows to run two implementing missions. Repeat
`tick --window` for diagnostics as needed; it does not provision panes.
The controller revalidates immutable claims under its lock. A queue state
transition, held prompt, cancellation request or failed delivery does not free
those claims. Independent new missions may dispatch while another is held or
awaiting acceptance; no global lock may be inferred from the singular legacy
active-item/mission projections.

Owed bounded recovery takes priority over new independent dispatch, except that
held dialogs are never automatically recovered. Terminal queue history remains
readable after repository removal, but occupied claims still require live
validation of the original command footprint, not the terminal queue record.

A normally terminated mission remains finished even while its queue item awaits
handoff/clearance. A released worker slot is not permission to dispatch that phase
again, nor is changing its instance ID. Advance to the next appropriate phase or
use explicit lifecycle recovery/replacement; never infer a retry from queue
state alone. Inspected recovery of an unaccepted reservation remains supported.
`fixing` and `e2e-related-fixing` are distinct phases even though both use
`worker-fix`. Their completion fences use immutable dispatch phase evidence.
Unknown historical phase evidence stays conservatively fenced by role.

**Scope drift protocol:** the worker stops before crossing a boundary, preserves
mission correlation and freshness, and raises a durable question/blocker.
Do not widen the active queue declaration or use a pane reset as release.
Resolve the question within the existing scope, or cooperatively stop/release
the old mission and review a newly queued mission with the expanded footprint.
Acceptance/retained-command validation rejects changed declarations; immutable journal claims
remain authoritative. This is not an OS sandbox.

Before dispatching new work:

```bash
cockpit-queue list
cockpit-queue start-next --actor overseer
cockpit-queue inspect <queue-id>
cockpit-protocol status --workers all --json
```

Rules:

- Start the next item only when serial, or when all active declarations are
  reviewed and disjoint. Draft/unknown/legacy scopes keep serial semantics.
- Multiple active items are valid only with disjoint reviewed footprints.
  On a conflict, stop admission and inspect the named QI-IDs and occupied claims;
  never reset a running worker or mutate scope to evade the conflict.
- Preserve FIFO admission: never bypass an earlier waiting conflict or
  unreviewed item. Earlier shaping work is not silently skipped at dispatch.
- Use `clear-current --item <QI-ID>` when several items are active; unaddressed
  clearance is retained only for an unambiguous single active item.
- Never dispatch to a busy worker.
- Keep the queue item ID in every worker mission and trace/report.
- Pause/resume with `cockpit-queue pause` and `cockpit-queue resume` when the
  human wants `start-next` advancement to stop.

### Queue states

`queued -> shaping -> planned -> implementing -> testing -> fixing -> delivered -> e2e-testing-runbooks -> e2e-related-fixing -> cleared`

`blocked` and `rejected` are escape states.

### Clearance

A queue item may clear only after local delivery evidence and queue-scoped E2E
operator evidence, or an explicit human waiver:

```bash
cockpit-queue clear-current --e2e-run RUN-<id> --e2e-result passed --reason "delivered and runbook green"
cockpit-queue clear-current --waiver "<human-approved waiver>" --e2e-result waived
```

If worker-test reports failures, transition the item through
`e2e-related-fixing` and dispatch one focused fix at a time.

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
- Track missions with `mission-status` / `command-status`; SQL or memory is
  scratch state only. Poll compact status rather than repeating pane dumps.

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
fact. These diagnostic archives are not mission authority; never include secrets
in briefs or paste secret-bearing prompt bodies into trace output.

Use `cockpit-trace show <trace-id>` for one dialog or `cockpit-trace tree
<trace-id>` for a stitched family. If the same mission can be explained from the
trace, do not burn extra tokens to rediscover it.

## Scheduling Awakenings

Use the **`cockpit-wake` CLI** for a future or recurring controller tick, not
arbitrary reminders or pane input. Queue deferred product/test work first.

Always pass the **exact tmux session name** of this cockpit when scheduling:
```bash
cockpit-wake schedule --once "07:15" -s "<THIS-SESSION>" -w overseer \
  -m "Observe mission progress" --mission "<mission-uuid>" --queue-item "<QI-ID>" \
  --owner operator --intent "bounded oversight" --stop-condition "mission terminal"
```
Retrieve the session name with: `cockpit-protocol meta current-session`
All five intent arguments are required, as is a ready `COCKPIT_CONTROL_ROOT`.
The schedule stores structured control root and target session/window and
restores them for a bounded tick. `-m` is an inbox note, never pasted input.
Use `--cron "*/5 * * * *"` instead of `--once` for recurrence; `--dry-run` is
read-only. `cockpit-wake stop <id>` aliases `cancel`, including fired recurring
jobs. Legacy `list`/`stop`/`cancel` work without a root. `migrate` diagnoses only:
no bootstrap, corrupt-state renaming, or job rewriting. Explicitly stop legacy
schedules, bootstrap/bind roots and pass preflight, then reschedule.

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

Before every tick, inspect durable mission slots and command state. A mental
or SQL summary is only a convenience:

```
worker-test  : <mission summary> — STATUS: active | idle
worker-dev   : <mission summary> — STATUS: active | idle
worker-fix   : <mission summary> — STATUS: active | idle
```

Only the controller may dispatch into a free durable slot; "idle" pane text or
a scratch summary is not authority.

### Sequencing missions to the same worker

When a worker finishes one mission and you have a follow-up:
1. Verify terminal lifecycle evidence and the released slot with `mission-status`;
   `wait-report` and pane status alone are insufficient
2. Let FIFO plus the next controller tick deliver a **new, clean dispatch**
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

**Recovery:** Inspect durable state, request `cancel-mission` or
`replace-mission` when appropriate, and await the owning worker's accepted then
applied ACK with typed result evidence. Never substitute a raw STOP message.
