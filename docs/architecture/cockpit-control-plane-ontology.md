# Cockpit Control-Plane Ontology

> Status: Proposed context packet for post-TH3 planning and implementation  
> Scope: shared vocabulary, ownership, evidence, and modularity context for
> humans and agents  
> Mechanism details: [VP3 control-plane architecture](./overseer-control-plane.md)
> and [ADR-011](../ADRs/ADR-011-control-plane-ownership.md) through
> [ADR-019](../ADRs/ADR-019-immutable-event-publication.md)

## 1. Philosophy

The cockpit control plane makes orchestration executable from durable evidence,
not from chat memory, pane intuition, or model-specific prompting habits.

Tools own state and enforce transitions. Skills explain how humans and agents
operate those tools. Pane text helps diagnosis but is never authority.

The design keeps the simplest viable local architecture: tmux remains the
process host, files remain the durable store, and every state-changing
controller turn is bounded, replayable, and fail-closed.

## 2. Governing principles

| Principle | Meaning |
|---|---|
| Deterministic replay | The same committed events rebuild the same ledger and decisions. |
| Idempotency | Retrying the same command ID and digest returns the same outcome. |
| Bounded action | One controller tick takes at most one state-changing action, then exits. |
| Explicit ambiguity | Conflicts are recorded and blocked or escalated; the controller does not guess. |
| Portability | Local Linux/macOS filesystem semantics and Python standard-library mechanisms are preferred. |
| Non-destructive recovery | Repair rebuilds derived state or quarantines non-authority; committed authority is not silently rewritten. |
| Proportional assurance | Test and review depth scales with risk while preserving core contracts. |

## 3. Context map

```text
                 human / product-owner
                         |
                         v
                 cockpit-queue
          product FIFO authority (QI-*)
                         |
                         v
scheduled wake ---> cockpit-overseer tick <--- reviewer / troubleshooter
   intent only       controller/reconciler          evidence and decisions
                         |
        +----------------+----------------+
        |                |                |
        v                v                v
 cockpit-control   cockpit-protocol   cockpit-trace
 control store     worker API         derived evidence view
        |                |                |
        v                v                v
 events + ledger    tmux workers       trace archive
```

Backlog and Git evidence frame what should be built and what changed; queue and
control events frame what runtime work is active; trace and reports explain why
the controller reached a decision.

## 4. Actors

| Actor | Role | Normal authority |
|---|---|---|
| Human | Approves scope, waivers, suspensions, and escalation decisions. | Explicit decision for the addressed action. |
| Product-owner | Owns backlog/theme/story planning and later ADR registration in the plan. | Planning state and prioritization. |
| Overseer/controller | Reconciles evidence and selects one valid transition. | Controller decisions in the control journal. |
| Worker | Performs one bounded mission and emits lifecycle/report evidence. | Structured lifecycle and command acknowledgements for its mission. |
| Scheduler/wake | Invokes controller ticks according to intent and stop conditions. | Wake metadata and leases; not mission transitions by itself. |
| Reviewer/troubleshooter | Produces review, diagnosis, test, or repair evidence. | Evidence references; human/controller decides transition impact. |

## 5. Entities and canonical identifiers

| Entity | Typical ID/reference | Owner | Notes |
|---|---|---|---|
| Queue item | `QI-*` | `cockpit-queue` | Product-work unit and FIFO position. |
| Mission | UUID | control journal/controller | Runtime orchestration unit linked to a queue item. |
| Command | UUID | command protocol/control journal | Idempotent state-changing request. |
| Acknowledgement | command ID + revision | worker/protocol | `accepted`, `applied`, `rejected`, or `duplicate`. |
| Worker observation | worker ID + mission ID + sequence | worker/protocol | Lifecycle and freshness evidence. |
| Lifecycle event | event revision + event ID | control journal | Accepted record of worker state transition. |
| Question/access prompt | command ID | mission control | Prompt body lives outside canonical event; event stores references/digests. |
| Escalation | escalation ID / event ID | controller | Bounded human-readable decision request. |
| Wake lease | mission/wake ID + revision | wake/controller | Suppresses duplicate recurrent ticks. |
| Trace/evidence | `trace:<uuid>`, `test:RUN-*` | producer; trace is derived | Causal reconstruction, not state authority. |
| Backlog/theme/story | path/ID | product-owner/planning | Planning authority, not runtime mission state. |
| Git change/review | commit, branch, PR, review ID | repository/Gitflow operator | Implementation evidence, not queue or mission authority. |

