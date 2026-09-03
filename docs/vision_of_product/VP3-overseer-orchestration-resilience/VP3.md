# VP3: Holistic Cockpit Control Plane and Overseer Resilience

> Status: Architecture complete · Date: 2026-09-03

## Problem

The UDM VP1 execution exposed operational gaps in the cockpit overseer model:
planning artifacts, implementation worktrees, and the backlog were split across
different locations; workers could stall in interactive prompts while reporting
misleading availability; and periodic wakes repeated status without escalating
or changing state.

These gaps become sharper when the overseer is run by different frontier LLM
families such as GPT Terra, Sol, or Luna. Each model may have different context
retention, tool-use confidence, verbosity, and risk tolerance. The cockpit must
therefore make the overseer role executable from external state and protocol
evidence, not from tacit memory, pane-reading intuition, or model-specific
prompting habits.

## Vision

The overseer is an active control plane, not a status reporter. It has one
writable source of truth, observable worker lifecycle state, bounded escalation,
and the authority to coordinate a safe handoff across planning, implementation,
review, testing, and deployment boundaries.

Any capable LLM should be able to become the overseer by loading the skill,
reading the canonical queue/backlog, inspecting structured worker state, and
choosing the next valid state transition. The desired product outcome is not a
smarter prompt; it is an operating surface where good orchestration is the path
of least resistance and unsafe improvisation is unnecessary.

## Product Scope: A Holistic Cockpit Control Plane

VP3 defines a cohesive cockpit control plane, not only a collection of overseer
policies. Policies, boundaries, guardrails, and success criteria specify the
desired behavior; corresponding tools and durable mechanisms must make that
behavior observable, enforceable, recoverable, and efficient.

The control plane spans the complete orchestration lifecycle:

| Plane | Product responsibility | Expected mechanism |
|---|---|---|
| Intent plane | Preserve the human mission, wake purpose, stop condition, approvals, and waivers. | Durable mission record linked to queue item and trace IDs. |
| State plane | Maintain one authoritative view of queue progress, worker ownership, blockers, and allowed transitions. | Canonical backlog/queue plus the overseer control ledger. |
| Command plane | Dispatch, cancel, replace, answer, suspend, resume, and clear work without raw tmux manipulation. | Managed `cockpit-protocol` and queue command verbs. |
| Observation plane | Report worker lifecycle and progress from structured evidence rather than prompt or pane heuristics. | Lifecycle events, JSON status, delta snapshots, and health timestamps. |
| Recovery plane | Reconcile contradictory state, recover after context loss, and escalate stalled work within finite bounds. | Reconciliation command, stale-mission detection, escalation records, and restart-safe state. |
| Evidence plane | Connect decisions and state transitions to their causes and outcomes. | Trace archive, worker reports, queue events, test run IDs, and human decisions. |
| Resource plane | Keep oversight proportional to useful worker execution. | Minimal wake mode, delta reads, AIC/token budgets, and automatic wake suspension. |
| Boundary plane | Detect when a story crosses repository, runtime, image, IAM, CI/CD, or deployment scope. | Declared mission boundaries, preflight checks, and architecture escalation gates. |

The architecture phase must decide how these planes are packaged, but the
product intent is one interoperable control surface rather than unrelated shell
helpers. Existing tools should evolve around clear ownership:

| Tool or capability | VP3 direction |
|---|---|
| `cockpit-protocol` | Becomes the managed command and worker-lifecycle API, including mission acceptance, cancellation, replacement, access-prompt response, questions, and terminal reports. |
| `cockpit-overseer` | Becomes the compact controller and reconciler: read durable state, compare evidence, select one valid transition, update the ledger, and emit escalation records. |
| `cockpit-queue` | Remains the durable product-work state machine and links each active item to worker missions, traces, and clearance evidence. |
| `cockpit-wake` | Schedules intent-aware wakes with an owner, stop condition, mission reference, and automatic suspension when escalation or completion terminates the loop. |
| `cockpit-trace` | Provides causal reconstruction across wake, dispatch, question, worker, review, test, and human-decision events. |
| Cockpit preflight/doctor | Verifies canonical roots, writable paths, worker protocol support, queue consistency, required runtime boundaries, and deployed entry-point reachability before work starts. |

