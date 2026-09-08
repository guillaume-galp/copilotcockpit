# BUG-001: Control-Plane Bootstrap and Durable Dispatch Are Disconnected

**Status:** Open  
**Reported:** 2026-09-07  
**Owner:** CopilotCockpit  
**Severity:** Critical  
**Affected components:** `cockpit-control`, `cockpit-overseer`,
`cockpit-protocol`, `cockpit-queue`, `cockpit-wake`, E2E cockpit launchers  
**Related decisions:** ADR-010, ADR-011, ADR-012, ADR-013, ADR-014, ADR-015,
ADR-016, ADR-017

## Summary

The installed VP3 control-plane tools fail closed at individual boundaries, but
cannot be bootstrapped into one operational controller. A control store can be
initialized without canonical queue/planning/implementation roots, and no
supported command can declare them afterward. The only available dispatch
commands then bypass the durable mission lifecycle and paste text directly into
tmux panes.

This prevents an overseer from truthfully claiming a durable FIFO dispatch,
worker cancellation, or lifecycle-based recovery.

## Symptoms

1. `cockpit-protocol meta cockpit --json` fails unless
   `COCKPIT_CONTROL_ROOT` is explicitly exported.
2. `cockpit-control init` succeeds with `queue_root`, `planning_root`, and
   `implementation_roots` unset.
3. `cockpit-control preflight` reports `ready`, while also reporting no product
   work authority and no declared planning or implementation roots.
4. `cockpit-overseer tick` cannot select a FIFO item because no canonical queue
   root is declared.
5. `cockpit-protocol mission` marks a pane as working but leaves
   `cockpit-control lifecycle-status` and `mission-status` at zero missions.
6. A worker can display an interactive approval prompt while
   `cockpit-protocol status` reports it as `available`.
7. A protocol `send` cannot durably acknowledge, cancel, or replace that
   pending worker action.

## Reproduction

Use an existing project cockpit that still exports only the legacy queue root:

```bash
export COCKPIT_CONTROL_ROOT="$PWD/docs/queue"
cockpit-control init
cockpit-control preflight
cockpit-overseer tick -s udm-k8s
cockpit-protocol mission \
  --worker worker-dev \
  --session udm-k8s \
  --id "$(cat /proc/sys/kernel/random/uuid)" \
  --message "planning-only mission"
cockpit-control lifecycle-status
cockpit-control mission-status
```

Observed results:

- preflight identifies a structurally valid store but no declared product
  boundaries;
- the tick cannot dispatch from the store;
- the protocol mission is visible only in the worker pane;
- the durable store still reports zero missions and zero worker slots.

For the launcher compatibility path, inspect:

```text
e2e/tmux-cockpit.sh
e2e/tmux-cockpit-local.sh
```

Both export and inject `COCKPIT_QUEUE_ROOT` only. They do not configure
`COCKPIT_CONTROL_ROOT` for the shell or tmux session.

## Root Cause

`cockpit-control init` deliberately initializes nullable canonical roots, but
the public CLI has no atomic follow-up operation that declares them. The
controller correctly refuses dispatch from an undeclared root, but
`cockpit-overseer dispatch` and `cockpit-protocol mission` remain direct tmux
delivery paths rather than controller-owned mission transitions.

The legacy E2E templates predate the separate control-root contract. They reuse
`docs/queue` as a queue location and export only the queue variable, leaving
the upgraded control store unconfigured. Status is derived from pane text and
does not model pending interactive confirmation, command acknowledgements, or
the durable lifecycle record.

## Required Fix

1. Provide one atomic bootstrap/configuration operation that creates or binds
   distinct absolute paths for:
   - `COCKPIT_CONTROL_ROOT`;
   - `COCKPIT_QUEUE_ROOT`;
   - planning root; and
   - one or more implementation roots.
2. Make the operation update control metadata and derived ledger atomically,
   or require all roots as mandatory `init` arguments. It must never require
   hand-editing immutable control-store files.
3. Make the controller-owned dispatch path the only normal mission entry point:
   it must persist the dispatch envelope, claim the worker slot, declare the
   mission/queue/trace boundaries, deliver the brief, and await an
   acknowledgement.
4. Make cancellation, replacement, worker heartbeat, and interactive approval
   prompts durable mission lifecycle events.
5. Make `status` distinguish `available`, `working`, `awaiting-approval`,
   `held`, `blocked`, and `unreachable`; pane observation must not override
   durable lifecycle evidence.
6. Update managed E2E launchers/templates to export and inject both root
   variables into tmux. Use separate directories such as
   `docs/cockpit-control` and `docs/cockpit-queue`.
