# VP3 Architecture: Holistic Cockpit Control Plane

> Status: Accepted
> Date: 2026-09-03
> Implements: [VP3](../vision_of_product/VP3-overseer-orchestration-resilience/VP3.md)

## 1. Purpose

VP3 evolves the existing cockpit helpers into one local control plane for
overseer-driven work. The architecture keeps tmux as the process host and keeps
the current Bash, Go, and single-file Python distribution model. It adds durable
mission state, structured worker lifecycle events, idempotent commands,
deterministic reconciliation, intent-aware wakes, and correlated evidence.

The control plane is local-first and file-backed. It is not a distributed
workflow platform. Skills tell an LLM how to operate; tools own state, enforce
transitions, and expose evidence so model memory is never authoritative.

## 2. Goals and Non-Goals

### Goals

- Resume an active mission after an overseer session is cleared or replaced.
- Give every worker mission a structured lifecycle and recoverable blocker.
- Provide managed dispatch, question/reply, cancellation, and replacement.
- Reconcile queue, mission, worker, and pane evidence deterministically.
- Ensure recurrent wakes take at most one state-changing action and terminate.
- Preserve traceability from human intent through tests and clearance.
- Remain portable, dependency-light, idempotent, and non-destructive.

### Non-goals

- Replacing tmux as the worker process host.
- Building a general workflow engine, daemon cluster, or hosted service.
- Granting autonomous production deployment approval.
- Encoding different orchestration state machines for individual LLM families.
- Providing a graphical UI in the VP3 MVP.

## 3. MVP Boundary

The MVP proves one restart-safe mission from queue selection to completion or
bounded human escalation. It includes:

- one versioned control root and materialized mission ledger;
- one append-only control event journal;
- structured worker lifecycle and command acknowledgements;
- controller reconciliation and stale-worker detection;
- mission-aware wake leases and automatic suspension;
- trace, queue, worker report, test, and human-decision correlation;
- compatibility diagnostics for legacy cockpits;
- contract, restart, duplicate-wake, and end-to-end resilience tests.

## 4. System Context

```text
human / scheduled wake
          |
          v
  cockpit-overseer controller
     |       |        |
     |       |        +------ cockpit-trace (derived evidence view)
     |       +--------------- cockpit-queue (product-work authority)
     +----------------------- cockpit-protocol (worker command/lifecycle API)
                                  |
                                  v
                          tmux worker processes
```

`cockpit-wake` triggers a controller tick. It does not directly decide or
advance mission state. Raw tmux output remains a diagnostic observation and
cannot complete a command or advance a mission.

## 5. Components and Ownership

| Component | Responsibility | Interface | Data ownership | Dependencies |
|---|---|---|---|---|
| `cockpit-overseer` | Reconcile evidence, select one valid transition, maintain the materialized ledger, manage wake leases, and emit escalation records. | `start`, `tick`, `status`, `reconcile`, `suspend`, `resume`, `reset-derived` subcommands. | Materialized ledger and controller decisions. | Control store, queue CLI, protocol CLI. |
| `cockpit-protocol` | Resolve workers, deliver idempotent mission commands, collect acknowledgements, record lifecycle events, and exchange questions/replies. | Existing commands plus managed `cancel-mission`, `replace-mission`, lifecycle and acknowledgement operations. | Command envelopes, acknowledgements, worker lifecycle events. | tmux transport and control store. |
| `cockpit-queue` | Own durable product request order and product-delivery state. | Existing FIFO commands and explicit mission/trace references. | Queue item files and queue event log from ADR-010. | Explicit `COCKPIT_QUEUE_ROOT`. |
| `cockpit-wake` | Schedule one-off or recurrent controller ticks with mission intent and a stop condition. | Existing schedule/cancel surface plus mission, owner, stop-condition, and controller-tick fields. | Schedule definition and scheduler job metadata. | `at`/`cron`, controller CLI, control store. |
| `cockpit-trace` | Reconstruct causal evidence without becoming authoritative state. | Trace, mission, queue-item, and session views. | No canonical state; derived indexes may be rebuilt. | Control and existing trace journals. |
| cockpit doctor/preflight | Validate roots, schemas, locks, worker capabilities, declared paths, and compatibility before dispatch. | Read-only checks by default; explicit repair commands for derived state. | Diagnostic reports only. | All configured roots and CLIs. |
| worker skills | Follow mission boundaries and emit structured lifecycle/report events. | Protocol commands and report schemas. | Worker-local implementation artefacts, not control state. | Project repositories and protocol CLI. |