Together these mechanisms form the runtime substrate that lets different LLMs
perform the overseer role consistently. Skills remain the operating
instructions, but tools hold state, enforce transitions, expose evidence, and
recover from failure.

## Observed Hindrances and Required Improvements

| Gap | How it hindered progress | Corrective change |
|---|---|---|
| Planning docs live in a separate, untracked workspace | Agents treated the workspace as unsafe, so approved plans were proposed but not persisted. | Use a tracked methodology worktree or explicitly configure it as the writable orchestration root. |
| Backlog and implementation checkout differ | Workers could not naturally read and update the same authoritative state. | Share one canonical planning directory with every worker, or keep the backlog in the implementation repository. |
| Workers can stall at interactive access prompts | A stale authorization dialog prevented mission replacement through the managed protocol. | Add `cancel-mission`, `replace-mission`, and access-prompt response protocol verbs; prevent missions from requiring undeclared paths. |
| Worker state is inferred from panes | Workers reported `available` or `blocked` despite failed or stalled missions. | Emit structured `accepted`, `running`, `blocked`, `failed`, and `completed` lifecycle events. |
| Story scope can cross a new architecture boundary | US6 accumulated CLI, Airflow, DPM, SQL, image, and CI changes without a prompt escalation. | Escalate immediately when work crosses repositories, deployment images, CI/CD, IAM, or runtime boundaries. |
| Wakes can repeat without progress | Ticks reported the same blocker instead of triggering recovery or escalation. | After one blocked tick dispatch troubleshooting; after two create an escalation record; after three request a human decision and suspend repetitive wakes. |
| Planning agents lack a clear write postcondition | The architect prepared artifacts but did not write them despite later authorization. | Require a declared writable output root and verify every planned artifact exists before reporting completion. |
| Reviews discover runtime reachability late | Unit-safe work was not exercised through the deployed DAG/image entry point. | Require entry point, deployed command, image, configuration source, and compatibility route in every review checklist. |
| Overseer instructions depend on implicit memory | A new model or cleared session can forget queue ownership, active missions, or previous escalation decisions. | Persist an overseer control ledger containing current queue item, active worker missions, trace IDs, blocker count, last action, and next allowed action. |
| Model verbosity consumes wake budget | Some models re-read panes and restate the playbook instead of taking a state-changing action. | Make minimal-mode the default for recurrent wakes: inspect deltas, update ledger, act once, then stop. |
| Raw tmux access bypasses governance | Pane scraping can contradict `cockpit-protocol` status and makes behavior model-dependent. | Treat `cockpit-protocol` and `cockpit-overseer` as the only normal observability and dispatch APIs; raw tmux is diagnostic-only by explicit human request. |
| Worker questions and access prompts share no SLA | A worker can wait indefinitely even though the overseer is waking recurrently. | Add a question/access-prompt SLA: relay on the next wake, escalate after one missed wake, and cancel or replace the mission after the bounded threshold. |
| Human intent is not preserved across wakes | A recurrent wake can keep running after the original mission is obsolete or superseded. | Store wake intent, stop condition, and owner in the ledger; every recurrent wake must prove the mission is still current before acting. |

## LLM-Agnostic Overseer Contract

An overseer-ready cockpit provides enough structure that GPT Terra, Sol, Luna,
or a smaller local model can run the same loop without relying on hidden context:

1. **Role boot**: load `e2e-cockpit`, then read only the project overlay,
   queue/backlog pointer, and the current overseer ledger.
2. **State read**: call `cockpit-queue inspect` for the active item and
   `cockpit-protocol status --workers all --json` for worker state.
3. **Evidence check**: trust structured lifecycle events and trace IDs before
   pane tails; use pane tails only to explain the latest state.
4. **Decision**: choose exactly one transition from the allowed state machine:
   dispatch, reply to a question, escalate, mark blocked, request human input,
   clear current, or suspend the wake.
5. **Persistence**: write the ledger update before reporting success.
6. **Stop condition**: end the turn after the state-changing action; do not keep
   exploring while workers own the active mission.

The contract must be short enough to fit in a wake prompt and strict enough that
model style differences do not change the orchestration outcome.

## Overseer Control Ledger