## 6. Bounded contexts and ownership

| Bounded context | Owns | Must not own | Main seams |
|---|---|---|---|
| Control root, schemas, validation, compatibility | `COCKPIT_CONTROL_ROOT`, schemas, preflight compatibility, forward-version refusal. | Queue progression or worker behavior. | Root resolver, schema validators, compatibility diagnostics. |
| Lock ownership/acquire/release/repair | `locks/control.lock`, owner metadata, guarded repair/quarantine. | Event allocation semantics. | Portable lock and repair protocol. |
| Immutable journal/projection/replay | Committed `events/`, derived `ledger.json`, rebuildable `events.jsonl`. | Product FIFO state. | Event publisher, history reader, projection fold. |
| Worker lifecycle/freshness | Lifecycle vocabulary, transition table, heartbeat/freshness observations. | Controller recovery decisions. | Lifecycle builders, validators, observers. |
| Mission command/question transitions | Command envelopes, acknowledgements, questions, replies, cancel, replace, recovery commands. | Raw tmux transport. | Command registry, mission-control fold. |
| Controller evidence/decision/reconciliation | Precedence, one-action tick, conflict handling, escalation selection. | Low-level file locking or queue storage. | Evidence snapshot, action selector, tick reporter. |
| Wake leases/scheduling | Wake intent, cadence, duplicate suppression, stop conditions. | Direct mission advancement outside tick. | Scheduler adapters, wake lease records. |
| CLI parsing/rendering/adapters | Human command surface, output format, subprocess/tmux/queue adapters. | Domain rules hidden in presentation. | Thin CLI functions and report formatters. |

Dependency direction should flow inward from adapters to domain contracts, not
from domain code back to CLI rendering.

## 7. Authority and derived state

| Source | Kind | Authority |
|---|---|---|
| Explicit human decision | Authoritative | Wins for the addressed approval, waiver, suspension, or escalation. |
| `cockpit-queue` item/event state | Authoritative | FIFO order and product delivery state. |
| Committed control event files | Authoritative | Mission runtime history, commands, lifecycle, wake leases, controller decisions. |
| `ledger.json` | Derived | Replayable projection; can be rebuilt from committed events. |
| Structured worker report | Evidence | Valid only when mission, command, and trace IDs match. |
| Live protocol status | Observation | Freshness/recovery input; not final mission authority. |
| Raw pane text | Diagnostic | Explains symptoms; never advances state. |
| Trace archive/index | Derived evidence | Rebuildable causal view; does not mutate canonical state. |
| Git/backlog files | Planning/implementation authority | Evidence of planned and implemented work; not worker lifecycle state. |

## 8. State transitions and ownership

| Transition area | Owner | Summary |
|---|---|---|
| Queue state transitions | `cockpit-queue` | Product work advances through FIFO states and clears only with required evidence or waiver. |
| Mission lifecycle | Control journal + worker/protocol | Worker emits `accepted`, `running`, `blocked`, `completed`, `failed`, `cancelled`, `replaced`; invalid regressions are retained but do not move state. |
| Command acknowledgement | Command protocol | Same command ID/digest is retry-safe; changed digest is conflict. |
| Question/reply/access prompt | Mission command context | Prompt and answer are correlated commands; canonical events store references, not body text. |
| Cancellation/replacement | Mission control | Cooperative commands tied to active mission; replacement creates a new mission ID. |
| Stale recovery | Controller | Stale is an observation, not failure; recovery ladder advances one rung per eligible tick. |
| Wake termination | Wake/controller | Completion, cancellation, supersession, terminal queue state, stop condition, or human suspension stops recurrence. |

Worker lifecycle shape:

```text
pending-dispatch -> accepted -> running
running -> blocked -> running
running -> completed | failed | cancelled
accepted | running | blocked -> replaced
blocked -> failed | cancelled
```

## 9. Evidence precedence and one-action semantics

Controller precedence is fixed by [ADR-014](../ADRs/ADR-014-deterministic-reconciliation.md):

| # | Evidence source | Role |
|---|---|---|
| 1 | Explicit human decision | Overrides only the addressed decision. |
| 2 | `cockpit-queue` product state | Product/FIFO authority. |
| 3 | Durable command acknowledgements and lifecycle events | Runtime protocol evidence. |
| 4 | Replayed control journal | Canonical runtime history. |
| 5 | Materialized ledger | Derived state, repairable by replay. |
| 6 | Matching structured worker reports | Corroborating evidence. |
| 7 | Live protocol observation | Freshness/reachability input. |
| 8 | Raw pane output | Diagnostic only; advances state: no. |