No component may infer a canonical root from its current working directory.

## 6. Authoritative State and Precedence

The control plane has multiple owners rather than one file pretending to own all
product and runtime state:

1. Explicit human stop, approval, waiver, or suspension is authoritative for
   the decision it addresses.
2. `cockpit-queue` is authoritative for FIFO order and product-work state.
3. The control event journal is authoritative for mission commands,
   acknowledgements, worker lifecycle, wake leases, and controller decisions.
4. The materialized ledger is a replayable projection of the event journal.
5. A structured worker report is evidence only when mission ID, command ID, and
   trace ID match the active mission.
6. Live protocol status is an observation used for freshness and recovery.
7. Pane text is diagnostic-only and never advances authoritative state.

When sources disagree, the controller applies that order, emits a
`reconciliation-conflict` event, and either repairs the projection or blocks the
mission. It never guesses a product transition from pane text.

## 7. Control Root and Data Model

Every cockpit sets an absolute `COCKPIT_CONTROL_ROOT` in both the launching
shell and tmux session environment. The queue root remains independently
explicit through `COCKPIT_QUEUE_ROOT`.

```text
<control-root>/
  control.json                 # root metadata and schema version
  ledger.json                  # materialized active-mission projection
  events/                      # immutable committed event records
  pending/                     # private unpublished event candidates
  events.jsonl                 # optional rebuildable compatibility view
  commands/<command-id>.json   # command envelope and latest acknowledgement
  escalations/<id>.json        # human-readable bounded escalation records
  locks/control.lock/          # portable mkdir lock with owner metadata
```

### Root metadata

`control.json` contains:

- `schema_version`;
- cockpit/session identity;
- queue root and canonical planning root;
- allowed implementation roots;
- tool capability versions;
- creation and last-migration timestamps.

### Materialized ledger

`ledger.json` contains:

- `schema_version` and monotonic `revision`;
- active queue item and mission IDs;
- wake intent, owner, stop condition, lease, and blocker count;
- worker mission slots with command, trace, state, freshness, and blocker;
- canonical roots and declared runtime boundaries;
- last state-changing event;
- next allowed actions;
- pending and resolved human decisions.

### Control events

Every JSONL event contains:

- `schema_version`, globally unique `event_id`, and UTC timestamp;
- monotonic control `revision`;
- cockpit, queue item, mission, worker, command, trace, and parent-trace IDs as
  applicable;
- actor, event type, previous state, next state, reason, and evidence refs;
- a payload limited to structured metadata.

Secrets, environment values, full prompts, and source-code bodies are excluded
from canonical events. Existing verbose trace archives remain optional
diagnostic evidence and may be redacted or rotated independently.

## 8. Atomicity, Locking, and Recovery

Control durability is split into two independently testable protocols:

1. a portable ownership protocol for acquisition, release, and stale repair;
2. an immutable event-publication protocol from which projections are rebuilt.

ADR-018 defines lock ownership and repair. ADR-019 defines committed events and
projection replay.

### 8.1 Lock safety and liveness invariants

The implementation must preserve these invariants:

- **Single owner:** at most one `control.lock` directory is published.
- **Complete publication:** a published lock always contains a complete,
  schema-valid `owner.json`; an owner-less published lock is impossible.
- **Exact release:** only the owner UUID that acquired the lock may release it.
- **No live repair:** repair cannot move or remove a lock while its positively
  identified same-host process is alive.
- **No pathname-only deletion:** no cleanup operation unlinks a shared marker
  after a separate identity check.
- **Replacement preservation:** release or repair cannot remove a lock that was
  replaced after the caller obtained its handle.
- **Crash release of transition guard:** process death cannot permanently hold
  the acquisition/repair serialization primitive.
- **Bounded wait:** contention returns a visible timeout without leaking a
  published lock owned by the timed-out caller.
- **Recoverable debris:** abandoned private candidates and quarantined locks are
  non-authoritative and can be diagnosed or cleaned without blocking a new
  valid owner.