7. Make wake schedules persist control-root and target identity as structured
   fields and inject them at execution time. Retain a backward-compatible
   `stop` alias or update all managed skills and documentation atomically.
8. Report `operationally-blocked` rather than `ready` when a store lacks
   declared work boundaries or required lifecycle capability.

## Acceptance Criteria

- [ ] A fresh E2E cockpit bootstrap creates distinct queue and control roots
  and declares planning/implementation boundaries without manual file edits.
- [ ] `cockpit-control preflight` exits non-zero and reports
  `operationally-blocked` when any required dispatch boundary is absent.
- [ ] One controller dispatch creates exactly one durable mission, command,
  worker-slot claim, trace, and lifecycle record before text reaches a pane.
- [ ] Redelivery of the same command is idempotent; a different command cannot
  claim an occupied worker slot without an explicit replacement event.
- [ ] A pending worker confirmation appears as `awaiting-approval` and can be
  held/canceled/replaced through the protocol with an acknowledgement.
- [ ] Controller `tick` dispatches the earliest eligible queue item and no
  longer requires the legacy direct-tmux dispatch path.
- [ ] A scheduled wake runs with its stored control root and target identity,
  and `stop`/`cancel` behavior is documented and compatible.
- [ ] Existing legacy cockpit state is migrated or diagnosed according to
  ADR-017 without destructive rewriting.

## Regression Coverage

- CLI tests for fresh bootstrap, missing-root refusal, root binding, and
  distinct root paths.
- Controller integration tests covering enqueue, FIFO dispatch, acknowledgement,
  heartbeat, cancellation, replacement, and stale acknowledgement recovery.
- E2E template tests proving both root variables enter the launched tmux
  environment.
- Wake tests proving root/target preservation and compatible cancellation.
- Status tests proving a pending confirmation cannot be rendered as
  `available`.

## Scope Boundary

This report does not change UDM application code, UDM profile semantics, or
Autopilot's product backlog schema. The companion Autopilot issue owns the
backlog-to-queue adapter and cross-workspace mission-packet requirements.

## Worker Acceptance Slice

Controller dispatch envelopes now declare an immutable acceptance deadline,
five minutes after dispatch creation. Redelivery reuses that deadline, command
ID and digest; it never extends the window. At or after the deadline, a tick
records `blocked / dispatch-acceptance-expired` once for unchanged evidence and
stops sending input. The reservation remains held. This is an observation of
silence, not a worker failure, cancellation, replacement, or new mission.
An operator must inspect the command and worker and decide explicit recovery
or queue disposition. Reservation release is not part of this slice:
`replace-mission` currently requires an accepted lifecycle, and queue disposition
alone does not release a worker reservation. Leave it blocked rather than
fabricating acceptance to enable replacement or editing immutable events.

Each delivered brief includes the boundaries, digest, deadline and exact
shell-quoted receipt command:

```bash
COCKPIT_CONTROL_ROOT=/absolute/control cockpit-control accept-dispatch \
  --command-id <uuid> --mission <uuid> --worker worker-dev \
  --queue-item <id> --trace <uuid> --payload-digest sha256:<digest> \
  --fresh-for 300
```

The receipt validates the registered dispatch, canonical boundaries, current
queue brief/state, worker slot and correlation before publishing a single
`worker-lifecycle-accepted` event containing both `worker_lifecycle` (sequence 1)
and `command_acknowledgement` (accepted). The journal rename commits both or
neither; projections are replayable. Acceptance and final transport validation
share the portable control lock. A receipt cannot accept another worker's
slot, a changed payload, or a terminal/replaced mission.

The CLI returns JSON. Only `outcome=accepted` with `start_work=true` permits a
new execution. A repeat returns `duplicate`, `start_work=false` and the original
receipt event ID without publishing or refreshing lifecycle state. Dry-run
returns `would-accept`, `start_work=false` without writing. If the worker loses
the first response or restarts after accepting, this protocol deliberately
does not infer whether work began: duplicates require observation/explicit
recovery, never a second execution. Workers next emit `running` sequence 2 and
renew freshness under the existing lifecycle protocol.

Old reservations without a deadline are diagnosed as
`dispatch-acceptance-unsupported`; no timeout or successful acceptance is
fabricated. Existing immutable history remains readable, but pending legacy
handoffs require explicit operator recovery rather than event rewriting.
This slice does not implement interactive approval prompts, wake migration,
legacy launcher migration, automatic mission replacement, or exactly-once
external side effects.