A controller tick:

1. validates configured roots;
2. settles replay/projection state;
3. reads queue and worker evidence;
4. applies precedence;
5. persists at most one state-changing action;
6. exits.

Projection repair from committed events is not counted as the one action because
it does not create new authority.

## 10. Recovery, reconciliation, escalation, fail-closed

Recovery is finite and non-destructive:

- missing, stale, or corrupt derived projections are rebuilt from committed
  events;
- malformed or missing committed authority blocks mutation and names the repair;
- stale workers follow the bounded ladder:
  `nudge -> troubleshoot -> cancel -> replace -> escalate`;
- queue-terminal plus running-worker conflicts cancel or escalate; the queue is
  not reopened automatically;
- duplicate worker mission claims keep the earliest valid committed claim by
  revision and retain the refused claim as evidence;
- escalation blocks when ambiguity would otherwise require guessing;
- repair never silently deletes or rewrites committed authority.

Fail-closed means no controller, wake, adapter, or skill should infer authority
from missing data, pane prose, path guesses, or unvalidated schema versions.

## 11. System evidence relationships

```text
             docs/plan/backlog.yaml
                     |
                     v
        docs/themes/* stories and ADR links
                     |
                     v
cockpit-queue ---- QI-* ---- control events ---- ledger.json
     |                         |      |              |
     |                         |      |              v
     |                         |      +------ cockpit-overseer status/tick
     |                         |
     v                         +------------- cockpit-trace views
queue events                                 trace/evidence refs
     |
     v
worker mission brief -> cockpit-protocol -> tmux worker
                              |              |
                              v              v
                       command ack      lifecycle/report
                              |              |
                              +------ committed control event
```

Related external evidence:

```text
Git commits / branches / reviews / test RUN-* / human decisions
        |
        +-- stored as typed evidence refs on queue and control events
```

## 12. Proportional assurance

| Change type | Minimum assurance |
|---|---|
| Documentation/vocabulary only | Link and vocabulary consistency review. |
| CLI facade or rendering only | Golden-output tests and import compatibility checks. |
| Schema/validation extraction | Contract tests for valid, invalid, unknown-future, and compatibility cases. |
| Lock/journal/projection changes | Fault-injection and concurrency tests at ADR-018/019 boundaries. |
| Controller decision changes | Precedence table tests, one-action-per-tick tests, conflict cases. |
| Wake/recovery changes | Duplicate wake, lease expiry, stop condition, escalation ladder tests. |
| Cross-boundary behavior | End-to-end resilience proof and explicit human/product-owner approval. |

## 13. Glossary and vocabulary rule

Code, CLI output, ADRs, stories, skills, and docs must use the same canonical
terms for the same entity or transition. New synonyms are defects unless they
are explicitly introduced as aliases in compatibility documentation.

Preferred vocabulary:

- control root, queue root, planning root, implementation root;
- queue item, mission, command, acknowledgement;
- worker lifecycle, heartbeat, freshness, stale observation;
- question, access prompt, reply, access-prompt response;
- wake intent, wake lease, stop condition;
- trace, evidence reference, escalation;
- authority, derived state, diagnostic observation;
- one-action tick, bounded recovery, fail closed.

## 14. Mechanism links

- Component ownership: [ADR-011](../ADRs/ADR-011-control-plane-ownership.md)
- File-backed control store: [ADR-012](../ADRs/ADR-012-control-store-persistence.md)
- Worker lifecycle and command protocol: [ADR-013](../ADRs/ADR-013-worker-lifecycle-command-protocol.md)
- Reconciliation precedence: [ADR-014](../ADRs/ADR-014-deterministic-reconciliation.md)
- Wake leases and termination: [ADR-015](../ADRs/ADR-015-intent-aware-wake-leases.md)
- Evidence and boundaries: [ADR-016](../ADRs/ADR-016-trace-and-boundary-correlation.md)
- Additive adoption: [ADR-017](../ADRs/ADR-017-control-plane-compatibility.md)
- Portable locks: [ADR-018](../ADRs/ADR-018-portable-lock-repair-protocol.md)
- Immutable events and replay: [ADR-019](../ADRs/ADR-019-immutable-event-publication.md)
- Post-TH3 module extraction: [ADR-020](../ADRs/ADR-020-incremental-control-plane-module-extraction.md)