### 8.2 Lock state machine

```text
absent
  -> candidate-prepared
  -> owned
  -> released-quarantine

owned
  -> repair-validated
  -> repaired-quarantine

candidate-prepared
  -> abandoned-candidate
```

`candidate-prepared`, `released-quarantine`, `repaired-quarantine`, and
`abandoned-candidate` paths use unique UUID names. Only `control.lock` is
authoritative ownership.

Every acquisition, release, or repair transition first obtains an advisory
exclusive `fcntl.flock` on `locks/control.guard`. This short-lived guard is
supported by the target Linux/macOS Python runtime and is automatically released
by the kernel when a process exits.

Acquisition prepares a complete private directory containing `owner.json`,
flushes it, then atomically renames that directory to `control.lock` while
holding the guard. Therefore there is no creation-to-owner-publication window.
If `control.lock` already exists, the private candidate is removed by its own
creator or retained as non-authoritative diagnostic debris.

Release reacquires the guard, validates the owner UUID and filesystem identity,
then atomically renames `control.lock` to a unique released quarantine before
cleanup. Repair follows the same pattern after proving same-host owner death or
receiving explicit guarded authorization. Because acquisition also requires the
guard, no new lock can replace the path between validation and rename.

Repair has no in-lock claim marker. The atomic rename to a unique repaired
quarantine is the claim. A crash before rename changes nothing; a crash after
rename leaves a non-authoritative quarantine and permits a new acquisition.

### 8.3 Event publication and projection

A single append-only JSONL file is not authoritative because process
interruption can expose a torn final record. Each authoritative event is instead
an immutable file:

```text
events/<zero-padded-revision>-<event-id>.json
```

While holding `control.lock`, the writer:

1. reads the latest committed revision;
2. writes one complete event to a unique file under `pending/`;
3. flushes the file and validates its exact serialized contents;
4. atomically renames it into `events/`;
5. flushes the containing directory where supported;
6. rebuilds the next ledger projection in memory;
7. writes and flushes `ledger.json.tmp`;
8. atomically replaces `ledger.json`;
9. releases `control.lock`.

Only files in `events/` are committed authority. Partial files under `pending/`
are ignored by replay, reported by preflight, and quarantined or removed only by
an explicit repair operation. `events.jsonl`, when present for compatibility or
inspection, is a derived view rebuilt from committed event files.

A missing, corrupt, or stale ledger is rebuilt from the longest contiguous
sequence of valid event revisions. A duplicate revision, gap, invalid event, or
event whose declared revision does not match its filename blocks mutation.
Interrupted ledger temporary files are non-authoritative and never override a
projection rebuilt from committed events.

### 8.4 Required interruption matrix

The lock and event protocols are not complete until deterministic tests cover
process interruption:

| Boundary | Required post-crash state |
|---|---|
| Before candidate owner write | No authoritative lock; private debris is diagnosable. |
| After candidate flush, before lock rename | No authoritative lock; another writer can acquire. |
| After lock rename, before guard release | Complete owned lock; guard is kernel-released on process exit. |
| During release/repair validation | Existing lock remains unchanged. |
| After quarantine rename, before cleanup | No authoritative lock; quarantine preserves evidence. |
| During private event write | No committed event; pending debris is diagnosable. |
| After event rename, before ledger replace | Event is committed; replay advances the ledger exactly once. |
| During ledger temporary write | Committed events remain authoritative. |
| After ledger replace, before lock release | Event and ledger revisions agree; later acquisition succeeds after owner-death repair. |

Tests must also coordinate real concurrent processes for acquire/acquire,
acquire/repair, release/acquire, repair/acquire, replacement preservation, and
bounded timeout behavior. Sleep-only race tests are insufficient; barriers or
fault hooks must place processes at the named protocol boundaries.

## 9. Worker Lifecycle

Each worker has one mission slot. Mission states are:

```text
pending-dispatch -> accepted -> running
running -> blocked -> running
running -> completed | failed | cancelled
blocked -> failed | cancelled
accepted | running | blocked -> replaced
```

Terminal states are `completed`, `failed`, `cancelled`, and `replaced`.
Replacement creates a new mission ID and links it to the prior mission.

