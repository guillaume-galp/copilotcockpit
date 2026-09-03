# ADR-013: Worker Lifecycle and Idempotent Mission Commands

## Status

Accepted

## Context

Pane markers currently conflate availability, execution, blocking, and
completion. Commands delivered through tmux can be duplicated or replaced
without a durable acknowledgement.

## Decision

Extend `cockpit-protocol` with a versioned worker lifecycle and command
contract.

Mission states are `pending-dispatch`, `accepted`, `running`, `blocked`,
`completed`, `failed`, `cancelled`, and `replaced`. Lifecycle events carry
mission, worker, command, trace, sequence, freshness, blocker, and evidence
fields. Terminal states cannot regress.

Every state-changing operation uses a durable command ID and payload digest.
Workers acknowledge `accepted`, `applied`, `rejected`, or `duplicate`.
Redelivery of the same ID and digest returns the stored result; reuse with a
different digest is a conflict.

Managed commands include dispatch, question/reply, access-prompt response,
cancel, and replace. Each worker retains one active mission slot.

## Consequences

### Positive

- Worker state is machine-readable and restart-safe.
- Lost delivery can be retried without duplicate action.
- Cancellation and replacement no longer require raw tmux control.

### Negative

- Worker skills and protocol implementations must adopt the schema together.
- Legacy workers have reduced capabilities during migration.

### Risks

- Incorrect sequence handling could regress state; transition tables and replay
  tests are mandatory.

## Alternatives Considered

### Continue parsing pane markers

Rejected because text presentation is neither durable nor a reliable protocol.

### Use a new command ID for every retry

Rejected because uncertain delivery would produce duplicate missions.