VP3 requires a durable, token-efficient ledger for recurrent wakes and cleared
sessions. It may be implemented as a file, queue metadata, or a protocol status
record, but it must expose these fields:

| Field | Purpose |
|---|---|
| `mission_id` | Stable human-visible mission or queue item identifier. |
| `wake_intent` | Why the recurrent wake exists and what condition stops it. |
| `active_worker_missions` | Worker, trace ID, current lifecycle state, and last accepted brief. |
| `canonical_roots` | Writable planning root, implementation repo root, queue root, and graph path if present. |
| `blocker_count` | Consecutive wakes with no state-changing progress for the same blocker. |
| `last_state_change` | Timestamp, actor, command, and evidence reference for the last meaningful transition. |
| `next_allowed_actions` | Bounded actions the next overseer turn may take without rediscovery. |
| `human_decisions` | Explicit approvals, waivers, rejected options, and pending questions. |

If the ledger is missing, corrupt, or contradicts the queue/protocol state, the
overseer must pause dispatch, rebuild the ledger from durable evidence, and emit
an escalation record instead of guessing.

## Recurrent Wake Policy

Recurrent wakes exist to move the mission forward, not to keep the overseer
alive. Each wake must perform this sequence:

1. Revalidate the wake intent and stop immediately if the mission was delivered,
   cancelled, superseded, or lacks an owner.
2. Read the ledger, queue item, and structured worker states in minimal mode.
3. If a worker question or access prompt is pending, relay it before any other
   work.
4. If no worker state changed since the previous wake, increment the blocker
   count and follow the bounded escalation policy.
5. Take at most one state-changing action.
6. Persist the ledger update and stop.

The default escalation ladder is:

| Consecutive blocked wakes | Required action |
|---|---|
| 1 | Dispatch focused troubleshooting or request the missing worker answer. |
| 2 | Create an escalation record with evidence, impact, and options. |
| 3 | Ask for a human decision and suspend repetitive wakes for that mission. |

## Model-Specific Hinderance Removal

The cockpit should compensate for known LLM differences through product design,
not through per-model folklore.

| Model tendency | Risk to overseer role | Product guardrail |
|---|---|---|
| High-context models may over-investigate | Overseer does worker work and burns budget. | Dispatch-first checklist, one action per wake, and AIC ratio guardrail. |
| Concise models may under-report evidence | State changes become hard to audit. | Mandatory structured lifecycle events and ledger fields. |
| Tool-confident models may bypass protocol | Raw tmux operations desynchronize from managed state. | Protocol-only normal path plus diagnostics exception. |
| Cautious models may stall on uncertainty | FIFO item remains active but idle. | Explicit allowed transitions and bounded escalation thresholds. |
| Cleared or compacted sessions lose intent | Wake repeats obsolete instructions. | Wake intent, stop condition, and human decisions persisted outside chat. |

## Non-Functional Requirements

| # | Requirement | Rationale |
|---|---|---|
| NFR-1 | **Token-efficient**: a normal wake can decide from the ledger, queue item, and structured worker JSON without loading full pane history. | Recurrent oversight must be cheaper than worker execution. |
| NFR-2 | **Model-portable**: no required behavior depends on a model remembering prior turns or inferring state from prose. | Terra, Sol, Luna, and future models must operate the same cockpit safely. |
| NFR-3 | **Protocol-first**: dispatch, status, questions, replies, cancellation, and mission replacement have managed verbs. | Reduces brittle pane-control behavior and enables audit trails. |
| NFR-4 | **Recoverable**: a fresh overseer session can reconstruct active work from durable evidence. | Human operators can change models or clear sessions without losing control. |
| NFR-5 | **Bounded**: blocked recurrence has finite escalation and suspension rules. | Prevents infinite status-only wake loops. |
| NFR-6 | **Auditable**: every state transition links to a trace, worker report, queue event, or explicit human decision. | Makes orchestration reviewable after the fact. |

## MVP Boundary

The first implementing theme should prove the control plane through one complete,
restart-safe mission lifecycle rather than attempting every future orchestration
feature at once.

The VP3 MVP includes:

- a durable mission/overseer ledger linked to a queue item and trace;
- structured worker lifecycle states with freshness and blocker evidence;
- managed dispatch, question/reply, cancel, and replace operations;
- intent-aware recurrent wakes with bounded escalation and automatic suspension;
- reconciliation after an overseer restart or contradictory worker state;
- an end-to-end resilience test covering dispatch through completion or human
  escalation.

The VP3 MVP does not include:

- a general workflow engine or distributed scheduler;
- autonomous deployment approval or production change execution;
- replacement of tmux as the cockpit process host;
- semantic planning of arbitrary multi-product programs;
- model-specific branches of orchestration logic;
- a graphical cockpit UI.

## Architecture Handoff and ADR Agenda

VP3 is ready for architecture when the architect treats the eight control-plane
planes as one system and specifies their contracts, persistence, ownership, and
failure behavior. Architecture should extend the existing cockpit rather than
rewrite the proven queue, trace, wake, and protocol capabilities.

The architecture phase must resolve at least these decisions:

| Decision | Required outcome |
|---|---|
| Control-plane component boundaries | Define ownership and invocation flow among protocol, overseer controller, queue, wake scheduler, trace archive, and doctor/preflight. |
| Durable state model | Select the canonical ledger format, location, schema versioning, atomic write strategy, and relationship to backlog and queue state. |
| Worker lifecycle contract | Define events, valid transitions, timestamps, freshness rules, terminal states, blocker schema, and backward compatibility. |
| Mission command semantics | Specify idempotency and acknowledgement for dispatch, cancel, replace, question/reply, suspend, resume, and clearance. |
| Reconciliation algorithm | Define precedence when ledger, queue, protocol state, pane state, or worker reports disagree. |
| Wake execution model | Define how recurrent wakes carry intent, acquire ownership, avoid duplicate action, escalate, and terminate. |
| Trace and evidence correlation | Define trace/parent-trace propagation and links to queue events, worker reports, tests, reviews, and human decisions. |
| Multi-repository boundaries | Define canonical roots, worker access declarations, architecture escalation triggers, and Gitflow delegation. |
| Compatibility and migration | Define how existing generated cockpits adopt VP3 without losing queue, trace, wake, or project-owned data. |
| Verification strategy | Define contract, fault-injection, restart-recovery, stale-worker, duplicate-wake, and end-to-end resilience tests. |

Likely ADR subjects are component ownership, ledger persistence, worker lifecycle
protocol, command idempotency, wake ownership and termination, reconciliation
precedence, and compatibility/migration. ADR numbering must continue from the
repository sequence when the architecture phase creates them.

## Stricter Overseer Control Loop

1. Read one canonical backlog and verify it against structured worker evidence.
2. Dispatch exactly one bounded story with an explicit runtime boundary.
3. On the first review failure, determine whether it is local or architectural.
4. Escalate immediately for cross-repository, Airflow, Docker, CI/CD, IAM, or deployment-image changes.
5. Require persisted artifacts and a structured worker report before advancing state.
6. Stop repeated wake-only reporting when no state-changing action is possible.

## Success Criteria

1. Every worker mission has a machine-readable lifecycle state and a recoverable
   block reason.
2. The overseer can cancel or replace a stuck mission without raw tmux control.
3. All workers operate against one writable, canonical backlog.
4. Repeated blockers automatically follow the bounded escalation policy.
5. Review gates verify the deployed execution path, not only isolated units.
6. A newly started overseer session can reconstruct the active mission, worker
   ownership, latest blocker, and next valid action without reading chat history.
7. Recurrent wakes stop automatically when their mission is delivered,
   superseded, cancelled, or awaiting a human decision after escalation.
8. The same cockpit mission can be run by GPT Terra, Sol, Luna, or another
   capable model with equivalent state transitions and audit evidence.
9. Overseer AIC/token spend remains materially lower than worker spend during
   normal operation, with minimal mode available as the default recurrent-wake
   path.
10. The cockpit exposes an integrated command, state, observation, recovery, and
    evidence surface; an overseer does not need raw tmux commands or private chat
    memory to complete the control loop.
11. An end-to-end resilience scenario proves that a recurrent mission can
    survive an overseer session reset, detect a stalled worker, cancel or replace
    the mission, escalate when necessary, and stop its wake after completion.