Lifecycle events include the worker, mission, command, trace, timestamp,
sequence number, reason, blocker category, and optional evidence references.
Events with an older sequence number are retained for audit but cannot regress
the materialized state.

Each active state has `heartbeat_at` and `fresh_until`. Expiry changes the
observation to `stale`; it does not itself claim failure. The controller must
reconcile and either nudge, troubleshoot, cancel, replace, or escalate.

## 10. Mission Commands and Acknowledgements

Every state-changing protocol operation uses a durable command envelope:

- globally unique `command_id`;
- mission and queue item IDs;
- target worker;
- command type and schema version;
- trace and parent-trace IDs;
- payload digest and declared repository/runtime boundaries;
- creation time and optional deadline.

Workers acknowledge commands as `accepted`, `applied`, `rejected`, or
`duplicate`. Repeating the same command ID and digest returns the stored result
without applying it again. Reusing a command ID with a different digest is a
hard conflict.

`cancel-mission` requests cooperative cancellation and records whether the
worker acknowledged it. `replace-mission` cannot reuse the cancelled mission
ID. Access-prompt responses and question replies are commands tied to the active
mission rather than uncorrelated pane input.

## 11. Controller Reconciliation

`cockpit-overseer tick` performs a bounded loop:

1. validate control and queue roots;
2. acquire or confirm the mission wake lease;
3. replay new events and validate the ledger revision;
4. read the active queue item and structured worker status;
5. apply precedence and freshness rules;
6. relay a pending question before other work;
7. choose at most one state-changing action;
8. persist the event and materialized ledger;
9. release the lease and exit.

Conflicts have deterministic outcomes:

| Conflict | Outcome |
|---|---|
| Queue item is terminal but worker is running | Issue cancellation; never reopen the queue item automatically. |
| Worker reports completion for another mission/trace | Retain as unmatched evidence; do not advance state. |
| Ledger says running but lifecycle is stale | Mark observation stale and follow the escalation ladder. |
| Pane says available while protocol says running | Protocol state wins; pane text is diagnostic. |
| Two active missions target one worker | Keep the earliest accepted valid mission, block dispatch, and escalate the conflict. |
| Journal and ledger revision differ | Replay journal and replace the derived ledger. |
| Human suspension conflicts with a scheduled wake | Suspension wins; record a skipped wake and terminate the tick. |

## 12. Recurrent Wake Model

A VP3 recurrent wake stores:

- mission and queue item IDs;
- owner;
- wake intent and stop condition;
- cadence;
- blocker threshold;
- active/suspended/terminated state.

Before acting, a tick acquires a short lease in the control journal. An
overlapping cron invocation that cannot acquire the lease records
`wake-duplicate-skipped` and exits successfully without dispatch.

The escalation ladder is fixed for the MVP:

1. first blocked wake: dispatch focused troubleshooting or request the missing
   answer;
2. second: create an escalation record with evidence, impact, and options;
3. third: request a human decision, suspend the wake, and take no further
   recurrent action.

Completion, cancellation, supersession, terminal queue state, or fulfilled stop
condition terminates the wake. Human suspension prevents lease acquisition.

## 13. Trace and Evidence Correlation

One root trace represents the product mission. Each command or focused worker
dialog receives a child trace. All control events carry the applicable IDs.

Evidence references use typed identifiers, for example:

- `queue:QI-...`;
- `command:<uuid>`;
- `trace:<uuid>`;
- `report:<worker>/<mission>/<sequence>`;
- `test:RUN-...`;
- `review:<repository>/<reference>`;
- `human-decision:<event-id>`.

`cockpit-trace` may index and render these records but cannot mutate them.
Clearance requires queue evidence plus the configured test/review evidence or an
explicit human waiver.

## 14. Canonical Roots and Boundaries

Every mission declares:

- planning/backlog root;
- queue root;
- control root;
- one or more implementation repository roots;
- optional Graphify path;
- runtime, image, CI/CD, IAM, deployment, and environment boundaries.

Preflight verifies that required roots exist, writes are permitted where
declared, and no mission asks a worker to access an undeclared path. Discovery
outside declared roots is blocked pending an amended mission or human approval.

