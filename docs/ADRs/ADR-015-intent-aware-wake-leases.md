# ADR-015: Intent-Aware Wake Leases and Termination

## Status

Accepted

## Context

Recurring wakes can overlap, repeat status without progress, and continue after
their mission is obsolete. Existing scheduler state does not carry a durable
mission stop condition.

## Decision

Store mission ID, queue item, owner, intent, stop condition, cadence, blocker
threshold, and lifecycle with every VP3 wake. Scheduled jobs invoke
`cockpit-overseer tick`; they do not paste an autonomous prose mission directly.

Each tick acquires a short event-backed mission lease. A concurrent loser records
a duplicate-skip event and exits. A tick takes at most one state-changing action.

Blocked wakes use a fixed ladder: troubleshoot on the first, create an
escalation on the second, and request human input plus suspend on the third.
Completion, cancellation, supersession, terminal queue state, or fulfilled stop
condition terminates the wake.

## Consequences

### Positive

- Recurrent oversight is bounded and self-terminating.
- Cron overlap cannot duplicate dispatch.
- Wake intent survives overseer context loss.

### Negative

- Legacy direct-message wakes require migration.
- Lease and stop-condition states add scheduler metadata.

### Risks

- A stuck lease could delay progress; leases expire and are reconciled rather
  than removed silently.

## Alternatives Considered

### Keep recurrent prompt injection

Rejected because each wake must rediscover intent and may repeat obsolete work.

### Run a permanent controller daemon

Rejected because cron/`at` plus short leases satisfy the MVP with less
operational complexity.