Crossing repository, deployment image, CI/CD, IAM, or runtime boundaries emits
an `architecture-boundary-crossed` event and requires explicit overseer
re-scoping before implementation continues. Git operations remain delegated to
the applicable Gitflow operator.

## 15. Security, Safety, and Portability

- Root paths must be absolute, normalized, and explicitly configured.
- Command payloads are data, never shell-evaluated strings.
- Tool output must not expose secrets stored in worker environments.
- Raw tmux operations remain an explicit human-requested diagnostic exception.
- State writes are fail-closed; malformed authoritative data is surfaced.
- File permissions follow user-private configuration defaults.
- Locks, timestamps, temporary replacement, and scripts remain compatible with
  Bash 3.2 and standard Linux/macOS filesystems.
- No always-running daemon or new database dependency is required.

## 16. Compatibility and Migration

VP3 is additive:

1. Existing queue items and ADR-010 storage remain unchanged.
2. Existing trace archives remain readable as diagnostic evidence.
3. Legacy wakes continue to list and cancel, but doctor labels direct-message
   recurrent wakes as legacy and recommends migration to controller ticks.
4. Existing workers without lifecycle capability are reported as
   `legacy-observed`; they may finish their current mission but cannot receive a
   VP3 replacement command.
5. `bootstrap.sh global` updates managed binaries idempotently.
6. Generated project-owned cockpit launchers and overlays are never overwritten
   contrary to `MANIFEST.toml`; setup/update guidance adds the required roots and
   capabilities through the existing ownership contract.

Schema migration writes a backup before replacing canonical control files.
Unknown future schema versions stop mutation and instruct the operator to
upgrade.

## 17. Failure Modes

| Failure | Required behavior |
|---|---|
| Corrupt ledger | Replay the journal and atomically rebuild it. |
| Corrupt journal | Stop mutations and require explicit repair with backup. |
| Stale lock | Diagnose owner; remove only through the guarded repair rule. |
| Worker disappears | Mark observation stale, then troubleshoot/cancel/escalate. |
| Command delivery is uncertain | Retry the same command ID; never mint a new command until its result is reconciled. |
| Duplicate wake | Lease loser records a skipped event and exits. |
| Queue/control roots disagree | Block dispatch and require root correction. |
| Trace evidence is missing | Keep mission state but block review/clearance that requires the evidence. |
| Overseer context is cleared | Reconstruct from control root, queue, and structured status. |

## 18. Verification Strategy

### Contract tests

- schema validation and forward-version refusal;
- lifecycle transition table;
- idempotent command replay and digest conflict;
- precedence and reconciliation table;
- wake stop-condition and escalation rules.

### Fault-injection tests

- process interruption at every boundary in section 8.4;
- malformed ledger and journal records;
- stale, contended, replaced, and quarantined locks;
- concurrent acquire, release, and repair under the transition guard;
- abandoned private lock candidates and event candidates;
- lost acknowledgement and duplicate command delivery;
- worker heartbeat expiry;
- overlapping recurrent wakes.

### Compatibility tests

- legacy queue and trace data remain readable;
- legacy wake detection and migration guidance;
- bootstrap update preserves project-owned template files;
- Linux and macOS-safe lock and replacement behavior.

### End-to-end resilience test

Create a queue-backed mission, dispatch it, clear the overseer session, rebuild
state, detect a stalled worker, cancel or replace the mission, receive a
structured completion report, attach a governed test run, clear the queue item,
and prove the recurrent wake terminated.

## 19. Decisions

- [ADR-011](../ADRs/ADR-011-control-plane-ownership.md)
- [ADR-012](../ADRs/ADR-012-control-store-persistence.md)
- [ADR-013](../ADRs/ADR-013-worker-lifecycle-command-protocol.md)
- [ADR-014](../ADRs/ADR-014-deterministic-reconciliation.md)
- [ADR-015](../ADRs/ADR-015-intent-aware-wake-leases.md)
- [ADR-016](../ADRs/ADR-016-trace-and-boundary-correlation.md)
- [ADR-017](../ADRs/ADR-017-control-plane-compatibility.md)
- [ADR-018](../ADRs/ADR-018-portable-lock-repair-protocol.md)
- [ADR-019](../ADRs/ADR-019-immutable-event-publication.md)
